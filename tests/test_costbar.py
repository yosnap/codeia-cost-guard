"""Tests de costbar con unittest (libreria estandar; sin dependencias).

Arrancar con:  python3 -m unittest discover tests

Foco en el bug de zona horaria (#1): los logs de Claude Code/Codex traen timestamps
UTC con Z y las claves de dia/hora han de salir en hora LOCAL. Se fija TZ a
Europe/Madrid para que los resultados sean deterministas en cualquier maquina.
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import costbar


def _escribir(d, nombre, regs):
    """Escribe regs (lista de dicts) como JSONL en d/nombre y devuelve la ruta."""
    p = os.path.join(d, nombre)
    with open(p, "w") as f:
        f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in regs))
    return p


class TZFix(unittest.TestCase):
    """Fija la zona horaria a Madrid (UTC+2 verano, UTC+1 invierno)."""

    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/Madrid"
        time.tzset()
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()

    def _carpeta(self, *trozos):
        d = os.path.join(self._tmp.name, *trozos)
        os.makedirs(d, exist_ok=True)
        return d


class ALocalTest(TZFix):
    def test_z_verano(self):
        self.assertEqual(costbar.a_local("2026-09-27T20:45:08.343Z"), "2026-09-27T22:45:08")

    def test_z_invierno(self):
        self.assertEqual(costbar.a_local("2026-01-10T12:00:00Z"), "2026-01-10T13:00:00")

    def test_offset_explicito(self):
        self.assertEqual(costbar.a_local("2026-09-27T20:45:08+02:00"), "2026-09-27T20:45:08")
        self.assertEqual(costbar.a_local("2026-09-27T22:45:08+0200"), "2026-09-27T22:45:08")

    def test_sin_zona_no_toca(self):
        self.assertEqual(costbar.a_local("2026-09-27T22:45:08"), "2026-09-27T22:45:08")

    def test_roto_o_vacio_no_lanza(self):
        self.assertEqual(costbar.a_local("no-fecha"), "no-fecha")
        self.assertEqual(costbar.a_local(""), "")
        self.assertEqual(costbar.a_local(None), "")


class ParsearTest(TZFix):
    def _ruta_claude(self, regs):
        d = self._carpeta(".claude", "projects", "-home-user-proyecto")
        return _escribir(d, "s.jsonl", regs)

    def test_horas_y_dias_en_local_con_z(self):
        r = costbar.parsear(self._ruta_claude([
            {"timestamp": "2026-09-27T20:45:08.343Z", "model": "claude-sonnet-5",
             "message": {"usage": {"input_tokens": 1000, "output_tokens": 100,
                                   "cache_read_input_tokens": 50}}},
        ]))
        self.assertEqual(list(r["horas"]), ["2026-09-27T22"])        # 20:45Z -> 22:45 local
        self.assertEqual(r["horas"]["2026-09-27T22"]["tok"], 1150)
        self.assertEqual(r["turnos"], {"2026-09-27": 1})
        self.assertEqual(r["dia"]["2026-09-27"]["claude-sonnet-5"], [1000, 100, 50, 0])
        self.assertEqual(r["plan"], "Claude (Max)")

    def test_dedup_doble_registro_codex_mismo_segundo(self):
        uso = {"input_tokens": 1000, "output_tokens": 100, "cached_input_tokens": 900}
        r = costbar.parsear(self._ruta_claude([
            {"timestamp": "2026-09-27T20:45:08.343Z", "type": "token_usage_record",
             "model": "gpt-5", "message": {"usage": uso}},
            {"timestamp": "2026-09-27T20:45:08.343Z", "type": "event_msg/token_count",
             "model": "gpt-5", "message": {"usage": uso}},
        ]))
        self.assertEqual(r["turnos"], {"2026-09-27": 1})
        self.assertEqual(r["horas"]["2026-09-27T22"]["tok"], 1100)
        # codex: cached va DENTRO de input -> entrada fresca 100, cache leida 900
        self.assertEqual(r["dia"]["2026-09-27"]["gpt-5"], [100, 100, 900, 0])

    def test_turnos_distintos_se_suman(self):
        r = costbar.parsear(self._ruta_claude([
            {"timestamp": "2026-09-27T20:45:08Z", "model": "claude-sonnet-5",
             "message": {"usage": {"input_tokens": 10, "output_tokens": 5}}},
            {"timestamp": "2026-09-27T21:45:08Z", "model": "claude-sonnet-5",
             "message": {"usage": {"input_tokens": 20, "output_tokens": 7}}},
        ]))
        self.assertEqual(r["turnos"], {"2026-09-27": 2})
        self.assertEqual(r["dia"]["2026-09-27"]["claude-sonnet-5"], [30, 12, 0, 0])


class ParsearPiTest(TZFix):
    def test_pi_z_a_local_reasoning_y_dedup(self):
        d = self._carpeta(".pi", "agent", "sessions", "x")
        uso = {"input": 100, "output": 10, "reasoning": 3, "cacheRead": 5, "cacheWrite": 1}
        _escribir(d, "s.jsonl", [
            {"type": "session", "cwd": "/home/user/proyecto"},
            {"timestamp": "2026-09-27T20:45:08Z",
             "message": {"role": "assistant", "model": "claude-sonnet-5", "provider": "nodeclub", "usage": uso}},
            {"timestamp": "2026-09-27T20:45:08Z",   # repeticion del mismo turno
             "message": {"role": "assistant", "model": "claude-sonnet-5", "provider": "nodeclub", "usage": uso}},
            {"timestamp": "2026-09-27T21:45:08Z",
             "message": {"role": "assistant", "model": "claude-sonnet-5", "provider": "nodeclub", "usage": uso}},
        ])
        r = costbar.parsear_pi(os.path.join(d, "s.jsonl"))
        self.assertEqual(r["proyecto"], "pi · user/proyecto")
        rec = r["proveedores"]["nodeclub"]
        self.assertEqual(rec["turnos"], {"2026-09-27": 2})
        self.assertEqual(set(rec["horas"]), {"2026-09-27T22", "2026-09-27T23"})
        # reasoning (3 x 2 turnos) va con la salida
        self.assertEqual(rec["dia"]["2026-09-27"]["claude-sonnet-5"], [200, 26, 10, 2])


if __name__ == "__main__":
    unittest.main()
