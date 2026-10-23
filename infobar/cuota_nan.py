"""Consumo real de la cuenta NaN (GET /v1/usage), con cache en disco."""
import json, os, time, urllib.parse, urllib.request

from . import claves, rutas
from .certificados import contexto_ssl
from .config_y_estado import cfg, log


# NaN publica el consumo real (server-side) por fecha y modelo: para su cuota es la fuente que manda,
# porque los logs locales no ven lo que corrio en la nube ni en otra maquina.
NAN_USO_URL = "https://api.nan.builders/v1/usage"
NAN_USO_TTL = 300           # segundos: /usage permite 30 rpm, con refrescar cada 5 min sobra


def invalidar():
    """Al pulsar Refrescar: la proxima lectura va a la API aunque la cache tenga menos de 5 min."""
    try:
        os.remove(rutas.NAN_USO_CACHE)
    except OSError:
        pass


_AVISADO = []              # el aviso de nan_api_key en config.json, una vez por proceso


def clave_nan():
    """API key de NaN, por orden: Llavero de macOS (cuenta "nan") -> entorno NAN_API_KEY ->
    config.json (nan_api_key) -> ~/.config/nan/api_key.

    Lo recomendado es el Llavero (`python3 costbar.py --clave nan`). Las demas fuentes se mantienen
    para no romper instalaciones que ya las usan; si la clave esta en config.json se avisa en el log.
    """
    v = claves.leer_clave("nan")
    if v:
        return v
    if cfg().get("nan_api_key") and not _AVISADO:
        _AVISADO.append(1)
        log("aviso: nan_api_key esta en config.json; muevela al Llavero con: python3 costbar.py --clave nan")
    for v in (os.environ.get("NAN_API_KEY") or "", cfg().get("nan_api_key") or ""):
        v = str(v).strip()
        if v:
            return v
    for ruta in (f"{rutas.HOME}/.config/nan/api_key", f"{rutas.HOME}/.nan/api_key"):
        try:
            v = open(ruta, encoding="utf-8").read().strip()
            if v:
                return v
        except Exception:
            pass
    return ""


def _por_modelo(d):
    """{modelo: tokens} de la respuesta de /usage. Una respuesta sin totals.by_model (un error con
    200, un cambio de formato) NO es "cero consumo": se lanza para no cachearla 5 min como buena."""
    totales = d.get("totals") if isinstance(d, dict) else None
    filas = totales.get("by_model") if isinstance(totales, dict) else None
    if not isinstance(filas, list):
        raise ValueError("respuesta de /usage sin totals.by_model")
    return {str(m.get("model") or "").lower(): int(m.get("total_tokens") or 0)
            for m in filas if isinstance(m, dict)}


def uso_nan(inicio_mes):
    """Consumo real de NaN en el periodo de facturacion, {modelo: total_tokens} o None.

    GET /v1/usage devuelve el contador de NaN: es lo que consume de verdad tu cuota (incluye web,
    nube y otras maquinas). Se cachea en disco para no pasar de 30 rpm (el icono y el panel
    refrescan cada minuto); si la red falla, se usa el ultimo dato bueno y, si no hay, None para
    que el panel caiga a los logs locales.
    """
    ahora = time.time()
    try:
        with open(rutas.NAN_USO_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    # la cache va antes que la clave: leer el Llavero lanza un proceso y no hace falta cada minuto
    if cache.get("inicio_mes") == inicio_mes and (ahora - (cache.get("ts") or 0)) < NAN_USO_TTL:
        return cache.get("por_modelo")          # solo existe si una clave funciono hace < 5 min
    key = clave_nan()
    if not key:
        return None
    try:
        req = urllib.request.Request(
            f"{NAN_USO_URL}?start_date={inicio_mes}",
            headers={"Authorization": f"Bearer {key}", "User-Agent": "CostBar/1.0",
                     "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=6, context=contexto_ssl()) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        por_modelo = _por_modelo(d)
        try:
            os.makedirs(os.path.dirname(rutas.NAN_USO_CACHE), exist_ok=True)
            tmp = f"{rutas.NAN_USO_CACHE}.{os.getpid()}.tmp"     # atomico: el panel puede leerla a la vez
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"ts": ahora, "inicio_mes": inicio_mes, "por_modelo": por_modelo}, f)
            os.replace(tmp, rutas.NAN_USO_CACHE)
        except Exception:
            pass
        return por_modelo
    except Exception as e:
        log(f"nan /usage: {type(e).__name__}: {e}")
        return cache.get("por_modelo") if cache.get("inicio_mes") == inicio_mes else None
