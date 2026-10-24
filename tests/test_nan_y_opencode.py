"""Consumo de NaN por API (/v1/usage) y lectura de OpenCode (SQLite): lo heredado del PR de la cuota
en vivo. Sin red ni ficheros reales: urlopen simulado y una base de OpenCode de prueba.

Arrancar con:  python3 -m unittest discover tests
"""
import io
import json
import os
import sqlite3
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401  (HOME y rutas temporales: antes que costbar)
from infobar import cuota_nan, lectores_logs, rutas  # noqa: E402

MES = "2026-10-01"


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _responde(obj):
    return lambda *a, **k: _Resp(json.dumps(obj).encode())


class UsoNanTest(unittest.TestCase):
    def setUp(self):
        if os.path.exists(rutas.NAN_USO_CACHE):
            os.remove(rutas.NAN_USO_CACHE)
        self.clave = mock.patch.object(cuota_nan, "clave_nan", return_value="clave-de-prueba")
        self.clave.start()

    def tearDown(self):
        self.clave.stop()

    def test_respuesta_buena_se_cachea(self):
        ok = {"totals": {"by_model": [{"model": "GLM5.3-Flash", "total_tokens": 1200}]}}
        with mock.patch("urllib.request.urlopen", _responde(ok)):
            self.assertEqual(cuota_nan.uso_nan(MES), {"glm5.3-flash": 1200})
        # la segunda llamada sale de la cache: sin red
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("no deberia llamar")):
            self.assertEqual(cuota_nan.uso_nan(MES), {"glm5.3-flash": 1200})

    def test_respuesta_sin_totals_no_se_cachea_como_cero(self):
        with mock.patch("urllib.request.urlopen", _responde({"error": "rate limited"})):
            self.assertIsNone(cuota_nan.uso_nan(MES))              # sin dato bueno previo
        self.assertFalse(os.path.exists(rutas.NAN_USO_CACHE))
        # con un dato bueno previo (caducado), una respuesta rara devuelve ese dato, no {}
        with open(rutas.NAN_USO_CACHE, "w") as f:
            json.dump({"ts": time.time() - 3600, "inicio_mes": MES, "por_modelo": {"glm": 7}}, f)
        with mock.patch("urllib.request.urlopen", _responde({"totals": None})):
            self.assertEqual(cuota_nan.uso_nan(MES), {"glm": 7})

    def test_sin_clave_no_hay_red(self):
        with mock.patch.object(cuota_nan, "clave_nan", return_value=""), \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("no deberia llamar")):
            self.assertIsNone(cuota_nan.uso_nan(MES))


def _base_opencode(ruta, mensajes):
    con = sqlite3.connect(ruta)
    con.execute("create table session (id text, directory text, model text, tokens_input int, "
                "tokens_output int, tokens_cache_read int, tokens_cache_write int, time_created int)")
    con.execute("create table message (id text, session_id text, time_created int, data text)")
    con.execute("insert into session values ('s1', '/home/u/tienda', '', 0, 0, 0, 0, 0)")
    for i, (ms, datos) in enumerate(mensajes):
        con.execute("insert into message values (?, 's1', ?, ?)", (f"m{i}", ms, json.dumps(datos)))
    con.commit()
    con.close()


class OpenCodeTest(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.join(rutas.HOME, ".local", "share", "opencode")
        os.makedirs(self.dir, exist_ok=True)
        self.db = os.path.join(self.dir, "opencode.db")
        for f in (self.db, self.db + "-wal"):
            if os.path.exists(f):
                os.remove(f)
        lectores_logs._OPENCODE.clear()
        ahora = int(time.time() * 1000)
        uso = {"providerID": "opencode-go", "modelID": "mimo-v2.6",
               "tokens": {"input": 100, "output": 20, "cache": {"read": 50, "write": 5}}}
        _base_opencode(self.db, [(ahora, uso), (ahora + 1000, uso)])

    def _total(self, proyectos):
        return sum(sum(sum(v) for v in mods.values()) for p in proyectos.values() for mods in p["dia"].values())

    def test_lee_por_mensaje_y_no_repite_la_consulta(self):
        proyectos, nombres = lectores_logs.leer_bases()
        self.assertTrue(any(n.startswith("OpenCode") for n in nombres))
        self.assertEqual(self._total(proyectos), 2 * (100 + 20 + 50 + 5))
        # base sin cambios: no se vuelve a abrir
        with mock.patch.object(lectores_logs.sqlite3, "connect", side_effect=AssertionError("no deberia consultar")):
            proyectos2, _ = lectores_logs.leer_bases()
        self.assertEqual(self._total(proyectos2), self._total(proyectos))

    def test_si_la_base_cambia_se_vuelve_a_leer(self):
        lectores_logs.leer_bases()
        con = sqlite3.connect(self.db)
        uso = {"providerID": "opencode-go", "modelID": "mimo-v2.6", "tokens": {"input": 1, "output": 1}}
        con.execute("insert into message values ('m9', 's1', ?, ?)", (int(time.time() * 1000), json.dumps(uso)))
        con.commit()
        con.close()
        # adelanta el mtime 2s a mano: la resolucion del reloj del FS (~15ms en Windows) puede dejar
        # el mismo tick que el de antes del insert, y el tamano no siempre crece (paginas libres
        # reutilizadas) -> con "ahora" la firma de cache podria no cambiar y el test seria inestable
        nuevo = os.path.getmtime(self.db) + 2
        os.utime(self.db, (nuevo, nuevo))
        proyectos, _ = lectores_logs.leer_bases()
        self.assertEqual(self._total(proyectos), 2 * (100 + 20 + 50 + 5) + 2)


if __name__ == "__main__":
    unittest.main()
