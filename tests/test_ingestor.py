"""Ingestor: una vuelta recorre las fuentes con presupuesto, salta lo que no ha cambiado, no gira con una línea a medias,
cede si otro proceso es el escritor, marca la base como construida, y solo la barra y el CLI importan la ingesta."""
import ast
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import almacen, ingestor  # noqa: E402
from test_ingesta import linea  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-ingestor-")
        self.home = os.path.join(self.dir, "home")
        self.proy = os.path.join(self.home, ".claude", "projects", "-Users-yo-demo")
        os.makedirs(self.proy)
        self.ruta = os.path.join(self.dir, "datos", "consumo.sqlite")
        self.ing = ingestor.Ingestor(home=self.home, ruta_base=self.ruta)

    def tearDown(self):
        self.ing.cierra()
        shutil.rmtree(self.dir, ignore_errors=True)

    def fichero(self, nombre, lineas, final="\n"):
        r = os.path.join(self.proy, nombre)
        with open(r, "w", encoding="utf-8") as f:
            f.write("\n".join(lineas) + final)
        return r

    def n_turnos(self):
        con = almacen.abrir(self.ruta, solo_lectura=True)
        try:
            return con.execute("SELECT COUNT(*) FROM turnos").fetchone()[0], almacen.meta(con, "construccion_completa")
        finally:
            con.close()


class TestVuelta(Base):
    def test_ingiere_marca_construida_y_salta_lo_que_no_cambia(self):
        self.fichero("a.jsonl", [linea("msg_01AAAAAAAAAAAAAAAA"), linea("msg_01BBBBBBBBBBBBBBBB", ts="2026-09-27T20:50:00Z")])
        r = self.ing.vuelta()
        self.assertEqual((r["escritor"], r["nuevos"], r["completo"], r["ficheros"]), (True, 2, True, 1))
        n, construida = self.n_turnos()
        self.assertEqual(n, 2)
        self.assertTrue(construida)
        r2 = self.ing.vuelta()
        self.assertEqual((r2["ficheros"], r2["nuevos"], r2["completo"]), (0, 0, True))

    def test_linea_a_medias_no_deja_trabajo_pendiente(self):
        self.fichero("a.jsonl", [linea("msg_01AAAAAAAAAAAAAAAA"), '{"type": "assistant", "mess'], final="")
        r = self.ing.vuelta()
        self.assertTrue(r["completo"])
        self.assertEqual(self.n_turnos()[0], 1)

    def test_presupuesto_agotado_deja_pendiente_y_no_marca_construida(self):
        for i in range(3):
            self.fichero(f"f{i}.jsonl", [linea(f"msg_01{i}AAAAAAAAAAAAAAA")])
        r = self.ing.vuelta(presupuesto_s=-1)
        self.assertFalse(r["completo"])
        self.assertFalse(self.n_turnos()[1])
        r = self.ing.vuelta()
        self.assertTrue(r["completo"])
        self.assertEqual(self.n_turnos()[0], 3)

    def test_fichero_que_crece_se_lee_de_nuevo(self):
        r = self.fichero("a.jsonl", [linea("msg_01AAAAAAAAAAAAAAAA")])
        self.ing.vuelta()
        with open(r, "a", encoding="utf-8") as f:
            f.write(linea("msg_01BBBBBBBBBBBBBBBB", ts="2026-09-27T21:00:00Z") + "\n")
        r2 = self.ing.vuelta()
        self.assertEqual((r2["ficheros"], r2["nuevos"]), (1, 1))

    def test_si_otro_proceso_es_el_escritor_no_escribe(self):
        self.fichero("a.jsonl", [linea("msg_01AAAAAAAAAAAAAAAA")])
        otro = almacen.abrir(self.ruta)
        try:
            self.assertTrue(almacen.adquirir_escritura(otro, almacen.identidad("cli")))
            r = self.ing.vuelta()
            self.assertEqual((r["escritor"], r["nuevos"]), (False, 0))
            self.assertEqual(self.n_turnos()[0], 0)
        finally:
            otro.close()

    def test_resumen_de_la_ultima_vuelta_en_meta(self):
        self.fichero("a.jsonl", [linea("msg_01AAAAAAAAAAAAAAAA")])
        self.ing.vuelta()
        con = almacen.abrir(self.ruta, solo_lectura=True)
        try:
            v = json.loads(almacen.meta(con, "ultima_vuelta"))
        finally:
            con.close()
        self.assertEqual(v["nuevos"], 1)


class TestRevision(Base):
    def test_linea_enorme_sin_terminar_no_gira_para_siempre(self):
        r = os.path.join(self.proy, "g.jsonl")
        with open(r, "w", encoding="utf-8") as f:
            f.write(linea("msg_01AAAAAAAAAAAAAAAA") + "\n")
            f.write("x" * (9 * 1024 * 1024))                     # sin salto de línea: se está escribiendo
        vueltas = 0
        while not self.ing.vuelta()["completo"]:
            vueltas += 1
            self.assertLess(vueltas, 4, "la ingesta no termina")
        self.assertTrue(self.n_turnos()[1])
        self.assertEqual(self.n_turnos()[0], 1)

    def test_el_puesto_se_readquiere_en_cada_vuelta(self):
        self.fichero("a.jsonl", [linea("msg_01AAAAAAAAAAAAAAAA")])
        self.assertTrue(self.ing.vuelta()["escritor"])
        con = almacen.abrir(self.ruta)
        try:
            con.execute("UPDATE meta SET valor='0' WHERE clave LIKE 'latido%'")
            con.execute("DELETE FROM meta WHERE clave LIKE '%escritor%' OR clave LIKE 'latido%'")
            self.assertTrue(almacen.adquirir_escritura(con, almacen.identidad("cli")))
            self.assertFalse(self.ing.vuelta()["escritor"])
        finally:
            con.close()

    def test_una_fuente_que_falla_no_marca_la_base_como_construida(self):
        from unittest import mock
        self.fichero("a.jsonl", [linea("msg_01AAAAAAAAAAAAAAAA")])
        with mock.patch("infobar.ingesta.ingerir_fichero", side_effect=RuntimeError("x")):
            r = self.ing.vuelta()
        self.assertEqual(r["fallos"], 1)
        self.assertFalse(self.n_turnos()[1])

    @unittest.skipIf(os.name == "nt", "el test borra el fichero sqlite mientras self.ing mantiene su conexión abierta (\"la barra en "
                     "marcha\"); en Windows la VFS de SQLite no pide FILE_SHARE_DELETE, así que no se puede borrar un fichero con una "
                     "conexión todavía abierta (en POSIX sí, por eso el escenario solo es reproducible ahí)")
    def test_base_borrada_con_la_barra_en_marcha_se_recrea(self):
        self.fichero("a.jsonl", [linea("msg_01AAAAAAAAAAAAAAAA")])
        self.ing.vuelta()
        for sufijo in ("", "-wal", "-shm"):
            if os.path.exists(self.ruta + sufijo):
                os.remove(self.ruta + sufijo)
        r = self.ing.vuelta()
        self.assertEqual(r["nuevos"], 1)
        self.assertEqual(self.n_turnos()[0], 1)

    def test_un_arranque_nuevo_no_repasa_lo_que_la_base_ya_leyo(self):
        self.fichero("a.jsonl", [linea("msg_01AAAAAAAAAAAAAAAA")])
        self.ing.vuelta()
        self.ing.cierra()
        otro = ingestor.Ingestor(home=self.home, ruta_base=self.ruta)
        try:
            self.assertEqual(otro.vuelta()["ficheros"], 0)
        finally:
            otro.cierra()


class TestSoloLectura(unittest.TestCase):
    PROHIBIDOS = ("ingestor", "ingesta", "ingesta_claude", "ingesta_codex", "ingesta_pi", "ingesta_opencode", "ingesta_hermes")
    LECTORES = ("panel.py", "popover.py", "analizar_gasto.py", "infobar/servidor_local.py", "infobar/consulta.py",
                "infobar/agregacion.py", "infobar/trabajador_barra.py")

    def test_importar_los_lectores_no_carga_la_ingesta(self):
        import subprocess
        codigo = ("import sys; sys.path.insert(0, %r); import infobar.consulta, infobar.sombra, infobar.servidor_local, "
                  "infobar.trabajador_barra; print(sorted(m for m in sys.modules if m.startswith(('infobar.ingest', 'infobar.ingesta'))))") % RAIZ
        out = subprocess.run([sys.executable, "-I", "-c", codigo], capture_output=True, text=True, timeout=60).stdout.strip()
        self.assertEqual(out, "[]")

    def test_los_lectores_no_importan_la_ingesta(self):
        for rel in self.LECTORES:
            with open(os.path.join(RAIZ, rel), encoding="utf-8") as f:
                arbol = ast.parse(f.read())
            for nodo in ast.walk(arbol):
                nombres = []
                if isinstance(nodo, ast.Import):
                    nombres = [a.name.split(".")[-1] for a in nodo.names]
                elif isinstance(nodo, ast.ImportFrom):
                    nombres = [(nodo.module or "").split(".")[-1]] + [a.name for a in nodo.names]
                for n in nombres:
                    self.assertNotIn(n, self.PROHIBIDOS, f"{rel} importa {n}: solo la barra y el CLI ingieren")


if __name__ == "__main__":
    unittest.main()
