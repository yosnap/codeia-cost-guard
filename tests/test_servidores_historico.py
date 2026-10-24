"""Historial de servidores: datos hostiles, dedupe por ok_en, IPs (canon, propias, topes), retención con reloj inyectado y borrado real."""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import rutas, servidores_datos as sd, servidores_historico as sh, servidores_lectura as sl  # noqa: E402
from infobar.historico import ErrorHistorico  # noqa: E402

T0 = 1_790_000_000          # 2026-09-21
SID, NOMBRE = "a" * 12, "Prueba"
SRV = {"id": SID, "nombre": NOMBRE}
IP1, IP2 = "203.0.113.9", "93.184.216.34"      # la primera es de documentación (no global): se usan IPs globales reales de prueba abajo
A, B, C = "8.8.8.8", "93.184.216.34", "1.1.1.1"


def entrada(ok_en, top=(), estado="verde", **extra):
    d = {"cpu": {"pct": 12.5, "carga": 0.5, "nucleos": 4}, "memoria": {"total": 1000, "usada": 400}, "disco": {"total": 100, "usado": 50},
         "uptime_s": 1000, "intentos": {"total": 30, "hora": 5, "top": [list(p) for p in top]},
         "sesiones": [{"usuario": "u", "desde": "198.51.100.7"}, {"usuario": "v", "desde": "9.9.9.9"}], "puertos": [22, 80],
         "servicios": [{"nombre": "x", "ok": True}, {"nombre": "y", "ok": False}], "fail2ban": {"instalado": True, "ahora": 2, "total": 9},
         "actualizaciones": {"pendientes": 1, "reinicio": True}}
    e = {"ok_en": ok_en, "intento_en": ok_en, "error": "", "estado": estado, "datos": d,
         "webs": [{"url": "u", "ms": 120, "codigo": 200, "error": ""}, {"url": "v", "ms": None, "codigo": None, "error": "x"}],
         "expuestos": [22], "bastionado": [{"id": "a", "ok": False}, {"id": "b", "ok": True}]}
    e.update(extra)
    return e


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-srv-")
        p = mock.patch.object(rutas, "DATOS", self.dir)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.con = sh.abrir()
        self.addCleanup(self.con.close)

    def reg(self, e, ahora=None, **kw):
        return sh.registrar(self.con, SRV, e, ahora or e.get("ok_en") or T0, **kw)

    def uno(self, sql, *a):
        return self.con.execute(sql, a).fetchone()[0]

    def descartes(self, motivo):
        return int(self.uno("SELECT COALESCE(SUM(CAST(valor AS INTEGER)), 0) FROM meta WHERE clave LIKE ?", f"descartes:{motivo}:%"))


class EsquemaTest(Base):
    def test_clases_cubren_todas_las_columnas(self):
        for tabla in {t for (t,) in self.con.execute("SELECT name FROM sqlite_master WHERE type='table'")} - {"sqlite_sequence"}:
            reales = {r[1] for r in self.con.execute(f"PRAGMA table_info({tabla})")}
            self.assertEqual(reales, set(sh.CLASES_SERVIDORES[tabla]), tabla)
        self.assertEqual(sh.CLASES_SERVIDORES["ips_atacantes"]["ip"], "personal")
        self.assertEqual(sh.CLASES_SERVIDORES["servidores"]["nombre"], "personal")

    def test_permisos_pragmas_y_version(self):
        self.assertEqual(self.uno("PRAGMA secure_delete"), 1)
        self.assertEqual(self.uno("PRAGMA user_version"), max(sh.MIGRACIONES))
        if os.name != "nt":
            self.assertEqual(os.stat(sh.ruta()).st_mode & 0o777, 0o600)

    def test_version_mas_nueva_se_rechaza_y_lectura_sin_fichero_es_sin_datos(self):
        self.con.execute("PRAGMA user_version=99")
        with self.assertRaises(Exception):
            sh.abrir()
        self.con.close()        # en Windows no se puede borrar el fichero con esta conexion aun abierta
        os.remove(sh.ruta())
        with self.assertRaises(sh.SinDatos):
            sh.abrir(solo_lectura=True)

    def test_migracion_hace_copia_sin_ips(self):
        self.reg(entrada(T0, [(A, 5)]))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM ips_atacantes"), 1)
        self.con.close()
        siguiente = max(sh.MIGRACIONES) + 1
        nueva = dict(sh.MIGRACIONES)
        nueva[siguiente] = "CREATE TABLE extra(x INTEGER) STRICT;"
        with mock.patch.object(sh, "MIGRACIONES", nueva):
            self.con = sh.abrir()
        self.assertEqual(self.uno("PRAGMA user_version"), siguiente)
        copias = os.listdir(os.path.join(self.dir, "copias"))
        self.assertEqual(len(copias), 1)
        with open(os.path.join(self.dir, "copias", copias[0]), "rb") as f:
            crudo = f.read()
        self.assertNotIn(A.encode(), crudo)                       # la copia previa no conserva IPs
        c = sqlite3.connect(os.path.join(self.dir, "copias", copias[0]))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM muestras").fetchone()[0], 1)     # pero sí el resto
        c.close()


class DatosHostilesTest(unittest.TestCase):
    def test_canonicas(self):
        self.assertEqual(sd.ip_global("::ffff:8.8.8.8"), "8.8.8.8")
        for malo in ("::ffff:10.0.0.1", "fe80::1%eth0", "100.64.0.1", "1.2.3", "2001:db8::1", "0.0.0.0", "255.255.255.255", "224.0.0.1",
                     "240.0.0.1", "169.254.1.1", "127.0.0.1", "::1", "01.2.3.4", "x" * 46, "", None, 5, "8.8.8.8%1", "８.８.８.８"):
            self.assertIsNone(sd.ip_global(malo), repr(malo))

    def test_entero_estricto(self):
        self.assertIsNone(sd.entero("5", 0, 10))
        self.assertIsNone(sd.entero(True, 0, 10))
        self.assertIsNone(sd.entero(float("nan"), 0, 10))
        self.assertIsNone(sd.entero(None, 0, 10))
        with self.assertRaises(sd.FueraDeRango):
            sd.entero(-5, 0, 10)
        with self.assertRaises(sd.FueraDeRango):
            sd.entero(1e308, 0, 10)
        with self.assertRaises(sd.FueraDeRango):
            sd.porcentaje(150, 100)

    def test_ilegible_es_none_no_cero(self):
        m, top, propios = sd.muestra_de({"datos": {}, "estado": "verde"})
        self.assertIsNone(m["carga_pct"])
        self.assertIsNone(m["intentos_24h"])
        self.assertIsNone(m["sesiones"])
        self.assertEqual((top, propios), ([], 0))

    def test_sesiones_ajenas_solo_con_ips_conocidas(self):
        m, _, _ = sd.muestra_de(entrada(T0), frozenset())
        self.assertIsNone(m["sesiones_ajenas"])
        m, _, _ = sd.muestra_de(entrada(T0), frozenset({"198.51.100.7"}))
        self.assertEqual(m["sesiones_ajenas"], 1)

    def test_top_se_recorta_tras_validar_y_excluye_propias(self):
        top = [("10.0.0.1", 900), ("bad", 800), (A, 5), ("::ffff:8.8.8.8", 9), (B, 4), (C, 3), ("9.9.9.9", 2), ("4.4.4.4", 1)]
        _, ips, propios = sd.muestra_de(entrada(T0, top), frozenset({"4.4.4.4"}))
        self.assertEqual(dict(ips)[A], 9)                       # mapeada y normal: una sola IP, el máximo
        self.assertEqual(len(ips), 4)
        self.assertNotIn("10.0.0.1", dict(ips))


class RegistroTest(Base):
    def test_lectura_buena_fila_y_hora(self):
        self.assertEqual(self.reg(entrada(T0, [(A, 7)])), "buena")
        r = self.con.execute("SELECT * FROM muestras").fetchone()
        self.assertEqual((r["estado"], r["carga_pct"], r["mem_pct"], r["disco_pct"], r["intentos_24h"], r["sesiones"], r["servicios_caidos"],
                          r["webs_caidas"], r["web_ms_max"], r["faltas_proteccion"], r["reinicio_pendiente"], r["baneadas"]),
                         ("verde", 12.5, 40.0, 50.0, 30, 2, 1, 1, 120, 1, 1, 2))
        h = self.con.execute("SELECT * FROM horas_servidor").fetchone()
        self.assertEqual((h["n"], h["errores"], h["intentos_pico_24h"]), (1, 0, 30))

    def test_dedupe_por_ok_en_y_fallos_no_repiten_metricas(self):
        self.reg(entrada(T0, [(A, 7)]))
        malo = entrada(T0, [(A, 7)], error="timeout", estado="rojo")          # entrada con error conserva datos viejos
        for i in range(1, 11):
            malo = dict(malo, intento_en=T0 + 300 * i)
            self.assertEqual(self.reg(malo, ahora=T0 + 300 * i), "estado")
        self.assertEqual(self.uno("SELECT COUNT(*) FROM muestras WHERE estado!='error'"), 1)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM muestras WHERE estado='error'"), 10)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM muestras WHERE estado='error' AND carga_pct IS NOT NULL"), 0)
        self.assertEqual(self.uno("SELECT horas_vista FROM ips_atacantes"), 1)
        self.assertIsNone(self.reg(entrada(T0, [(A, 7)])))                    # releer la misma ok_en no anota nada
        self.assertEqual(self.uno("SELECT COUNT(*) FROM muestras WHERE estado!='error'"), 1)

    def test_una_muestra_por_franja_pero_el_pico_se_conserva(self):
        self.reg(entrada(T0))
        e = entrada(T0 + 60)
        e["datos"]["intentos"]["total"] = 900
        self.reg(e)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM muestras"), 1)
        self.assertEqual(self.uno("SELECT intentos_pico_24h FROM horas_servidor"), 900)

    def test_ip_horas_vista_max_y_monotonia(self):
        self.reg(entrada(T0, [(A, 5)]))
        for i in range(1, 60):                                                # 60 lecturas en la misma hora
            self.reg(entrada(T0 + i * 10, [(A, 3 + i % 7)]))
        r = self.con.execute("SELECT * FROM ips_atacantes").fetchone()
        self.assertEqual((r["horas_vista"], r["max_24h"]), (1 if T0 // 3600 == (T0 + 590) // 3600 else 2, 9))
        self.reg(entrada(T0 + 7200, [(A, 1)]))
        r = self.con.execute("SELECT * FROM ips_atacantes").fetchone()
        self.assertGreaterEqual(r["horas_vista"], 2)
        self.assertEqual(r["max_24h"], 9)                                     # solo sube
        self.assertEqual(r["ultima"], T0 + 7200)

    def test_ips_propias_privadas_y_raras_no_se_guardan(self):
        top = [("10.0.0.1", 9), ("::ffff:10.0.0.1", 9), ("fe80::1%x", 9), ("1.2.3", 9), (B, 4)]
        self.reg(entrada(T0, top), conocidas=[B])
        self.assertEqual(self.uno("SELECT COUNT(*) FROM ips_atacantes"), 0)
        self.assertEqual(self.descartes("ips_propias"), 1)

    def test_guardar_ips_falso_no_guarda(self):
        self.reg(entrada(T0, [(A, 5)]), guardar_ips=False)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM ips_atacantes"), 0)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM muestras"), 1)

    def test_fuera_de_rango_descarta_la_muestra_entera_y_se_cuenta(self):
        e = entrada(T0)
        e["datos"]["intentos"]["total"] = -5
        self.assertIsNone(self.reg(e))
        e = entrada(T0 + 400)
        e["datos"]["intentos"]["hora"] = 1e308
        self.assertIsNone(self.reg(e))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM muestras"), 0)
        self.assertEqual(self.descartes("fuera_de_rango"), 2)

    def test_reloj_del_futuro_o_absurdo_se_descarta(self):
        self.assertIsNone(self.reg(entrada(T0 + 10 ** 7), ahora=T0))
        self.assertIsNone(self.reg(entrada(5), ahora=T0))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM muestras"), 0)
        self.assertEqual(self.descartes("reloj"), 2)

    def test_topes_altas_dia_y_expulsion(self):
        gen = (f"{a}.{b}.{c}.1" for a in range(11, 200) for b in range(0, 255) for c in range(0, 255))
        with mock.patch.object(sh, "MAX_ALTAS_DIA", 6), mock.patch.object(sh, "MAX_IPS", 100):
            self.reg(entrada(T0, [(A, 50)]))
            for i in range(1, 6):
                self.reg(entrada(T0 + 300 * i, [(next(gen), 1) for _ in range(4)]))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM ips_atacantes"), 6)       # tope diario
        self.assertTrue(self.uno("SELECT COUNT(*) FROM ips_atacantes WHERE ip=?", A))      # la persistente sigue
        self.assertGreater(self.descartes("ips_sobre_tope_diario"), 0)
        with mock.patch.object(sh, "MAX_ALTAS_DIA", 10 ** 6), mock.patch.object(sh, "MAX_IPS", 3):
            self.reg(entrada(T0 + 86400 + 300, [(B, 5)]))
        self.assertLessEqual(self.uno("SELECT COUNT(*) FROM ips_atacantes"), 4)

    def test_expulsion_quita_primero_las_menos_relevantes(self):
        with mock.patch.object(sh, "MAX_IPS", 3):
            self.reg(entrada(T0, [(A, 5)]))
            self.reg(entrada(T0 + 7200, [(A, 5)]))                      # A: vista en dos horas distintas
            self.reg(entrada(T0 + 7500, [(B, 1), (C, 1)]))
            self.reg(entrada(T0 + 7800, [("9.9.9.9", 1)]))              # al llegar la cuarta se expulsa una de las de una sola hora
        ips = {r[0] for r in self.con.execute("SELECT ip FROM ips_atacantes")}
        self.assertIn(A, ips)
        self.assertEqual(len(ips), 3)

    def test_intentos_fallidos_seguidos_no_llenan_la_base(self):
        self.reg(entrada(T0))
        for i in range(1, 11):
            self.reg(entrada(T0, error="timeout", estado="rojo", intento_en=T0 + 60 * i), ahora=T0 + 60 * i)
        self.assertLessEqual(self.uno("SELECT COUNT(*) FROM muestras WHERE estado='error'"), 2)
        self.assertEqual(self.uno("SELECT errores FROM horas_servidor"), 10)         # pero la hora sí cuenta todos

    def test_identidad_estable_al_renombrar_y_distinta_al_reanadir(self):
        self.reg(entrada(T0))
        sh.registrar(self.con, {"id": SID, "nombre": "Otro nombre"}, entrada(T0 + 400), T0 + 400)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM servidores"), 1)
        self.assertEqual(self.uno("SELECT nombre FROM servidores"), "Otro nombre")
        sh.registrar(self.con, {"id": "b" * 12, "nombre": "Otro nombre"}, entrada(T0), T0)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM servidores"), 2)

    def test_id_o_nombre_invalidos_no_escriben(self):
        for srv in ({"id": "corto", "nombre": "x"}, {"id": "a" * 12, "nombre": ""}, {"id": "a" * 12, "nombre": "x" * 41}, {}):
            self.assertIsNone(sh.registrar(self.con, srv, entrada(T0), T0))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM servidores"), 0)


class RetencionTest(Base):
    POL = {SID: {"guardar_ips": True, "retencion_dias": 90}}

    def test_purga_por_ultima_con_reloj_inyectado(self):
        self.reg(entrada(T0, [(A, 5)]))
        self.reg(entrada(T0 + 80 * 86400, [(B, 5)]))
        r = sh.mantener(self.con, self.POL, T0 + 95 * 86400)
        self.assertEqual(r["ips_borradas"], 1)                 # A (95 días) fuera; B (15 días) dentro
        self.assertEqual([x[0] for x in self.con.execute("SELECT ip FROM ips_atacantes")], [B])

    def test_crudo_14_dias_pero_horas_permanentes(self):
        self.reg(entrada(T0))
        sh.mantener(self.con, self.POL, T0 + 20 * 86400)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM muestras"), 0)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM horas_servidor"), 1)

    def test_filtro_al_leer_aunque_no_haya_purgado_y_bajar_retencion(self):
        self.reg(entrada(T0, [(A, 5)]))
        ahora = T0 + 40 * 86400
        self.assertEqual(len(sl.ips(self.con, SID, ahora, 90)), 1)
        self.assertEqual(len(sl.ips(self.con, SID, ahora, 30)), 0)       # retención bajada: no sale aunque siga en la base
        self.assertEqual(len(sl.ips(self.con, SID, ahora, 90)), 1)       # y subirla no resucita lo ya borrado (no se ha borrado nada aún)
        self.assertEqual(sl.ips(self.con, SID, T0 + 100, 90)[0]["caduca_en"], T0 + 90 * 86400)

    def test_servidor_quitado_o_sin_guardar_ips_pierde_las_ips(self):
        self.reg(entrada(T0, [(A, 5)]))
        sh.mantener(self.con, {SID: {"guardar_ips": False}}, T0 + 10)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM ips_atacantes"), 0)
        self.reg(entrada(T0 + 400, [(B, 5)]))
        sh.mantener(self.con, {}, T0 + 500)                    # ya no está en la config
        self.assertEqual(self.uno("SELECT COUNT(*) FROM ips_atacantes"), 0)
        self.assertIsNotNone(self.uno("SELECT retirado_en FROM servidores"))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM horas_servidor"), 1)

    def test_olvidar_ips_y_borrar_servidor(self):
        self.reg(entrada(T0, [(A, 5)]))
        self.assertEqual(sh.olvidar_ips(self.con, SID)[0], 1)
        self.reg(entrada(T0 + 400, [(B, 5)]))
        self.assertEqual(sh.olvidar_ips(self.con)[0], 1)
        self.assertEqual(sh.borrar_servidor(self.con, SID), 1)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM horas_servidor"), 0)

    def test_bytes_de_la_base_y_del_wal_sin_ips_tras_borrar_con_un_lector_abierto_y_sin_el(self):
        self.reg(entrada(T0, [(A, 5), (B, 4)]))
        lector = sh.abrir(solo_lectura=True)
        lector.execute("BEGIN")
        lector.execute("SELECT COUNT(*) FROM ips_atacantes").fetchone()
        sh.mantener(self.con, {SID: {"guardar_ips": False}}, T0 + 10)
        lector.execute("COMMIT")
        lector.close()
        r = sh.mantener(self.con, {SID: {"guardar_ips": False}}, T0 + 20)
        self.assertTrue(r["consolidado"])
        self.assertEqual(self.uno("SELECT valor FROM meta WHERE clave='consolidado_en'"), str(T0 + 20))
        for suf in ("", "-wal"):
            p = sh.ruta() + suf
            if os.path.exists(p):
                with open(p, "rb") as f:
                    crudo = f.read()
                self.assertNotIn(A.encode(), crudo, suf)
                self.assertNotIn(B.encode(), crudo, suf)


class RelojTest(Base):
    def test_reloj_que_estuvo_adelantado_no_bloquea_ni_alarga_la_retencion(self):
        futuro = T0 + 365 * 86400
        self.reg(entrada(futuro, [(A, 5)]), ahora=futuro)                 # el Mac creía estar un año por delante
        # el reloj se corrige: lo nuevo se registra y lo marcado en el futuro se trae al presente
        self.assertEqual(self.reg(entrada(T0 + 100, [(B, 5)]), ahora=T0 + 100), "buena")
        self.assertEqual(self.uno("SELECT COUNT(*) FROM muestras WHERE estado!='error'"), 2)
        sh.mantener(self.con, {SID: {"guardar_ips": True, "retencion_dias": 7}}, T0 + 200)
        self.assertEqual(self.uno("SELECT MAX(ultima) FROM ips_atacantes"), T0 + 200)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM horas_servidor WHERE hora > ?", T0 + 4000), 0)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM muestras WHERE ts > ?", T0 + 4000), 0)
        sh.mantener(self.con, {SID: {"guardar_ips": True, "retencion_dias": 7}}, T0 + 8 * 86400)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM ips_atacantes"), 0)       # envejece y caduca como cualquier otra

    def test_lectura_acota_ultima_al_presente_y_no_sirve_ips_fuera_de_politica(self):
        futuro = T0 + 365 * 86400
        self.reg(entrada(futuro, [(A, 5)]), ahora=futuro)
        r = sl.ips(self.con, SID, T0, 7)
        self.assertEqual(r[0]["ultima"], T0)
        self.assertEqual(r[0]["caduca_en"], T0 + 7 * 86400)
        self.assertEqual(sl.ips(self.con, SID, T0, 7, guardar_ips=False), [])
        sh.mantener(self.con, {}, T0 + 10)                                      # servidor quitado de la config
        self.assertEqual(sl.ips(self.con, SID, T0 + 10, 7), [])

    def test_intento_fallido_en_el_futuro_no_se_anota_ni_bloquea_los_siguientes(self):
        futuro = T0 + 10 ** 7
        self.assertIsNone(self.reg(entrada(T0, error="x", estado="rojo", intento_en=futuro), ahora=T0))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM muestras"), 0)
        self.assertEqual(self.reg(entrada(T0, error="x", estado="rojo", intento_en=T0 + 600), ahora=T0 + 600), "estado")


class MantenimientoTest(Base):
    def test_retencion_propia_de_cada_servidor(self):
        otro = {"id": "c" * 12, "nombre": "Otro"}
        self.reg(entrada(T0, [(A, 5)]))
        sh.registrar(self.con, otro, entrada(T0, [(B, 5)]), T0)
        sh.mantener(self.con, {SID: {"guardar_ips": True, "retencion_dias": 7}, "c" * 12: {"guardar_ips": True, "retencion_dias": 60}}, T0 + 10 * 86400)
        self.assertEqual([r[0] for r in self.con.execute("SELECT ip FROM ips_atacantes")], [B])

    def test_borrar_servidor_consolida_el_wal_tambien_con_lector_abierto_despues(self):
        self.reg(entrada(T0, [(A, 5)]))
        self.assertEqual(sh.borrar_servidor(self.con, SID), 1)
        with open(sh.ruta() + "-wal", "rb") as f:
            self.assertNotIn(A.encode(), f.read())

    def test_copia_previa_no_deja_ips_ni_temporales_y_caduca(self):
        self.reg(entrada(T0, [(A, 5)]))
        carpeta = os.path.join(self.dir, "copias")
        destino = sh.copia_sin_ips(self.con, carpeta)
        with open(destino, "rb") as f:
            self.assertNotIn(A.encode(), f.read())
        self.assertFalse([f for f in os.listdir(carpeta) if f.endswith(".tmp")])
        if os.name != "nt":
            self.assertEqual(os.stat(destino).st_mode & 0o777, 0o600)
        huerfano = os.path.join(carpeta, "servidores.sqlite.premigracion.20200101-000000.tmp")
        open(huerfano, "wb").close()
        sh.caducar_copias(carpeta)
        self.assertFalse(os.path.exists(huerfano))
        self.assertTrue(os.path.exists(destino))
        sh.caducar_copias(carpeta, ahora=time_now() + 31 * 86400)
        self.assertFalse(os.path.exists(destino))                         # caduca sola, sin esperar a otra migración

    def test_copia_previa_sin_restos_de_muchas_ips_borradas(self):
        # con miles de filas, un DELETE deja las IPs en páginas libres: solo el VACUUM en memoria las quita antes de escribir a disco
        self.reg(entrada(T0, [(A, 5)]))
        self.con.executemany("INSERT INTO ips_atacantes VALUES (?, ?, ?, ?, 1, 1, ?)",
                             [(SID, f"198.51.{i // 250}.{i % 250}", T0, T0, T0) for i in range(5000)])
        destino = sh.copia_sin_ips(self.con, os.path.join(self.dir, "copias"))
        with open(destino, "rb") as f:
            datos = f.read()
        self.assertNotIn(b"198.51.", datos)
        self.assertNotIn(A.encode(), datos)

    def test_mantener_caduca_las_copias_previas(self):
        carpeta = os.path.join(self.dir, "copias")
        os.makedirs(carpeta)
        vieja = os.path.join(carpeta, "servidores.sqlite.premigracion.20200101-000000")
        open(vieja, "wb").close()
        sh.mantener(self.con, {}, int(time_now()) + 40 * 86400)
        self.assertFalse(os.path.exists(vieja))

    def test_intento_fallido_repetido_no_se_anota_dos_veces(self):
        e = entrada(T0, error="x", estado="rojo", intento_en=T0 + 600)
        self.assertEqual(self.reg(e, ahora=T0 + 600), "estado")
        self.assertIsNone(self.reg(e, ahora=T0 + 900))
        self.assertEqual(self.uno("SELECT errores FROM horas_servidor"), 1)

    def test_copia_fallida_no_deja_temporal(self):
        carpeta = os.path.join(self.dir, "copias")
        real = sqlite3.connect
        with mock.patch.object(sh.sqlite3, "connect", side_effect=lambda *a, **k: (_ for _ in ()).throw(OSError("x")) if a and a[0] == ":memory:" else real(*a, **k)):
            with self.assertRaises(OSError):
                sh.copia_sin_ips(self.con, carpeta)
        self.assertFalse([f for f in os.listdir(carpeta) if f.endswith(".tmp")])

    def test_checkout_de_desarrollo_no_escribe_en_los_datos_reales(self):
        from infobar import plataforma
        with mock.patch.object(rutas, "DATOS", plataforma.dir_datos()), mock.patch.object(plataforma, "es_checkout_de_desarrollo", return_value=True):
            with self.assertRaises(sh.ErrorAlmacen):
                sh.abrir()


def time_now():
    import time
    return time.time()


class LecturaTest(Base):
    def test_historia_por_dia_y_hora_y_limites(self):
        for i in range(5):
            self.reg(entrada(T0 + i * 3600 * 5, [(A, 5)]))
        import time
        d = time.strftime("%Y-%m-%d", time.localtime(T0))
        h = sl.historia(self.con, SID, d, d, "dia")
        self.assertEqual(h["servidor"], NOMBRE)
        self.assertGreaterEqual(len(h["puntos"]), 1)
        self.assertNotIn(A, repr(h))                                          # la serie nunca lleva IPs
        self.assertIn("picos", h["nota"])
        sl.historia(self.con, SID, d, d, "hora")
        with self.assertRaises(ErrorHistorico):
            sl.historia(self.con, SID, "2020-01-01", "2026-01-01", "dia")
        with self.assertRaises(ErrorHistorico):
            sl.historia(self.con, SID, "2026-01-01", "2026-03-01", "hora")
        with self.assertRaises(ErrorHistorico):
            sl.historia(self.con, SID, "20260101", d, "dia")
        with self.assertRaises(ErrorHistorico):
            sl.historia(self.con, SID, d, d, "mes")
        with self.assertRaises(sh.SinDatos):
            sl.historia(self.con, "z" * 12, d, d, "dia")

    def test_peor_estado_y_picos_al_agrupar_por_dia(self):
        import time
        self.reg(entrada(T0, estado="verde"))
        e = entrada(T0 + 3600 * 2, estado="rojo")
        e["datos"]["intentos"]["total"] = 777
        self.reg(e)
        d = time.strftime("%Y-%m-%d", time.localtime(T0))
        pts = sl.historia(self.con, SID, d, d, "dia")["puntos"]
        self.assertEqual((pts[0]["estado"], pts[0]["intentos_pico_24h"], pts[0]["n"]), ("rojo", 777, 2))


if __name__ == "__main__":
    unittest.main()
