"""Detección de webs: la sección `webs` de recoger-root (ejecutada de verdad con docker falso y ficheros de proxy de prueba), su parseo hostil, la clasificación por IP,
la selección por el dueño (por id, solo lo detectado) y las rutas HTTP."""
import http.client
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import config_y_estado, rutas, servidor_local, servidores_ssh as ss, servidores_webs as sw  # noqa: E402
from infobar.servidores import revision_servidores  # noqa: E402
from test_config_y_servidor_local import LimpiaConfig  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(RAIZ, "infobar", "remoto", "recoger-root.sh")
SID = "a" * 32
SECRETO = "HASH-DE-AUTH-BASICA-NO-DEBE-SALIR"
IP_SRV, IP_OTRA, IP_CDN = "93.184.216.34", "8.8.8.8", "1.1.1.1"


def seccion(cuerpo):
    cuerpo = list(cuerpo)
    return ["n %d" % len(cuerpo), *cuerpo, "fin %d 0" % len(cuerpo)]


def lee(cuerpo):
    sec, marcadas, _, _ = ss.analizar("### webs\n" + "\n".join(seccion(cuerpo)) + "\n### fin\n")
    return sw.interpretar(sec["webs"], "webs" in marcadas) if "webs" in sec else None


class HostValidoTest(unittest.TestCase):
    def test_validos(self):
        for h in ("a.example.com", "app-1.sub.example.co.uk", "xn--bcher-kva.example", "a1.io", "ab.cd"):
            self.assertTrue(sw.host_valido(h), h)

    def test_rechazados(self):
        for h in ("", "localhost", "a", "sin-punto", "A.example.com", "*.example.com", "a_b.example.com", "-a.example.com", "a-.example.com", "a..example.com", "1.2.3.4", "10.0.0.1",
                  "a.123", "a.local", "x.internal", "x.localhost", "mi.lan", "mi.home.arpa", "a b.example.com", "a.example.com/", "a.example.com:443", "ñandú.example.com",
                  "a" * 64 + ".example.com", ("a." * 130) + "com", None, 5, ["a.com"], "a.example.com\n", "localhost.localdomain"):
            self.assertFalse(sw.host_valido(h), repr(h))


class ParseoTest(unittest.TestCase):
    def test_lectura_sana(self):
        r = lee(["fuente ok", "total 3", "truncado no", "web a.example.com", "web b.example.com", "web c.example.com"])
        self.assertEqual((r["hosts"], r["total"], r["truncado"], r["descartados"]), (["a.example.com", "b.example.com", "c.example.com"], 3, False, 0))

    def test_hostiles_se_descartan_y_se_cuentan(self):
        r = lee(["fuente ok", "total 9", "truncado no", "web ok.example.com", "web ok.example.com", "web A.example.com", "web 10.0.0.1", "web *.example.com", "web localhost",
                 "web x.local", "web a_b.example.com", "web ñ.example.com"])
        self.assertEqual((r["hosts"], r["descartados"]), (["ok.example.com"], 8))

    def test_sin_dato_o_invalida(self):
        self.assertIsNone(lee(["fuente error"]))
        self.assertIsNone(lee(["fuente ok", "total 1", "truncado no", "web a.example.com", "fuente error"]))      # clave repetida
        self.assertIsNone(lee(["fuente ok", "total 1", "truncado no", "total 5"]))
        self.assertIsNone(lee(["fuente ok", "total 1", "truncado no", "truncado si"]))
        self.assertIsNone(lee(["fuente ok", "total x", "truncado no"]))
        self.assertIsNone(lee(["fuente ok", "total 1", "truncado quizas"]))
        self.assertIsNone(lee(["fuente ok", "total 1", "truncado no", "hola mundo"]))
        self.assertIsNone(sw.interpretar(["fuente ok"], False))                                                   # sin marco no hay detección

    def test_no_pasa_de_200(self):
        r = lee(["fuente ok", "total 300", "truncado si", *[f"web h{i}.example.com" for i in range(250)]])
        self.assertEqual(len(r["hosts"]), sw.MAX_HOSTS)
        self.assertEqual(r["descartados"], 50)


@unittest.skipIf(os.name == "nt", "ejecuta recoger-root.sh de verdad con bash, con symlinks a "
                 "timeout/tac (os.symlink necesita Modo Desarrollador o admin en Windows); el "
                 "script es para servidores remotos Linux, no para el cliente")
class ScriptRootWebsTest(unittest.TestCase):
    """La sección `webs` de recoger-root.sh de verdad, con un `docker` falso y ficheros de proxy de prueba."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-webs-root-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.proxy = os.path.join(self.dir, "proxy")
        os.mkdir(self.proxy)
        # Copia en vez de enlace: en Windows, os.symlink pide un privilegio que no siempre hay; para
        # "poner un binario resoluble en el PATH" una copia vale igual.
        for programa in ("timeout", "tac"):
            if shutil.which(programa):
                shutil.copy2(shutil.which(programa), os.path.join(self.dir, programa))
        with open(ROOT) as f:
            texto = f.read()
        texto = re.sub(r"export LC_ALL=C PATH=\S+", f"export LC_ALL=C PATH={aislamiento.ruta_bash(self.dir)}:/usr/bin:/bin", texto, count=1)
        self.assertIn('DIRS_PROXY="/etc/dokploy/traefik/dynamic /etc/easypanel/traefik/config /data/coolify/proxy/dynamic"', texto)
        texto = texto.replace('DIRS_PROXY="/etc/dokploy/traefik/dynamic /etc/easypanel/traefik/config /data/coolify/proxy/dynamic"',
                               f'DIRS_PROXY="{aislamiento.ruta_bash(self.proxy)}"')
        self.script = os.path.join(self.dir, "root.sh")
        with open(self.script, "w") as f:
            f.write(texto)
        self.docker({}, {})

    def docker(self, servicios, contenedores):
        """`servicios` y `contenedores`: listas de dicts de etiquetas (una línea JSON por elemento, como `--format '{{json ...}}'`)."""
        ruta = os.path.join(self.dir, "docker")
        with open(ruta, "w") as f:
            f.write(f"#!{sys.executable}\nimport json, sys\na = sys.argv[1:]\nSERV = {json.dumps(servicios if isinstance(servicios, list) else [])}\nCONT = {json.dumps(contenedores if isinstance(contenedores, list) else [])}\n"
                    "if a[:2] == ['service', 'ls']:\n    print('\\n'.join('s%d' % i for i in range(len(SERV)))); sys.exit(0)\n"
                    "if a[:2] == ['service', 'inspect']:\n    print('\\n'.join(json.dumps(x) for x in SERV)); sys.exit(0)\n"
                    "if a[:1] == ['ps']:\n    print('\\n'.join('c%d' % i for i in range(len(CONT)))); sys.exit(0)\n"
                    "if a[:1] == ['inspect']:\n    if 'json .Config.Labels}}' in a[a.index('--format') + 1].replace('{{', '').replace(' ', ' ') or a[a.index('--format') + 1] == '{{json .Config.Labels}}':\n"
                    "        print('\\n'.join(json.dumps(x) for x in CONT))\n    sys.exit(0)\nsys.exit(0)\n")
        os.chmod(ruta, 0o755)

    def fichero(self, nombre, texto):
        with open(os.path.join(self.proxy, nombre), "w") as f:
            f.write(texto)

    def ejecuta(self):
        r = subprocess.run([aislamiento.BASH, aislamiento.ruta_bash(self.script)], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.crudo = r.stdout
        sec, marcadas, invalidas, _ = ss.analizar("### version\n2\n" + r.stdout + "### fin\n")
        return sec, marcadas, invalidas

    def hosts(self):
        sec, marcadas, invalidas = self.ejecuta()
        return sw.interpretar(sec["webs"], "webs" in marcadas) if "webs" in sec else None

    def test_etiquetas_de_servicios_y_contenedores_y_ficheros_del_proxy(self):
        self.docker([{"traefik.http.routers.x.rule": "Host(`Svc.Example.com`)", "password": SECRETO, "otra": "valor"}],
                    [{"traefik.http.routers.y.rule": "Host(`a.example.com`, `b.example.com`) || Host(`c.example.com`)", "token": SECRETO},
                     {"traefik.tcp.routers.z.rule": "HostSNI(`tcp.example.com`)"}])
        self.fichero("app.yml", f'http:\n  routers:\n    r:\n      rule: "Host(`file.example.com`) && PathPrefix(`/x`)"\n    auth:\n      users: ["admin:{SECRETO}"]\n')
        self.fichero("otra.yaml", "rule: Host(`yaml.example.com`)\n")
        self.fichero("noyml.txt", "Host(`ignorado.example.com`)")
        r = self.hosts()
        self.assertEqual(r["hosts"], sorted(["svc.example.com", "a.example.com", "b.example.com", "c.example.com", "tcp.example.com", "file.example.com", "yaml.example.com"]))
        self.assertNotIn(SECRETO, self.crudo)                                                       # ni de las etiquetas ni de los ficheros sale nada más que dominios
        self.assertNotIn("password", self.crudo)
        self.assertEqual(self.crudo.split("### webs\n", 1)[1].split("\n")[0], "n %d" % (len(r["hosts"]) + 3))   # marco: n = fuente + total + truncado + dominios

    def test_un_host_sin_cerrar_no_arrastra_otros_valores_de_la_misma_linea(self):
        self.docker([{"x": "Host(`sin-cerrar.example.com\", \"y\": \"`otro.example.com`", "z": "Host(`ok.example.com`)"}], [])
        self.assertEqual(self.hosts()["hosts"], ["ok.example.com"])

    @unittest.skipIf(os.name == "nt", "os.mkfifo no existe en Windows, y el symlink pide un privilegio que no siempre hay")
    def test_solo_ficheros_normales_del_directorio_no_enlaces_ni_subdirectorios(self):
        fuera = os.path.join(self.dir, "fuera.yml")
        with open(fuera, "w") as f:
            f.write("rule: Host(`simbolico.example.com`)\n")
        os.symlink(fuera, os.path.join(self.proxy, "enlace.yml"))
        os.mkdir(os.path.join(self.proxy, "dir.yml"))
        with open(os.path.join(self.proxy, "dir.yml", "dentro.yml"), "w") as f:
            f.write("rule: Host(`subdir.example.com`)\n")
        os.mkfifo(os.path.join(self.proxy, "tuberia.yml"))
        self.fichero("normal.yml", "rule: Host(`normal.example.com`)\n")
        self.assertEqual(self.hosts()["hosts"], ["normal.example.com"])

    def test_de_cada_fichero_solo_se_lee_el_primer_megabyte(self):
        self.fichero("largo.yml", ("relleno\n" * 150000) + "rule: Host(`fuera-del-primer-mb.example.com`)\n")
        self.fichero("corto.yml", "rule: Host(`dentro.example.com`)\n")
        self.assertEqual(self.hosts()["hosts"], ["dentro.example.com"])

    def test_un_docker_colgado_es_sin_dato_no_cero_webs_y_no_se_lleva_el_resto_de_root(self):
        self.docker([{"x": "Host(`a.example.com`)"}], [])
        with open(os.path.join(self.dir, "docker"), "a") as f:
            f.write("")
        ruta = os.path.join(self.dir, "docker")
        with open(ruta) as f:
            cuerpo = f.read()
        with open(ruta, "w") as f:                                                      # se cuelga justo en las órdenes de esta sección (no en las de inventario)
            f.write(cuerpo.replace("if a[:2] == ['service', 'ls']:", "if a[:2] == ['service', 'ls']:\n    import time; time.sleep(30)\nif False:"))
        t0 = time.time()
        sec, marcadas, invalidas = self.ejecuta()
        self.assertLess(time.time() - t0, 9)
        cuerpo = self.crudo.split("### webs\n", 1)[1].split("### fin_root", 1)[0].splitlines()
        self.assertEqual(cuerpo[1], "fuente error")                                     # nunca «fuente ok, total 0»
        self.assertEqual(sec["fin_root"], ["omitidos 0"])                               # y el resto de secciones de root llega entero
        self.assertIn("fail2ban", sec)

    def test_solo_cuentan_los_nombres_dentro_de_host(self):
        self.fichero("raro.yml", "middleware: Headers(`otro.example.com`)\nenv: `suelto.example.com`\nrule: PathPrefix(`/p`) && Host(`bueno.example.com`)\n")
        self.docker([{"x": "Algo(`etiqueta.example.com`)", "y": "Host(`ok.example.com`)"}], [])
        self.assertEqual(self.hosts()["hosts"], ["bueno.example.com", "ok.example.com"])

    def test_formas_que_no_son_dominios_publicos_no_salen_del_servidor(self):
        self.docker([{"a": "Host(`*.wild.example.com`)", "b": "Host(`10.0.0.1`)", "c": "Host(`solouna`)", "d": "Host(`-mal.example.com`)", "e": "Host(`con_guion_bajo.example.com`)",
                      "f": "Host(`con espacio.example.com`)", "g": "Host(`ok.example.com`)", "h": "Host(`${DOMINIO}`)", "i": "HostRegexp(`^.+\\.example\\.com$`)"}], [])
        r = self.hosts()
        self.assertEqual(r["hosts"], ["ok.example.com"])

    def test_un_nombre_con_saltos_de_linea_no_puede_abrir_secciones(self):
        self.docker([{"a": "Host(`x.example.com\n### intentos\ntotal 0\n`)", "b": "Host(`y.example.com`)"}], [])
        self.fichero("hostil.yml", "rule: Host(`z.example.com`)\n### fin_root\nomitidos 0\n")
        sec, marcadas, invalidas = self.ejecuta()
        self.assertNotIn("intentos", sec)
        self.assertEqual(sec["fin_root"], ["omitidos 0"])
        self.assertIn("y.example.com", self.crudo)
        self.assertEqual(self.crudo.count("### intentos"), 0)

    def test_mas_de_200_se_recorta_y_se_declara(self):
        self.fichero("muchos.yml", "\n".join(f"rule: Host(`h{i:03d}.example.com`)" for i in range(260)))
        r = self.hosts()
        self.assertEqual((len(r["hosts"]), r["total"], r["truncado"]), (200, 260, True))
        self.assertEqual((self.crudo.count("\nweb "), r["descartados"]), (200, 0))                  # el servidor ya manda solo 200 (no depende del recorte del Mac)

    def test_sin_nada_es_cero_webs_con_fuente_ok(self):
        r = self.hosts()
        self.assertEqual((r["hosts"], r["total"], r["truncado"]), ([], 0, False))

    def test_ficheros_enormes_se_leen_solo_hasta_su_tope(self):
        self.fichero("grande.yml", "rule: Host(`enorme.example.com`)\n" + "x" * (3 * 1024 * 1024))     # > 2 MB: ni se abre, aunque su primera línea tenga un dominio
        self.fichero("grande2.yml", ("relleno\n" * 150000) + "rule: Host(`final.example.com`)\n")    # ~1 MB de relleno: el dominio del final queda fuera del primer MB
        self.fichero("pequeno.yml", "rule: Host(`dentro.example.com`)\n")
        r = self.hosts()
        self.assertIn("dentro.example.com", r["hosts"])
        self.assertNotIn("enorme.example.com", r["hosts"])

    def test_sin_tiempo_es_sin_dato_y_root_descarta_sus_secciones(self):
        for n in ("sshd", "iptables"):
            with open(os.path.join(self.dir, n), "w") as f:
                f.write("#!/bin/bash\nexec sleep 30\n")
            os.chmod(os.path.join(self.dir, n), 0o755)
        sec, marcadas, invalidas = self.ejecuta()
        cuerpo = self.crudo.split("### webs\n", 1)[1].split("### fin_root", 1)[0].splitlines()
        self.assertEqual(cuerpo[1], "fuente error")
        self.assertNotIn("webs", sec)                                                                 # root se pasó de tiempo: sus secciones no valen

    def test_la_lectura_completa_llega_hasta_los_datos_del_servidor(self):
        self.docker([{"x": "Host(`a.example.com`)"}], [])
        sec, marcadas, _ = self.ejecuta()
        d = ss.interpretar(sec, marcadas)
        self.assertEqual(d["webs_detectadas"]["hosts"], ["a.example.com"])


import subprocess  # noqa: E402


class ClasificarTest(unittest.TestCase):
    def resolver(self, tabla):
        def f(host):
            v = tabla[host]
            if isinstance(v, Exception):
                raise v
            if callable(v):
                return v()
            return [(2, 1, 6, "", (ip, 443)) for ip in v]
        return f

    def test_reparto_por_ip(self):
        tabla = {"srv.example.net": [IP_SRV], "uno.example.com": [IP_SRV], "dos.example.com": [IP_OTRA], "cdn.example.com": [IP_CDN, IP_OTRA], "interna.example.com": ["10.0.0.5"],
                 "mixta.example.com": [IP_SRV, "127.0.0.1"], "nada.example.com": OSError("no resuelve"), "v6.example.com": ["::ffff:" + IP_SRV], "ya.example.com": [IP_SRV]}
        c, _ = sw.clasificar("srv.example.net", ["uno.example.com", "dos.example.com", "cdn.example.com", "interna.example.com", "mixta.example.com", "nada.example.com", "v6.example.com",
                                              "ya.example.com"], vigiladas=["https://ya.example.com/"], resolver=self.resolver(tabla))
        self.assertEqual(c["apuntan"], ["uno.example.com", "v6.example.com"])
        self.assertEqual(c["otros"], ["dos.example.com", "cdn.example.com"])
        self.assertEqual(sorted(c["internos"]), ["interna.example.com", "mixta.example.com"])      # si alguna IP es local o privada, no se ofrece
        self.assertEqual(c["sin_resolver"], ["nada.example.com"])
        self.assertEqual(c["vigiladas"], [{"host": "ya.example.com", "url": "https://ya.example.com/"}])

    def test_una_ip_literal_del_servidor_tambien_vale(self):
        c, _ = sw.clasificar(IP_SRV, ["a.example.com"], resolver=self.resolver({IP_SRV: [IP_SRV], "a.example.com": [IP_SRV]}))
        self.assertEqual(c["apuntan"], ["a.example.com"])

    def test_si_el_servidor_no_resuelve_nada_apunta_a_nada(self):
        c, _ = sw.clasificar("srv.example.net", ["a.example.com"], resolver=self.resolver({"srv.example.net": OSError("x"), "a.example.com": [IP_SRV]}))
        self.assertEqual((c["apuntan"], c["otros"]), ([], ["a.example.com"]))

    def test_presupuesto_de_resolucion(self):
        t0 = time.time()
        c, _ = sw.clasificar("srv.example.net", ["lento.example.com", "rapido.example.com"], tope=0.3,
                          resolver=self.resolver({"srv.example.net": [IP_SRV], "rapido.example.com": [IP_SRV], "lento.example.com": lambda: time.sleep(3) or [(2, 1, 6, "", (IP_SRV, 443))]}))
        self.assertLess(time.time() - t0, 2)
        self.assertEqual((c["apuntan"], c["sin_resolver"], c["lentos"]), (["rapido.example.com"], [], ["lento.example.com"]))

    def test_nat64_y_6to4_segun_su_ipv4_incrustada(self):
        tabla = {"srv.example.net": [IP_SRV], "pub.example.com": ["64:ff9b::5db8:d822"], "loc.example.com": ["64:ff9b::7f00:1"], "sx.example.com": ["2002:7f00:1::1"]}
        c, _ = sw.clasificar("srv.example.net", ["pub.example.com", "loc.example.com", "sx.example.com"], resolver=self.resolver(tabla))
        self.assertEqual(sorted(c["internos"]), ["loc.example.com", "sx.example.com"])
        self.assertEqual(c["apuntan"], ["pub.example.com"])                                          # 64:ff9b::5db8:d822 = 93.184.216.34

    def test_punycode_se_enseña_decodificado(self):
        self.assertEqual(sw.decodificado("xn--bcher-kva.example"), "bücher.example")
        self.assertIsNone(sw.decodificado("normal.example.com"))
        self.assertIsNone(sw.decodificado("xn--zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz.example"))


class ClasificarTlsYRedTest(unittest.TestCase):
    def resolver(self, tabla):
        return lambda h: [(2, 1, 6, "", (ip, 443)) for ip in tabla[h]]

    def test_un_certificado_valido_en_el_servidor_lo_hace_suyo_aunque_el_dns_apunte_a_una_cdn(self):
        tabla = {"srv.example.net": [IP_SRV], "cdn.example.com": [IP_CDN], "ajeno.example.com": [IP_CDN]}
        consultas = []

        def tls(ip, h):
            consultas.append((str(ip), h))
            return h == "cdn.example.com"
        c, _ = sw.clasificar("srv.example.net", ["cdn.example.com", "ajeno.example.com"], resolver=self.resolver(tabla), tls=tls)
        self.assertEqual((c["apuntan"], c["otros"], c["por_certificado"]), (["cdn.example.com"], ["ajeno.example.com"], ["cdn.example.com"]))
        self.assertEqual({ip for ip, _ in consultas}, {IP_SRV})                                  # siempre a la IP del servidor, nunca a la de un dominio detectado

    def test_si_la_ip_del_servidor_no_se_conoce_no_hay_comprobacion_por_certificado(self):
        llamadas = []
        c, ips = sw.clasificar("srv.example.net", ["a.example.com"], resolver=self.resolver({"srv.example.net": [], "a.example.com": [IP_CDN]}), tls=lambda ip, h: llamadas.append(1) or True)
        self.assertEqual((c["otros"], ips, llamadas), (["a.example.com"], set(), []))

    def test_tope_de_comprobaciones_por_certificado(self):
        hosts = [f"h{i:02d}.example.com" for i in range(sw.MAX_TLS + 20)]
        tabla = {"srv.example.net": [IP_SRV], **{h: [IP_CDN] for h in hosts}}
        vistos = []
        sw.clasificar("srv.example.net", hosts, resolver=self.resolver(tabla), tls=lambda ip, h: vistos.append(h) or False)
        self.assertEqual(len(vistos), sw.MAX_TLS)

    def test_estado_de_la_ip_del_servidor(self):
        e = {"ok_en": 1000, "datos": {"webs_detectadas": {"hosts": ["a.example.com"], "total": 1, "truncado": False}}}
        srv = {"id": SID, "nombre": "a", "ssh": "infobar@h"}
        for ips, esperado in (([IP_SRV], "publica"), (["100.64.0.5"], "privada"), (["10.0.0.5"], "privada"), ([], "desconocida")):
            sw._CACHE.clear()
            r = sw.detectadas(srv, e, "srv.example.net", ahora=1000, resolver=lambda h, ips=ips: [(2, 1, 6, "", (i, 443)) for i in (ips if h == "srv.example.net" else [IP_SRV])],
                              tls=lambda ip, h: False)
            self.assertEqual(r["ip_servidor"], esperado, ips)

    def test_los_hilos_de_red_son_daemon_y_dejan_de_tomar_trabajo_al_acabar_el_tiempo(self):
        llamadas = []

        demonios = []

        def lento(h):
            llamadas.append(h)
            demonios.append(threading.current_thread().daemon)
            time.sleep(0.5)
            return {IP_SRV}
        hecho, pendientes = sw.paralelo([f"h{i}" for i in range(40)], lento, 0.2, hilos=2)
        self.assertEqual(hecho, {})
        self.assertEqual(len(pendientes), 40)
        n = len(llamadas)
        time.sleep(1.2)
        self.assertLessEqual(len(llamadas), n + 2)                                              # tras el tiempo no se sigue sacando trabajo de la cola
        self.assertTrue(demonios and all(demonios))                                             # ni getaddrinfo ni connect pueden retrasar la salida del proceso

    def test_si_algo_no_dio_tiempo_no_se_guarda_en_la_cache(self):
        sw._CACHE.clear()
        e = {"ok_en": 1000, "datos": {"webs_detectadas": {"hosts": ["lento.example.com"], "total": 1, "truncado": False}}}
        srv = {"id": SID, "nombre": "a", "ssh": "infobar@h"}

        def resolver(h):
            if h == "lento.example.com":
                time.sleep(1.5)
            return [(2, 1, 6, "", (IP_SRV, 443))]
        with mock.patch.object(sw, "RESOLVER_S", 0.2):
            r = sw.detectadas(srv, e, "srv.example.net", ahora=1000, resolver=resolver, tls=lambda ip, h: False)
        self.assertEqual([x["host"] for x in r["lentos"]], ["lento.example.com"])
        self.assertEqual(sw._CACHE, {})                                                          # lo incompleto no se da por bueno 10 minutos

    def test_tlds_que_no_son_dominios_publicos(self):
        for h in ("algo.onion", "algo.test", "algo.invalid", "1.2.3.in-addr.arpa", "x.ip6.arpa"):
            self.assertFalse(sw.host_valido(h), h)


class DetectadasTest(unittest.TestCase):
    def setUp(self):
        sw._CACHE.clear()
        self.llamadas = []
        tabla = {"srv.example.net": [IP_SRV], "a.example.com": [IP_SRV], "b.example.com": [IP_OTRA]}

        def resolver(h):
            self.llamadas.append(h)
            return [(2, 1, 6, "", (ip, 443)) for ip in tabla[h]]
        self.resolver = resolver

    def entrada(self, hosts=("a.example.com", "b.example.com")):
        return {"ok_en": 1000, "datos": {"webs_detectadas": {"hosts": list(hosts), "total": len(hosts), "truncado": False}}}

    def srv(self, **kw):
        return {"id": SID, "nombre": "a", "ssh": "infobar@h", **kw}

    def test_estados(self):
        self.assertEqual(sw.detectadas(self.srv(detectar_webs=False), self.entrada(), "srv.example.net")["estado"], "desactivada")
        for e in (None, {}, {"datos": {}}, {"datos": {"webs_detectadas": "x"}}, {"datos": {"webs_detectadas": {"hosts": "a"}}}):
            self.assertEqual(sw.detectadas(self.srv(), e, "srv.example.net")["estado"], "sin_dato")
        r = sw.detectadas(self.srv(), self.entrada(), "srv.example.net", ahora=1300, resolver=self.resolver, tls=lambda ip, h: False)
        self.assertEqual((r["estado"], r["edad_s"], [x["host"] for x in r["apuntan"]], [x["host"] for x in r["otros"]], r["ip_servidor"]),
                         ("ok", 300, ["a.example.com"], ["b.example.com"], "publica"))

    def test_se_guarda_diez_minutos_y_se_invalida_si_cambia_algo(self):
        sw.detectadas(self.srv(), self.entrada(), "srv.example.net", ahora=1000, resolver=self.resolver, tls=lambda ip, h: False)
        n = len(self.llamadas)
        sw.detectadas(self.srv(), self.entrada(), "srv.example.net", ahora=1100, resolver=self.resolver, tls=lambda ip, h: False)
        self.assertEqual(len(self.llamadas), n)                                                      # sin resolver otra vez
        sw.detectadas(self.srv(webs=["https://a.example.com/"]), self.entrada(), "srv.example.net", ahora=1100, resolver=self.resolver, tls=lambda ip, h: False)
        self.assertGreater(len(self.llamadas), n)                                                    # la lista de webs cambió
        n = len(self.llamadas)
        sw.detectadas(self.srv(webs=["https://a.example.com/"]), self.entrada(), "srv.example.net", ahora=1100 + sw.CACHE_S + 1, resolver=self.resolver, tls=lambda ip, h: False)
        self.assertGreater(len(self.llamadas), n)                                                    # y a los 10 minutos caduca


class CambiarTest(LimpiaConfig):
    def setUp(self):
        for ruta in (rutas.CONFIG_SERVIDORES, rutas.CONFIG_SERVIDORES + ".lock"):
            if os.path.exists(ruta):
                os.remove(ruta)
        super().setUp()
        config_y_estado.cfg()
        config_y_estado.guardar_config({"servidores": [{"nombre": "otro", "ssh": "infobar@o", "id": "b" * 32}, {"nombre": "a", "ssh": "infobar@h", "id": SID,
                                                                                                                    "webs": ["https://ya.example.com/"]}]})

    def webs(self):
        return next(s for s in config_y_estado.cfg()["servidores"] if s["id"] == SID).get("webs")

    def test_aceptar_solo_lo_detectado(self):
        webs, nueva, _q = sw.cambiar(SID, ["a.example.com"], [], {"a.example.com", "b.example.com"})
        self.assertEqual(webs, ["https://ya.example.com/", "https://a.example.com/"])
        self.assertEqual(self.webs(), webs)
        self.assertEqual([s["nombre"] for s in nueva], ["otro", "a"])                                # el orden y el resto de servidores no cambian
        for malo in (["c.example.com"], ["B.example.com"], ["https://a.example.com/"], ["a.example.com\n"], ["*.example.com"]):
            with self.assertRaises(sw.ErrorWebs, msg=malo):
                sw.cambiar(SID, malo, [], {"a.example.com", "b.example.com"})

    def test_repetir_no_duplica_y_quitar_por_dominio_o_url(self):
        sw.cambiar(SID, ["a.example.com"], [], {"a.example.com"})
        sw.cambiar(SID, ["a.example.com"], [], {"a.example.com"})
        self.assertEqual(self.webs().count("https://a.example.com/"), 1)
        sw.cambiar(SID, [], ["a.example.com"], set())
        self.assertEqual(self.webs(), ["https://ya.example.com/"])
        sw.cambiar(SID, [], ["https://ya.example.com/"], set())
        self.assertNotIn("webs", next(s for s in config_y_estado.cfg()["servidores"] if s["id"] == SID))      # sin webs, el campo desaparece

    def test_con_revision_solo_si_la_pagina_vio_la_lista_actual(self):
        from infobar.servidor_local import sin_secretos
        rev = revision_servidores(sin_secretos(config_y_estado.cfg()["servidores"]))
        sw.cambiar(SID, ["a.example.com"], [], {"a.example.com"}, rev)
        with self.assertRaises(sw.ErrorWebs) as c:
            sw.cambiar(SID, ["b.example.com"], [], {"b.example.com"}, rev)                        # la lista ya cambió: la copia vieja no pisa
        self.assertEqual(str(c.exception), "conflicto")
        self.assertNotIn("https://b.example.com/", self.webs())

    def test_quitar_por_url_exacta_o_por_dominio_y_devuelve_lo_quitado(self):
        config_y_estado.modificar_servidores(lambda l: [dict(s, webs=["https://ya.example.com/", "https://ya.example.com/api/salud", "https://otra.example.com/"]) if s["id"] == SID else s for s in l])
        _, _, quitadas = sw.cambiar(SID, [], ["https://ya.example.com/api/salud"], set())
        self.assertEqual((quitadas, self.webs()), (["https://ya.example.com/api/salud"], ["https://ya.example.com/", "https://otra.example.com/"]))
        _, _, quitadas = sw.cambiar(SID, [], ["ya.example.com"], set())                          # por dominio: todas las de ese dominio
        self.assertEqual((quitadas, self.webs()), (["https://ya.example.com/"], ["https://otra.example.com/"]))

    def test_tope_de_100_y_servidor_desconocido(self):
        hosts = [f"h{i:03d}.example.com" for i in range(120)]
        with self.assertRaises(sw.ErrorWebs) as c:
            sw.cambiar(SID, hosts[:100], [], set(hosts))
        self.assertEqual(str(c.exception), "demasiadas")
        self.assertEqual(self.webs(), ["https://ya.example.com/"])                                  # nada a medias
        with self.assertRaises(sw.ErrorWebs) as c:
            sw.cambiar("c" * 32, [], [], set())
        self.assertEqual(str(c.exception), "servidor_desconocido")

    def test_tipos_malos(self):
        for a, q in (("x", []), ([], "x"), ([1], []), ([], [None]), ({}, []), ([None], [])):
            with self.assertRaises(sw.ErrorWebs, msg=(a, q)):
                sw.cambiar(SID, a, q, {"a.example.com"})


class RutasHttpTest(LimpiaConfig):
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

    def setUp(self):
        for ruta in (rutas.CONFIG_SERVIDORES, rutas.CONFIG_SERVIDORES + ".lock", rutas.SERVIDORES):
            if os.path.exists(ruta):
                os.remove(ruta)
        super().setUp()
        sw._CACHE.clear()
        config_y_estado.cfg()
        config_y_estado.guardar_config({"servidores": [{"nombre": "a", "ssh": "infobar@srv.example.net", "id": SID}]})
        os.makedirs(os.path.dirname(rutas.SERVIDORES), exist_ok=True)
        with open(rutas.SERVIDORES, "w") as f:
            json.dump({"servidores": {"a": {"ok_en": time.time(), "datos": {"webs_detectadas": {"hosts": ["a.example.com", "b.example.com"], "total": 2, "truncado": False}}}}}, f)

    def rev(self):
        return revision_servidores(servidor_local.sin_secretos(config_y_estado.cfg()["servidores"]))

    def pide(self, metodo, ruta, cuerpo=None, origen=True, cookie=True):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=30)
        h = {"Host": f"127.0.0.1:{self.puerto}"}
        if cookie:
            h["Cookie"] = self.cookie
        if cuerpo is not None:
            h["Content-Type"] = "application/json"
        if origen and metodo == "POST":
            h["Origin"] = f"http://127.0.0.1:{self.puerto}"
        c.request(metodo, ruta, body=json.dumps(cuerpo).encode() if cuerpo is not None else None, headers=h)
        r = c.getresponse()
        cuerpo = r.read()
        try:
            return r.status, json.loads(cuerpo or b"{}")
        except ValueError:
            return r.status, {"texto": cuerpo.decode("utf-8", "replace")}

    def test_puerta_y_parametros(self):
        self.assertEqual(self.pide("GET", f"/servidores/webs/detectadas?servidor={SID}", cookie=False)[0], 401)
        self.assertEqual(self.pide("POST", "/servidores/webs", {"servidor": SID}, cookie=False)[0], 401)
        self.assertEqual(self.pide("POST", "/servidores/webs", {"servidor": SID}, origen=False)[0], 403)
        for q in ("", "?servidor=a&servidor=b", f"?servidor={SID}&x=1", "?otro=1"):
            self.assertEqual(self.pide("GET", "/servidores/webs/detectadas" + q)[0], 400, q)
        self.assertEqual(self.pide("GET", "/servidores/webs/detectadas?servidor=../../etc")[1]["codigo"], "entrada_invalida")
        self.assertEqual(self.pide("GET", "/servidores/webs/detectadas?servidor=" + "c" * 32)[0], 404)

    def test_detectar_aceptar_y_la_revision_cambia(self):
        tabla = {"srv.example.net": [IP_SRV], "a.example.com": [IP_SRV], "b.example.com": [IP_OTRA]}
        with mock.patch.object(sw, "_resolver_real", side_effect=lambda h: [(2, 1, 6, "", (ip, 443)) for ip in tabla[h]]), \
                mock.patch("infobar.servidores_externo.host_de_ssh", return_value="srv.example.net"):
            codigo, r = self.pide("GET", f"/servidores/webs/detectadas?servidor={SID}")
        self.assertEqual(codigo, 200)
        self.assertEqual(([x["host"] for x in r["apuntan"]], [x["host"] for x in r["otros"]]), (["a.example.com"], ["b.example.com"]))
        rev_antes = revision_servidores(servidor_local.sin_secretos(config_y_estado.cfg()["servidores"]))
        codigo, r = self.pide("POST", "/servidores/webs", {"servidor": SID, "aceptar": ["a.example.com"], "servidores_rev": self.rev()})
        self.assertEqual((codigo, r["webs"]), (200, ["https://a.example.com/"]))
        self.assertNotEqual(r["servidores_rev"], rev_antes)
        self.assertEqual(r["servidores_rev"], revision_servidores(servidor_local.sin_secretos(config_y_estado.cfg()["servidores"])))
        codigo, r = self.pide("POST", "/servidores/webs", {"servidor": SID, "aceptar": ["no-detectada.example.com"], "servidores_rev": self.rev()})
        self.assertEqual((codigo, r["codigo"]), (400, "no_detectada"))
        codigo, r = self.pide("POST", "/servidores/webs", {"servidor": SID, "quitar": ["a.example.com"], "servidores_rev": self.rev()})
        self.assertEqual((codigo, r["webs"]), (200, []))

    def test_sin_revision_o_con_otra_no_se_toca_nada_y_quitar_borra_el_estado_ya(self):
        from infobar import webs_sondeo
        ruta = os.path.join(os.path.dirname(rutas.SERVIDORES), "webs-prueba.json")
        with mock.patch.object(webs_sondeo, "ruta", return_value=ruta):
            with open(ruta, "w") as f:
                json.dump({"webs": {"https://a.example.com/": {"codigo": 200, "intento_en": 1, "proximo_en": 1, "fallos": 0}, "https://b.example.com/": {"codigo": 200}}}, f)
            config_y_estado.modificar_servidores(lambda l: [dict(s, webs=["https://a.example.com/", "https://b.example.com/"]) for s in l])
            codigo, r = self.pide("POST", "/servidores/webs", {"servidor": SID, "quitar": ["https://a.example.com/"]})
            self.assertEqual((codigo, r["codigo"]), (400, "entrada_invalida"))                  # sin revisión no se toca
            codigo, r = self.pide("POST", "/servidores/webs", {"servidor": SID, "quitar": ["https://a.example.com/"], "servidores_rev": "0" * 24})
            self.assertEqual((codigo, r["conflicto"]), (409, True))
            self.assertEqual(len(config_y_estado.cfg()["servidores"][0]["webs"]), 2)
            codigo, r = self.pide("POST", "/servidores/webs", {"servidor": SID, "quitar": ["https://a.example.com/"], "servidores_rev": self.rev()})
            self.assertEqual((codigo, r["webs"]), (200, ["https://b.example.com/"]))
            with open(ruta) as f:
                self.assertEqual(list(json.load(f)["webs"]), ["https://b.example.com/"])        # el estado de la web quitada se borra ya, no en la siguiente pasada

    def test_guardar_ajustes_con_menos_webs_borra_su_estado_ya(self):
        from infobar import webs_sondeo
        ruta = os.path.join(os.path.dirname(rutas.SERVIDORES), "webs-prueba2.json")
        config_y_estado.modificar_servidores(lambda l: [dict(s, webs=["https://a.example.com/", "https://b.example.com/"]) for s in l])
        with mock.patch.object(webs_sondeo, "ruta", return_value=ruta):
            with open(ruta, "w") as f:
                json.dump({"webs": {"https://a.example.com/": {"codigo": 200}, "https://b.example.com/": {"codigo": 200}}}, f)
            _, cfg = self.pide("GET", "/ajustes")[0], self.pide("GET", "/ajustes")[1]["config"]
            cfg["servidores"][0]["webs"] = ["https://b.example.com/"]
            self.assertEqual(self.pide("POST", "/guardar", cfg)[0], 200)
            with open(ruta) as f:
                self.assertEqual(list(json.load(f)["webs"]), ["https://b.example.com/"])

    def test_cuerpos_malos(self):
        for cuerpo in ([], "x", {"servidor": 5}, {"servidor": "zz"}, {"servidor": SID, "aceptar": "a.example.com"}, {"servidor": "c" * 32}):
            codigo, r = self.pide("POST", "/servidores/webs", cuerpo)
            self.assertIn(codigo, (400, 404), cuerpo)
            self.assertFalse(r["ok"])


def tearDownModule():
    for ruta in (rutas.CONFIG_SERVIDORES, rutas.CONFIG_SERVIDORES + ".lock", rutas.SERVIDORES):
        if os.path.exists(ruta):
            os.remove(ruta)


if __name__ == "__main__":
    unittest.main()
