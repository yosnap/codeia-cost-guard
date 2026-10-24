"""Comparación en sombra: por agente y día cerrado, con la dirección de la diferencia, Hermes fuera, solo días cerrados y
recientes, y un fichero privado sin proyectos."""
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import almacen, consulta, rutas, sombra  # noqa: E402
from test_consulta import Base as BaseConsulta, turno  # noqa: E402

AHORA = 1_790_000_000                                  # 2026-09-21 ~14:13Z


def dia(n):
    return consulta._dia(AHORA - n * 86400, None)


def vista(**por_clave):
    return por_clave


class TestComparar(unittest.TestCase):
    def test_clases(self):
        ant = {("claude", "2026-09-01", "m"): [10, 0, 0, 0], ("claude", "2026-09-05", "a"): [5, 0, 0, 0], ("codex", "2026-09-01", "m"): [100, 0, 0, 0],
               ("pi", "2026-09-01", "m"): [1, 0, 0, 0]}
        nue = {("claude", "2026-09-01", "m"): [10, 0, 0, 0], ("claude", "2026-09-05", "b"): [5, 0, 0, 0], ("codex", "2026-09-01", "m"): [60, 0, 0, 0],
               ("pi", "2026-09-01", "m"): [9, 0, 0, 0]}
        f = {(x["agente"], x["dia"]): x for x in sombra.comparar(ant, nue)}
        self.assertEqual(f[("claude", "2026-09-01")]["clase"], "igual")
        self.assertEqual(f[("claude", "2026-09-05")]["clase"], "atribucion_de_modelo")
        self.assertEqual(f[("codex", "2026-09-01")]["clase"], "el_antiguo_cuenta_de_mas_grande")
        self.assertEqual(f[("pi", "2026-09-01")]["clase"], "la_base_cuenta_de_mas_grande")

    def test_pequeña_diferencia_y_traspaso_entre_contadores(self):
        ant = {("codex", "2026-09-01", "m"): [1000, 0, 0, 0], ("claude", "2026-09-01", "m"): [500, 0, 500, 0]}
        nue = {("codex", "2026-09-01", "m"): [980, 0, 0, 0], ("claude", "2026-09-01", "m"): [0, 0, 1000, 0]}
        f = {x["agente"]: x for x in sombra.comparar(ant, nue)}
        self.assertEqual(f["codex"]["clase"], "el_antiguo_cuenta_de_mas")
        self.assertNotEqual(f["claude"]["clase"], "atribucion_de_modelo")      # mismo total, otros contadores: se ve

    def test_perdida_de_la_base_no_se_archiva_como_explicada(self):
        f = sombra.comparar({("codex", "2026-09-01", "m"): [1000, 0, 0, 0]}, {("codex", "2026-09-01", "m"): [10, 0, 0, 0]})
        self.assertTrue(f[0]["clase"].endswith("_grande"))

    def test_cruce_de_dia_marca_los_dos_dias(self):
        ant = {("claude", "2026-09-10", "m"): [10, 0, 0, 0], ("claude", "2026-09-11", "m"): [0, 0, 0, 5]}
        nue = {("claude", "2026-09-10", "m"): [4, 0, 0, 0], ("claude", "2026-09-11", "m"): [6, 0, 0, 5]}
        self.assertEqual({x["clase"] for x in sombra.comparar(ant, nue)}, {"cruce_de_dia"})

    def test_dias_cerrados_en_cambio_de_hora(self):
        from zoneinfo import ZoneInfo
        try:
            z = ZoneInfo("Europe/Madrid")
        except Exception as e:          # falta tzdata (tipico en Windows sin el paquete)
            self.skipTest(f"falta tzdata para zoneinfo ({e}); instala con: pip install tzdata")
        import datetime as dt
        ahora = int(dt.datetime(2026, 10, 25, 23, 30, tzinfo=z).timestamp())
        self.assertNotIn("2026-10-25", sombra.dias_cerrados(ahora, z))
        self.assertIn("2026-10-24", sombra.dias_cerrados(ahora, z))
        ahora = int(dt.datetime(2026, 3, 30, 0, 30, tzinfo=z).timestamp())
        self.assertIn("2026-03-29", sombra.dias_cerrados(ahora, z))

    def test_cruce_de_dia(self):
        ant = {("claude", "2026-09-10", "m"): [10, 0, 0, 0], ("claude", "2026-09-11", "m"): [0, 0, 0, 5]}
        nue = {("claude", "2026-09-10", "m"): [4, 0, 0, 0], ("claude", "2026-09-11", "m"): [6, 0, 0, 5]}
        f = {x["dia"]: x for x in sombra.comparar(ant, nue)}
        self.assertEqual(f["2026-09-10"]["clase"], "cruce_de_dia")

    def test_hermes_fuera(self):
        f = sombra.comparar({("hermes", "2026-09-01", "m"): [1, 0, 0, 0]}, {("hermes", "2026-09-01", "m"): [99, 0, 0, 0]})
        self.assertEqual(f, [])

    def test_agente_de(self):
        self.assertEqual(sombra.agente_de("/h/.claude/projects/x.jsonl", {}), "claude")
        self.assertEqual(sombra.agente_de("/h/.codex/sessions/x.jsonl", {}), "codex")
        self.assertEqual(sombra.agente_de("pi:a|b", {}), "pi")
        self.assertEqual(sombra.agente_de("sqlite:x", {"proyecto": "OpenCode · a"}), "opencode")
        self.assertEqual(sombra.agente_de("sqlite:x", {"proyecto": "Hermes/p"}), "hermes")


class TestRegistrar(BaseConsulta):
    def test_registra_solo_dias_cerrados_y_recientes(self):
        self.mete([turno("claude:ayer", AHORA - 86400, gi=10, go=0, cr=0, cw=0),
                   turno("claude:hoy", AHORA - 60, gi=7, go=0, cr=0, cw=0),
                   turno("claude:viejo", AHORA - 40 * 86400, gi=3, go=0, cr=0, cw=0)])
        self.completa()
        vistos = {"/h/.claude/projects/a.jsonl": {"proyecto": "x/y/z", "dia": {
            dia(1): {"claude-sonnet-5": [10, 0, 0, 0]}, dia(0): {"claude-sonnet-5": [99, 0, 0, 0]},
            dia(40): {"claude-sonnet-5": [3, 0, 0, 0]}}}}
        res = sombra.registrar(vistos, ahora=AHORA)
        self.assertEqual(res["claude"]["dias"], 1)               # ni hoy (abierto) ni hace 40 días
        self.assertEqual(res["claude"]["iguales"], 1)
        ruta = os.path.join(rutas.DATOS, "sombra.jsonl")
        if os.name != "nt":                    # NTFS no tiene bits de permiso POSIX
            self.assertEqual(stat.S_IMODE(os.stat(ruta).st_mode), 0o600)
        with open(ruta, encoding="utf-8") as f:
            linea = json.loads(f.readline())
        self.assertNotIn("x/y/z", json.dumps(linea))

    def test_sin_base_no_registra(self):
        rutas.ALMACEN = os.path.join(self.dir, "no.sqlite")
        self.assertIsNone(sombra.registrar({"/h/.claude/p.jsonl": {"dia": {}}}, ahora=AHORA))


if __name__ == "__main__":
    unittest.main()
