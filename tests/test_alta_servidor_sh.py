"""alta-servidor.sh ejecutado de verdad con un `ssh`/`scp` de pega: una sola autenticación (conexión maestra) y todo lo demás por ella, errores de ssh
traducidos (nada crudo), autorizar la clave personal solo si se entró con contraseña (nunca la de infobar), `-i` para elegir clave, aviso si se lanza
dentro del servidor y alta en la app con la misma regla que Ajustes. La app se copia a una carpeta temporal: el registro escribe en SU servidores.json."""
import base64
import json
import os
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMOTO = os.path.join(RAIZ, "infobar", "remoto")


def _clave(tipo="ssh-ed25519", n=32):
    t = tipo.encode()
    return tipo + " " + base64.b64encode(struct.pack(">I", len(t)) + t + struct.pack(">I", n) + b"\x02" * n).decode()


PERSONAL = _clave() + " paulo@mac.example.com"

FAKE_SSH = r'''#!%(py)s
import json, os, sys
a = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"prog": "ssh", "args": a}) + "\n")
if "-O" in a:
    sys.exit(255 if "check" in a and os.environ.get("FAKE_SIN_MAESTRA") else 0)
def opcion(nombre):
    for i, x in enumerate(a[:-1]):
        if x == "-o" and a[i + 1].startswith(nombre + "="):
            return a[i + 1].split("=", 1)[1]
modo = os.environ.get("FAKE_MODO", "")
if "-M" in a:
    fallos = {"solo_claves": "root@srv.example.com: Permission denied (publickey).\n",
              "contrasena_mal": "Permission denied, please try again.\nroot@srv.example.com: Permission denied (publickey,password).\n",
              "rechazada": "ssh: connect to host srv.example.com port 22: Connection refused\n",
              "tiempo": "ssh: connect to host srv.example.com port 22: Operation timed out\n",
              "huella": "@@@@@@@@\n@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\nHost key verification failed.\n",
              "rara": "algo que no conozco SALIDA-CRUDA\n"}
    if modo in fallos:
        sys.stderr.write(fallos[modo]); sys.exit(255)
    open(opcion("ControlPath"), "w").close()
    sys.stderr.write('Authenticated to srv.example.com ([203.0.113.10]:22) using "%%s".\n' %% os.environ.get("FAKE_METODO", "password"))
    sys.exit(0)
i = a.index("--")
destino, cmd = a[i + 1], " ".join(a[i + 2:])
if destino.startswith("infobar@"):
    if os.environ.get("FAKE_PRUEBA_MAL"):
        sys.exit(255)
    sys.stdout.write("### version\n3\n"); sys.exit(0)
if "id -u" in cmd:
    print(os.environ.get("FAKE_PRIV", "root")); sys.exit(0)
if "grep -qF" in cmd:
    sys.exit(0 if os.environ.get("FAKE_YA_AUTORIZADA") else 1)
if "authorized_keys" in cmd:
    open(os.environ["FAKE_AUTORIZADA"], "w").write(cmd); sys.exit(0)
if "mktemp -d" in cmd:
    print("/tmp/infobar-alta.AbC123"); sys.exit(0)
if "instalar-en-servidor.sh" in cmd:
    sys.exit(int(os.environ.get("FAKE_INSTALAR_RC", "0")))
sys.exit(0)
'''

FAKE_SCP = r'''#!%(py)s
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"prog": "scp", "args": sys.argv[1:]}) + "\n")
'''


class AltaServidorSh(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-alta-sh-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.app = os.path.join(self.dir, "app")
        shutil.copytree(os.path.join(RAIZ, "infobar"), os.path.join(self.app, "infobar"), ignore=shutil.ignore_patterns("__pycache__"))
        self.script = os.path.join(self.app, "infobar", "remoto", "alta-servidor.sh")
        self.home = os.path.join(self.dir, "home")
        os.makedirs(os.path.join(self.home, ".ssh"), 0o700)
        self.bin = os.path.join(self.dir, "bin")
        os.mkdir(self.bin)
        for nombre, fuente in (("ssh", FAKE_SSH % {"py": sys.executable}), ("scp", FAKE_SCP % {"py": sys.executable}),
                               # sys.executable va DENTRO del cuerpo del script (no en el shebang):
                               # /bin/sh se come cada "\" de una ruta de Windows al interpretar texto
                               # normal (a diferencia del shebang, que no pasa por ese reparseo)
                               ("python3", f'#!/bin/sh\nexec {aislamiento.ruta_bash(sys.executable)} "$@"\n')):
            ruta = os.path.join(self.bin, nombre)
            with open(ruta, "w") as f:
                f.write(fuente)
            os.chmod(ruta, 0o755)
        # "ssh" pelado (sin extensión) lo resuelve bash solo (interpreta el shebang él mismo), pero
        # infobar/plataforma.py::resuelve_programa() en Windows nunca lo encuentra así (solo .exe/.com
        # o un sidecar .py vía COSTGUARD_FAKES_DIR): el "$PY -m infobar..." que lanza el propio script
        # para --probar necesita este sidecar para dar con el ssh de pega.
        if os.name == "nt":
            with open(os.path.join(self.bin, "ssh.py"), "w") as f:
                f.write(FAKE_SSH % {"py": sys.executable})
        self.log = os.path.join(self.dir, "llamadas.jsonl")
        self.autorizada = os.path.join(self.dir, "autorizada.txt")

    def entorno(self, **extra):
        env = {"PATH": self.bin + os.pathsep + os.environ.get("PATH", ""), "HOME": self.home, "FAKE_LOG": self.log,
               "FAKE_AUTORIZADA": self.autorizada, "LANG": "es_ES.UTF-8", "COSTGUARD_FAKES_DIR": self.bin, **extra}
        if os.name == "nt":
            # ntpath.expanduser() (lo que usa servidores_ssh.CLAVE="~/.ssh/infobar_ed25519") nunca mira
            # HOME en Windows, solo USERPROFILE (o HOMEDRIVE+HOMEPATH); en una sesión real de Windows
            # esa variable siempre la pone el propio sistema - aquí hay que simularla a mano porque el
            # entorno del proceso nativo anidado ("$PY" -m infobar... dentro del propio script) es
            # justo el que se construye aquí, no el real del sistema.
            env.setdefault("USERPROFILE", self.home)
        return env

    def corre(self, *args, script=None, **extra):
        # "bash" pelado en Windows cae en el *launcher* de WSL de System32 (ver aislamiento.BASH); el
        # script y el PATH de los fakes van en forma POSIX de MSYS2 (argv/env reparseados si no)
        env = self.entorno(**extra)
        env["PATH"] = aislamiento.ruta_bash(self.bin) + os.pathsep + env["PATH"]
        env["HOME"] = aislamiento.ruta_bash(self.home)
        # encoding="utf-8" explicito: subprocess.run(text=True) sin el decodifica con el codepage ANSI
        # del sistema en Windows, no UTF-8 - el script SI emite UTF-8 de verdad (tiene «»/ñ literales).
        # errors="replace": un subproceso anidado (p.ej. el "$PY -m infobar..." que lanza el propio
        # script) puede imprimir el suyo con el codepage del sistema, no UTF-8 - no debe romper el test
        r = subprocess.run([aislamiento.BASH, aislamiento.ruta_bash(script or self.script), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60, stdin=subprocess.DEVNULL, env=env)
        return r.returncode, r.stdout + r.stderr

    def llamadas(self, prog=None):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [c for c in map(json.loads, f) if prog is None or c["prog"] == prog]

    def servidores(self):
        ruta = os.path.join(self.app, "servidores.json")
        if not os.path.exists(ruta):
            return []
        with open(ruta) as f:
            return json.load(f).get("servidores") or []

    def pon_personal(self, nombre="id_ed25519", publica=PERSONAL):
        with open(os.path.join(self.home, ".ssh", nombre), "w") as f:
            f.write("PRIVADA-DE-PRUEBA\n")
        with open(os.path.join(self.home, ".ssh", nombre + ".pub"), "w") as f:
            f.write(publica + "\n")


class SintaxisTest(unittest.TestCase):
    def test_bash_n_de_todos_los_scripts(self):
        for n in sorted(os.listdir(REMOTO)):
            if n.endswith(".sh"):
                r = subprocess.run([aislamiento.BASH, "-n", aislamiento.ruta_bash(os.path.join(REMOTO, n))], capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, 0, n + ": " + r.stderr)


class FlujoTest(AltaServidorSh):
    def test_una_sola_autenticacion_todo_por_la_maestra_y_lo_da_de_alta(self):
        rc, out = self.corre("--nombre", "web1", "root@srv.example.com")
        self.assertEqual(rc, 0, out)
        self.assertIn("Servidor «web1» añadido", out)
        ssh = self.llamadas("ssh")
        maestras = [c["args"] for c in ssh if "-M" in c["args"]]
        self.assertEqual(len(maestras), 1)                                             # la contraseña se pide como mucho una vez
        m = maestras[0]
        ctl = m[m.index("-o", m.index("-M")) + 1].split("=", 1)[1]
        if os.name == "nt":
            # MSYS2 traduce cualquier argumento que empiece por "/" a su forma nativa de Windows al
            # lanzar un ejecutable nativo (nuestro ssh de pega es python.exe por detrás; pasaría igual
            # con el ssh.exe real de Windows): el socket que mktemp creó bajo /tmp llega aquí ya como
            # ruta nativa con barras "/", no con el prefijo POSIX original.
            self.assertRegex(ctl, r"[\\/]cg-alta\.")
        else:
            self.assertTrue(ctl.startswith("/tmp/cg-alta."))
        self.assertIn("ControlPersist=30m", m)                                     # la cierra el «-O exit» del final; si el script muere de golpe, caduca sola
        admin = [c["args"] for c in ssh if "-M" not in c["args"] and "-O" not in c["args"] and c["args"][c["args"].index("--") + 1] == "root@srv.example.com"]
        self.assertGreaterEqual(len(admin), 3)                                         # permisos, carpeta, instalación
        for a in admin:
            self.assertIn("ControlPath=" + ctl, a)
            self.assertIn("ControlMaster=no", a)
        scp = self.llamadas("scp")
        self.assertEqual(len(scp), 1)
        self.assertIn("ControlPath=" + ctl, scp[0]["args"])
        self.assertTrue(scp[0]["args"][-1].endswith(":/tmp/infobar-alta.AbC123/"))
        instalar = [a for a in admin if "instalar-en-servidor.sh" in a[-1]][0][-1]
        self.assertTrue(instalar.startswith("bash /tmp/infobar-alta.AbC123/instalar-en-servidor.sh 'ssh-ed25519 "), instalar)   # root: sin sudo
        self.assertIn("-O", ssh[-1]["args"])                                          # el maestro se cierra al salir
        self.assertFalse(os.path.exists(os.path.dirname(ctl)))                         # y su carpeta privada se borra
        self.assertEqual([(s["nombre"], s["ssh"], "puerto" in s) for s in self.servidores()], [("web1", "infobar@srv.example.com", False)])
        self.assertIn("ssh-keygen -t ed25519", out)                                    # entró con contraseña y no hay clave personal: se le dice cómo crearla

        rc, out = self.corre("--nombre", "web1", "root@srv.example.com")
        self.assertEqual(rc, 0, out)
        self.assertIn("ya estaba", out)
        self.assertEqual(len(self.servidores()), 1)                                    # repetir no duplica

    def test_por_defecto_el_nombre_es_el_host_y_el_puerto_se_guarda(self):
        rc, out = self.corre("root@srv.example.com", "2222")
        self.assertEqual(rc, 0, out)
        self.assertEqual([(s["nombre"], s.get("puerto")) for s in self.servidores()], [("srv.example.com", 2222)])
        self.assertEqual(self.llamadas("scp")[0]["args"][self.llamadas("scp")[0]["args"].index("-P") + 1], "2222")

    def test_un_nombre_de_otro_servidor_se_rechaza_antes_de_conectar(self):
        r = subprocess.run([sys.executable, "-m", "infobar.servidores_alta", "registrar", "web1", "203.0.113.50", "22"], cwd=self.app,
                           capture_output=True, text=True, timeout=60, env=self.entorno(PYTHONPATH=self.app))
        self.assertEqual(r.returncode, 0, r.stderr)
        rc, out = self.corre("--nombre", "web1", "root@srv.example.com")
        self.assertEqual(rc, 1)
        self.assertIn("Ya hay otro servidor llamado «web1»", out)
        self.assertEqual(self.llamadas("ssh"), [])

    def test_si_no_puede_registrar_explica_como_sin_perder_lo_instalado(self):
        venv = os.path.join(self.home, ".cost-guard", "venv", "bin")
        os.makedirs(venv)
        with open(os.path.join(venv, "python"), "w") as f:
            f.write("#!/bin/sh\nexit 1\n")
        os.chmod(os.path.join(venv, "python"), 0o755)
        rc, out = self.corre("--nombre", "web1", "root@srv.example.com")
        self.assertEqual(rc, 1)
        self.assertIn("no pude consultar la lista de servidores", out)
        self.assertTrue(any("instalar-en-servidor.sh" in c["args"][-1] for c in self.llamadas("ssh")))
        self.assertIn("Lo instalado queda bien", out)
        self.assertIn('{"nombre": "web1", "ssh": "infobar@srv.example.com"}', out)

    def test_la_prueba_final_es_la_de_la_app(self):
        rc, out = self.corre("root@srv.example.com")
        self.assertEqual(rc, 0, out)
        prueba = [c["args"] for c in self.llamadas("ssh") if c["args"][-1] == "infobar@srv.example.com"]
        self.assertEqual(len(prueba), 1)
        for opcion in ("BatchMode=yes", "StrictHostKeyChecking=yes", "ProxyCommand=none", "IdentitiesOnly=yes", "ControlPath=none"):
            self.assertIn(opcion, prueba[0])
        # ntpath.expanduser() concatena HOME (con "\") y el resto de la ruta original ("/.ssh/...")
        # tal cual, sin normalizar separador: en Windows da una ruta valida pero con barras mixtas.
        self.assertEqual(os.path.normpath(prueba[0][prueba[0].index("-i") + 1]),
                          os.path.normpath(os.path.join(self.home, ".ssh", "infobar_ed25519")))

    def test_sin_python_la_prueba_ignora_el_ssh_config_y_la_ficha_es_json_valido(self):
        venv = os.path.join(self.home, ".cost-guard", "venv", "bin")
        os.makedirs(venv)
        with open(os.path.join(venv, "python"), "w") as f:
            f.write("#!/bin/sh\nexit 1\n")
        os.chmod(os.path.join(venv, "python"), 0o755)
        rc, out = self.corre("--nombre", 'web "1" \\ x', "root@srv.example.com", "2222")
        self.assertEqual(rc, 1)
        prueba = [c["args"] for c in self.llamadas("ssh") if c["args"][-1] == "infobar@srv.example.com"][0]
        # MSYS2 traduce "/dev/null" a "nul" solo (sin ruta) al pasarlo como argumento a un programa
        # nativo de Windows (nuestro ssh de pega vía sys.executable): comportamiento correcto ahi, no
        # un bug - es justo lo que necesitaria un ssh.exe nativo de verdad para "sin fichero de config"
        self.assertEqual(prueba[prueba.index("-F") + 1], "nul" if os.name == "nt" else "/dev/null")
        self.assertIn("StrictHostKeyChecking=yes", prueba)
        ficha = out[out.index("servidores.json:") + len("servidores.json:"):].strip().splitlines()[0]
        self.assertEqual(json.loads(ficha), {"nombre": 'web "1" \\ x', "ssh": "infobar@srv.example.com", "puerto": 2222})

    def test_la_prueba_como_infobar_falla_y_no_se_registra(self):
        rc, out = self.corre("root@srv.example.com", FAKE_PRUEBA_MAL="1")
        self.assertEqual(rc, 1)
        self.assertIn("la prueba como infobar falló", out)
        self.assertEqual(self.servidores(), [])

    def test_sudo_sin_contrasena_usa_sudo_n_y_con_contrasena_exige_terminal(self):
        rc, out = self.corre("victor@srv.example.com", FAKE_PRIV="sudo_n")
        self.assertEqual(rc, 0, out)
        instalar = [c["args"][-1] for c in self.llamadas("ssh") if "instalar-en-servidor.sh" in c["args"][-1]]
        self.assertTrue(instalar[0].startswith("sudo -n bash "), instalar)
        os.remove(self.log)
        rc, out = self.corre("victor@srv.example.com", FAKE_PRIV="sudo_pide")
        self.assertEqual(rc, 1)
        self.assertIn("no se está ejecutando en un terminal", out)
        self.assertFalse(any("instalar-en-servidor.sh" in c["args"][-1] for c in self.llamadas("ssh")))
        rc, out = self.corre("victor@srv.example.com", FAKE_PRIV="sin_sudo")
        self.assertEqual(rc, 1)
        self.assertIn("no tiene sudo", out)

    def test_la_instalacion_fallida_lo_dice_y_no_registra(self):
        rc, out = self.corre("root@srv.example.com", FAKE_INSTALAR_RC="1")
        self.assertEqual(rc, 1)
        self.assertIn("La instalación no terminó bien (código 1)", out)
        self.assertEqual(self.servidores(), [])


class ErroresTest(AltaServidorSh):
    def test_los_fallos_de_conexion_se_explican_en_español(self):
        self.pon_personal()
        casos = {"solo_claves": ["solo acepta claves SSH", "grep -qxF '" + PERSONAL + "' ~/.ssh/authorized_keys"],
                 "contrasena_mal": ["PermitRootLogin"], "rechazada": ["rechazó la conexión en el puerto 22"], "tiempo": ["Tiempo agotado"],
                 "huella": ["ha CAMBIADO", "ssh-keygen -R srv.example.com"], "rara": ["No se pudo entrar como root@srv.example.com"]}
        for modo, esperado in casos.items():
            rc, out = self.corre("root@srv.example.com", FAKE_MODO=modo)
            self.assertEqual(rc, 1, modo)
            for e in esperado:
                self.assertIn(e, out, modo)
            if modo != "rara":
                self.assertNotIn("Permission denied", out, modo)                       # nada en bruto
            self.assertEqual(self.llamadas("scp"), [], modo)

    def test_contrasena_mal_con_un_usuario_normal(self):
        rc, out = self.corre("victor@srv.example.com", FAKE_MODO="contrasena_mal")
        self.assertIn("Contraseña incorrecta para victor", out)

    def test_solo_claves_sin_clave_personal_dice_como_crearla(self):
        rc, out = self.corre("root@srv.example.com", FAKE_MODO="solo_claves")
        self.assertIn("ssh-keygen -t ed25519", out)
        self.assertNotIn("grep -qxF", out)

    def como_linux(self):
        with open(os.path.join(self.bin, "uname"), "w") as f:
            f.write("#!/bin/sh\necho Linux\n")
        os.chmod(os.path.join(self.bin, "uname"), 0o755)

    def copia_suelta(self):
        solo = os.path.join(self.dir, "suelta", "infobar", "remoto")
        os.makedirs(solo)
        shutil.copy(self.script, solo)
        return os.path.join(solo, "alta-servidor.sh")

    def test_dentro_del_servidor_lo_dice_claramente(self):
        self.como_linux()
        for script, extra in ((self.script, {"SSH_CONNECTION": "203.0.113.5 50000 203.0.113.10 22"}), (self.copia_suelta(), {})):
            rc, out = self.corre("root@srv.example.com", script=script, **extra)
            self.assertEqual(rc, 1)
            self.assertIn("Este script se ejecuta en el Terminal de tu Mac, no dentro del servidor", out)
        self.assertEqual(self.llamadas(), [])

    def test_una_copia_suelta_en_el_mac_dice_que_le_falta_la_app(self):
        rc, out = self.corre("root@srv.example.com", script=self.copia_suelta())
        self.assertEqual(rc, 1)
        self.assertIn("no tiene la app CostGuard al lado", out)
        self.assertNotIn("dentro del servidor", out)
        self.assertEqual(self.llamadas(), [])

    def test_host_con_las_reglas_de_la_app(self):
        for destino in ("root@srv_x.example.com", "root@-srv.example.com", "1root@srv.example.com", "root@" + "a" * 254):
            rc, out = self.corre(destino)
            self.assertEqual(rc, 1, destino)
            self.assertIn("no es un usuario@host válido", out)
            self.assertNotIn("nombre", out)                                              # no culpa al nombre
        self.assertEqual(self.llamadas(), [])

    def test_si_la_maestra_muere_no_se_conecta_por_su_cuenta(self):
        rc, out = self.corre("root@srv.example.com", FAKE_SIN_MAESTRA="1")
        self.assertEqual(rc, 1)
        self.assertIn("Se perdió la conexión con el servidor", out)
        self.assertFalse(any("instalar-en-servidor.sh" in c["args"][-1] or "id -u" in c["args"][-1] for c in self.llamadas("ssh")))
        self.assertEqual(self.llamadas("scp"), [])

    def test_entradas_malas(self):
        for args in ((), ("root@srv.example.com", "0"), ("root@srv.example.com", "22", "extra"), ("-x", "root@srv.example.com"), ("root@host;id",), ("-i",)):
            rc, out = self.corre(*args)
            self.assertEqual(rc, 1, args)
        self.assertEqual(self.llamadas(), [])


class ClaveElegidaTest(AltaServidorSh):
    def test_i_entra_solo_con_esa_clave(self):
        self.pon_personal("trabajo.pem")
        ruta = os.path.join(self.home, ".ssh", "trabajo.pem")
        rc, out = self.corre("-i", ruta, "root@srv.example.com")
        self.assertEqual(rc, 0, out)
        m = [c["args"] for c in self.llamadas("ssh") if "-M" in c["args"]][0]
        self.assertEqual(m[m.index("-i") + 1], ruta)
        self.assertIn("IdentitiesOnly=yes", m)
        self.assertIn("ControlPersist=30m", m)
        # todas las órdenes de admin y la copia llevan la misma clave: si la maestra cayera, no entrarían con otras
        admin = [c["args"] for c in self.llamadas("ssh") if "-M" not in c["args"] and "-O" not in c["args"]
                 and c["args"][c["args"].index("--") + 1] == "root@srv.example.com"] + [c["args"] for c in self.llamadas("scp")]
        self.assertGreaterEqual(len(admin), 4)
        for a in admin:
            self.assertEqual(a[a.index("-i") + 1], ruta)
            self.assertIn("IdentitiesOnly=yes", a)

    def test_i_rechaza_la_de_infobar_una_pub_y_una_que_no_existe(self):
        self.pon_personal()
        for ruta, texto in ((os.path.join(self.home, ".ssh", "infobar_ed25519"), "solo lectura de infobar"),
                            (os.path.join(self.home, ".ssh", "id_ed25519.pub"), "PRIVADA"), (os.path.join(self.home, "no-existe"), "No encuentro la clave")):
            rc, out = self.corre("-i", ruta, "root@srv.example.com")
            self.assertEqual(rc, 1, ruta)
            self.assertIn(texto, out)
        self.assertEqual(self.llamadas(), [])


@unittest.skipIf(os.name == "nt", "con_terminal() necesita un pty de verdad (pty.fork/os.execve); ninguno de "
                  "los dos existe en Windows (ni pty ni fork/exec estan en os en esa plataforma) - probarlo "
                  "ahi necesitaria una dependencia nueva (pywinpty), fuera de alcance aqui")
class AutorizarClaveTest(AltaServidorSh):
    """La pregunta solo sale en un terminal: se ejecuta con un pty de verdad y se contesta."""

    def con_terminal(self, respuesta, **extra):
        import pty
        pid, fd = pty.fork()
        if pid == 0:                                              # hijo: solo exec
            try:
                os.execve("/bin/bash", ["bash", self.script, "root@srv.example.com"], self.entorno(**extra))
            finally:
                os._exit(127)
        salida, preguntado, fin = b"", False, time.time() + 60
        while time.time() < fin:
            listo, _, _ = select.select([fd], [], [], 1)
            if not listo:
                continue
            try:
                trozo = os.read(fd, 4096)
            except OSError:
                break
            if not trozo:
                break
            salida += trozo
            if not preguntado and "[s/N]".encode() in salida:
                os.write(fd, respuesta.encode() + b"\n")
                preguntado = True
        _, estado = os.waitpid(pid, 0)
        os.close(fd)
        return os.waitstatus_to_exitcode(estado), salida.decode("utf-8", "replace"), preguntado

    def test_con_contrasena_pregunta_y_autoriza_la_personal_de_forma_idempotente(self):
        self.pon_personal()
        rc, out, preguntado = self.con_terminal("s")
        self.assertEqual(rc, 0, out)
        self.assertTrue(preguntado)
        with open(self.autorizada) as f:
            orden = f.read()
        self.assertIn("umask 077", orden)
        self.assertIn(f"(grep -qxF '{PERSONAL}' ~/.ssh/authorized_keys 2>/dev/null || echo '{PERSONAL}' >> ~/.ssh/authorized_keys)", orden)
        self.assertIn("chmod 700 ~/.ssh", orden)
        self.assertIn("chmod 600 ~/.ssh/authorized_keys", orden)
        with open(os.path.join(self.home, ".ssh", "infobar_ed25519.pub")) as f:
            self.assertNotIn(f.read().split()[1], orden)                               # nunca la de infobar

    def test_si_dice_que_no_no_se_toca_nada(self):
        self.pon_personal()
        rc, out, preguntado = self.con_terminal("n")
        self.assertEqual(rc, 0, out)
        self.assertTrue(preguntado)
        self.assertFalse(os.path.exists(self.autorizada))

    def test_con_clave_o_ya_autorizada_no_pregunta(self):
        self.pon_personal()
        for extra in ({"FAKE_METODO": "publickey"}, {"FAKE_YA_AUTORIZADA": "1"}):
            rc, out, preguntado = self.con_terminal("s", **extra)
            self.assertEqual(rc, 0, out)
            self.assertFalse(preguntado, extra)
            self.assertFalse(os.path.exists(self.autorizada))

    def test_la_personal_que_coincide_con_la_de_infobar_no_se_autoriza(self):
        rc, _ = self.corre("root@srv.example.com", FAKE_METODO="publickey")          # crea la clave de infobar
        with open(os.path.join(self.home, ".ssh", "infobar_ed25519.pub")) as f:
            self.pon_personal(publica=f.read().strip())
        rc, out, preguntado = self.con_terminal("s")
        self.assertEqual(rc, 0, out)
        self.assertFalse(preguntado)
        self.assertFalse(os.path.exists(self.autorizada))


if __name__ == "__main__":
    unittest.main()
