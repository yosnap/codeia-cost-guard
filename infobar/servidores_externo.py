"""Lo que se mide DESDE el Mac, no desde el servidor: latencia de las webs y puertos que de verdad
se alcanzan desde Internet (dentro del servidor no se ve el cortafuegos del proveedor).

Antes de conectar se resuelve el nombre y se rechaza cualquier IP no publica; despues se conecta a
ESA IP (con SNI y verificacion del certificado por nombre). Asi un DNS que cambia entre la
comprobacion y la conexion (rebinding, nip.io) no lleva la peticion a 127.0.0.1 ni a la red local.
"""
import http.client, socket, ssl, subprocess, time, urllib.parse

from . import plataforma
from .certificados import contexto_ssl
from .servidores import ip_no_publica, url_valida

TIMEOUT_WEB = 5
TIMEOUT_PUERTO = 1.5
# puertos que se esperan abiertos al publico; el resto, si responde desde fuera, es un aviso
PUERTOS_PUBLICOS = {22, 80, 443}
# los de un servidor de correo (SMTP, SMTPS, envío, IMAPS, POP3S): se aceptan solo si hay uno en marcha
PUERTOS_CORREO = {25, 465, 587, 993, 995}


def ip_publica(host, puerto):
    """Primera IP publica a la que resuelve host, o error si alguna no lo es."""
    infos = socket.getaddrinfo(host, puerto, proto=socket.IPPROTO_TCP)
    ips = [i[4][0] for i in infos]
    if not ips:
        raise OSError("no resuelve")
    if any(ip_no_publica(ip) for ip in ips):
        raise OSError("resuelve a una IP local o privada")
    return ips[0]


class _HTTPSFijo(http.client.HTTPSConnection):
    """HTTPS contra una IP ya comprobada, validando el certificado del nombre original."""

    def __init__(self, host, ip, puerto, timeout):
        super().__init__(host, puerto, timeout=timeout, context=contexto_ssl())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        self.certificado = self.sock.getpeercert()       # justo tras el handshake verificado: la respuesta puede cerrar el socket antes de leerlo


class _HTTPFijo(http.client.HTTPConnection):
    def __init__(self, host, ip, puerto, timeout):
        super().__init__(host, puerto, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


MAX_DIAS_CERT = 36500


def dias_de_caducidad(cert, ahora=None):
    """Días enteros hasta `notAfter` del certificado que el servidor presentó (la HOJA, no la cadena), o None si falta, no se entiende o es
    imposible (±100 años). Solo se mira después de un handshake que ya verificó la cadena y el nombre; aquí nada se verifica."""
    try:
        final = ssl.cert_time_to_seconds(cert["notAfter"])
    except (TypeError, KeyError, ValueError, OverflowError):
        return None
    dias = int((final - (ahora if ahora is not None else time.time())) // 86400)
    return dias if -MAX_DIAS_CERT <= dias <= MAX_DIAS_CERT else None


def medir_web(url):
    """{"url", "ms", "codigo", "error", "cert_dias"}. HEAD sin seguir redirecciones (una 301 tambien es 'arriba'). `cert_dias` = días hasta que caduca el
    certificado que sirve la web (solo https y con el certificado ya verificado; un certificado caducado o no válido no completa el handshake: ahí hay `error`)."""
    res = {"url": url[:120], "ms": None, "codigo": None, "error": "", "cert_dias": None}
    motivo = url_valida(url)
    if motivo:
        res["error"] = motivo
        return res
    u = urllib.parse.urlsplit(url)
    puerto = u.port or (443 if u.scheme == "https" else 80)
    try:
        ip = ip_publica(u.hostname, puerto)
        clase = _HTTPSFijo if u.scheme == "https" else _HTTPFijo
        c = clase(u.hostname, ip, puerto, TIMEOUT_WEB)
        t0 = time.time()
        c.request("HEAD", (u.path or "/") + (f"?{u.query}" if u.query else ""), headers={"User-Agent": "infobar/1.0"})
        r = c.getresponse()
        res["ms"] = int((time.time() - t0) * 1000)
        res["codigo"] = r.status
        if u.scheme == "https":
            res["cert_dias"] = dias_de_caducidad(getattr(c, "certificado", None))
        c.close()
    except ssl.SSLError as e:
        res["error"] = f"certificado: {getattr(e, 'reason', '') or 'no valido'}"
    except (OSError, ValueError, http.client.HTTPException) as e:
        res["error"] = str(e)[:80] or type(e).__name__
    return res


def host_de_ssh(destino, puerto=22):
    """Host real (y puerto) de usuario@host o de un alias de ~/.ssh/config, via `ssh -G`."""
    try:
        r = subprocess.run(["ssh", "-G", "-p", str(puerto), "--", destino], capture_output=True,
                           text=True, timeout=5, **plataforma.sin_ventana_kwargs())
        cfg = dict(l.split(" ", 1) for l in r.stdout.splitlines() if " " in l)
        return cfg.get("hostname") or destino.split("@")[-1]
    except Exception:
        return destino.split("@")[-1]


MAX_PUERTOS = 64


def puertos_expuestos(host, puertos):
    """De los puertos que escuchan en el servidor, cuales responden desde aqui (Internet).
    En paralelo y con tope: la lista viene del servidor y no debe poder alargar el ciclo."""
    import concurrent.futures
    try:
        ip = ip_publica(host, 22)
    except (OSError, ValueError):
        return None

    def abierto(p):
        try:
            with socket.create_connection((ip, p), TIMEOUT_PUERTO):
                return p
        except OSError:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        return sorted(p for p in pool.map(abierto, list(puertos)[:MAX_PUERTOS]) if p)
