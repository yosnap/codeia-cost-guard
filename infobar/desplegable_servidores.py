"""Pestana Servidores del desplegable (como la captura de referencia): estado de UN servidor, el
elegido en el selector, con la revision de bastionado.

Lee solo la cache (logs/servidores.json): pintar nunca espera a la red.
"""
import time

from .config_y_estado import leer_ui
from .desplegable_comun import cabecera, esc, icono, pagina, pestanas, pie, tarjeta
from .servidores_ciclo import estado_visible, intervalos, peor_estado
from .servidores_estado import WEB_LENTA_MS
from .servidores_externo import PUERTOS_CORREO
from .servidores_panel import VENTANA_S as VENTANA_PANEL_S

MAX_WEBS = 14
MAX_CAIDOS = 6
MAX_MOTIVOS = 6
BOTONES = [("terminal", "Terminal"), ("caja", "Panel"), ("brujula", "Abrir")]


def _gb(b):
    g = (b or 0) / 1e9
    return f"{g:.1f}".rstrip("0").rstrip(".").replace(".", ",") if g < 10 else f"{g:.0f}"


def _hora(ts):
    return time.strftime("%H:%M", time.localtime(ts)) if ts else "—"


def _hace(ts, ahora):
    if not ts:
        return "—"
    m = int((ahora - ts) / 60)
    return "ahora" if m < 1 else (f"hace {m} min" if m < 60 else f"hace {m // 60} h")


def _corto(servicio):
    """tienda-production_tienda-app -> tienda-app (Swarm antepone el proyecto con '_')."""
    return servicio.split("_", 1)[1] if "_" in servicio.strip("_") else servicio


def _estado(t, clave):
    return (t.get(clave) or {}).get("estado") or "gris"


def _plegable(clave, titulo, cuerpo, abiertas, n=None, sev=None):
    """Sección que se despliega al pulsar su título. Cerrada solo enseña el título, cuántas cosas trae y un «!» de color si hay algo que atender; el marcador
    `.mk` es lo que el renderizador usa para saber dónde está el título en la imagen (y lo borra)."""
    abierto = clave in abiertas
    badge = f'<b class="exc {sev}">!</b>' if sev in ("rojo", "ambar") else ""
    cuenta = f'<span class="nota">{n}</span>' if n is not None else ""
    cab = (f'<div class="sec plg {"abierto" if abierto else "cerrado"}" data-plg="{clave}"><i class="mk"></i><span>{esc(titulo)}</span>'
           f'<span class="der">{cuenta}{badge}{icono("flecha")}</span></div>')
    return cab + (cuerpo if abierto else "")


def _clase_web(w):
    """verde (responde bien), ambar (responde con error de cliente o muy lenta) o rojo (error del servidor o sin respuesta)."""
    c, ms = w.get("codigo"), w.get("ms")
    if not isinstance(c, int) or isinstance(c, bool) or c >= 500 or w.get("error"):
        return "rojo"
    if _respuesta_buena(w):
        return "ambar" if isinstance(ms, (int, float)) and ms > WEB_LENTA_MS else "verde"
    return "ambar"


def _dominio(w):
    return w["url"].split("//")[-1].strip("/")


def _respuesta_buena(w):
    c = w.get("codigo")
    return isinstance(c, int) and (200 <= c < 400 or c in (401, 403))


def _seccion_webs(webs):
    """Todas las webs vigiladas, las que fallan primero: punto de color, dirección, código HTTP y latencia."""
    if not webs:
        return ""
    orden = {"rojo": 0, "ambar": 1, "verde": 2}
    filas = sorted(webs, key=lambda w: orden[_clase_web(w)])
    caidas = sum(1 for w in webs if _clase_web(w) == "rojo")
    html = []
    for w in filas[:MAX_WEBS]:
        c = _clase_web(w)
        dato = (str(w["codigo"]) if isinstance(w.get("codigo"), int) else "sin respuesta") + (f' · {w["ms"]} ms' if isinstance(w.get("ms"), (int, float)) and w.get("codigo") else "")
        html.append(f'<div class="f"><span><i class="p {c}"></i> {esc(_dominio(w))}</span><span class="mono">{esc(dato)}</span></div>')
    nota = f'{len(webs)}' + (f' · {caidas} con error' if caidas else " · todas responden")
    extra = f'<div class="nota" style="padding:4px 0">+{len(webs) - MAX_WEBS} más</div>' if len(webs) > MAX_WEBS else ""
    return f'<div class="sec"><span>Webs</span><span class="nota">{nota}</span></div><div class="lista">{"".join(html)}{extra}</div>'


def _motivos(t, res, d, webs, panel=None, ahora=None):
    """Por qué el servidor no está en verde, en palabras: [(color, texto)] de las tarjetas ámbar y rojas y del bastionado."""
    out = []

    def mal(clave):
        return (t.get(clave) or {}).get("estado") in ("ambar", "rojo")

    def color(clave):
        return (t.get(clave) or {}).get("estado")
    raros = (t.get("puertos") or {}).get("raros") or []
    if mal("puertos"):
        out.append((color("puertos"), "Puertas abiertas a Internet que no deberían: " + ", ".join(map(str, raros[:8])) + ("…" if len(raros) > 8 else "")))
    if res.get("total") and res.get("estado") in ("ambar", "rojo"):
        out.append((res["estado"], f'Protección: solo {res.get("hechos", 0)} de {res["total"]} ajustes de seguridad hechos'))
    caidas = sum(1 for w in webs if _clase_web(w) == "rojo")
    if caidas:
        out.append(("rojo", f"{caidas} web{'s' if caidas > 1 else ''} con error o sin responder"))
    elif mal("webs"):
        flojas = [w for w in webs if _clase_web(w) == "ambar"]
        nombres_w = ", ".join(_dominio(w) + (f' ({w["ms"]} ms)' if isinstance(w.get("ms"), (int, float)) and _respuesta_buena(w) else f' ({w.get("codigo")})')
                              for w in flojas[:3]) + ("…" if len(flojas) > 3 else "")
        out.append((color("webs"), (f"Va lenta o responde con error: {nombres_w}" if flojas else "Alguna web va lenta o responde con error")))
    nombres = {"cpu": "Procesador al límite", "memoria": "Memoria justa", "disco": "El disco se está llenando", "sesiones": "Sesiones SSH que no reconozco",
               "intentos": "Muchos intentos de acceso",
               "bloqueadas": "Muchas direcciones bloqueadas por fail2ban" if (d.get("fail2ban") or {}).get("instalado") else "fail2ban no está instalado (nadie bloquea a quien insiste)",
               "mantenimiento": "Paquetes del sistema por actualizar", "certificado": "Un certificado caduca pronto o no es válido"}
    for clave, texto in nombres.items():
        if mal(clave):
            out.append((color(clave), texto))
    p = panel if isinstance(panel, dict) else {}
    vigente = bool(p.get("ok_en")) and isinstance(ahora, (int, float)) and 0 <= ahora - p["ok_en"] <= VENTANA_PANEL_S          # una lectura vieja no se enseña como actual
    antiguas = ((p.get("copias_resumen") or {}).get("antiguas") or 0) if vigente else 0
    if antiguas:
        out.append(("ambar", f"{antiguas} copia{'s' if antiguas > 1 else ''} de seguridad sin hacer desde hace más de lo normal"))
    if vigente:
        fallidos = sum(1 for sv in (p.get("servicios") or []) if isinstance(sv, dict) and sv.get("estado") == "error" and sv.get("tipo") in ("application", "compose")
                       and isinstance(sv.get("despliegue"), (int, float)) and 600 < ahora - sv["despliegue"] <= 7 * 86400)
        if fallidos:
            out.append(("ambar", f"{fallidos} aplicación{'es' if fallidos > 1 else ''} con el último despliegue fallido"))
    n_caidos = len((t.get("servicios") or {}).get("caidos") or [])
    if mal("servicios") and n_caidos:
        out.append(("rojo", f"{n_caidos} servicio{'s' if n_caidos > 1 else ''} caído{'s' if n_caidos > 1 else ''}"))
    n_parados = len((t.get("servicios") or {}).get("parados") or [])
    if n_parados:
        out.append(("ambar", f"{n_parados} servicio{'s' if n_parados > 1 else ''} parado{'s' if n_parados > 1 else ''} a mano (0/0)"))
    out.sort(key=lambda x: 0 if x[0] == "rojo" else 1)
    return out


MAX_PUERTOS_GRANDES = 4             # en la cifra grande de la tarjeta caben 4 puertos; más se salen de la tarjeta


def _puertas(exp, tp):
    """(cifra, texto) de la tarjeta de puertos. Con un servidor de correo aceptado, sus puertos van al texto pequeño, no a la cifra."""
    if not exp:
        return "—", "abiertas a Internet"
    correo, raros = tp.get("correo"), tp.get("raros") or []
    grandes = [p for p in exp if not (correo and p in PUERTOS_CORREO)]
    de_correo = [p for p in exp if correo and p in PUERTOS_CORREO]
    cifra = ("·".join(map(str, grandes)) or "—") if len(grandes) <= MAX_PUERTOS_GRANDES else f"{len(exp)} abiertas"
    if raros:
        return cifra, f'¡{", ".join(map(str, raros))} expuesto!'
    if de_correo:
        return cifra, f'+ correo ({correo})'
    if len(grandes) <= MAX_PUERTOS_GRANDES:
        return cifra, "abiertas a Internet"
    return cifra, "·".join(map(str, exp[:6])) + ("…" if len(exp) > 6 else "")


def _cpu_texto(pct):
    return "Tranquilo" if pct < 30 else ("Con trabajo" if pct < 80 else "Al limite")


def cuerpo_servidor(nombre, e, servidor_cfg, ahora, abiertas=frozenset()):
    """Cuerpo de la pestana para un servidor (e = su entrada en la cache)."""
    d = (e or {}).get("datos") or {}
    t = (e or {}).get("tarjetas") or {}
    h = []
    if (e or {}).get("error"):
        h.append(f'<div class="aviso">Sin conexión: {esc(e["error"])} · último dato {esc(_hace(e.get("ok_en"), ahora))}</div>')
    if not d:
        if not e:
            h.append('<div class="aviso" style="background:var(--tarjeta);border-color:var(--borde)">'
                     'Todavía sin datos: la primera lectura llega en unos segundos.</div>')
        return "".join(h)

    webs = (e.get("webs") or [])
    w0 = webs[0] if webs else None
    hw = [tarjeta("web", "Webs", f'{w0["ms"]} ms' if w0 and w0.get("ms") is not None else "—",
                  (f"{len(webs)} vigilada{'s' if len(webs) != 1 else ''}" if webs else "sin webs")
                  + (" · certificado inválido" if (t.get("certificado") or {}).get("invalido") else
                     f' · certificado: {t["certificado"]["cert_dias"]} d' if isinstance((t.get("certificado") or {}).get("cert_dias"), int) else ""),
                  _estado(t, "webs"))]
    cpu = d.get("cpu") or {}
    hw.append(tarjeta("cpu", "Procesador", f'{cpu.get("pct", 0):.0f} %',
                      f'{_cpu_texto(cpu.get("pct", 0))} · {cpu.get("nucleos", "?")} núcleos', _estado(t, "cpu")))
    m = d.get("memoria") or {}
    hw.append(tarjeta("memoria", "Memoria", f'{_gb(m.get("usada"))} / {_gb(m.get("total"))} GB',
                      "Va holgada" if _estado(t, "memoria") == "verde" else "Va justa", _estado(t, "memoria")))
    k = d.get("disco") or {}
    hw.append(tarjeta("disco", "Disco", f'{_gb(k.get("usado"))} / {_gb(k.get("total"))} GB',
                      "Espacio de sobra" if _estado(t, "disco") == "verde" else "Se está llenando", _estado(t, "disco")))
    motivos = _motivos(t, e.get("bastionado_resumen") or {}, d, webs, e.get("panel"), ahora)
    if motivos:
        filas = "".join(f'<div class="f"><span style="white-space:normal"><i class="p {c}"></i> {esc(x)}</span></div>' for c, x in motivos[:MAX_MOTIVOS])
        extra = f'<div class="nota" style="padding:4px 0">+{len(motivos) - MAX_MOTIVOS} más</div>' if len(motivos) > MAX_MOTIVOS else ""
        h.append(_plegable("motivos", "Por qué no está en verde", f'<div class="lista">{filas}{extra}</div>', abiertas, len(motivos),
                           "rojo" if any(c == "rojo" for c, _ in motivos) else "ambar"))
    h.append(f'<div class="rejilla" style="margin-top:8px">{"".join(hw)}</div>')

    h.append(_seccion_webs(webs))
    # los servicios sanos no se enseñan (estorban); solo los caídos (rojo) y los parados a mano (gris), primero los caídos
    caidos = [s for s in (d.get("servicios") or []) if not s["ok"]] + [s for s in (d.get("servicios") or []) if s["ok"] and s.get("parado")]
    if caidos:
        n_rojos = sum(1 for s in caidos if not s["ok"])
        pild = "".join(f'<div class="pil"><i class="p {"rojo" if not s["ok"] else "gris"}"></i><span>{esc(_corto(s["nombre"]))}</span></div>'
                       for s in caidos[:MAX_CAIDOS])
        mas = f'<div class="nota" style="padding:4px 0">+{len(caidos) - MAX_CAIDOS} más</div>' if len(caidos) > MAX_CAIDOS else ""
        titulo = "Servicios caídos" if n_rojos == len(caidos) else ("Servicios parados" if not n_rojos else "Servicios caídos o parados")
        h.append(_plegable("caidos", titulo, f'<div class="pildoras">{pild}</div>{mas}', abiertas, len(caidos), "rojo" if n_rojos else "ambar"))

    ses = d.get("sesiones") or []
    it = d.get("intentos") or {}
    f2b = d.get("fail2ban") or {}
    exp = e.get("expuestos")
    res = e.get("bastionado_resumen") or {}
    seg = [tarjeta("persona", "Dentro ahora", "Nadie" if not ses else f"{len(ses)} sesión" + ("es" if len(ses) > 1 else ""),
                   "Sin sesiones SSH" if not ses else ", ".join(s["usuario"] for s in ses[:3]), _estado(t, "sesiones")),
           tarjeta("mano", "Intentos 1 h", f'{it.get("hora", 0):,}'.replace(",", ".") if it else "—",
                   (f'{it.get("total", 0):,} en 24 h'.replace(",", ".") + (" · lectura parcial" if it.get("parcial") else "")
                    + (" · origen sin verificar" if it.get("origen_fiable") is False else "")) if it
                   else "sin dato (el servidor no registra intentos)",
                   _estado(t, "intentos")),
           tarjeta("prohibido", "Bloqueadas", str(f2b.get("ahora", "—")) if f2b.get("instalado") else "—",
                   f'{f2b.get("total", 0)} en total (fail2ban)' if f2b.get("instalado") else "sin fail2ban",
                   _estado(t, "bloqueadas")),
           tarjeta("puerta", "Puertas abiertas", *_puertas(exp, t.get("puertos") or {}), _estado(t, "puertos")),
           tarjeta("candado", "Protección", f'{res.get("hechos", 0)} de {res.get("total", 0)}' if res.get("total") else "—",
                   "ajustes de seguridad" if res.get("estado") != "verde" else "todo en su sitio", res.get("estado") or "gris")]
    a = d.get("actualizaciones") or {}
    seg.append(tarjeta("llave", "Mantenimiento", "Al día" if not a.get("pendientes") else f'{a["pendientes"]} paquetes',
                       "reinicio pendiente" if a.get("reinicio") else
                       (f'{a["en_espera"]} en espera' if a.get("en_espera") else "Paquetes del sistema"),
                       _estado(t, "mantenimiento")))
    h.append(f'<div class="sec"><span>Seguridad</span></div><div class="rejilla">{"".join(seg)}</div>')

    falta = [c for c in (e.get("bastionado") or []) if c["ok"] is False]
    if falta:
        filas = "".join(f'<div class="f falta"><div><div class="t">{esc(c["texto"])}</div>'
                        f'<div class="a">{esc(c["arreglo"][:240])}</div></div></div>' for c in falta[:3])
        h.append(_plegable("falta", "Qué falta para protegerlo", f'<div class="lista">{filas}</div>', abiertas, len(falta),
                           res.get("estado") if res.get("estado") in ("rojo", "ambar") else None))

    top = it.get("top") or []
    if top and it.get("total"):
        filas = "".join(f'<div class="f mono"><span>{esc(ip)}</span><span>{int(n)}</span></div>' for ip, n in top)
        h.append(_plegable("intentos", "Quién más intenta entrar (24 h)", f'<div class="lista">{filas}</div>', abiertas, len(top)))
    return "".join(h)


def documento(nombre, cache, servidores_cfg, oscuro, ahora=None, abiertas=None):
    """HTML de la pestana Servidores con `nombre` seleccionado. `abiertas`: las secciones desplegadas (por defecto, las que guardó el usuario; ninguna al empezar)."""
    ahora = ahora or time.time()
    if abiertas is None:
        a = leer_ui().get("abiertas")
        abiertas = frozenset(x for x in a if isinstance(x, str)) if isinstance(a, list) else frozenset()
    entradas = (cache or {}).get("servidores") or {}
    normal, _ = intervalos()
    global_ = peor_estado(cache, ahora) if entradas else "gris"
    n_mal = sum(1 for e in entradas.values() if estado_visible(e, ahora, normal) in ("ambar", "rojo"))
    detalle = None if not entradas else (None if global_ == "verde" else f"{n_mal} con avisos de {len(entradas)}")
    h = [cabecera("Mis servidores", global_, detalle), pestanas("servidores")]
    if not servidores_cfg:
        h.append('<div class="aviso" style="background:var(--tarjeta);border-color:var(--borde);margin-top:12px">'
                 'Aún no hay servidores. Da de alta uno con <b>infobar/remoto/alta-servidor.sh</b>.</div>')
        h.append(pie([], "—"))
        return pagina("".join(h), oscuro)
    e = entradas.get(nombre)
    est = estado_visible(e, ahora, normal)
    cfg_s = next((s for s in servidores_cfg if s["nombre"] == nombre), {})
    host = (e or {}).get("host") or cfg_s.get("ssh", "").split("@")[-1]
    h.append(f'<div class="selector"><i class="p {est}"></i><span>Servidor ·</span><b>{esc(nombre)}</b>'
             f'<span class="flecha">{icono("flecha")}</span><span class="ip">{esc(host)}</span></div>')
    h.append(cuerpo_servidor(nombre, e, next((s for s in servidores_cfg if s["nombre"] == nombre), {}), ahora, abiertas))
    h.append(pie(BOTONES, f'Medido a las {_hora((e or {}).get("ok_en"))}'))
    return pagina("".join(h), oscuro)
