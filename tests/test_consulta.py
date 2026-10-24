"""El informe leído de la base: mismo formato que escanear(), plan y proyecto reproducidos, días locales correctos (también el
de 25 h y las zonas de media hora), base ausente o ilegible = SinAlmacen, y la lectura no toca el fichero."""
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import agregacion, almacen, consulta, rutas  # noqa: E402


def turno(clave, hora, agente="claude", proveedor="anthropic", modelo="claude-sonnet-5", proyecto="a/b/proy", gi=10, go=5, cr=100, cw=20):
    return {"clave": clave, "hora_utc": int(hora), "agente": agente, "proveedor": proveedor, "modelo": modelo,
            "gi": gi, "go": go, "cr": cr, "cw": cw, "proyecto_raw": proyecto}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-consulta-")
        self._ant = rutas.ALMACEN
        rutas.ALMACEN = os.path.join(self.dir, "datos", "consumo.sqlite")
        self.con = almacen.abrir(rutas.ALMACEN)
        self.q = almacen.identidad("barra")
        almacen.adquirir_escritura(self.con, self.q)
        self.esp = almacen.espacio(self.con, self.q)

    def tearDown(self):
        self.con.close()
        rutas.ALMACEN = self._ant
        shutil.rmtree(self.dir, ignore_errors=True)

    def mete(self, turnos, agente="claude", id_fuente="f1"):
        fid = almacen.fuente(self.con, self.q, self.esp, agente, id_fuente)
        almacen.ingerir_turnos(self.con, self.q, self.esp, fid, turnos)

    def completa(self):
        self.con.execute("INSERT OR REPLACE INTO meta(clave, valor) VALUES('construccion_completa', ?)", (str(int(time.time())),))


class TestSinAlmacen(Base):
    def test_sin_base(self):
        rutas.ALMACEN = os.path.join(self.dir, "no-existe.sqlite")
        with self.assertRaises(consulta.SinAlmacen):
            consulta.consultar()

    def test_a_medio_construir(self):
        self.mete([turno("claude:m1", time.time() - 60)])
        with self.assertRaisesRegex(consulta.SinAlmacen, "construyendo"):
            consulta.consultar()

    def test_version_mas_nueva(self):
        self.completa()
        self.con.execute(f"PRAGMA user_version={almacen.VERSION_ESQUEMA + 1}")
        with self.assertRaisesRegex(consulta.SinAlmacen, "más nueva"):
            consulta.consultar()

    def test_base_dañada(self):
        self.completa()
        self.con.close()
        with open(rutas.ALMACEN, "r+b") as f:
            f.truncate(os.path.getsize(rutas.ALMACEN) // 2)
        for sufijo in ("-wal", "-shm"):
            if os.path.exists(rutas.ALMACEN + sufijo):
                os.remove(rutas.ALMACEN + sufijo)
        self.con = sqlite3.connect(":memory:")
        with self.assertRaises(consulta.SinAlmacen):
            consulta.consultar()


class TestInforme(Base):
    def test_formato_plan_y_proyecto(self):
        ahora = int(time.time()); ahora -= ahora % 3600       # inicio de la hora en curso (una sola lectura del reloj): siempre dentro del ritmo
        self.mete([turno("claude:m1", ahora), turno("claude:m2", ahora, gi=1, go=1, cr=1, cw=1)])
        self.mete([turno("pi:r1", ahora, agente="pi", proveedor="nan", modelo="deepseek-v4", proyecto="x/y/z")], "pi", "f2")
        self.completa()
        r = consulta.consultar()
        self.assertEqual(r["hoy"]["tok"] + r["ayer"]["tok"], 135 + 4 + 135)
        self.assertEqual(r["cinco_h"]["tok"], 135 + 4 + 135)
        self.assertEqual(r["ritmo"]["tok"], 135 + 4 + 135)
        self.assertEqual(r["origenes"]["Claude (Max)"], 139)
        self.assertEqual(r["origenes"]["nan"], 135)
        self.assertEqual(r["bases"], ["Claude Code (1)", "pi (1)"])
        self.assertIn("a/b/proy", r["proyectos"])

    def test_alias_antiguo_de_pi_sigue_valiendo(self):
        ahora = time.time()
        self.mete([turno("pi:r1", ahora - 300, agente="pi", proveedor="nan", proyecto="x/y/z")], "pi")
        self.completa()
        conf = {"proyectos": {"pi · y/z": {"alias": "Mi pi"}}}
        with unittest_cfg(conf):
            r = consulta.consultar()
        self.assertIn("Mi pi", r["proyectos"])

    def test_informe_cae_a_logs_si_no_hay_base(self):
        rutas.ALMACEN = os.path.join(self.dir, "no.sqlite")
        with unittest_cfg({"almacen": "activo"}):
            r = agregacion.informe()
        self.assertIn("hoy", r)

    def test_lectura_no_toca_el_fichero(self):
        self.mete([turno("claude:m1", time.time() - 60)])
        self.completa()
        self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        def huella():
            with open(rutas.ALMACEN, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest(), os.path.getmtime(rutas.ALMACEN)
        antes = huella()
        consulta.consultar()
        self.assertEqual(antes, huella())

    def test_lectura_no_crea_ficheros_nuevos_con_el_escritor_abierto(self):
        self.mete([turno("claude:m1", time.time() - 60)])
        self.completa()
        antes = sorted(os.listdir(os.path.dirname(rutas.ALMACEN)))
        consulta.consultar()
        self.assertEqual(antes, sorted(os.listdir(os.path.dirname(rutas.ALMACEN))))

    def test_recargo_por_contexto_largo_en_la_ventana_de_cinco_horas(self):
        from infobar.planes_y_tarifas import CLIFF, tarifa
        t = tarifa("claude-sonnet-5")
        self.assertTrue(t)
        ahora = time.time()
        self.mete([turno("claude:corto", ahora - 100, gi=1000, go=0, cr=0, cw=0),
                   turno("claude:largo", ahora - 120, gi=CLIFF + 1000, go=0, cr=0, cw=0)])
        self.completa()
        usd = consulta.consultar()["cinco_h"]["usd"]
        esperado = (1000 * t[0] + (CLIFF + 1000) * t[0] * 2) / 1e6
        self.assertAlmostEqual(usd, esperado, places=6)

    def test_la_ventana_de_dias_recorta(self):
        ahora = time.time()
        self.mete([turno("claude:viejo", ahora - 10 * 86400), turno("claude:nuevo", ahora - 60)])
        self.completa()
        self.assertEqual(consulta.consultar(dias=3)["mes"]["tok"], 135)
        self.assertEqual(consulta.consultar(dias=20)["mes"]["tok"], 270)

    def test_ritmo_usa_epoch_sin_ambiguedad(self):
        ahora = time.time()
        self.mete([turno("claude:m1", ahora - 7200)])
        self.completa()
        r = consulta.consultar()
        self.assertEqual(r["ritmo"]["tok"], 0)
        self.assertEqual(r["cinco_h"]["tok"], 135)


class TestPerfilesYNuevos(Base):
    def test_perfil_de_hermes_por_huella_y_perfil_borrado(self):
        from infobar import plataforma
        home = os.path.join(self.dir, "home")
        d = os.path.join(home, ".hermes", "profiles", "trabajo")
        os.makedirs(d)
        open(os.path.join(d, "state.db"), "w").close()
        h_trabajo = plataforma.id_fuente(os.path.join(d, "state.db"), home)
        h_viejo = plataforma.id_fuente(os.path.join(home, ".hermes", "profiles", "borrado", "state.db"), home)
        ahora = time.time()
        for hid, clave in ((h_trabajo, "hermes:s1:a"), (h_viejo, "hermes:s2:b")):
            fid = almacen.fuente(self.con, self.q, self.esp, "hermes", hid)
            almacen.ingerir_turnos(self.con, self.q, self.esp, fid, [turno(clave, ahora - 60, agente="hermes", proveedor="x")])
        res = consulta.perfiles_hermes(self.con, self.esp, 31, home=home)
        self.assertEqual(res["trabajo"], 135)
        self.assertEqual(res[f"hermes/{h_viejo[:8]}"], 135)

    def test_nuevos_solo_si_la_vuelta_es_reciente(self):
        self.completa()
        import json
        for en, esperado in ((int(time.time()), 7), (int(time.time()) - 3600, 0)):
            almacen.guardar_meta(self.con, self.q, "ultima_vuelta", json.dumps({"nuevos": 7, "en": en}))
            self.assertEqual(consulta.consultar()["nuevos"], esperado)


class TestConcurrencia(Base):
    def test_cuatro_consultores_mientras_el_escritor_aplica_lotes(self):
        import threading
        ahora = time.time()
        self.completa()
        parar, tiempos, fallos = threading.Event(), [], []

        def consulta_en_bucle():
            while not parar.is_set():
                t = time.monotonic()
                try:
                    consulta.consultar()
                except Exception as e:                      # noqa: BLE001
                    fallos.append(repr(e))
                tiempos.append(time.monotonic() - t)

        hilos = [threading.Thread(target=consulta_en_bucle) for _ in range(4)]
        for h in hilos:
            h.start()
        fid = almacen.fuente(self.con, self.q, self.esp, "claude", "lote")
        for lote in range(8):
            almacen.ingerir_turnos(self.con, self.q, self.esp, fid,
                                   [turno(f"claude:m{lote}-{i}", ahora - 10 * i) for i in range(500)])
        parar.set()
        for h in hilos:
            h.join()
        self.assertEqual(fallos, [])
        self.assertGreater(len(tiempos), 4)
        self.assertLess(max(tiempos), 1.0)


class TestZonas(Base):
    def setUp(self):
        # se comprueba ANTES de super().setUp(): si falta tzdata y saltamos despues, Base.tearDown()
        # no llega a correr (deja el directorio temporal, la conexion sqlite y rutas.ALMACEN sin restaurar)
        try:
            ZoneInfo("Europe/Madrid")
        except Exception as e:          # falta tzdata (tipico en Windows sin el paquete)
            self.skipTest(f"falta tzdata para zoneinfo ({e}); instala con: pip install tzdata")
        super().setUp()

    def dias(self, zona, horas_utc):
        self.mete([turno(f"claude:m{i}", t) for i, t in enumerate(horas_utc)])
        self.completa()
        ahora = max(horas_utc) + 3600
        con = almacen.abrir(rutas.ALMACEN, solo_lectura=True)
        try:
            vistos, _ = consulta.vistos_desde(con, 5, ZoneInfo(zona), ahora=ahora)
        finally:
            con.close()
        n = {}
        for r in vistos.values():
            for d, k in r["turnos"].items():
                n[d] = n.get(d, 0) + k
        return n

    def test_dia_de_25_horas(self):
        # 2026-10-24T22:00Z es el 25 a las 00:00 en Madrid (CEST); el 25 acaba a las 2026-10-25T23:00Z (CET): 25 horas
        inicio = 1_792_886_400 - 2 * 3600          # 2026-10-25T00:00Z menos 2 h
        self.assertEqual(time.strftime("%Y-%m-%dT%H", time.gmtime(inicio)), "2026-10-24T22")
        n = self.dias("Europe/Madrid", [inicio + 3600 * i + 60 for i in range(25)])
        self.assertEqual(n, {"2026-10-25": 25})

    def test_dia_de_23_horas(self):
        inicio = 1_774_738_800                   # 2026-03-28T23:00Z = 29 a las 00:00 (CET); a las 02:00 salta a las 03:00
        self.assertEqual(time.strftime("%Y-%m-%dT%H", time.gmtime(inicio)), "2026-03-28T23")
        n = self.dias("Europe/Madrid", [inicio + 3600 * i + 60 for i in range(23)])
        self.assertEqual(n, {"2026-03-29": 23})

    def test_media_hora(self):
        base = 1_790_000_000 - 1_790_000_000 % 3600
        # en Kolkata (+05:30) 18:20Z son las 23:50 y 18:40Z las 00:10 del día siguiente
        base = base - base % 86400 + 18 * 3600
        n = self.dias("Asia/Kolkata", [base + 20 * 60, base + 40 * 60])
        self.assertEqual(sorted(n.values()), [1, 1])
        self.assertEqual(len(n), 2)


class unittest_cfg:
    """Config de prueba: sustituye cfg() en los módulos que la leen."""
    def __init__(self, conf):
        self.conf = conf

    def __enter__(self):
        from unittest import mock
        self.ps = [mock.patch(f"infobar.{m}.cfg", return_value=self.conf)
                   for m in ("agregacion", "planes_y_tarifas")]
        for p in self.ps:
            p.start()

    def __exit__(self, *a):
        for p in self.ps:
            p.stop()


if __name__ == "__main__":
    unittest.main()
