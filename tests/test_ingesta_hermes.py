"""Ingesta de Hermes: una fila acumulada de session_model_usage es un turno (máximo de los contadores), hora = first_seen,
tareas auxiliares incluidas, un perfil por fuente sin entrar en la clave, base sin cambios sin abrirla y filas hostiles."""
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
from infobar import almacen, ingesta_hermes  # noqa: E402

PRIMERA = 1788095712.105706      # s (float, como Hermes)
SESION = "20260830_103000_abc123"


def crea_base(ruta):
    c = sqlite3.connect(ruta)
    c.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY, cwd TEXT)")
    c.execute("""CREATE TABLE session_model_usage(session_id TEXT, model TEXT, billing_provider TEXT, billing_base_url TEXT, billing_mode TEXT, task TEXT,
                 api_call_count INTEGER, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER,
                 reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, cost_source TEXT, first_seen REAL, last_seen REAL,
                 PRIMARY KEY(session_id, model, billing_provider, billing_base_url, billing_mode, task))""")
    c.commit()
    return c


class BaseH(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-hermes-")
        self.home = os.path.join(self.dir, "home")
        carpeta = os.path.join(self.home, ".hermes", "profiles", "codeia")
        os.makedirs(carpeta)
        self.ruta = os.path.join(carpeta, "state.db")
        self.origen = crea_base(self.ruta)
        self.con = almacen.abrir(os.path.join(self.dir, "datos", "c.sqlite"))
        self.quien = almacen.identidad("barra")
        almacen.adquirir_escritura(self.con, self.quien)
        self.esp = almacen.espacio(self.con, self.quien)

    def tearDown(self):
        self.origen.close()
        self.con.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def sesion(self, sid=SESION, cwd="/Users/yo/Proyectos/demo/app"):
        self.origen.execute("INSERT OR REPLACE INTO sessions VALUES(?,?)", (sid, cwd))
        self.origen.commit()

    def uso(self, sid=SESION, modelo="gemma4", tarea="", inp=1000, out=100, cr=0, cw=0, primera=PRIMERA, ultima=None, burl="https://api.nan.builders/v1", bprov="custom"):
        self.origen.execute("INSERT OR REPLACE INTO session_model_usage(session_id, model, billing_provider, billing_base_url, billing_mode, task, api_call_count, input_tokens, "
                            "output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens, first_seen, last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (sid, modelo, bprov, burl, "", tarea, 1, inp, out, cr, cw, 0, primera, ultima or primera))
        self.origen.commit()

    def pasada(self, ruta=None):
        return ingesta_hermes.ingerir_base(self.con, self.quien, self.esp, ruta or self.ruta, self.home)

    def turnos(self):
        return {t["clave"]: dict(t) for t in self.con.execute("SELECT * FROM turnos")}


class HermesTest(BaseH):
    def test_fila_acumulada_es_un_turno_con_hora_first_seen(self):
        self.sesion()
        self.uso(inp=7822197, out=41793, ultima=PRIMERA + 80000)
        self.assertEqual(self.pasada()["nuevos"], 1)
        t = next(iter(self.turnos().values()))
        self.assertEqual((t["gi"], t["go"], t["modelo"], t["agente"], t["hora_utc"]), (7822197, 41793, "gemma4", "hermes", int(PRIMERA)))
        self.assertEqual((t["proveedor"], t["proyecto_raw"]), ("https://api.nan.builders/v1", "Proyectos/demo/app"))

    def test_las_tareas_auxiliares_cuentan_y_son_turnos_distintos(self):
        self.sesion()
        self.uso(tarea="", inp=1000)
        self.uso(tarea="title_generation", inp=260, out=11)
        self.assertEqual(self.pasada()["nuevos"], 2)
        self.assertEqual(sum(t["gi"] for t in self.turnos().values()), 1260)

    def test_misma_sesion_y_modelo_con_dos_proveedores_son_dos_turnos(self):
        self.sesion()
        self.uso(modelo="qwen3.6", inp=3389, out=1264, bprov="vision", burl="")
        self.uso(modelo="qwen3.6", inp=60890, out=19497, bprov="custom", burl="https://api.example.com/v1")
        self.assertEqual(self.pasada()["nuevos"], 2)
        self.assertEqual(sum(t["gi"] for t in self.turnos().values()), 3389 + 60890)

    def test_los_contadores_acumulados_suben_sin_duplicar(self):
        self.sesion()
        self.uso(inp=1000, out=100, ultima=PRIMERA + 10)
        self.pasada()
        self.uso(inp=5000, out=400, ultima=PRIMERA + 9000)                           # la misma fila, más consumo
        # mtime claramente futuro: las dos pasadas pueden caer en el mismo tick del reloj de
        # ficheros (mas grueso en Windows que el tiempo que tarda este test), y sin esto la firma
        # de la base no cambia y la segunda pasada se sirve de la cache de la primera.
        futuro = time.time() + 5
        os.utime(self.ruta, (futuro, futuro))
        r = self.pasada()
        self.assertEqual((r["nuevos"], r["subidos"]), (0, 1))
        t = next(iter(self.turnos().values()))
        self.assertEqual((t["gi"], t["go"], t["hora_utc"]), (5000, 400, int(PRIMERA)))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM anomalias").fetchone()[0], 0)    # la hora no se mueve: no hay anomalía

    def test_un_contador_que_baja_no_se_aplica(self):
        self.sesion()
        self.uso(inp=5000)
        self.pasada()
        self.uso(inp=100)
        futuro = time.time() + 5          # mismo motivo que en test_los_contadores_acumulados_suben_sin_duplicar
        os.utime(self.ruta, (futuro, futuro))
        self.pasada()
        self.assertEqual(next(iter(self.turnos().values()))["gi"], 5000)
        self.assertTrue(any(a["tipo"] == "contador_baja" for a in almacen.anomalias(self.con)))

    def test_nunca_se_usa_la_hora_de_la_lectura(self):
        self.sesion()
        self.uso()
        self.pasada()
        self.assertEqual(next(iter(self.turnos().values()))["hora_utc"], int(PRIMERA))

    def test_sin_sesion_en_sessions_el_proyecto_es_generico(self):
        self.uso(sid="sesion_huerfana_1")
        self.pasada()
        self.assertEqual(next(iter(self.turnos().values()))["proyecto_raw"], "hermes/otros")

    def test_la_url_del_proveedor_no_lleva_credenciales_ni_consulta_y_las_largas_no_descartan_la_fila(self):
        self.sesion()
        self.uso(sid="sesion_url_001", burl="https://usuario:secreto@api.example.com:8443/v1/?token=abc123#x")
        self.uso(sid="sesion_url_002", burl="https://api.example.com/" + "a" * 300)
        self.uso(sid="sesion_url_003", burl="https://api.nan.builders/v1")
        self.assertEqual(self.pasada()["nuevos"], 3)
        prov = {t["clave"].split(":")[1]: t["proveedor"] for t in self.turnos().values()}
        self.assertEqual(prov["sesion_url_001"], "https://api.example.com:8443/v1")
        self.assertEqual(prov["sesion_url_002"], "https://api.example.com")
        self.assertEqual(prov["sesion_url_003"], "https://api.nan.builders/v1")
        self.assertNotIn("secreto", repr(self.turnos()) + repr(self.con.execute("SELECT * FROM horas").fetchall()))

    def test_bases_incluye_la_raiz_y_los_perfiles(self):
        raiz = os.path.join(self.home, ".hermes", "state.db")
        crea_base(raiz).close()
        b = ingesta_hermes.bases(self.home)
        self.assertEqual(b[0], raiz)
        self.assertIn(self.ruta, b)

    def test_la_base_raiz_se_ingiere_como_otra_fuente(self):
        raiz = os.path.join(self.home, ".hermes", "state.db")
        c = crea_base(raiz)
        c.execute("INSERT INTO session_model_usage(session_id, model, billing_provider, billing_base_url, billing_mode, task, input_tokens, output_tokens, first_seen, last_seen) VALUES('sesion_raiz_1','m','','','','',120,2,?,?)", (PRIMERA, PRIMERA))
        c.commit()
        c.close()
        self.sesion()
        self.uso()
        for b in ingesta_hermes.bases(self.home):
            self.pasada(ruta=b)
        self.assertEqual(len(self.turnos()), 2)

    def test_el_perfil_no_entra_en_la_clave_y_cada_perfil_es_una_fuente(self):
        carpeta2 = os.path.join(self.home, ".hermes", "profiles", "life")
        os.makedirs(carpeta2)
        otra = os.path.join(carpeta2, "state.db")
        crea_base(otra).close()
        c = sqlite3.connect(otra)
        c.execute("INSERT INTO session_model_usage(session_id, model, billing_provider, billing_base_url, billing_mode, task, input_tokens, output_tokens, first_seen, last_seen) VALUES('sesion_xx_1','m','','','','',5,1,?,?)", (PRIMERA, PRIMERA))
        c.commit()
        c.close()
        self.sesion()
        self.uso()
        self.pasada()
        self.pasada(ruta=otra)
        self.assertEqual(len(self.turnos()), 2)
        self.assertTrue(all("codeia" not in k and "life" not in k for k in self.turnos()))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM fuentes WHERE agente='hermes'").fetchone()[0], 2)

    def test_base_sin_cambios_no_se_abre(self):
        self.sesion()
        self.uso()
        self.pasada()
        with mock.patch.object(ingesta_hermes, "abrir_sqlite_solo_lectura", side_effect=AssertionError("no debía abrirse")):
            self.assertTrue(self.pasada()["sin_cambios"])

    def test_filas_hostiles_no_bloquean_la_fuente(self):
        self.sesion()
        self.uso(sid="x'; DROP TABLE turnos; --")
        self.uso(sid="sesion_nul_1", modelo="a\x00b")
        self.uso(sid="sesion_gran_1", inp=10 ** 15)
        self.uso(sid="sesion_hora_1", primera=0)
        self.uso(sid="sesion_txt_01", inp="uno")
        self.origen.execute("INSERT INTO session_model_usage(session_id, model, billing_provider, billing_base_url, billing_mode, task, input_tokens, first_seen) VALUES('sesion_null_1', NULL, '', '', '', '', 1, ?)", (PRIMERA,))
        self.origen.commit()
        self.uso(sid="sesion_buena_1")
        r = self.pasada()
        self.assertEqual(r["nuevos"], 1)
        self.assertEqual(len(self.turnos()), 1)

    def test_sin_tabla_o_base_ilegible_se_anota(self):
        mala = os.path.join(self.dir, "mala.db")
        c = sqlite3.connect(mala)
        c.execute("CREATE TABLE otra(x)")
        c.close()
        self.assertEqual(self.pasada(ruta=mala)["anomalias"], 1)

    def test_el_origen_no_se_modifica(self):
        self.sesion()
        self.uso()
        self.origen.close()
        antes = (open(self.ruta, "rb").read(), sorted(os.listdir(os.path.dirname(self.ruta))))
        self.pasada()
        self.assertEqual((open(self.ruta, "rb").read(), sorted(os.listdir(os.path.dirname(self.ruta)))), antes)
        self.origen = sqlite3.connect(self.ruta)


if __name__ == "__main__":
    unittest.main()
