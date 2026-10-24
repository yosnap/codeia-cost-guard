"""Alta guiada de un servidor (sin contraseñas): diagnóstico de solo lectura, huella (TOFU) con confirmación, instalación con la clave del dueño por stdin, prueba como
`infobar` y guardado; entradas hostiles, un alta a la vez, enfriamiento, y que nada crudo del servidor llegue a la respuesta. Con ssh/ssh-keygen/ssh-keyscan falsos."""
import http.client
import io
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import config_y_estado, rutas, servidor_local, servidores_alta as alta, servidores_ssh  # noqa: E402
from test_config_y_servidor_local import CONFIG_BASE, LimpiaConfig  # noqa: E402

CLAVE_B64 = "AAAAC3NzaC1lZDI1NTE5AAAAIJm3qQ2Yk0Uq0cX4q6xv0P7Gm1rA9u8y1pZ4yB2cD5eF"       # 68 caracteres base64, con la forma de una ed25519
HUELLA = "SHA256:" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789+/AbCdE"[:43]
PUB = "ssh-ed25519 " + CLAVE_B64 + " infobar"
SECRETO = "SALIDA-CRUDA-DEL-SERVIDOR-NO-DEBE-SALIR"

FAKE_SSH = r'''#!%(py)s
import json, os, sys, time
cfg = json.load(open(os.environ["FAKE_CFG"]))
a = sys.argv[1:]
entrada = b"" if sys.stdin.isatty() else sys.stdin.buffer.read()
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"prog": "ssh", "args": a, "stdin": len(entrada)}) + "\n")
if cfg.get("espera"):
    time.sleep(cfg["espera"])
i = a.index("--")
destino = a[i + 1]
cmd = a[i + 2] if len(a) > i + 2 else ""
if cfg.get("cambio_huella"):
    sys.stderr.write("Host key verification failed.\n" + %(sec)r); sys.exit(255)
if cfg.get("sin_red"):
    sys.stderr.write("ssh: Could not resolve hostname x\n" + %(sec)r); sys.exit(255)
if destino.startswith("infobar@"):                                  # la prueba de lectura como infobar
    if cfg.get("prueba_ok", True):
        sys.stdout.write("### version\n2\n### fin\n" + %(sec)r); sys.exit(0)
    sys.stderr.write("Permission denied (publickey).\n"); sys.exit(255)
if cfg.get("sin_auth"):
    sys.stderr.write("Permission denied (publickey).\n" + %(sec)r); sys.exit(255)
if "instalar-en-servidor.sh" in cmd:
    open(os.environ["FAKE_TAR"], "wb").write(entrada)
    if cfg.get("instalar_rc") == 255:
        sys.stderr.write("Permission denied (publickey).\n" + %(sec)r); sys.exit(255)
    sys.stdout.write(%(sec)r); sys.exit(cfg.get("instalar_rc", 0))
d = cfg.get("diag", "root")
out = ["uid 0" if d == "root" else "uid 1000",
       "privilegio " + {"root": "root", "allowusers": "root", "sin_systemd": "root", "sudo": "sudo"}.get(d, "ninguno"),
       "so ubuntu 24.04", "systemd " + ("no" if d == "sin_systemd" else "si"), "apt si",
       "visudo " + ("no" if cfg.get("sin_visudo") else "si"),
       "infobar " + ("si" if cfg.get("instalado") else "no"), "scripts " + ("si" if cfg.get("instalado") else "no"),
       "allowusers " + ("si" if d == "allowusers" else "no"), "texto libre que no debe pasar: " + %(sec)r, "fin"]
sys.stdout.write("\n".join(out) + "\n")
'''

FAKE_KEYGEN = r'''#!%(py)s
import json, os, sys
cfg = json.load(open(os.environ["FAKE_CFG"]))
a = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"prog": "ssh-keygen", "args": a}) + "\n")
if "-F" in a:
    if cfg.get("conocida"):
        sys.stdout.write("# Host encontrado: linea 1\n")
    sys.exit(0 if cfg.get("conocida") else 1)
if "-lf" in a:
    sys.stdout.write("256 %(huella)s host (ED25519)\n"); sys.exit(0)
if "-t" in a:                                                       # generar la clave dedicada
    ruta = a[a.index("-f") + 1]
    open(ruta, "w").write("PRIVADA-DE-PRUEBA\n"); os.chmod(ruta, 0o600)
    open(ruta + ".pub", "w").write(cfg.get("publica", %(pub)r) + "\n")
    sys.exit(0)
sys.exit(1)
'''

FAKE_KEYSCAN = r'''#!%(py)s
import json, os, sys
cfg = json.load(open(os.environ["FAKE_CFG"]))
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"prog": "ssh-keyscan", "args": sys.argv[1:]}) + "\n")
if cfg.get("sin_escaneo"):
    sys.exit(1)
host = sys.argv[-1]
puerto = sys.argv[sys.argv.index("-p") + 1]
nombre = host if puerto == "22" else "[%%s]:%%s" %% (host, puerto)
sys.stdout.write("# " + host + ":" + puerto + " SSH-2.0-OpenSSH\n" + nombre + " ssh-ed25519 %(b64)s\n")
'''


def _borra_servidores():
    for ruta in (rutas.CONFIG_SERVIDORES, rutas.CONFIG_SERVIDORES + ".lock"):
        if os.path.exists(ruta):
            os.remove(ruta)


class Base(LimpiaConfig):
    def setUp(self):
        _borra_servidores()
        self.addCleanup(_borra_servidores)          # lo que escribe un alta no puede llegar a los tests que vienen después
        super().setUp()
        config_y_estado.cfg()
        self.dir = tempfile.mkdtemp(prefix="cg-alta-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.bin = os.path.join(self.dir, "bin")
        os.mkdir(self.bin)
        py = sys.executable
        for nombre, fuente in (("ssh", FAKE_SSH % {"py": py, "sec": SECRETO}), ("ssh-keygen", FAKE_KEYGEN % {"py": py, "huella": HUELLA, "pub": PUB}),
                               ("ssh-keyscan", FAKE_KEYSCAN % {"py": py, "b64": CLAVE_B64})):
            if os.name == "nt":
                # en Windows, lanzar un nombre pelado (p.ej. "ssh") solo encuentra un .exe (nunca un
                # fichero sin extension con shebang, aunque este antes en el PATH): la logica va en un
                # .py de verdad (el shebang de la primera linea es un comentario inofensivo para
                # python); plataforma.resuelve_programa() lo encuentra via COSTGUARD_FAKES_DIR (mas
                # abajo) y lo lanza con el interprete actual, sin pasar por un .cmd/cmd.exe (que
                # reparsearia mal un comando remoto con caracteres especiales dentro, p.ej. el
                # diagnostico que ssh manda al servidor)
                with open(os.path.join(self.bin, nombre + ".py"), "w") as f:
                    f.write(fuente)
            else:
                ruta = os.path.join(self.bin, nombre)
                with open(ruta, "w") as f:
                    f.write(fuente)
                os.chmod(ruta, 0o755)
        self.cfg_ruta, self.log_ruta, self.tar_ruta = (os.path.join(self.dir, n) for n in ("cfg.json", "calls.jsonl", "paquete.tar"))
        self.conocidas = os.path.join(self.dir, "known_hosts")
        self.clave = os.path.join(self.dir, "infobar_ed25519")
        self.fake({})
        for p in (mock.patch.dict(os.environ, {"PATH": self.bin + os.pathsep + os.environ["PATH"], "COSTGUARD_FAKES_DIR": self.bin,
                                               "FAKE_CFG": self.cfg_ruta, "FAKE_LOG": self.log_ruta, "FAKE_TAR": self.tar_ruta}),
                  mock.patch.object(alta, "CONOCIDAS", self.conocidas), mock.patch.object(servidores_ssh, "CLAVE", self.clave)):
            p.start()
            self.addCleanup(p.stop)
        alta._ALTAS.clear()
        alta._ENFRIA_HASTA[0] = 0.0
        self.addCleanup(alta._ALTAS.clear)

    def fake(self, cambios, base=None):
        d = base if base is not None else {}
        d.update(cambios)
        with open(self.cfg_ruta, "w") as f:
            json.dump(d, f)
        self.estado_fake = d

    def llamadas(self):
        if not os.path.exists(self.log_ruta):
            return []
        with open(self.log_ruta) as f:
            return [json.loads(l) for l in f if l.strip()]

    def espera(self, id_, estados=("huella", "listo", "terminal", "error", "hecho", "no_soportado"), tope=10):
        fin = time.time() + tope
        while time.time() < fin:
            codigo, r = alta.atiende("estado", {"id": id_})
            if r.get("estado") in estados:
                return r
            time.sleep(0.02)
        self.fail("el alta no llegó a ningún estado final")

    def diagnostica(self, ssh="root@srv.example.com", puerto=22, nombre="nuevo"):
        codigo, r = alta.atiende("diagnostico", {"nombre": nombre, "ssh": ssh, "puerto": puerto})
        self.assertEqual(codigo, 200, r)
        return r["id"]

    def listo(self, **kw):
        self.fake({"conocida": True, **kw})
        id_ = self.diagnostica()
        r = self.espera(id_)
        self.assertEqual(r["estado"], "listo", r)
        return id_


class DiagnosticoTest(Base):
    def test_host_desconocido_enseña_la_huella_y_no_se_conecta(self):
        r = self.espera(self.diagnostica())
        self.assertEqual((r["estado"], r["huella"]), ("huella", HUELLA))
        self.assertIn("ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub", r["comprobar_en_servidor"])
        self.assertEqual([c["prog"] for c in self.llamadas()], ["ssh-keygen", "ssh-keyscan", "ssh-keygen"])      # -F, escaneo, -lf: ninguna conexión ssh
        self.assertFalse(os.path.exists(self.conocidas))                                                          # y nada se añade sin confirmar
        scan = [c for c in self.llamadas() if c["prog"] == "ssh-keyscan"][0]["args"]
        self.assertIn("ed25519", scan)                                                                            # un solo tipo = una conexión

    def test_aceptar_la_huella_anade_exactamente_esa_linea_y_sigue(self):
        id_ = self.diagnostica()
        self.espera(id_)
        self.fake({"conocida": True})
        self.assertEqual(alta.atiende("huella", {"id": id_})[0], 400)                    # sin «acepto» no se añade
        self.assertEqual(alta.atiende("huella", {"id": id_, "acepto": "true"})[0], 400)
        self.assertFalse(os.path.exists(self.conocidas))
        codigo, _ = alta.atiende("huella", {"id": id_, "acepto": True})
        self.assertEqual(codigo, 200)
        r = self.espera(id_)
        self.assertEqual(r["estado"], "listo")
        with open(self.conocidas) as f:
            self.assertEqual(f.read(), f"srv.example.com ssh-ed25519 {CLAVE_B64}\n")
        if os.name != "nt":                # NTFS no tiene bits de permiso POSIX
            self.assertEqual(oct(os.stat(self.conocidas).st_mode & 0o777), "0o600")

    def test_puerto_distinto_usa_el_formato_corchetes(self):
        id_ = self.diagnostica(puerto=2222)
        self.espera(id_)
        self.fake({"conocida": True})
        alta.atiende("huella", {"id": id_, "acepto": True})
        self.espera(id_)
        with open(self.conocidas) as f:
            self.assertTrue(f.read().startswith("[srv.example.com]:2222 ssh-ed25519 "))
        f_ = [c for c in self.llamadas() if c["prog"] == "ssh-keygen" and "-F" in c["args"]][0]["args"]
        self.assertIn("[srv.example.com]:2222", f_)

    def test_con_huella_conocida_la_orden_ssh_es_la_endurecida(self):
        id_ = self.listo()
        ssh = [c for c in self.llamadas() if c["prog"] == "ssh"][0]["args"]
        for opcion in ("BatchMode=yes", "StrictHostKeyChecking=yes", "ProxyCommand=none", "PermitLocalCommand=no", "ForwardAgent=no", "ClearAllForwardings=yes"):
            self.assertIn(opcion, ssh)
        self.assertNotIn("-i", ssh)                                                       # la clave del dueño (agente o ~/.ssh), no la dedicada
        self.assertEqual((ssh[ssh.index("--") + 1], ssh[-1]), ("root@srv.example.com", alta.DIAGNOSTICO))
        self.assertEqual(ssh[ssh.index("-p") + 1], "22")

    def test_casos_del_diagnostico(self):
        casos = {"root": ("listo", "listo"), "sudo": ("listo", "listo"), "ninguno": ("terminal", "terminal_sudo"), "sin_systemd": ("no_soportado", "no_soportado"),
                 "allowusers": ("terminal", "allowusers")}
        for d, (estado, codigo) in casos.items():
            alta._ALTAS.clear()
            self.fake({"conocida": True, "diag": d})
            r = self.espera(self.diagnostica())
            self.assertEqual((r["estado"], r["codigo"]), (estado, codigo), d)

    def test_la_huella_solo_sale_en_su_paso_y_ssh_no_hereda_el_stdin_del_proceso(self):
        r = self.espera(self.diagnostica())
        self.assertIn("huella", r)
        self.fake({"conocida": True})
        # un stdin heredado que NUNCA se cierra (una tubería abierta): si ssh lo heredara, el diagnóstico se quedaría esperando
        lee, escribe = os.pipe()
        guardado = os.dup(0)
        os.dup2(lee, 0)
        try:
            alta._ALTAS.clear()
            id_ = self.diagnostica()
            t0 = time.time()
            r = self.espera(id_, tope=6)
        finally:
            os.dup2(guardado, 0)
            os.close(guardado)
            os.close(lee)
            os.close(escribe)
        self.assertEqual(r["estado"], "listo")
        self.assertLess(time.time() - t0, 5)
        self.assertNotIn("huella", r)
        self.assertNotIn("comprobar_en_servidor", r)

    def test_se_ignora_el_ssh_config_del_dueño_y_se_usa_su_known_hosts(self):
        self.listo()
        ssh = [c for c in self.llamadas() if c["prog"] == "ssh"][0]["args"]
        self.assertEqual(ssh[ssh.index("-F") + 1], os.devnull)                           # ni alias, ni ProxyJump, ni Match exec, ni otro known_hosts
        f_ = [c for c in self.llamadas() if c["prog"] == "ssh-keygen" and "-F" in c["args"]][0]["args"]
        self.assertEqual(f_[f_.index("-f") + 1], self.conocidas)                         # y se mira el MISMO known_hosts que usa el monitor

    def test_sin_sudo_instalado_no_es_soportado_con_su_mensaje(self):
        self.fake({"conocida": True, "sin_visudo": True})
        r = self.espera(self.diagnostica())
        self.assertEqual((r["estado"], r["codigo"]), ("no_soportado", "falta_sudo"))

    def test_el_diagnostico_solo_prueba_sudo_si_el_usuario_esta_en_un_grupo_de_sudo(self):
        # `sudo -n true` de un usuario que no está en sudoers deja un «incidente» en el log del servidor y a veces un correo a root
        self.assertIn('grep -qxE "sudo|wheel|admin" && sudo -n true', alta.DIAGNOSTICO)

    def test_ya_instalado_se_avisa(self):
        self.fake({"conocida": True, "instalado": True})
        r = self.espera(self.diagnostica())
        self.assertEqual((r["codigo"], r["ya_instalado"]), ("listo_actualiza", True))

    def test_terminal_ofrece_la_linea_exacta(self):
        self.fake({"conocida": True, "diag": "ninguno"})
        r = self.espera(self.diagnostica(ssh="admin@srv.example.com", puerto=2222))
        # con --nombre el script da de alta con el mismo nombre que la página: «Comprobar conexión» después no lo duplica
        self.assertEqual(r["linea_terminal"], f"bash {shlex.quote(os.path.join(alta.REMOTO, 'alta-servidor.sh'))} --nombre {shlex.quote('nuevo')} admin@srv.example.com 2222")
        self.fake({"conocida": True, "diag": "ninguno"})
        alta._ALTAS.clear()
        r = self.espera(self.diagnostica())
        self.assertTrue(r["linea_terminal"].endswith("root@srv.example.com"))

    def test_errores_de_conexion_son_mensajes_cerrados_y_arman_el_enfriamiento(self):
        for fallo, codigo in (({"sin_auth": True}, "terminal_auth"), ({"cambio_huella": True}, "huella_cambio"), ({"sin_red": True}, "sin_red")):
            alta._ALTAS.clear()
            alta._ENFRIA_HASTA[0] = 0.0
            self.fake({"conocida": True, **fallo})
            r = self.espera(self.diagnostica())
            self.assertEqual((r["estado"], r["codigo"]), ("error", codigo), fallo)
            self.assertEqual(alta.atiende("diagnostico", {"nombre": "x", "ssh": "root@otro.example.com"})[1]["codigo"], "espera")

    def test_pasado_el_enfriamiento_se_puede_reintentar(self):
        self.fake({"conocida": True, "sin_red": True})
        self.espera(self.diagnostica())
        self.assertEqual(alta.atiende("diagnostico", {"nombre": "x", "ssh": "root@otro.example.com"}, ahora=time.time() + 1)[0], 429)
        self.fake({"conocida": True})
        codigo, r = alta.atiende("diagnostico", {"nombre": "x", "ssh": "root@otro.example.com"}, ahora=time.time() + alta.ENFRIAMIENTO_S + 1)
        self.assertEqual(codigo, 200)

    def test_nada_crudo_del_servidor_llega_a_la_respuesta(self):
        for cambios in ({}, {"diag": "ninguno"}, {"sin_auth": True}, {"sin_red": True}, {"instalar_rc": 1}):
            alta._ALTAS.clear()
            alta._ENFRIA_HASTA[0] = 0.0
            self.fake({"conocida": True, **cambios})
            id_ = self.diagnostica()
            r = self.espera(id_)
            if r["estado"] == "listo":
                alta.atiende("instalar", {"id": id_})
                r = self.espera(id_, ("hecho", "error"))
            self.assertNotIn(SECRETO, json.dumps(r), cambios)
            self.assertIn(r["codigo"], alta.MENSAJES, cambios)

    def test_escaneo_fallido(self):
        self.fake({"sin_escaneo": True})
        r = self.espera(self.diagnostica())
        self.assertEqual((r["estado"], r["codigo"]), ("error", "fallo_huella"))


class EntradasHostilesTest(Base):
    def test_destinos_y_nombres_malos_no_lanzan_nada(self):
        malos = [{"nombre": "x", "ssh": "-oProxyCommand=curl@evil"}, {"nombre": "x", "ssh": "root@host extra"}, {"nombre": "x", "ssh": "root@host\nId"},
                 {"nombre": "x", "ssh": "root@host;ls"}, {"nombre": "x", "ssh": "root@-oProxyCommand=x"}, {"nombre": "x", "ssh": "root@"}, {"nombre": "x", "ssh": "@host"},
                 {"nombre": "x", "ssh": "sinarroba"}, {"nombre": "x", "ssh": "ro ot@host"}, {"nombre": "x", "ssh": "root@host", "puerto": 0},
                 {"nombre": "x", "ssh": "root@host", "puerto": 70000}, {"nombre": "x", "ssh": "root@host", "puerto": True}, {"nombre": "x", "ssh": "root@host", "puerto": "22"},
                 {"nombre": "", "ssh": "root@host"}, {"nombre": "a" * 41, "ssh": "root@host"}, {"nombre": "x/y", "ssh": "root@host"}, {"nombre": "x", "ssh": 5},
                 {"nombre": "x"}, "texto", None, [], {"nombre": "x", "ssh": "root@" + "a" * 300}, {"nombre": "x", "ssh": "root@ñandú.example.com"}]
        for m in malos:
            codigo, r = alta.atiende("diagnostico", m)
            self.assertEqual((codigo, r["codigo"]), (400, "entrada_invalida"), m)
        self.assertEqual(self.llamadas(), [])

    def test_ids_desconocidos_no_tocan_nada(self):
        # sin skipIf: alta.atiende() con un id desconocido es un simple lookup en el dict _ALTAS
        # (ver _busca() en infobar/servidores_alta.py), no toca ssh/subprocess/filesystem
        for accion in ("huella", "instalar", "comprobar", "estado"):
            for cuerpo in ({"id": "no-existe"}, {}, {"id": 5}, None, "x"):
                self.assertEqual(alta.atiende(accion, cuerpo)[1]["codigo"], "desconocida", (accion, cuerpo))

    def test_pasos_incorrectos_segun_el_estado_del_alta(self):
        id_ = self.diagnostica()
        self.espera(id_)                                                                  # estado «huella»
        self.assertEqual(alta.atiende("instalar", {"id": id_})[1]["codigo"], "estado_incorrecto")
        self.assertEqual(alta.atiende("comprobar", {"id": id_})[1]["codigo"], "estado_incorrecto")
        self.assertEqual(alta.atiende("rara", {"id": id_})[0], 404)

    def test_un_alta_a_la_vez(self):
        self.fake({"conocida": True, "espera": 1.0})
        id_ = self.diagnostica()
        codigo, r = alta.atiende("diagnostico", {"nombre": "otro", "ssh": "root@otro.example.com"})
        self.assertEqual((codigo, r["codigo"]), (429, "ocupado"))
        self.espera(id_)

    def test_las_altas_viejas_se_olvidan(self):
        id_ = self.listo()
        alta.atiende("estado", {"id": id_}, ahora=time.time() + alta.VIDA_S + 5)
        self.assertEqual(alta.atiende("estado", {"id": id_})[1]["codigo"], "desconocida")

    def test_una_alta_en_curso_no_se_olvida_por_vieja(self):
        self.fake({"conocida": True, "espera": 1.5})
        id_ = self.diagnostica()
        alta.atiende("estado", {"id": id_}, ahora=time.time() + alta.VIDA_S + 5)
        self.assertEqual(alta.atiende("estado", {"id": id_})[1]["estado"], "en_curso")
        self.espera(id_)

    def test_tope_de_altas_guardadas(self):
        for i in range(alta.MAX_ALTAS):
            self.listo()
            alta._ENFRIA_HASTA[0] = 0.0
        self.assertEqual(alta.atiende("diagnostico", {"nombre": "x", "ssh": "root@srv.example.com"})[1]["codigo"], "demasiadas")
        # las terminadas no cuentan: con una en error (o hecha) vuelve a haber hueco
        a = next(iter(alta._ALTAS.values()))
        a.pon("error", "sin_red")
        alta._ENFRIA_HASTA[0] = 0.0
        self.assertEqual(alta.atiende("diagnostico", {"nombre": "x", "ssh": "root@srv.example.com"})[0], 200)


class InstalacionTest(Base):
    def instala(self, **kw):
        id_ = self.listo(**kw)
        codigo, _ = alta.atiende("instalar", {"id": id_})
        self.assertEqual(codigo, 200)
        return id_, self.espera(id_, ("hecho", "error"))

    def test_instala_con_un_tar_por_stdin_prueba_y_guarda(self):
        id_, r = self.instala()
        self.assertEqual((r["estado"], r["codigo"]), ("hecho", "hecho"))
        ssh = [c for c in self.llamadas() if c["prog"] == "ssh" and any("instalar-en-servidor.sh" in a for a in c["args"])]
        self.assertEqual(len(ssh), 1)                                                     # UNA conexión para instalar
        orden = ssh[0]["args"][-1]
        self.assertIn(f'bash "$d/instalar-en-servidor.sh" "{PUB}"', orden)
        self.assertNotIn("sudo", orden)                                                   # root: sin sudo
        with tarfile.open(self.tar_ruta) as t:
            self.assertEqual(sorted(m.name for m in t.getmembers()), sorted(alta.SCRIPTS))
            for m in t.getmembers():
                self.assertEqual((m.mode, m.uid, m.gid), (0o755, 0, 0))
                with open(os.path.join(alta.REMOTO, m.name), "rb") as f:
                    self.assertEqual(t.extractfile(m).read(), f.read())
        prueba = [c for c in self.llamadas() if c["prog"] == "ssh" and c["args"][-1].startswith("infobar@")]
        self.assertEqual(len(prueba), 1)
        self.assertIn(self.clave, prueba[0]["args"])
        servidor = [s for s in config_y_estado.cfg()["servidores"] if s["nombre"] == "nuevo"][0]
        self.assertEqual((servidor["ssh"], len(servidor["id"]), "puerto" in servidor), ("infobar@srv.example.com", 32, False))
        self.assertEqual(len([c for c in self.llamadas() if c["prog"] == "ssh"]), 3)    # diagnóstico, instalación y prueba: tres conexiones en total

    def test_con_sudo_sin_contrasena_usa_sudo_n(self):
        id_, r = self.instala(diag="sudo")
        self.assertEqual(r["estado"], "hecho")
        orden = [c for c in self.llamadas() if c["prog"] == "ssh" and any("instalar-en-servidor.sh" in a for a in c["args"])][0]["args"][-1]
        self.assertIn('sudo -n bash "$d/instalar-en-servidor.sh"', orden)

    def test_puerto_distinto_se_guarda(self):
        self.fake({"conocida": True})
        id_ = self.diagnostica(puerto=2222)
        self.espera(id_)
        alta.atiende("instalar", {"id": id_})
        self.espera(id_, ("hecho",))
        servidor = [s for s in config_y_estado.cfg()["servidores"] if s["nombre"] == "nuevo"][0]
        self.assertEqual(servidor["puerto"], 2222)

    def test_genera_la_clave_dedicada_si_falta_y_la_valida(self):
        self.assertFalse(os.path.exists(self.clave))
        id_, r = self.instala()
        gen = [c for c in self.llamadas() if c["prog"] == "ssh-keygen" and "-t" in c["args"]][0]["args"]
        self.assertEqual((gen[gen.index("-t") + 1], gen[gen.index("-N") + 1], gen[gen.index("-C") + 1]), ("ed25519", "", "infobar"))
        if os.name != "nt":                # NTFS no tiene bits de permiso POSIX
            self.assertEqual(oct(os.stat(self.clave).st_mode & 0o777), "0o600")
        self.assertEqual(r["estado"], "hecho")

    def test_una_clave_publica_rara_no_se_usa(self):
        for publica in ("ssh-rsa AAAA", PUB + "'; rm -rf /; '", "ssh-ed25519 " + CLAVE_B64 + " co mentario", "", "ssh-ed25519 corta"):
            alta._ALTAS.clear()
            alta._ENFRIA_HASTA[0] = 0.0
            if os.path.exists(self.clave):
                os.remove(self.clave)
            self.fake({"conocida": True, "publica": publica})
            id_ = self.diagnostica()
            self.espera(id_)
            alta.atiende("instalar", {"id": id_})
            r = self.espera(id_, ("hecho", "error"))
            self.assertEqual((r["estado"], r["codigo"]), ("error", "sin_clave"), publica)
        self.assertFalse(any("instalar-en-servidor.sh" in " ".join(c["args"]) for c in self.llamadas() if c["prog"] == "ssh"))

    def test_una_clave_con_intento_de_inyeccion_no_genera_comando(self):
        # sin skipIf: comando_instalacion() solo valida con regex en Python puro, no toca ssh/ssh-keygen
        # ni el sistema de ficheros, asi que esta comprobacion (que la clave no cuela en el comando remoto)
        # corre igual en Windows que en POSIX
        with self.assertRaises(alta.AltaError):
            alta.comando_instalacion("ssh-ed25519 x'; id #", True)

    def test_un_255_de_ssh_no_es_un_fallo_de_instalacion(self):
        id_, r = self.instala(instalar_rc=255)
        self.assertEqual((r["estado"], r["codigo"]), ("error", "terminal_auth"))
        self.assertFalse(any(s["nombre"] == "nuevo" for s in config_y_estado.cfg()["servidores"]))

    def test_si_la_instalacion_falla_no_se_guarda_nada(self):
        antes = json.dumps(config_y_estado.cfg()["servidores"], sort_keys=True)
        id_, r = self.instala(instalar_rc=1)
        self.assertEqual((r["estado"], r["codigo"]), ("error", "fallo_instalacion"))
        self.assertEqual(json.dumps(config_y_estado.cfg()["servidores"], sort_keys=True), antes)

    def test_si_la_prueba_falla_no_se_guarda_nada(self):
        antes = json.dumps(config_y_estado.cfg()["servidores"], sort_keys=True)
        id_, r = self.instala(prueba_ok=False)
        self.assertEqual((r["estado"], r["codigo"]), ("error", "fallo_prueba"))
        self.assertEqual(json.dumps(config_y_estado.cfg()["servidores"], sort_keys=True), antes)

    def test_repetir_el_alta_no_duplica_y_un_nombre_ocupado_se_rechaza(self):
        self.instala()
        alta._ALTAS.clear()
        id_, r = self.instala()
        self.assertEqual(r["estado"], "hecho")
        self.assertEqual(len([s for s in config_y_estado.cfg()["servidores"] if s["nombre"] == "nuevo"]), 1)
        alta._ALTAS.clear()
        alta._ENFRIA_HASTA[0] = 0.0
        self.fake({"conocida": True})
        codigo, r = alta.atiende("diagnostico", {"nombre": "nuevo", "ssh": "root@otro.example.com"})
        id_ = r["id"]
        self.espera(id_)
        alta.atiende("instalar", {"id": id_})
        r = self.espera(id_, ("hecho", "error"))
        self.assertEqual((r["estado"], r["codigo"]), ("error", "nombre_repetido"))

    def test_actualizar_un_servidor_ya_instalado_lo_dice_y_no_lo_duplica(self):
        self.instala()
        alta._ALTAS.clear()
        id_, r = self.instala(instalado=True)
        self.assertEqual((r["estado"], r["codigo"]), ("hecho", "hecho_actualiza"))
        self.assertEqual(len([s for s in config_y_estado.cfg()["servidores"] if s["nombre"] == "nuevo"]), 1)

    def test_la_version_del_script_que_trae_la_app(self):
        self.assertIsInstance(alta.version_script(), int)
        self.assertGreaterEqual(alta.version_script(), 3)

    def test_comprobar_tras_el_terminal_prueba_y_guarda(self):
        self.fake({"conocida": True, "diag": "ninguno"})
        id_ = self.diagnostica()
        self.assertEqual(self.espera(id_)["estado"], "terminal")
        codigo, _ = alta.atiende("comprobar", {"id": id_})
        self.assertEqual(codigo, 200)
        self.assertEqual(self.espera(id_, ("hecho", "error"))["estado"], "hecho")
        self.assertTrue(any(s["nombre"] == "nuevo" for s in config_y_estado.cfg()["servidores"]))

    def test_comprobar_sin_haber_hecho_el_alta_falla_y_no_guarda(self):
        self.fake({"conocida": True, "diag": "ninguno", "prueba_ok": False})
        id_ = self.diagnostica()
        self.espera(id_)
        alta.atiende("comprobar", {"id": id_})
        r = self.espera(id_, ("hecho", "error"))
        self.assertEqual((r["estado"], r["codigo"]), ("error", "fallo_prueba"))
        self.assertFalse(any(s["nombre"] == "nuevo" for s in config_y_estado.cfg()["servidores"]))


class ComandoRemotoTest(Base):
    """La orden remota EJECUTADA de verdad con `sh`, `mktemp` y `tar` locales y un `bash`/`sudo` de pega: directorio temporal propio y aleatorio, 0700, se borra siempre."""

    def corre(self, como_root):
        import re as _re
        shims = os.path.join(self.dir, "shims")
        os.makedirs(shims, exist_ok=True)
        registro = os.path.join(self.dir, "registro.jsonl")
        with open(os.path.join(shims, "bash"), "w") as f:                      # sustituye a `bash instalar-en-servidor.sh KEY`
            f.write(f'#!{sys.executable}\nimport json, os, sys\nd = os.path.dirname(sys.argv[1])\n'
                    'info = {"dir": d, "modo": oct(os.stat(d).st_mode & 0o777), "scripts": sorted(os.listdir(d)), '
                    '"modos": sorted(oct(os.stat(os.path.join(d, n)).st_mode & 0o777) for n in os.listdir(d)), "clave": sys.argv[2], "argc": len(sys.argv)}\n'
                    f'open({registro!r}, "a").write(json.dumps(info) + "\\n")\n')
        with open(os.path.join(shims, "sudo"), "w") as f:
            # la ruta va DENTRO del texto del script (no como argv): sin ruta_bash, cada "\" de una
            # ruta de Windows se come el caracter siguiente al interpretarla /bin/sh como escape
            f.write('#!/bin/sh\n[ "$1" = "-n" ] && shift\necho sudo >> ' + aislamiento.ruta_bash(os.path.join(self.dir, "sudo.log")) + '\nexec "$@"\n')
        for n in ("bash", "sudo"):
            os.chmod(os.path.join(shims, n), 0o755)
        cmd = alta.comando_instalacion(PUB, como_root)
        # "sh" pelado en Windows cae en el *launcher* de WSL de System32 (ver aislamiento.BASH); el
        # PATH se extiende sobre el real (nunca se sustituye por uno sintetico: /usr/bin no existe
        # tal cual en Windows), con los shims resueltos a forma POSIX de MSYS2
        env = dict(os.environ)
        env["PATH"] = aislamiento.ruta_bash(shims) + os.pathsep + env["PATH"]
        env["HOME"] = aislamiento.ruta_bash(self.dir)
        r = subprocess.run([aislamiento.BASH, "-c", cmd], input=alta._paquete(), capture_output=True, timeout=30, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(registro) as f:
            return [json.loads(l) for l in f], _re

    def test_directorio_temporal_aleatorio_privado_y_borrado(self):
        a, _re = self.corre(True)
        b, _ = self.corre(True)
        for visto in (a[-1], b[-1]):
            self.assertRegex(visto["dir"], r"/infobar-alta\.[A-Za-z0-9]{6}$")
            self.assertEqual(visto["scripts"], sorted(alta.SCRIPTS))
            if os.name != "nt":             # NTFS no tiene bits de permiso POSIX (visto["modo"]/["modos"] vienen de mkdir/tar en el "servidor")
                self.assertEqual(visto["modo"], "0o700")                              # otro usuario del servidor no puede plantar nada ahí
                self.assertEqual(set(visto["modos"]), {"0o755"})
            self.assertEqual((visto["clave"], visto["argc"]), (PUB, 3))               # la clave llega como UN solo argumento
            self.assertFalse(os.path.exists(visto["dir"]))                            # y el trap lo borra
        self.assertNotEqual(a[-1]["dir"], b[-1]["dir"])                               # nunca una ruta fija

    def test_sudo_solo_si_no_es_root_y_se_limpia_aunque_falle(self):
        sudo_log = os.path.join(self.dir, "sudo.log")
        self.corre(True)
        self.assertFalse(os.path.exists(sudo_log))
        self.corre(False)
        self.assertTrue(os.path.exists(sudo_log))
        # un instalador que falla: el directorio se borra igual
        shims = os.path.join(self.dir, "shims")
        with open(os.path.join(shims, "bash"), "w") as f:
            f.write("#!/bin/sh\necho $(dirname $1) > " + aislamiento.ruta_bash(os.path.join(self.dir, "ultimo.txt")) + "\nexit 7\n")
        env = dict(os.environ)
        env["PATH"] = aislamiento.ruta_bash(shims) + os.pathsep + env["PATH"]
        env["HOME"] = aislamiento.ruta_bash(self.dir)
        r = subprocess.run([aislamiento.BASH, "-c", alta.comando_instalacion(PUB, True)], input=alta._paquete(), capture_output=True, timeout=30, env=env)
        self.assertEqual(r.returncode, 7)
        with open(os.path.join(self.dir, "ultimo.txt")) as f:
            self.assertFalse(os.path.exists(f.read().strip()))


class KnownHostsTest(Base):
    def test_solo_se_añaden_lineas_con_la_forma_de_una_clave_de_host(self):
        for mala in ("host ssh-rsa AAAA", "host ssh-ed25519 corta", "dos\nlineas ssh-ed25519 " + CLAVE_B64, "host ssh-ed25519 " + CLAVE_B64 + " extra", "", "-oX ssh-ed25519 " + CLAVE_B64,
                     "ho st ssh-ed25519 " + CLAVE_B64):
            with self.assertRaises(alta.AltaError, msg=repr(mala)):
                alta._anade_conocida(mala)
        self.assertFalse(os.path.exists(self.conocidas))
        alta._anade_conocida(f"[h.example.com]:22 ssh-ed25519 {CLAVE_B64}")
        alta._anade_conocida(f"h2.example.com ssh-ed25519 {CLAVE_B64}")
        alta._anade_conocida(f"h2.example.com ssh-ed25519 {CLAVE_B64}")           # la misma línea otra vez: no se duplica
        with open(self.conocidas) as f:
            self.assertEqual(len(f.read().splitlines()), 2)


class RutasHttpTest(Base):
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

    def pide(self, metodo, ruta, cuerpo=None, origen=True, tipo="application/json", cookie=True):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=30)
        h = {"Host": f"127.0.0.1:{self.puerto}"}
        if cookie:
            h["Cookie"] = self.cookie
        if tipo:
            h["Content-Type"] = tipo
        if origen:
            h["Origin"] = f"http://127.0.0.1:{self.puerto}"
        c.request(metodo, ruta, body=json.dumps(cuerpo).encode() if cuerpo is not None else None, headers=h)
        r = c.getresponse()
        cuerpo = r.read()
        try:
            return r.status, json.loads(cuerpo or b"{}")
        except ValueError:                                  # las puertas del panel responden texto plano
            return r.status, {"texto": cuerpo.decode("utf-8", "replace")}

    def test_la_puerta_del_panel_protege_el_alta(self):
        cuerpo = {"nombre": "x", "ssh": "root@srv.example.com"}
        self.assertEqual(self.pide("POST", "/servidores/alta/diagnostico", cuerpo, origen=False)[0], 403)
        self.assertEqual(self.pide("POST", "/servidores/alta/diagnostico", cuerpo, tipo="text/plain")[0], 403)
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=30)
        c.request("POST", "/servidores/alta/diagnostico", body=json.dumps(cuerpo).encode(), headers={"Host": "evil.com", "Content-Type": "application/json",
                                                                                                  "Origin": "http://evil.com"})
        self.assertEqual(c.getresponse().status, 403)
        self.assertEqual(self.pide("POST", "/servidores/alta/diagnostico", cuerpo, cookie=False)[0], 401)          # sin sesión no se atiende nada
        self.assertEqual(self.pide("GET", "/servidores/alta/estado?id=x", cookie=False, tipo=None, origen=False)[0], 401)
        self.assertEqual(self.llamadas(), [])

    def test_flujo_completo_por_http(self):
        self.fake({"conocida": True})
        codigo, r = self.pide("POST", "/servidores/alta/diagnostico", {"nombre": "web1", "ssh": "root@srv.example.com"})
        self.assertEqual(codigo, 200)
        self.espera(r["id"])
        self.assertEqual(self.pide("GET", f"/servidores/alta/estado?id={r['id']}", tipo=None, origen=False)[1]["estado"], "listo")
        self.assertEqual(self.pide("POST", "/servidores/alta/instalar", {"id": r["id"]})[0], 200)
        self.espera(r["id"], ("hecho",))
        self.assertTrue(any(s["nombre"] == "web1" for s in config_y_estado.cfg()["servidores"]))
        # el servidor nuevo cambia la revisión de la lista: una página con la copia vieja recibe un 409
        self.assertEqual(self.pide("POST", "/guardar", {"proveedores": [], "aliases": {}, "servidores": CONFIG_BASE["servidores"], "servidores_rev": "0" * 24})[0], 409)

    def test_parametros_raros_del_estado(self):
        for q in ("", "?id=a&id=b", "?otro=1", "?id=a&x=1"):
            self.assertEqual(self.pide("GET", "/servidores/alta/estado" + q, tipo=None, origen=False)[0], 400, q)

    def test_json_malo_y_cuerpos_no_objeto(self):
        for cuerpo in ([], "x", 5):
            codigo, r = self.pide("POST", "/servidores/alta/diagnostico", cuerpo)
            self.assertEqual((codigo, r["codigo"]), (400, "entrada_invalida"))


def tearDownModule():
    for ruta in (rutas.CONFIG_SERVIDORES, rutas.CONFIG_SERVIDORES + ".lock"):
        if os.path.exists(ruta):
            os.remove(ruta)


if __name__ == "__main__":
    unittest.main()
