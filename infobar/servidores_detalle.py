"""Detalle de UN servidor para la pestaña «Servidores» del panel: webs, contenedores y servicios, y línea de tiempo. Solo lectura de `servidores.sqlite`.

· El servidor se valida contra la CONFIGURACIÓN (un id que no está en ella da siempre el mismo 404, sin decir si existió); uno configurado sin lecturas da «sin datos».
· Todo va en UNA transacción de lectura: webs, recursos y eventos son coherentes entre sí aunque el sondeador escriba a la vez.
· Nada de lo que sale es «verde» por defecto: cada fila lleva su última lectura (`ultima`) y la respuesta lleva la ventana de frescura, para que quien pinta pueda decir
  «sin lectura desde hace X» en vez de enseñar un estado viejo. Las webs se parten de la lista configurada: la que aún no tiene sondeo sale como `sondeada: false`.
· No sale ninguna IP. Los nombres de contenedor y las URL son los que el dueño ya ve en Ajustes y en el desplegable. Los errores no citan texto de la base."""
import re
import sqlite3
import time

import urllib.parse

from . import servidores_ciclo, servidores_copias, servidores_historico as sh, servidores_panel, servidores_seguimiento as sg
from .config_y_estado import cfg, log
from .servidores import servidores_validos
from .servidores_registro import webs_vigentes

ID = re.compile(r"[0-9a-f]{32}")
MAX_EVENTOS = 150
MAX_RECURSOS = 500
DIAS_LINEA = 14
VENTANA_MIN_S = 900
SIN_DATOS = {"disponible": False, "motivo": "Aún no hay lecturas de este servidor: llegan con el primer ciclo."}
ACTUALIZANDO = {"disponible": False, "motivo": "El historial se está actualizando: vuelve a abrir esta pestaña en un minuto."}


def _texto(v, n=130):
    return v[:n] if isinstance(v, str) else None


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _webs(con, sid, vigentes, ahora):
    por_url = {w["url"]: w for w in sg.lista(con, sid, ahora)}
    out = []
    for url in sorted(vigentes):
        w = por_url.get(url)
        if w is None:
            out.append({"url": url, "sondeada": False})
            continue
        out.append({"sondeada": True, **{k: w.get(k) for k in ("url", "estado", "codigo", "ms", "cert_dias", "desde", "ultima",
                                                              "disp_24h", "disp_7d", "disp_30d", "n_24h", "n_7d", "n_30d", "mal_24h", "mal_7d", "mal_30d")}})
    return out


def _recursos(con, sid):
    filas = con.execute("SELECT tipo, etiqueta, estado, salud, reinicios, activo, visto_n, primera, ultima FROM recursos WHERE servidor_id=? "
                        "ORDER BY activo DESC, ultima DESC LIMIT ?", (sid, MAX_RECURSOS)).fetchall()
    return [{"tipo": f["tipo"], "nombre": _texto(f["etiqueta"]), "estado": f["estado"], "salud": f["salud"], "reinicios": _num(f["reinicios"]),
             "activo": bool(f["activo"]), "visto_n": f["visto_n"], "primera": f["primera"], "ultima": f["ultima"]} for f in filas]


def _eventos(con, sid, desde):
    de_recursos = con.execute(
        "SELECT e.ts, e.tipo, e.evento, e.severidad, r.etiqueta FROM eventos e LEFT JOIN recursos r ON r.servidor_id = e.servidor_id AND r.tipo = e.tipo AND r.clave = e.clave "
        "WHERE e.servidor_id=? AND e.ts>=? ORDER BY e.ts DESC LIMIT ?", (sid, desde, MAX_EVENTOS)).fetchall()
    de_webs = con.execute(
        "SELECT e.ts, e.evento, e.codigo, w.etiqueta FROM eventos_web e LEFT JOIN webs w ON w.servidor_id = e.servidor_id AND w.clave = e.clave "
        "WHERE e.servidor_id=? AND e.ts>=? ORDER BY e.ts DESC LIMIT ?", (sid, desde, MAX_EVENTOS)).fetchall()
    todos = ([{"ts": f["ts"], "origen": "recurso", "tipo": f["tipo"], "evento": f["evento"], "severidad": f["severidad"], "nombre": _texto(f["etiqueta"])} for f in de_recursos]
             + [{"ts": f["ts"], "origen": "web", "evento": f["evento"], "codigo": f["codigo"], "nombre": _texto(f["etiqueta"], 300)} for f in de_webs])
    todos.sort(key=lambda e: -e["ts"])
    truncado = len(de_recursos) >= MAX_EVENTOS or len(de_webs) >= MAX_EVENTOS or len(todos) > MAX_EVENTOS
    return todos[:MAX_EVENTOS], truncado


def _panel(con, sid, s, webs_vigiladas, ahora):
    """Lo que dice el panel (Dokploy) de este servidor: árbol de servicios con sus dominios, y las copias con su evaluación. None si no hay panel configurado o aún no se ha leído.
    Un dominio solo se marca como «vigilado» si el dueño ya vigila una web de ese nombre: el panel nunca añade webs."""
    entrada = (servidores_ciclo.leer_cache().get("servidores") or {}).get(s["nombre"])
    p = entrada.get("panel") if isinstance(entrada, dict) and isinstance(entrada.get("panel"), dict) else None
    if p is None or not servidores_panel.cuenta(s) or p.get("cuenta") != servidores_panel.cuenta(s):
        return None                                                  # sin panel, o lo guardado es de otro panel
    hosts = {urllib.parse.urlsplit(u).hostname for u in webs_vigiladas}
    por_dueno = {}
    for d in p.get("dominios") or []:
        if isinstance(d, dict) and isinstance(d.get("host"), str):
            por_dueno.setdefault(d.get("dueno"), []).append({"host": d["host"][:253], "vigilado": d["host"] in hosts, "https": d.get("https") is True})
    servicios = [{k: sv.get(k) for k in ("id", "tipo", "estado", "nombre", "slug", "despliegue", "proyecto", "entorno")} | {"dominios": por_dueno.get(sv.get("id"), [])[:5]}
                 for sv in (p.get("servicios") or []) if isinstance(sv, dict)]
    copias = [{k: c[k] for k in ("tipo", "etiqueta", "ultima_ok", "previa_ok", "intervalo_s", "ultimo_estado", "ultimo_ts", "evaluacion")} for c in servidores_copias.lista(con, sid, ahora)]
    return {"ok_en": p.get("ok_en"), "intento_en": p.get("intento_en"), "error": p.get("error") or "", "version": p.get("version"), "version_no_probada": bool(p.get("version_no_probada")),
            "truncadas": bool(p.get("truncadas")), "ventana_s": servidores_panel.VENTANA_S, "n_servicios": p.get("n_servicios"), "servicios": servicios, "copias": copias}


def _vivo(e, ahora, normal):
    """La última lectura de la caché del ciclo (la misma que pinta el desplegable), reducida a números y estados para las
    tarjetas del panel. Sin IPs ni usuarios: de las sesiones y de los intentos solo salen las cuentas."""
    if not isinstance(e, dict):
        return None
    d, t = e.get("datos") or {}, e.get("tarjetas") or {}
    cpu, mem, disco = d.get("cpu") or {}, d.get("memoria") or {}, d.get("disco") or {}
    it, f2b, act = d.get("intentos") or {}, d.get("fail2ban") or {}, d.get("actualizaciones") or {}
    res = e.get("bastionado_resumen") or {}
    return {"estado": servidores_ciclo.estado_visible(e, ahora, normal), "ok_en": e.get("ok_en"), "error": _texto(e.get("error") or "", 60),
            "cpu": {"pct": _num(cpu.get("pct")), "nucleos": _num(cpu.get("nucleos"))},
            "memoria": {"usada": _num(mem.get("usada")), "total": _num(mem.get("total"))},
            "disco": {"usado": _num(disco.get("usado")), "total": _num(disco.get("total"))},
            "tarjetas": {k: v.get("estado") for k, v in t.items() if isinstance(v, dict) and isinstance(v.get("estado"), str)},
            "sesiones": len(d.get("sesiones") or []),
            "intentos": {"hora": _num(it.get("hora")), "total": _num(it.get("total"))} if it else None,
            "fail2ban": {"instalado": bool(f2b.get("instalado")), "ahora": _num(f2b.get("ahora")), "total": _num(f2b.get("total"))},
            "expuestos": [p for p in (e.get("expuestos") or []) if isinstance(p, int)][:30],
            "raros": [p for p in ((t.get("puertos") or {}).get("raros") or []) if isinstance(p, int)][:30],
            "bastionado": {"hechos": _num(res.get("hechos")), "total": _num(res.get("total")), "estado": res.get("estado"),
                           "falta": [{"texto": _texto(c.get("texto")), "arreglo": _texto(c.get("arreglo"), 400)}
                                     for c in (e.get("bastionado") or []) if isinstance(c, dict) and c.get("ok") is False][:20]},
            "actualizaciones": {"pendientes": _num(act.get("pendientes")), "reinicio": bool(act.get("reinicio")), "en_espera": _num(act.get("en_espera"))},
            "correo": _texto((t.get("puertos") or {}).get("correo")),
            "servicios": [{"nombre": _texto(x.get("nombre")), "ok": bool(x.get("ok")), "parado": bool(x.get("parado"))}
                          for x in (d.get("servicios") or []) if isinstance(x, dict)][:60]}


def atiende(q, ahora=None, conf=None):
    """GET /servidores/detalle?servidor=<id> → (código, dict). `q` = {parámetro: [valores]} ya parseado."""
    if set(q) != {"servidor"} or len(q["servidor"]) != 1 or not ID.fullmatch(q["servidor"][0]):
        return 400, {"error": "parámetros no permitidos"}
    sid = q["servidor"][0]
    ahora = int(ahora if ahora is not None else time.time())
    conf = conf if conf is not None else cfg()
    s = next((x for x in servidores_validos(conf.get("servidores")) if x.get("id") == sid), None)
    if s is None:
        return 404, {"error": "servidor desconocido"}
    try:
        con = sh.abrir(solo_lectura=True)
    except sh.SinDatos:
        return 200, dict(SIN_DATOS)
    except Exception as e:
        log(f"servidores/detalle: {type(e).__name__}")
        return 503, {"error": "la base de servidores no se puede abrir"}
    try:
        if con.execute("PRAGMA user_version").fetchone()[0] < 5:
            return 200, dict(ACTUALIZANDO)
        con.execute("BEGIN")
        fila = con.execute("SELECT creado_en, ok_en, intento_en FROM servidores WHERE id=?", (sid,)).fetchone()
        if fila is None:
            return 200, dict(SIN_DATOS)
        ult = con.execute("SELECT estado FROM muestras WHERE servidor_id=? ORDER BY ts DESC LIMIT 1", (sid,)).fetchone()
        desde = ahora - DIAS_LINEA * 86400
        horas = con.execute("SELECT COUNT(*) FROM horas_servidor WHERE servidor_id=? AND hora>=?", (sid, desde - desde % 3600)).fetchone()[0]
        total_horas = min(DIAS_LINEA * 24, max(1, (ahora - fila["creado_en"]) // 3600 + 1))
        eventos, truncado = _eventos(con, sid, desde)
        recursos = _recursos(con, sid)
        normal, _ = servidores_ciclo.intervalos(conf)
        salida = {"disponible": True, "generado_en": ahora, "ventana_s": max(VENTANA_MIN_S, 3 * normal + 60),
                  "servidor": {"id": sid, "nombre": s["nombre"], "estado": ult["estado"] if ult else None, "ok_en": fila["ok_en"] or None,
                               "intento_en": fila["intento_en"] or None, "vigilado_desde": fila["creado_en"]},
                  "webs": _webs(con, sid, webs_vigentes(conf).get(sid, set()), ahora), "recursos": recursos,
                  "eventos": eventos, "truncado": truncado, "recursos_truncado": len(recursos) >= MAX_RECURSOS,
                  "panel": _panel(con, sid, s, webs_vigentes(conf).get(sid, set()), ahora), "horas": {"con_datos": horas, "total": total_horas},
                  "vivo": _vivo((servidores_ciclo.leer_cache().get("servidores") or {}).get(s["nombre"]), ahora, normal)}
        con.execute("COMMIT")
        return 200, salida
    except sqlite3.DatabaseError as e:                     # base dañada, ocupada, sin tablas: nunca se cita el texto del error
        log(f"servidores/detalle: {type(e).__name__}")
        return 503, {"error": "la base de servidores no se puede leer ahora"}
    finally:
        con.close()
