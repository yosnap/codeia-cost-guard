"""Recolector de servidores: lectura de la salida del script, colores de las tarjetas, revision de
bastionado, clasificacion de errores de ssh, orden ssh segura y cache del ciclo.

Arrancar con:  python3 -m unittest discover tests
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401  (HOME y rutas temporales: antes que costbar)
from infobar import config_y_estado, rutas, servidores_ciclo, servidores_estado, servidores_ssh  # noqa: E402

DATOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos")


def _lee(nombre):
    with open(os.path.join(DATOS, nombre), encoding="utf-8") as f:
        return servidores_ssh.interpretar(servidores_ssh.secciones(f.read()))


class InterpretarTest(unittest.TestCase):
    def test_dokploy_con_root(self):
        d = _lee("servidor-dokploy-con-root.txt")
        self.assertEqual(d["cpu"], {"pct": 4.0, "carga": 0.16, "nucleos": 4})
        self.assertEqual(d["memoria"]["total"], 8127725568)
        self.assertEqual(d["memoria"]["usada"], 8127725568 - 4749873152)
        self.assertEqual(d["disco"]["usado"], 30314622976)
        self.assertEqual(d["sesiones"], [{"usuario": "victor", "desde": "203.0.113.7"}])
        self.assertEqual(d["puertos"], [22, 80, 443, 2377, 3000, 7946])
        self.assertEqual(d["intentos"]["total"], 1131)
        self.assertEqual(d["intentos"]["top"][0], ["198.51.100.60", 145])
        self.assertEqual(d["actualizaciones"], {"pendientes": 50, "reinicio": True})
        self.assertEqual([s["nombre"] for s in d["servicios"] if not s["ok"]], ["blog-app-x1"])
        self.assertEqual(d["fail2ban"], {"instalado": True, "ahora": 3, "total": 15})
        self.assertTrue(d["con_root"])
        self.assertNotIn("inventada", servidores_ssh.secciones("### inventada\nx\n"))

    def test_easypanel_sin_root(self):
        d = _lee("servidor-easypanel-sin-root.txt")
        self.assertFalse(d["con_root"])
        self.assertNotIn("fail2ban", d)
        self.assertEqual(d["actualizaciones"]["reinicio"], False)

    def test_salida_basura_no_rompe(self):
        d = servidores_ssh.interpretar(servidores_ssh.secciones(
            "### sistema\nnada\nx\n### memoria\nuno dos\n### intentos\nip <script> 3\ntotal mucho\n"))
        self.assertEqual(d["intentos"]["top"], [])
        self.assertEqual(d["intentos"]["total"], 0)


class EstadoTest(unittest.TestCase):
    def test_tarjetas_y_bastionado_con_root(self):
        d = _lee("servidor-dokploy-con-root.txt")
        webs = [{"url": "https://x.es", "ms": 113, "codigo": 200, "error": ""}]
        t = servidores_estado.tarjetas(d, webs, [22, 80, 443], conocidas=["203.0.113.7"])
        self.assertEqual(t["cpu"]["estado"], "verde")
        self.assertEqual(t["webs"]["estado"], "verde")
        self.assertEqual(t["servicios"]["estado"], "rojo")            # blog-app-x1 0/1
        self.assertEqual(t["intentos"]["estado"], "ambar")            # 386 en 1 h frente a 47/h de media
        self.assertEqual(t["mantenimiento"]["estado"], "ambar")       # reinicio pendiente
        self.assertEqual(t["sesiones"]["estado"], "verde")            # IP conocida
        checks = {c["id"]: c for c in servidores_estado.bastionado(d, [22, 80, 443])}
        self.assertTrue(checks["ssh_contrasena"]["ok"])
        self.assertTrue(checks["ssh_root"]["ok"])
        self.assertFalse(checks["cortafuegos"]["ok"])                 # INPUT ACCEPT y sin ufw
        self.assertIn("ufw", checks["cortafuegos"]["arreglo"])
        self.assertFalse(checks["reinicio"]["ok"])
        r = servidores_estado.resumen_bastionado(list(checks.values()))
        self.assertEqual(r["estado"], "rojo")                         # cortafuegos es critico

    def test_puerto_raro_expuesto_es_rojo(self):
        d = _lee("servidor-easypanel-sin-root.txt")
        t = servidores_estado.tarjetas(d, [], [22, 80, 443, 3000])
        self.assertEqual(t["puertos"]["estado"], "rojo")
        self.assertEqual(t["puertos"]["raros"], [3000])
        checks = {c["id"]: c for c in servidores_estado.bastionado(d, [22, 80, 443, 3000])}
        self.assertIsNone(checks["ssh_contrasena"]["ok"])             # sin root: no se sabe
        self.assertFalse(checks["auto_updates"]["ok"])
        self.assertFalse(checks["parches"]["ok"])
        self.assertIn("3000", checks["puertos"]["arreglo"])


class SudoTest(unittest.TestCase):
    def test_sudo_sin_contrasena(self):
        c = {x["id"]: x for x in servidores_estado.bastionado({"bastionado": {"sudo_nopasswd": "1"}}, None)}
        self.assertFalse(c["sudo"]["ok"])
        c = {x["id"]: x for x in servidores_estado.bastionado({"bastionado": {"sudo_nopasswd": "0"}}, None)}
        self.assertTrue(c["sudo"]["ok"])


class RevisionCodigoTest(unittest.TestCase):
    def test_salidas_raras_no_rompen(self):
        d = servidores_ssh.interpretar(servidores_ssh.secciones(
            "### sistema\n0.5 0.4 0.3 1/2 3\n\n123456.7\n### disco\nnan inf\n### puertos\n22 ² 80 99999\n"))
        self.assertNotIn("cpu", d)                                  # nproc vacio: no se inventa
        self.assertEqual(d["disco"], {"total": 0, "usado": 0})
        self.assertEqual(d["puertos"], [22, 80])

    def test_servicios_terminados_no_son_caidas(self):
        s = servidores_ssh._servicios(["app\t0/0", "tarea\t0/1 (1/1 completed)", "web\t0/1"], [])
        self.assertEqual([x["ok"] for x in s], [True, True, False])
        c = servidores_ssh._servicios([], ["migra\texited\tExited (0) 2 hours ago", "web\texited\tExited (1) 1 min ago"])
        self.assertEqual([x["ok"] for x in c], [True, False])

    def test_servicio_parado_no_es_verde_ni_caido(self):
        s = servidores_ssh._servicios(["app\t0/0", "tarea\t0/1 (1/1 completed)", "web\t0/1", "db\t1/1"], [])
        self.assertEqual([x["parado"] for x in s], [True, False, False, False])     # 0/0 = parado con «stop»
        t = servidores_estado.tarjetas({"servicios": s}, [], None)
        self.assertEqual((t["servicios"]["estado"], t["servicios"]["caidos"], t["servicios"]["parados"]), ("rojo", ["web"], ["app"]))
        t = servidores_estado.tarjetas({"servicios": [x for x in s if x["nombre"] != "web"], "cpu": {"pct": 5}}, [], None)
        self.assertEqual(t["servicios"]["estado"], "ambar")
        # parado: el servidor sale ámbar, pero no entra en modo incidencia (salud viva)
        vivo, estado = servidores_ciclo.estados(t, {"estado": "verde"})
        self.assertEqual((vivo, estado), ("verde", "ambar"))

    def test_detecta_correo_en_marcha(self):
        c = lambda *a: servidores_ssh._correo(*a)[0]
        self.assertEqual(c([], ["correo-mailu-nqcdcp-front-1\trunning\tUp 4 hours"], []), "Mailu")
        self.assertEqual(c([], ["mailu-front-1\texited\tExited (0)"], []), "")              # parado no cuenta
        self.assertEqual(c(["mail_smtp\t0/0", "mailcow_x\t1/1"], [], []), "mailcow")
        self.assertEqual(c(["mailu_smtp\t0/0"], [], []), "")
        self.assertEqual(c(["pspro-db\t1/1"], ["app\trunning\tUp"], []), "")
        # palabra entera, no trozo de texto
        self.assertEqual(c([], ["emailupdater\trunning\tUp", "postfixadmin-web\trunning\tUp"], []), "")
        self.assertEqual(c(["emailupdater\t1/1", "maximo\t1/1"], ["postgres\trunning\tUp"], []), "")
        self.assertEqual(c(["mail.mailu_smtp\t1/1"], [], []), "Mailu")
        self.assertEqual(c([], [], ["exim4"]), "Exim")
        # cada software trae SUS puertos: Postfix no sirve IMAP
        self.assertEqual(servidores_ssh._correo([], [], ["postfix"]), ("Postfix", [25, 465, 587]))
        self.assertEqual(servidores_ssh._correo([], [], ["postfix", "dovecot"]), ("Postfix + Dovecot", [25, 465, 587, 993, 995]))
        self.assertEqual(servidores_ssh._correo([], ["x-mailu-front-1\trunning\tUp"], [])[1], [25, 465, 587, 993, 995])
        d = servidores_ssh.interpretar(servidores_ssh.secciones("### correo\ndovecot\n"))
        self.assertEqual((d.get("correo"), d.get("correo_puertos")), ("Dovecot", [993, 995]))

    def test_correo_solo_acepta_los_puertos_de_su_software(self):
        d = {"bastionado": {}, "puertos": [22, 25, 993], "correo": "Postfix", "correo_puertos": [25, 465, 587]}
        with mock.patch.object(servidores_ciclo, "leer", return_value=(d, "", 0.1)), \
             mock.patch.object(servidores_ciclo, "puertos_expuestos", return_value=[22, 25, 993]), \
             mock.patch.object(servidores_ciclo.webs_sondeo, "medidas", return_value=[]):
            r = servidores_ciclo.recoger_uno({"nombre": "a", "ssh": "infobar@203.0.113.9"}, [])
            self.assertEqual(r["tarjetas"]["puertos"]["raros"], [993])           # el 993 no lo sirve Postfix: sigue avisando
            chk = {x["id"]: x for x in r["bastionado"]}["puertos"]
            self.assertIn("no los sirve", chk["arreglo"])
            # `correo: false` en Ajustes: nada se acepta
            r = servidores_ciclo.recoger_uno({"nombre": "a", "ssh": "infobar@203.0.113.9", "correo": False}, [])
            self.assertEqual(r["tarjetas"]["puertos"]["raros"], [25, 993])
            # `correo: true` acepta todos los de correo, también con un software detectado que no los sirve (un Dovecot sin reconocer)
            r = servidores_ciclo.recoger_uno({"nombre": "a", "ssh": "infobar@203.0.113.9", "correo": True}, [])
            self.assertEqual(r["tarjetas"]["puertos"]["raros"], [])
            # y aunque no se reconozca ningún software
            d.pop("correo"); d.pop("correo_puertos")
            r = servidores_ciclo.recoger_uno({"nombre": "a", "ssh": "infobar@203.0.113.9", "correo": True}, [])
            self.assertEqual(r["tarjetas"]["puertos"]["raros"], [])

    def test_puertos_de_correo(self):
        from infobar.servidores_externo import PUERTOS_CORREO
        d = {"bastionado": {}, "correo": "Mailu"}
        exp = [22, 25, 80, 443, 465, 587, 993]
        t = servidores_estado.tarjetas(d, [], exp, permitidos=PUERTOS_CORREO, correo="Mailu")
        self.assertEqual((t["puertos"]["estado"], t["puertos"]["correo"]), ("verde", "Mailu"))
        chk = {x["id"]: x for x in servidores_estado.bastionado(d, exp, PUERTOS_CORREO, "Mailu")}["puertos"]
        self.assertTrue(chk["ok"])
        self.assertIn("Mailu", chk["texto"])
        # detectado pero desmarcado en Ajustes: fallo, y el arreglo dice dónde se acepta
        chk = {x["id"]: x for x in servidores_estado.bastionado(d, exp)}["puertos"]
        self.assertFalse(chk["ok"])
        self.assertIn("Servidor de correo", chk["arreglo"])
        # sin servidor de correo: el 25 abierto es raro
        chk = {x["id"]: x for x in servidores_estado.bastionado({"bastionado": {}}, [22, 25])}["puertos"]
        self.assertIn("ningún servidor de correo", chk["arreglo"])

    def test_tarjeta_de_puertos_cabe(self):
        from infobar.desplegable_servidores import _puertas
        # los del correo van al texto pequeño: en la cifra grande no caben 7 puertos
        self.assertEqual(_puertas([22, 25, 80, 443, 465, 587, 993], {"correo": "Mailu"}), ("22·80·443", "+ correo (Mailu)"))
        self.assertEqual(_puertas([22, 80, 443, 3000, 8080, 9000], {})[0], "6 abiertas")
        self.assertEqual(_puertas([25, 465], {"correo": "Mailu"}), ("—", "+ correo (Mailu)"))
        self.assertEqual(_puertas([22, 80, 443, 3000], {"raros": [3000]}), ("22·80·443·3000", "¡3000 expuesto!"))

    def test_correo_en_config(self):
        from infobar.servidores import errores_servidores
        base = {"nombre": "a", "ssh": "infobar@203.0.113.9"}
        self.assertEqual(errores_servidores([dict(base, correo=False)]), [])
        self.assertTrue(errores_servidores([dict(base, correo="no")]))

    def test_paquetes_en_espera_no_son_pendientes(self):
        d = servidores_ssh.interpretar(servidores_ssh.secciones("### actualizaciones\n3\nsin-reinicio\n### espera\ninstalables 0\n"))
        self.assertEqual(d["actualizaciones"], {"pendientes": 0, "reinicio": False, "en_espera": 3})
        d = servidores_ssh.interpretar(servidores_ssh.secciones("### actualizaciones\n24\nsin-reinicio\n### espera\ninstalables 21\n"))
        self.assertEqual(d["actualizaciones"], {"pendientes": 21, "reinicio": False, "en_espera": 3})
        # un parche de seguridad retenido sigue siendo pendiente: «espera» nunca lo esconde
        d = servidores_ssh.interpretar(servidores_ssh.secciones(
            "### actualizaciones\n5\nsin-reinicio\n### bastionado\nparches_seguridad 2\n### espera\ninstalables 0\n"))
        self.assertEqual((d["actualizaciones"]["pendientes"], d["actualizaciones"]["en_espera"]), (2, 3))
        d = servidores_ssh.interpretar(servidores_ssh.secciones("### actualizaciones\n3\nsin-reinicio\n### espera\ninstalables " + "9" * 5000 + "\n"))
        self.assertEqual(d["actualizaciones"], {"pendientes": 3, "reinicio": False})          # número absurdo: se ignora
        d = servidores_ssh.interpretar(servidores_ssh.secciones("### actualizaciones\n3\nsin-reinicio\n"))     # script antiguo
        self.assertEqual(d["actualizaciones"], {"pendientes": 3, "reinicio": False})

    def test_puerto_ssh_propio_no_es_raro(self):
        d = {"bastionado": {}}
        t = servidores_estado.tarjetas(d, [], [22, 80, 443, 2222], permitidos={2222})
        self.assertEqual(t["puertos"]["estado"], "verde")

    def test_urls_no_ascii_y_cgnat(self):
        from infobar.servidores import url_valida, ip_no_publica
        self.assertTrue(url_valida("https://example.com/ñ"))
        self.assertEqual(url_valida("https://example.com/%C3%B1?x=1"), "")
        self.assertTrue(ip_no_publica("100.64.0.1"))
        self.assertFalse(ip_no_publica("8.8.8.8"))

    def test_intervalos_con_minimo(self):
        from infobar import servidores_ciclo as C
        self.assertEqual(C.intervalos({"intervalo_normal_s": 1, "intervalo_incidencia_s": "x"}), (60, 60))


class HoraTrasArrancarTest(unittest.TestCase):
    def test_ntp_no_avisa_recien_arrancado(self):
        d = {"bastionado": {"hora_sincronizada": "no"}, "uptime_s": 120}
        self.assertIsNone({c["id"]: c for c in servidores_estado.bastionado(d, None)}["hora"]["ok"])
        d["uptime_s"] = 3600
        self.assertFalse({c["id"]: c for c in servidores_estado.bastionado(d, None)}["hora"]["ok"])


class SshTest(unittest.TestCase):
    def test_orden_segura(self):
        o = servidores_ssh.orden_ssh({"nombre": "a", "ssh": "infobar@203.0.113.9", "puerto": 2222})
        self.assertEqual(o[-2:], ["--", "infobar@203.0.113.9"])
        for opcion in ("ProxyCommand=none", "PermitLocalCommand=no", "BatchMode=yes", "IdentitiesOnly=yes",
                       "StrictHostKeyChecking=yes", "ForwardAgent=no"):
            self.assertIn(opcion, o)
        self.assertIn("2222", o)
        with self.assertRaises(ValueError):
            servidores_ssh.orden_ssh({"nombre": "a", "ssh": "-oProxyCommand=sh"})

    def test_errores_clasificados(self):
        c = servidores_ssh.clasifica_error
        self.assertEqual(c(255, "Host key verification failed."), "clave de host no confiada")
        self.assertEqual(c(255, "infobar@h: Permission denied (publickey)."), "autenticacion")
        self.assertEqual(c(255, "ssh: connect to host h port 22: Operation timed out"), "timeout")
        self.assertEqual(c(255, "ssh: Could not resolve hostname h"), "red")

    def test_salida_enorme_se_corta_mientras_se_lee(self):
        # python en vez de "sh -c yes | head": sh/yes/head no existen en Windows; el propio
        # interprete (sys.executable) si esta disponible en los tres sistemas.
        orden = [sys.executable, "-c", "import sys\nd = b'A' * 65536\nwhile True:\n sys.stdout.buffer.write(d)"]
        with mock.patch.object(servidores_ssh, "orden_ssh", return_value=orden):
            t0 = time.time()
            salida, error = servidores_ssh.ejecutar({"nombre": "a", "ssh": "h"})
        self.assertEqual(error, "")
        self.assertTrue(salida.endswith("\n### cortada\n"))                   # se corta en el tope y se deja constancia
        self.assertLessEqual(len(salida), servidores_ssh.TOPE_BYTES + 20)
        self.assertLess(time.time() - t0, 5)

    def test_servidor_colgado_respeta_el_tope(self):
        orden = [sys.executable, "-c", "import time; time.sleep(30)"]      # "sleep 30" no existe en Windows
        with mock.patch.object(servidores_ssh, "orden_ssh", return_value=orden), \
             mock.patch.object(servidores_ssh, "TOPE_S", 1):
            t0 = time.time()
            salida, error = servidores_ssh.ejecutar({"nombre": "a", "ssh": "h"})
        self.assertEqual(error, "timeout")
        self.assertLess(time.time() - t0, 5)


class CicloTest(unittest.TestCase):
    def setUp(self):
        for r in (rutas.SERVIDORES, rutas.UI_ESTADO, rutas.CONFIG):
            if os.path.exists(r):
                os.remove(r)
        config_y_estado._ULTIMO_CFG = {}
        config_y_estado.guardar_config({"servidores": [{"nombre": "a", "ssh": "infobar@a.example.com"},
                                                       {"nombre": "b", "ssh": "infobar@b.example.com"}]})

    def test_fallo_conserva_el_ultimo_dato_bueno(self):
        bueno = {"error": "", "estado": "verde", "datos": {"cpu": {"pct": 5}}, "tarjetas": {}}
        with mock.patch.object(servidores_ciclo, "recoger_uno", return_value=bueno):
            servidores_ciclo.ciclo(forzar=True)
        with mock.patch.object(servidores_ciclo, "recoger_uno", return_value={"error": "timeout", "estado": "rojo"}):
            c = servidores_ciclo.ciclo(forzar=True)
        a = c["servidores"]["a"]
        self.assertEqual(a["error"], "timeout")
        self.assertEqual(a["datos"]["cpu"]["pct"], 5)                 # el dato bueno sigue
        self.assertIn("ok_en", a)

    def test_obsoleto_y_peor_estado(self):
        ahora = time.time()
        viejo = {"estado": "verde", "ok_en": ahora - 3600, "error": ""}
        self.assertEqual(servidores_ciclo.estado_visible(viejo, ahora), "gris")
        fresco = {"estado": "ambar", "ok_en": ahora - 10, "error": ""}
        self.assertEqual(servidores_ciclo.estado_visible(fresco, ahora), "ambar")
        cache = {"servidores": {"a": fresco, "b": {"error": "red", "estado": "rojo"}}}
        self.assertEqual(servidores_ciclo.peor_estado(cache, ahora), "rojo")

    def test_incidencia_acorta_el_intervalo(self):
        ahora = time.time()
        normal, inc = servidores_ciclo.intervalos()
        previo = {"estado": "verde", "intento_en": ahora - 90}
        self.assertFalse(servidores_ciclo.toca("a", previo, {}, ahora, False, normal, inc))
        self.assertTrue(servidores_ciclo.toca("a", dict(previo, estado_vivo="rojo"), {}, ahora, False, normal, inc))
        # lo que falta de proteccion (estado rojo pero salud viva verde) no acelera la recogida
        self.assertFalse(servidores_ciclo.toca("a", dict(previo, estado="rojo", estado_vivo="verde"), {}, ahora, False, normal, inc))
        ui = {"incidencia": {"a": ahora + 600}}
        self.assertTrue(servidores_ciclo.toca("a", previo, ui, ahora, False, normal, inc))

    def test_clave_rechazada_no_insiste(self):
        ahora = time.time()
        normal, inc = servidores_ciclo.intervalos()
        previo = {"estado": "rojo", "error": "autenticacion", "intento_en": ahora - 600}
        self.assertFalse(servidores_ciclo.en_incidencia("a", previo, {"incidencia": {"a": ahora + 600}}, ahora))
        self.assertFalse(servidores_ciclo.toca("a", previo, {}, ahora, False, normal, inc))
        self.assertTrue(servidores_ciclo.toca("a", dict(previo, intento_en=ahora - 1900), {}, ahora, False, normal, inc))
        self.assertTrue(servidores_ciclo.toca("a", previo, {}, ahora, True, normal, inc))      # Refrescar

    def test_config_mala_no_conecta(self):
        config_y_estado.guardar_config({"servidores": [{"nombre": "x", "ssh": "-oProxyCommand=sh"}]})
        with mock.patch.object(servidores_ciclo, "recoger_uno") as r:
            servidores_ciclo.ciclo(forzar=True)
        r.assert_not_called()


if __name__ == "__main__":
    unittest.main()
