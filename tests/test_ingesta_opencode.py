"""Ingesta de OpenCode: dos bases como fuentes, solo assistant con tokens, reasoning sumado a la salida, avance por (time_updated, id)
con margen y paginación, mensajes que se completan después, base sin cambios sin abrirla, filas podadas que no se pierden y filas hostiles."""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import almacen, ingesta_opencode  # noqa: E402
from infobar.ingesta import firma_sqlite  # noqa: E402
ingesta_opencode.firma_sqlite = firma_sqlite

T0 = 1_790_000_000_000      # ms


def datos(modelo="deepseek-v4-flash", prov="nan", inp=100, out=50, reas=300, cr=900, cw=10, rol="assistant", cwd="/Users/yo/Proyectos/demo/app", con_tokens=True):
    d = {"role": rol, "time": {"created": T0}, "modelID": modelo, "providerID": prov, "path": {"cwd": cwd, "root": "/"}, "cost": 0}
    if con_tokens:
        d["tokens"] = {"input": inp, "output": out, "reasoning": reas, "cache": {"read": cr, "write": cw}}
    if rol == "user":
        d.pop("modelID"), d.pop("providerID")
    return json.dumps(d)


class BaseOC(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-oc-")
        self.home = os.path.join(self.dir, "home")
        carpeta = os.path.join(self.home, ".local", "share", "opencode")
        os.makedirs(carpeta)
        self.ruta = os.path.join(carpeta, "opencode.db")
        self.origen = sqlite3.connect(self.ruta)
        self.origen.execute("CREATE TABLE message(id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)")
        self.origen.commit()
        self.con = almacen.abrir(os.path.join(self.dir, "datos", "c.sqlite"))
        self.quien = almacen.identidad("barra")
        almacen.adquirir_escritura(self.con, self.quien)
        self.esp = almacen.espacio(self.con, self.quien)

    def tearDown(self):
        self.origen.close()
        self.con.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def inserta(self, mid, data, creado=T0, actualizado=None):
        self.origen.execute("INSERT OR REPLACE INTO message VALUES(?,?,?,?,?)", (mid, "ses", creado, actualizado if actualizado is not None else creado, data))
        self.origen.commit()

    def pasada(self, ruta=None, **kw):
        return ingesta_opencode.ingerir_base(self.con, self.quien, self.esp, ruta or self.ruta, self.home, **kw)

    def turnos(self):
        return {t["clave"]: dict(t) for t in self.con.execute("SELECT * FROM turnos")}

    def anomalias(self):
        return {(a["tipo"], a["detalle"]) for a in almacen.anomalias(self.con, 500)}


class OpenCodeTest(BaseOC):
    def test_turno_basico_solo_assistant_y_reasoning_se_suma_a_la_salida(self):
        self.inserta("msg_aaaa1111", datos())
        self.inserta("msg_user0001", datos(rol="user"))
        self.inserta("msg_sinmod01", json.dumps({"role": "assistant", "tokens": {"input": 1, "output": 1, "cache": {"read": 0, "write": 0}}}))
        self.assertEqual(self.pasada()["nuevos"], 1)
        t = self.turnos()["opencode:msg_aaaa1111"]
        self.assertEqual((t["gi"], t["go"], t["cr"], t["cw"], t["modelo"], t["proveedor"], t["agente"]), (100, 350, 900, 10, "deepseek-v4-flash", "nan", "opencode"))       # salida 50 + reasoning 300
        self.assertEqual(t["hora_utc"], T0 // 1000)
        self.assertEqual(t["proyecto_raw"], "Proyectos/demo/app")

    def test_mensaje_en_curso_que_se_completa_despues_sube_los_tokens(self):
        self.inserta("msg_aaaa1111", datos(inp=0, out=0, reas=0, cr=0, cw=0), actualizado=T0 + 10)
        self.pasada()
        self.inserta("msg_aaaa1111", datos(inp=100, out=50), actualizado=T0 + 5000)
        # mtime claramente futuro: las dos pasadas pueden caer en el mismo tick del reloj de
        # ficheros (mas grueso en Windows que el tiempo que tarda este test), y sin esto la firma
        # de la base no cambia y la segunda pasada se sirve de la cache de la primera.
        futuro = time.time() + 5
        os.utime(self.ruta, (futuro, futuro))
        r = self.pasada()
        self.assertEqual((r["subidos"], r["nuevos"]), (1, 0))
        self.assertEqual(self.turnos()["opencode:msg_aaaa1111"]["go"], 350)

    def test_segunda_pasada_solo_lee_lo_nuevo_y_una_base_sin_cambios_ni_se_abre(self):
        for i in range(5):
            self.inserta(f"msg_{i:08d}", datos(), actualizado=T0 + i)
        self.assertEqual(self.pasada()["filas"], 5)
        with mock.patch.object(ingesta_opencode, "abrir_sqlite_solo_lectura", side_effect=AssertionError("no debía abrirse")):
            self.assertTrue(self.pasada()["sin_cambios"])
        self.inserta("msg_nuevo001", datos(), actualizado=T0 + 99)
        futuro = time.time() + 5          # mismo motivo que en test_mensaje_en_curso_que_se_completa_despues_sube_los_tokens
        os.utime(self.ruta, (futuro, futuro))
        r = self.pasada()
        self.assertEqual((r["nuevos"], r["iguales"]), (1, 5))             # releyó el margen de 5 minutos (inocuo) y solo 1 es nuevo

    def test_paginacion_con_muchas_filas_con_el_mismo_time_updated(self):
        for i in range(25):
            self.inserta(f"msg_{i:08d}", datos(out=i + 1), actualizado=T0)           # todas con la misma marca
        for _ in range(50):
            if self.pasada(max_filas=4)["completo"]:
                break
        self.assertEqual(len(self.turnos()), 25)
        self.assertEqual(sum(t["go"] for t in self.turnos().values()), sum(range(1, 26)) + 25 * 300)

    def test_un_mensaje_confirmado_tarde_con_time_updated_menor_que_la_marca_no_se_pierde(self):
        self.inserta("msg_aaaa1111", datos(), actualizado=T0 + 10 * 60000)
        self.pasada()
        self.inserta("msg_tarde001", datos(), actualizado=T0 + 9 * 60000)             # otro proceso lo confirma después, con una marca anterior
        futuro = time.time() + 5          # mismo motivo que en test_mensaje_en_curso_que_se_completa_despues_sube_los_tokens
        os.utime(self.ruta, (futuro, futuro))
        self.assertEqual(self.pasada()["nuevos"], 1)
        self.assertEqual(len(self.turnos()), 2)

    def test_paginacion_con_solape_termina_aunque_el_margen_tenga_mas_filas_que_la_pagina(self):
        for i in range(30):
            self.inserta(f"msg_{i:08d}", datos(out=i + 1), actualizado=T0 + i * 1000)       # 30 filas dentro de un margen de 5 minutos
        for _ in range(60):
            if self.pasada(max_filas=4)["completo"]:
                break
        else:
            self.fail("el drenado no termina")
        self.assertEqual(len(self.turnos()), 30)

    def test_time_updated_con_texto_decimales_nulo_o_blob_no_bloquea_ni_repite(self):
        for i, valor in enumerate(("1790000000100", 1790000000200.5, None, b"x", "no-numerico")):
            self.origen.execute("INSERT INTO message VALUES(?,?,?,?,?)", (f"msg_raro{i:04d}", "s", T0, valor, datos()))
        self.origen.commit()
        for _ in range(10):
            if self.pasada(max_filas=3)["completo"]:
                break
        self.assertEqual(len(self.turnos()), 5)
        self.assertTrue(self.pasada()["sin_cambios"])

    def test_ruta_relativa_o_inexistente_se_anota_sin_romper(self):
        self.inserta("msg_aaaa1111", datos())
        os.chdir(os.path.dirname(self.ruta))
        try:
            self.assertEqual(self.pasada(ruta="opencode.db")["nuevos"], 1)       # relativa: se resuelve a absoluta
        finally:
            os.chdir(self.dir)
        self.assertEqual(self.pasada(ruta=os.path.join(self.dir, "no-existe.db"))["anomalias"], 1)

    def test_abrir_una_base_con_wal_cerrada_no_crea_ficheros_ni_necesita_escribir_en_su_directorio(self):
        carpeta = os.path.join(self.dir, "ajena")
        os.makedirs(carpeta)
        ruta = os.path.join(carpeta, "opencode.db")
        c = sqlite3.connect(ruta)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE message(id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)")
        c.execute("INSERT INTO message VALUES('msg_wal00001','s',?,?,?)", (T0, T0, datos()))
        c.commit()
        c.close()
        for f in os.listdir(carpeta):
            if f != "opencode.db":
                os.remove(os.path.join(carpeta, f))
        antes = sorted(os.listdir(carpeta))
        os.chmod(carpeta, 0o500)
        try:
            self.assertEqual(self.pasada(ruta=ruta)["nuevos"], 1)
        finally:
            os.chmod(carpeta, 0o700)
        self.assertEqual(sorted(os.listdir(carpeta)), antes)                         # ni -wal ni -shm nuevos

    def test_la_firma_incluye_ctime(self):
        self.assertEqual(ingesta_opencode.firma_sqlite(self.ruta).split("|")[0].count(":"), 2)

    def test_el_json_roto_no_crea_una_anomalia_nueva_en_cada_pasada(self):
        self.inserta("msg_json0001", "{no es json")
        self.pasada()
        self.inserta("msg_json0002", "{tampoco", actualizado=T0 + 5)
        self.pasada()
        self.assertEqual(len([a for a in almacen.anomalias(self.con, 50) if "JSON inválido" in a["detalle"]]), 1)

    def test_las_filas_descartadas_cuentan_como_anomalias_en_el_resultado(self):
        self.inserta("msg_gran0001", datos(inp=10 ** 15))
        self.assertEqual(self.pasada()["anomalias"], 1)

    def test_las_filas_podadas_del_origen_no_se_pierden(self):
        self.inserta("msg_aaaa1111", datos())
        self.pasada()
        self.origen.execute("DELETE FROM message")
        self.origen.commit()
        self.pasada()
        self.assertEqual(len(self.turnos()), 1)

    def test_una_base_recreada_se_relee_sin_duplicar(self):
        self.inserta("msg_aaaa1111", datos(), actualizado=T0 + 100000)
        self.pasada()
        self.origen.execute("DELETE FROM message")
        self.inserta("msg_aaaa1111", datos(), actualizado=T0 + 5)                     # la marca retrocede: base nueva
        self.inserta("msg_bbbb2222", datos(), actualizado=T0 + 6)
        futuro = time.time() + 5          # mismo motivo que en test_mensaje_en_curso_que_se_completa_despues_sube_los_tokens
        os.utime(self.ruta, (futuro, futuro))
        r = self.pasada()
        self.assertEqual((r["nuevos"], r["iguales"]), (1, 1))

    def test_dos_bases_son_dos_fuentes_con_el_mismo_formato(self):
        otra = os.path.join(os.path.dirname(self.ruta), "opencode-.db")
        con2 = sqlite3.connect(otra)
        con2.execute("CREATE TABLE message(id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)")
        con2.execute("INSERT INTO message VALUES('msg_otra0001','s',?,?,?)", (T0, T0, datos(modelo="otro")))
        con2.commit()
        con2.close()
        self.inserta("msg_aaaa1111", datos())
        self.pasada()
        self.pasada(ruta=otra)
        self.assertEqual(set(self.turnos()), {"opencode:msg_aaaa1111", "opencode:msg_otra0001"})
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM fuentes WHERE agente='opencode'").fetchone()[0], 2)

    def test_la_fuente_no_guarda_rutas_absolutas(self):
        self.inserta("msg_aaaa1111", datos())
        self.pasada()
        f = self.con.execute("SELECT id_fuente FROM fuentes WHERE agente='opencode'").fetchone()[0]
        self.assertRegex(f, r"^[0-9a-f]{32}$")

    def test_filas_hostiles_no_bloquean_la_fuente(self):
        self.inserta("x'; DROP TABLE turnos; --", datos())
        self.inserta("msg_nul00001", datos(modelo="a\x00b"))
        self.inserta("msg_gran0001", datos(inp=10 ** 15))
        self.inserta("msg_texto001", json.dumps({"role": "assistant", "modelID": "m", "providerID": "p", "tokens": {"input": "uno", "output": 1, "cache": {"read": 0, "write": 0}}}))
        self.inserta("msg_json0001", "{no es json")
        self.inserta("msg_nulo0001", None)
        self.inserta("msg_bueno001", datos())
        r = self.pasada()
        self.assertEqual(r["nuevos"], 1)
        self.assertEqual(set(self.turnos()), {"opencode:msg_bueno001"})
        self.assertEqual(self.pasada()["filas"], 0)

    def test_sin_tabla_message_o_base_ilegible_se_anota(self):
        mala = os.path.join(os.path.dirname(self.ruta), "mala.db")
        c = sqlite3.connect(mala)
        c.execute("CREATE TABLE otra(x)")
        c.close()
        self.assertEqual(self.pasada(ruta=mala)["anomalias"], 1)
        basura = os.path.join(os.path.dirname(self.ruta), "basura.db")
        with open(basura, "wb") as f:
            f.write(os.urandom(2048))
        self.assertEqual(self.pasada(ruta=basura)["anomalias"], 1)
        self.assertTrue(any("sin tabla message" in d or "no legible" in d or "consulta fallida" in d for _, d in self.anomalias()))

    def test_el_origen_no_se_modifica(self):
        self.inserta("msg_aaaa1111", datos())
        self.origen.close()
        antes = (open(self.ruta, "rb").read(), sorted(os.listdir(os.path.dirname(self.ruta))))
        self.pasada()
        self.assertEqual((open(self.ruta, "rb").read(), sorted(os.listdir(os.path.dirname(self.ruta)))), antes)
        self.origen = sqlite3.connect(self.ruta)

    def test_ruta_con_caracteres_especiales(self):
        # "?" es ilegal en nombres de fichero de Windows: se cambia por otro caracter especial valido
        # alli pero con significado reservado en una URI (igual que "?").
        nombre = "a#b;c d.db" if os.name == "nt" else "a#b?c d.db"
        raro = os.path.join(os.path.dirname(self.ruta), nombre)
        shutil.copy(self.ruta, raro)
        self.inserta("msg_aaaa1111", datos())
        shutil.copy(self.ruta, raro)
        self.assertEqual(self.pasada(ruta=raro)["nuevos"], 1)


if __name__ == "__main__":
    unittest.main()
