"""Cliente de la API de Dokploy: SOLO consultas, a cinco rutas fijas, con la clave de un miembro del panel.

La clave de un miembro de Dokploy puede escribir (desplegar, parar, borrar copias) porque no hay clave de solo lectura sin licencia Enterprise. Por eso la garantía de que
CostGuard solo lee no está en la clave sino aquí: existe UNA sola llamada HTTP en este módulo (`_get`), su método es la constante «GET», su ruta tiene que estar en `RUTAS`
y no hay cuerpo. Un test recorre el código y falla si aparece cualquier otra cosa.

Reglas del cliente:
· Solo https con certificado válido, a `esquema://host:puerto` de `panel_url` (su ruta se ignora); el nombre se resuelve a una IP pública y se conecta a ESA IP; sin redirecciones
  (una 3xx es un error y no se repite la petición); sin variables de proxy.
· Sin clave guardada no se hace ninguna petición (tampoco la de salud). La clave nunca sale en un error: los errores son códigos de una lista cerrada, nunca `str(e)`.
· Plazo por petición y por pasada, tope de bytes, sin `Content-Encoding`, `application/json` obligatorio, JSON sin valores no finitos ni anidado hasta el desbordamiento.
· De cada fila solo pasan los campos de una lista blanca, con tipo y valores de un conjunto cerrado; lo demás se descarta y se cuenta con un motivo cerrado. Nada de `icon`,
  `env`, contraseñas ni mensajes de error de los despliegues. Si menos del 80 % de las filas de una lista valen, la lectura entera no vale («contrato»).
· Las horas del panel se corrigen con la cabecera `Date` de la propia respuesta, para que un reloj distinto no adelante ni atrase nada."""
import datetime
import email.utils
import http.client
import json
import math
import re
import socket
import ssl
import threading
import time
import unicodedata
import urllib.parse

from .servidores import url_valida
from .certificados import contexto_ssl
from .servidores_externo import ip_publica

RUTAS = ("/api/settings.health", "/api/settings.getDokployVersion", "/api/overview.services", "/api/overview.domains", "/api/overview.backups")
PLAZO_PETICION_S = 4.0
PLAZO_PASADA_S = 12.0
MAX_BYTES = 2 * 1024 * 1024
MAX_BYTES_SERVICIOS = 6 * 1024 * 1024          # `overview.services` trae iconos de unos 10 KB por servicio
MAX_SERVICIOS, MAX_DOMINIOS, MAX_COPIAS = 2000, 5000, 500
MAX_DOMINIOS_GUARDADOS = 200
MIN_VALIDAS = 0.8
VERSION_MINIMA, VERSION_PROBADA = (0, 30, 0), (0, 30, 8)
CLAVE_OK = re.compile(r"[\x21-\x7e]{1,1024}")
ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
SLUG = re.compile(r"[a-z0-9][a-z0-9_.-]{0,99}")
DNS = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
TIPOS_SERVICIO = frozenset({"application", "compose", "postgres", "mysql", "mariadb", "mongo", "redis", "libsql"})
ESTADOS_SERVICIO = frozenset({"idle", "running", "done", "error"})
ESTADOS_COPIA = frozenset({"running", "done", "error", "cancelled"})
TIPOS_COPIA = frozenset({"backup", "volumeBackup"})
ERRORES = frozenset({"sin_clave", "clave_invalida", "url_invalida", "red", "tls", "timeout", "redireccion", "clave_rechazada", "http_5xx", "http_otro", "contrato",
                     "demasiado_grande", "no_json", "version_antigua", "plazo"})
DESCARTES = frozenset({"panel_campo", "panel_estado", "panel_reloj", "panel_copia_sin_identidad", "panel_truncado", "panel_nombre"})
_BIDI = frozenset("‎‏‪‫‬‭‮⁦⁧⁨⁩")


class ErrorPanel(Exception):
    """Fallo de la lectura con un código cerrado (de ERRORES). Nunca lleva texto de la red ni de la clave."""

    def __init__(self, codigo):
        super().__init__(codigo)
        self.codigo = codigo


class _Conexion(http.client.HTTPSConnection):
    """HTTPS contra una IP ya comprobada, validando el certificado del nombre original."""

    def __init__(self, host, ip, puerto, plazo):
        super().__init__(host, puerto, timeout=plazo, context=contexto_ssl())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _conectar(host, ip, puerto, plazo):
    return _Conexion(host, ip, puerto, plazo)


def _no_finito(_texto):
    raise ValueError("no finito")


def _decimal(texto):
    v = float(texto)
    if not math.isfinite(v):
        raise ValueError("no finito")
    return v


def _get(c, ruta, clave, limite, fin, reloj=time.monotonic):
    """LA única llamada HTTP del módulo. Devuelve (JSON ya analizado, cabecera Date de la respuesta). Lanza ErrorPanel."""
    if ruta not in RUTAS:
        raise ErrorPanel("contrato")
    cabeceras = {"Accept": "application/json", "User-Agent": "infobar/1.0"}
    if ruta != RUTAS[0]:
        cabeceras["x-api-key"] = clave            # la ruta de salud es pública: no se le manda la clave
    vigilante = threading.Timer(max(0.1, fin - reloj()), _cortar, args=(c,))          # al llegar el plazo total se cierra el socket aunque el panel gotee
    vigilante.daemon = True
    vigilante.start()
    try:
        return _get_con_plazo(c, ruta, cabeceras, limite, fin, reloj)
    except (OSError, http.client.HTTPException, ValueError):
        if reloj() >= fin - 0.01:
            raise ErrorPanel("plazo")                  # el socket lo cerró el vigilante porque se acabó el plazo total
        raise
    finally:
        vigilante.cancel()


def _cortar(c):
    try:
        c.close()
    except Exception:
        pass


def _plazo_de_operacion(c, fin, reloj):
    if hasattr(c, "sock") and c.sock is not None:
        c.sock.settimeout(max(0.1, min(PLAZO_PETICION_S, fin - reloj())))


def _get_con_plazo(c, ruta, cabeceras, limite, fin, reloj):
    c.request("GET", ruta, headers=cabeceras)
    _plazo_de_operacion(c, fin, reloj)
    r = c.getresponse()
    if r.status == 401:
        raise ErrorPanel("clave_rechazada")
    if 300 <= r.status < 400:
        raise ErrorPanel("redireccion")
    if r.status >= 500:
        raise ErrorPanel("http_5xx")
    if r.status != 200:
        raise ErrorPanel("http_otro")
    if (r.getheader("Content-Encoding") or "identity").strip().lower() != "identity":
        raise ErrorPanel("no_json")
    if not (r.getheader("Content-Type") or "").lower().startswith("application/json"):
        raise ErrorPanel("no_json")                # un login HTML de un proxy delante del panel no es «clave rechazada»
    datos, total = bytearray(), 0
    leer_trozo = r.read1 if hasattr(r, "read1") else r.read       # una sola lectura de red por vuelta: el plazo se comprueba entre una y otra
    while True:
        if reloj() > fin:
            raise ErrorPanel("plazo")
        _plazo_de_operacion(c, fin, reloj)
        trozo = leer_trozo(65536)
        if not trozo:
            break
        total += len(trozo)
        if total > limite:
            raise ErrorPanel("demasiado_grande")
        datos += trozo
    try:
        return json.loads(datos.decode("utf-8"), parse_constant=_no_finito, parse_float=_decimal), r.getheader("Date")
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise ErrorPanel("no_json")


def _texto(v, n=80):
    """Texto de una sola línea y sin controles (ni marcas bidireccionales), de hasta n caracteres; None si no vale."""
    if not isinstance(v, str) or not v or len(v) > n:
        return None
    if any(unicodedata.category(ch).startswith("C") or ch in _BIDI for ch in v):
        return None
    return v


def _id(v):
    return v if isinstance(v, str) and ID.fullmatch(v) else None


def _epoch(v, desfase, ahora):
    """Segundos del Mac a partir de una hora ISO del panel (corregida con el reloj del panel); None si no vale o está en el futuro."""
    if not isinstance(v, str) or len(v) > 40:
        return None
    try:
        t = datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    ts = t.timestamp() + desfase
    return ts if 1.5e9 < ts <= ahora + 3600 else None


def _filas(crudo, maximo, normaliza, descartes):
    if not isinstance(crudo, list):
        raise ErrorPanel("contrato")
    if len(crudo) > maximo:
        descartes["panel_truncado"] = descartes.get("panel_truncado", 0) + len(crudo) - maximo
        crudo = crudo[:maximo]
    out = []
    for f in crudo:
        r = normaliza(f, descartes) if isinstance(f, dict) else None
        if r is None:
            descartes["panel_campo"] = descartes.get("panel_campo", 0) + 1
        else:
            out.append(r)
    if crudo and len(out) < MIN_VALIDAS * len(crudo):
        raise ErrorPanel("contrato")
    return out


def _servicio(desfase, ahora):
    def normaliza(f, descartes):
        sid, tipo = _id(f.get("id")), f.get("type")
        if sid is None or tipo not in TIPOS_SERVICIO:
            return None
        estado = f.get("status")
        if estado not in ESTADOS_SERVICIO:
            descartes["panel_estado"] = descartes.get("panel_estado", 0) + 1
            estado = None                                           # «no sé»: no se toma por error ni por bueno
        nombre = _texto(f.get("name"))
        if f.get("name") is not None and nombre is None:
            descartes["panel_nombre"] = descartes.get("panel_nombre", 0) + 1
        slug = f.get("appName") if isinstance(f.get("appName"), str) and SLUG.fullmatch(f.get("appName")) else None
        despliegue = _epoch(f.get("lastDeployAt"), desfase, ahora) if f.get("lastDeployAt") else None
        if f.get("lastDeployAt") and despliegue is None:
            descartes["panel_reloj"] = descartes.get("panel_reloj", 0) + 1
        return {"id": sid, "tipo": tipo, "estado": estado, "nombre": nombre, "slug": slug, "despliegue": despliegue, "servidor_panel": _id(f.get("serverId")),
                "proyecto_id": _id(f.get("projectId")), "proyecto": _texto(f.get("projectName")), "entorno": _texto(f.get("environmentName"))}
    return normaliza


def _dominio(f, descartes):
    host, dueno = f.get("host"), _id(f.get("serviceOwnerId"))
    if not (isinstance(host, str) and DNS.fullmatch(host.lower()) and len(host) <= 253) or dueno is None:
        return None
    puerto = f.get("port")
    ruta = f.get("path")
    return {"host": host.lower(), "dueno": dueno, "tipo_dueno": f.get("serviceOwnerType") if f.get("serviceOwnerType") in ("application", "compose") else None,
            "https": f.get("https") is True, "puerto": puerto if isinstance(puerto, int) and not isinstance(puerto, bool) and 0 < puerto < 65536 else None,
            "ruta": ruta if isinstance(ruta, str) and len(ruta) <= 100 and ruta.isascii() and ruta.isprintable() else None}


def _copia(desfase, ahora):
    def normaliza(f, descartes):
        tipo = f.get("kind")
        propio = f.get("serviceOwnerId") is None and f.get("serviceOwnerType") == "web-server"          # la copia de la base de datos del propio Dokploy
        dueno, destino = ("panel" if propio else _id(f.get("serviceOwnerId"))), _id(f.get("destinationId"))
        if tipo not in TIPOS_COPIA:
            return None
        if dueno is None or destino is None:
            descartes["panel_copia_sin_identidad"] = descartes.get("panel_copia_sin_identidad", 0) + 1
            return None
        estado = f.get("status")
        if estado not in ESTADOS_COPIA:
            descartes["panel_estado"] = descartes.get("panel_estado", 0) + 1
            estado = None
        ts = _epoch(f.get("createdAt"), desfase, ahora)
        if ts is None:
            descartes["panel_reloj"] = descartes.get("panel_reloj", 0) + 1
            return None
        bd, cop = f.get("databaseType"), f.get("backupType")
        return {"dueno": dueno, "tipo": tipo, "destino": destino, "bd": _id(bd) or "", "variante": _id(cop) or "", "estado": estado,         # un valor raro no rompe la identidad: queda vacío
                "ts": ts, "nombre": "panel-dokploy" if propio else _texto(f.get("serviceName")), "tipo_servicio": f.get("serviceType") if f.get("serviceType") in TIPOS_SERVICIO else None}
    return normaliza


def _desfase(fecha, ahora):
    """Segundos que hay que sumar a una hora del panel para ponerla en el reloj del Mac, según la cabecera `Date` de la misma respuesta (0 si falta o es absurda)."""
    if not fecha:
        return 0.0
    try:
        d = ahora - email.utils.parsedate_to_datetime(fecha).timestamp()
    except (TypeError, ValueError):
        return 0.0
    return d if abs(d) < 7 * 86400 else 0.0


def _version(v):
    if not isinstance(v, str):
        raise ErrorPanel("contrato")
    m = re.fullmatch(r"v?(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?:[-+][\w.]{0,30})?", v.strip())
    if not m:
        raise ErrorPanel("contrato")
    return tuple(int(x) for x in m.groups())


def leer(panel_url, clave, ahora=None, conectar=_conectar, reloj=time.monotonic):
    """Lee el panel y devuelve {"ok": True, "version", "version_no_probada", "servicios", "dominios", "copias", "copias_truncadas", "descartes"} o {"ok": False, "error": código}.
    Sin clave no se hace ninguna petición. Nunca lanza y nunca deja la clave en lo que devuelve."""
    try:
        return _leer(panel_url, clave, ahora if ahora is not None else time.time(), conectar, reloj)
    except ErrorPanel as e:
        return {"ok": False, "error": e.codigo if e.codigo in ERRORES else "red"}
    except ssl.SSLError:
        return {"ok": False, "error": "tls"}
    except (socket.timeout, TimeoutError):
        return {"ok": False, "error": "timeout"}
    except (OSError, http.client.HTTPException, ValueError):
        return {"ok": False, "error": "red"}


def _leer(panel_url, clave, ahora, conectar, reloj):
    if not clave:
        raise ErrorPanel("sin_clave")
    if not CLAVE_OK.fullmatch(clave):
        raise ErrorPanel("clave_invalida")
    if not isinstance(panel_url, str) or url_valida(panel_url) or not panel_url.startswith("https://"):
        raise ErrorPanel("url_invalida")
    u = urllib.parse.urlsplit(panel_url)
    puerto = u.port or 443
    fin = reloj() + PLAZO_PASADA_S
    ip = ip_publica(u.hostname, puerto)
    c = conectar(u.hostname, ip, puerto, PLAZO_PETICION_S)
    try:
        salud, _ = _get(c, RUTAS[0], clave, MAX_BYTES, fin, reloj)
        if not (isinstance(salud, dict) and salud.get("status") == "ok"):
            raise ErrorPanel("contrato")
        version_texto, _ = _get(c, RUTAS[1], clave, MAX_BYTES, fin, reloj)
        version = _version(version_texto)
        if version < VERSION_MINIMA:
            raise ErrorPanel("version_antigua")
        servicios, fecha_servicios = _get(c, RUTAS[2], clave, MAX_BYTES_SERVICIOS, fin, reloj)
        dominios, _ = _get(c, RUTAS[3], clave, MAX_BYTES, fin, reloj)
        copias, fecha_copias = _get(c, RUTAS[4], clave, MAX_BYTES, fin, reloj)
    finally:
        try:
            c.close()
        except Exception:
            pass
    descartes = {}
    s = _filas(servicios, MAX_SERVICIOS, _servicio(_desfase(fecha_servicios, ahora), ahora), descartes)         # cada lista se corrige con la cabecera Date de SU respuesta
    d = _filas(dominios, MAX_DOMINIOS, _dominio, descartes)
    k = _filas(copias, MAX_COPIAS, _copia(_desfase(fecha_copias, ahora), ahora), descartes)
    return {"ok": True, "version": ".".join(map(str, version)), "version_no_probada": version > VERSION_PROBADA, "servicios": s, "dominios": d[:MAX_DOMINIOS_GUARDADOS],
            "copias": k, "copias_truncadas": isinstance(copias, list) and len(copias) >= MAX_COPIAS, "descartes": descartes}
