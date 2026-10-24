"""Alta guiada con una clave elegida de ~/.ssh (solo nombres, validados contra la lista real), la línea para autorizar la clave pública del dueño dentro del
servidor (validada con una regex estricta) y el punto de entrada `registrar` que usa alta-servidor.sh (la misma regla que la página: no duplica, rechaza un
nombre ocupado). Con ssh falsos, como el resto de tests del alta."""
import base64
import contextlib
import http.client
import io
import json
import os
import shlex
import struct
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402
from infobar import config_y_estado, servidor_local, servidores_alta as alta, servidores_alta_claves as claves_ssh  # noqa: E402
from test_servidores_alta import Base  # noqa: E402


def blob(tipo, n=32, relleno=b"\x01"):
    t = tipo.encode()
    return base64.b64encode(struct.pack(">I", len(t)) + t + struct.pack(">I", n) + relleno * n).decode()


ED = "ssh-ed25519 " + blob("ssh-ed25519")
ED_INFOBAR = "ssh-ed25519 " + blob("ssh-ed25519", relleno=b"\x07")                     # la dedicada: otra clave
ECDSA = "ecdsa-sha2-nistp256 " + blob("ecdsa-sha2-nistp256", 65)
RSA = "ssh-rsa " + blob("ssh-rsa", 257)


class ConClaves(Base):
    def setUp(self):
        super().setUp()
        self.ssh_dir = os.path.join(self.dir, "dot-ssh")
        os.mkdir(self.ssh_dir, 0o700)
        p = mock.patch.object(claves_ssh, "DIR_SSH", self.ssh_dir)
        p.start()
        self.addCleanup(p.stop)

    def pon(self, nombre, texto="PRIVADA-DE-PRUEBA\n"):
        with open(os.path.join(self.ssh_dir, nombre), "w") as f:
            f.write(texto)


class ListaDeClavesTest(ConClaves):
    def test_solo_claves_privadas_y_nunca_la_de_infobar_ni_ficheros_de_ssh(self):
        for n in ("id_ed25519", "infobar_ed25519", "solo_privada", ".oculta", "con espacio"):
            self.pon(n)
        for n in ("id_ed25519.pub", ".oculta.pub", "con espacio.pub", "known_hosts", "known_hosts.old", "config", "authorized_keys"):
            self.pon(n, ED + " x\n")
        self.pon("infobar_ed25519.pub", ED_INFOBAR + " infobar\n")
        self.pon("known_hosts.pub", ED)                                                   # aunque tenga .pub al lado
        self.pon("aws.pem", "-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----\n")
        self.pon("otra.key", "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n")
        self.pon("certificado.pem", "-----BEGIN CERTIFICATE-----\nAAAA\n")                # un .pem que no es una clave privada
        os.mkdir(os.path.join(self.ssh_dir, "carpeta"))
        self.pon("carpeta.pub", ED)
        self.assertEqual(claves_ssh.listar(), ["aws.pem", "id_ed25519", "otra.key"])

    def test_una_copia_de_la_clave_de_infobar_con_otro_nombre_no_se_lista_ni_se_ofrece(self):
        self.pon("infobar_ed25519")
        self.pon("infobar_ed25519.pub", ED + " infobar")
        self.pon("copia")
        self.pon("copia.pub", ED + " otro-comentario")                                  # mismo tipo y base64, otro nombre y comentario
        self.pon("id_ed25519")
        self.pon("id_ed25519.pub", ED + " x")                                            # también entre las de siempre
        self.pon("buena")
        self.pon("buena.pub", RSA)
        self.assertEqual(claves_ssh.listar(), ["buena"])
        self.assertIsNone(claves_ssh.ruta("copia"))
        self.assertEqual(claves_ssh.autorizar("copia"), {"linea": None, "motivo": "sin_pub_elegida"})
        self.assertEqual(claves_ssh.autorizar(), {"linea": None, "motivo": "sin_clave"})
        self.pon("id_rsa.pub", RSA)
        self.assertEqual(claves_ssh.autorizar()["fichero"], "id_rsa.pub")

    def test_sin_carpeta_ssh_la_lista_esta_vacia(self):
        with mock.patch.object(claves_ssh, "DIR_SSH", os.path.join(self.dir, "no-existe")):
            self.assertEqual(claves_ssh.listar(), [])

    def test_ruta_solo_de_nombres_de_la_lista_real(self):
        self.pon("id_ed25519")
        self.pon("id_ed25519.pub", ED)
        self.assertEqual(claves_ssh.ruta("id_ed25519"), os.path.join(self.ssh_dir, "id_ed25519"))
        for malo in ("../id_ed25519", "/etc/passwd", "id_ed25519/../id_ed25519", "infobar_ed25519", "known_hosts", "id_ed25519.pub", "", None, 5, ["id_ed25519"]):
            self.assertIsNone(claves_ssh.ruta(malo), repr(malo))


class ClavePublicaTest(ConClaves):
    def test_claves_validas_de_los_tres_tipos(self):
        for k in (ED, ECDSA, RSA):
            self.assertEqual(claves_ssh.publica_valida(k + " paulo@mac.example.com\n"), k + " paulo@mac.example.com")
            self.assertEqual(claves_ssh.publica_valida(k), k)

    def test_un_comentario_raro_se_quita_y_la_clave_sigue_valiendo(self):
        for raro in ("x'; rm -rf ~; '", "$(id)", "a`id`", 'co "mi" llas', "a" * 101):
            self.assertEqual(claves_ssh.publica_valida(ED + " " + raro), ED, raro)

    def test_claves_raras_no_valen(self):
        for mala in ("ssh-dss " + blob("ssh-dss"), "ssh-rsa " + blob("ssh-ed25519"), "ssh-ed25519 corta", "ssh-ed25519 " + blob("ssh-ed25519") + "'", ED + "\n" + ED,
                     "ssh-ed25519 " + blob("ssh-ed25519")[:-1] + "!", "ssh-ed25519", "", None, 5, "restrict " + ED, "ssh-ed25519 " + "A" * 68):
            self.assertIsNone(claves_ssh.publica_valida(mala), repr(mala))

    def test_linea_para_autorizar(self):
        k = ED + " paulo@mac"
        self.assertEqual(claves_ssh.linea_autorizar(k),
                         f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && (grep -qxF '{k}' ~/.ssh/authorized_keys 2>/dev/null || echo '{k}' >> ~/.ssh/authorized_keys)"
                         " && chmod 600 ~/.ssh/authorized_keys")
        self.assertIsNone(claves_ssh.linea_autorizar("ssh-dss AAAA"))
        linea = claves_ssh.linea_autorizar(ED + " x';id;'")
        self.assertEqual(linea.count("'"), 4)                                              # solo las comillas que abren y cierran la clave, dos veces

    def test_de_que_pub_sale_la_clave(self):
        self.assertEqual(claves_ssh.autorizar(), {"linea": None, "motivo": "sin_clave"})
        self.pon("infobar_ed25519.pub", ED_INFOBAR)
        self.assertEqual(claves_ssh.autorizar()["motivo"], "sin_clave")                    # la de infobar nunca
        self.assertIsNone(claves_ssh.autorizar("infobar_ed25519")["linea"])
        self.pon("id_rsa.pub", RSA)
        self.pon("id_ecdsa.pub", ECDSA)
        r = claves_ssh.autorizar()
        self.assertEqual((r["fichero"], ECDSA in r["linea"]), ("id_ecdsa.pub", True))     # la primera de las de siempre que exista
        self.pon("id_ed25519.pub", "esto no es una clave\n")
        self.assertEqual(claves_ssh.autorizar()["fichero"], "id_ecdsa.pub")                # una .pub rota se salta
        self.pon("trabajo.pub", ED + " trabajo")
        self.assertEqual(claves_ssh.autorizar("trabajo")["fichero"], "trabajo.pub")
        self.assertEqual(claves_ssh.autorizar("aws.pem"), {"linea": None, "motivo": "sin_pub_elegida"})


class AltaConClaveTest(ConClaves):
    def setUp(self):
        super().setUp()
        self.pon("trabajo")
        self.pon("trabajo.pub", ED + " trabajo")

    def args_admin(self):
        return [c["args"] for c in self.llamadas() if c["prog"] == "ssh" and not c["args"][-1].startswith("infobar@")
                and c["args"][c["args"].index("--") + 1].startswith("root@")]

    def test_la_clave_elegida_va_con_identitiesonly_en_todas_las_conexiones_de_admin(self):
        self.fake({"conocida": True})
        codigo, r = alta.atiende("diagnostico", {"nombre": "nuevo", "ssh": "root@srv.example.com", "clave": "trabajo"})
        self.assertEqual(codigo, 200, r)
        self.assertEqual(self.espera(r["id"])["estado"], "listo")
        alta.atiende("instalar", {"id": r["id"]})
        self.assertEqual(self.espera(r["id"], ("hecho", "error"))["estado"], "hecho")
        admin = self.args_admin()
        self.assertEqual(len(admin), 2)                                                     # diagnóstico e instalación
        for a in admin:
            self.assertEqual(a[a.index("-i") + 1], os.path.join(self.ssh_dir, "trabajo"))
            self.assertIn("IdentitiesOnly=yes", a)
            self.assertEqual(a[a.index("-F") + 1], os.devnull)
            self.assertIn("StrictHostKeyChecking=yes", a)
        prueba = [c["args"] for c in self.llamadas() if c["prog"] == "ssh" and c["args"][-1].startswith("infobar@")][0]
        self.assertNotIn(os.path.join(self.ssh_dir, "trabajo"), prueba)                    # la prueba sigue con la dedicada

    def test_las_de_siempre_no_llevan_i(self):
        self.fake({"conocida": True})
        codigo, r = alta.atiende("diagnostico", {"nombre": "nuevo", "ssh": "root@srv.example.com", "clave": ""})
        self.espera(r["id"])
        self.assertNotIn("-i", self.args_admin()[0])

    def test_nombres_de_clave_que_no_estan_en_la_lista_se_rechazan_sin_conectar(self):
        for mala in ("../trabajo", "/etc/passwd", "infobar_ed25519", "trabajo.pub", "no-existe", 5, ["trabajo"], True):
            codigo, r = alta.atiende("diagnostico", {"nombre": "x", "ssh": "root@srv.example.com", "clave": mala})
            self.assertEqual((codigo, r["codigo"]), (400, "clave_invalida"), repr(mala))
        self.assertEqual(self.llamadas(), [])

    def test_si_la_clave_desaparece_no_se_usa_otra(self):
        self.fake({"conocida": True})
        codigo, r = alta.atiende("diagnostico", {"nombre": "nuevo", "ssh": "root@srv.example.com", "clave": "trabajo"})
        self.espera(r["id"])
        os.remove(os.path.join(self.ssh_dir, "trabajo"))
        alta.atiende("instalar", {"id": r["id"]})
        r = self.espera(r["id"], ("hecho", "error"))
        self.assertEqual((r["estado"], r["codigo"]), ("error", "clave_invalida"))
        self.assertEqual(len(self.args_admin()), 1)

    def test_sin_entrar_con_la_clave_se_ofrece_autorizarla_y_la_linea_del_terminal_lleva_i(self):
        self.fake({"conocida": True, "sin_auth": True})
        codigo, r = alta.atiende("diagnostico", {"nombre": "mi web", "ssh": "root@srv.example.com", "clave": "trabajo"})
        r = self.espera(r["id"])
        self.assertEqual((r["estado"], r["codigo"]), ("error", "terminal_auth"))
        self.assertEqual(r["autorizar"], {"linea": claves_ssh.linea_autorizar(ED + " trabajo"), "fichero": "trabajo.pub"})
        ruta = os.path.join(self.ssh_dir, "trabajo")
        self.assertEqual(r["linea_terminal"], f"bash {shlex.quote(os.path.join(alta.REMOTO, 'alta-servidor.sh'))} -i {shlex.quote(ruta)} "
                         f"--nombre {shlex.quote('mi web')} root@srv.example.com")

    def test_sin_clave_personal_se_dice_y_no_hay_linea(self):
        self.fake({"conocida": True, "sin_auth": True})
        codigo, r = alta.atiende("diagnostico", {"nombre": "nuevo", "ssh": "root@srv.example.com"})
        r = self.espera(r["id"])
        self.assertEqual(r["autorizar"], {"linea": None, "motivo": "sin_clave"})

    def test_otros_fallos_no_ofrecen_autorizar(self):
        self.fake({"conocida": True, "sin_red": True})
        codigo, r = alta.atiende("diagnostico", {"nombre": "nuevo", "ssh": "root@srv.example.com"})
        self.assertNotIn("autorizar", self.espera(r["id"]))


class RegistroCliTest(Base):
    def cli(self, args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return alta.main(args)

    def servidores(self, nombre):
        return [s for s in config_y_estado.servidores_actuales() if isinstance(s, dict) and s.get("nombre") == nombre]

    def test_registra_no_duplica_y_rechaza_un_nombre_ocupado(self):
        self.assertEqual(self.cli(["registrar", "web1", "203.0.113.10", "22"]), 0)
        self.assertEqual([(s["ssh"], "puerto" in s, len(s["id"])) for s in self.servidores("web1")], [("infobar@203.0.113.10", False, 32)])
        self.assertEqual(self.cli(["registrar", "web1", "203.0.113.10", "22"]), 4)         # repetir no duplica
        self.assertEqual(len(self.servidores("web1")), 1)
        self.assertEqual(self.cli(["registrar", "web1", "203.0.113.11", "22"]), 3)         # mismo nombre, otro servidor
        self.assertEqual(self.cli(["registrar", "web1", "203.0.113.10", "2222"]), 3)
        self.assertEqual(len(self.servidores("web1")), 1)
        self.assertEqual(self.cli(["registrar", "web2", "srv.example.com", "2222"]), 0)
        self.assertEqual(self.servidores("web2")[0]["puerto"], 2222)

    def test_probar_lee_como_la_app_antes_de_guardar(self):
        self.fake({"prueba_ok": False})
        self.assertEqual(self.cli(["registrar", "--probar", "web1", "203.0.113.10", "2222"]), 5)
        self.assertEqual(self.servidores("web1"), [])                                       # sin lectura no se guarda
        prueba = [c["args"] for c in self.llamadas() if c["prog"] == "ssh"][-1]
        for opcion in ("BatchMode=yes", "StrictHostKeyChecking=yes", "ProxyCommand=none", "IdentitiesOnly=yes"):
            self.assertIn(opcion, prueba)                                                   # la MISMA orden que el monitor
        self.assertEqual((prueba[prueba.index("-i") + 1], prueba[prueba.index("-p") + 1], prueba[-1]), (self.clave, "2222", "infobar@203.0.113.10"))
        self.fake({})
        self.assertEqual(self.cli(["registrar", "--probar", "web1", "203.0.113.10", "2222"]), 0)
        self.assertEqual(len(self.servidores("web1")), 1)

    def test_comprobar_no_escribe_nada(self):
        antes = json.dumps(config_y_estado.servidores_actuales(), sort_keys=True)
        self.assertEqual(self.cli(["registrar", "--comprobar", "web1", "203.0.113.10", "22"]), 0)
        self.assertEqual(json.dumps(config_y_estado.servidores_actuales(), sort_keys=True), antes)
        self.cli(["registrar", "web1", "203.0.113.10", "22"])
        self.assertEqual(self.cli(["registrar", "--comprobar", "web1", "203.0.113.10", "22"]), 0)
        self.assertEqual(self.cli(["registrar", "--comprobar", "web1", "203.0.113.99", "22"]), 3)

    def test_entradas_malas(self):
        antes = json.dumps(config_y_estado.servidores_actuales(), sort_keys=True)
        for args in ([], ["otra"], ["registrar"], ["registrar", "web1", "203.0.113.10"], ["registrar", "a/b", "203.0.113.10", "22"],
                     ["registrar", "", "203.0.113.10", "22"], ["registrar", "web1", "203.0.113.10", "22", "extra"], ["registrar", "--otra", "web1", "203.0.113.10", "22"]):
            self.assertEqual(self.cli(args), 2, args)
        # un host o un puerto malos tienen su propio código: el script no culpa al nombre
        for args in (["registrar", "web1", "203.0.113.10", "0"], ["registrar", "web1", "203.0.113.10", "70000"], ["registrar", "web1", "203.0.113.10", "２２"],
                     ["registrar", "web1", "-oProxyCommand=x", "22"], ["registrar", "web1", "h;id", "22"], ["registrar", "web1", "ñandú.example.com", "22"],
                     ["registrar", "--comprobar", "web1", "h_x", "22"]):
            self.assertEqual(self.cli(args), 6, args)
        self.assertEqual(json.dumps(config_y_estado.servidores_actuales(), sort_keys=True), antes)


class AjustesTraeLasClavesTest(ConClaves):
    def test_ajustes_devuelve_solo_los_nombres(self):
        self.pon("trabajo", "CONTENIDO-PRIVADO-NO-DEBE-SALIR\n")
        self.pon("trabajo.pub", ED + " trabajo")
        srv = servidor_local.crear_servidor(0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        puerto = srv.server_address[1]
        c = http.client.HTTPConnection("127.0.0.1", puerto, timeout=30)
        c.request("GET", "/ajustes", headers={"Host": f"127.0.0.1:{puerto}", "Cookie": aislamiento.cookie_de(srv)})
        r = c.getresponse()
        cuerpo = r.read().decode()
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(cuerpo)["claves_ssh"], ["trabajo"])
        self.assertNotIn("CONTENIDO-PRIVADO", cuerpo)
        self.assertNotIn(blob("ssh-ed25519"), cuerpo)



class PaginaTest(unittest.TestCase):
    def test_la_pagina_ofrece_clave_autorizar_y_deja_claro_donde_va_cada_linea(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "panel_assets", "ajustes.js"), encoding="utf-8") as f:
            js = f.read()
        js = js[js.index("function altaGuiada("):js.index("function detectorWebs(")]                # el alta guiada entera
        for marca in ("A.claves_ssh", "clave: claveSel.value", "Las de siempre", "en el Terminal de tu Mac (no dentro del servidor)",
                      "Autoriza tu clave en el servidor", "DENTRO del servidor", "Comprobar de nuevo", "botonEspera(\"Comprobar de nuevo\"", "pausa de un minuto", "ssh-keygen -t ed25519"):
            self.assertIn(marca, js)
        for malo in ("innerHTML", "insertAdjacentHTML", "document.write", "outerHTML"):
            self.assertNotIn(malo, js)


if __name__ == "__main__":
    unittest.main()
