"""Aisla los tests de la maquina: HOME vacio (sin ~/.claude/settings.json ni logs reales) y todas
las rutas de datos (config.json, state.json, logs) en una carpeta temporal. Importar ANTES que
costbar: HOME se fija antes de cargarlo porque los lectores lo leen al importarse."""
import os
import shutil
import sys
import tempfile

HOME_FALSO = tempfile.mkdtemp(prefix="infobar-tests-")
os.environ["HOME"] = HOME_FALSO
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infobar import rutas  # noqa: E402

# en Windows, os.path.expanduser("~") ignora HOME y usa USERPROFILE: sin esto, lectores_logs
# calcularia FUENTES sobre el ~/.claude y ~/.codex reales (miles de sesiones) al importarse.
rutas.HOME = HOME_FALSO
rutas.CONFIG = os.path.join(HOME_FALSO, "config.json")
rutas.CONFIG_SERVIDORES = os.path.join(HOME_FALSO, "servidores.json")
rutas.STATE = os.path.join(HOME_FALSO, "state.json")
rutas.LOG = os.path.join(HOME_FALSO, "logs", "costbar.log")
rutas.NAN_USO_CACHE = os.path.join(HOME_FALSO, "logs", "nan_uso.json")
rutas.UI_ESTADO = os.path.join(HOME_FALSO, "logs", "ui_estado.json")
rutas.SERVIDORES = os.path.join(HOME_FALSO, "logs", "servidores.json")
rutas.DATOS = os.path.join(HOME_FALSO, "datos")
rutas.ALMACEN = os.path.join(rutas.DATOS, "consumo.sqlite")
rutas.COPIAS = os.path.join(rutas.DATOS, "copias")

# ni el Llavero real ni la API de NaN: sin clave, la cuota cae a los logs (sin red)
os.environ.pop("NAN_API_KEY", None)
from infobar import claves  # noqa: E402

if not hasattr(claves, "leer_clave_real"):          # una sola vez, aunque el módulo se importe por dos nombres
    claves.leer_clave_real = claves.leer_clave      # para las pruebas del propio almacén
claves.leer_clave = lambda cuenta: ""


BASH = shutil.which("bash") or "bash"
"""La ruta completa al bash.exe de verdad (Git/MSYS2), resuelta por orden de PATH.

En Windows, `subprocess.run(["bash", ...])` con el nombre pelado NO busca por PATH: CreateProcess
mira antes el directorio de System32, y ahi vive el *launcher* de WSL (otro `bash.exe`, el de
Windows) que entiende `/mnt/c/...`, no `/c/...` de MSYS2 - con el nombre pelado, cualquier ruta
"de verdad" sale como "No such file or directory" aunque exista. Pasando la ruta ya resuelta de
`shutil.which` (que si respeta el orden de PATH) se evita el launcher de WSL."""


def ruta_bash(ruta):
    """Una ruta de os.path.join, lista para pasarsela a bash/sh (como argv o embebida en un PATH=).

    En Windows hacen falta DOS conversiones que una simple barra no resuelve:
    - El runtime MSYS2 de Git Bash reparsea el argv recibido de un proceso no-MSYS con reglas de
      escape estilo POSIX: cada '\\X' se convierte en 'X' (la barra desaparece), asi que una ruta
      con '\\' le llega troceada.
    - 'C:\\...' puesta dentro de un PATH=... separado por ':' se corta en la propia 'C:' (la letra
      de unidad choca con el separador de PATH).
    La forma POSIX de MSYS2 ('/c/Users/...') no tiene ninguno de los dos problemas: sin barras
    invertidas y sin ':' de por medio. En POSIX la ruta ya viene asi; no hace falta tocarla."""
    if os.name != "nt":
        return ruta
    unidad, resto = os.path.splitdrive(ruta)
    resto = resto.replace("\\", "/")
    return f"/{unidad[0].lower()}{resto}" if unidad else resto


def cookie_de(srv):
    """Cookie de sesion valida para un servidor de pruebas (canjea un ticket como haria la barra)."""
    import http.client
    from infobar import servidor_local
    puerto = srv.server_address[1]
    t = servidor_local.nuevo_ticket(srv)
    c = http.client.HTTPConnection("127.0.0.1", puerto, timeout=30)
    c.request("GET", f"/entrar?t={t}", headers={"Host": f"127.0.0.1:{puerto}"})
    r = c.getresponse()
    r.read()
    return r.getheader("Set-Cookie").split(";")[0]
