#!/usr/bin/env python3
"""CostBar - consumo de tokens de la flota de agentes, en la barra de menus del Mac.

Lee los logs de Claude Code / Codex / OpenCode y muestra, en primer plano, **tokens**: entrada,
salida, cache leida y cache escrita, mas los turnos, la **ventana de 5 horas** (la que se agota en
las suscripciones) y la **semana**. Los dolares quedan solo como referencia (equivalente API),
porque con suscripcion no se paga por token.

Desduplica los turnos copiados por los forks de Claude Code (el sesgo que inflaba ~15x el panel de
Orca). Cuenta los tokens de TODOS los modelos, tengan tarifa conocida o no.

Uso:
    ./venv/bin/python costbar.py --print      # imprime el informe y sale (sin GUI)
    ./venv/bin/python costbar.py              # icono en la barra de menus
"""
import collections, glob, hashlib, json, os, re, sqlite3, subprocess, sys, time, urllib.parse

AQUI = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
STATE = f"{AQUI}/state.json"
CONFIG = f"{AQUI}/config.json"
LOG = f"{AQUI}/logs/costbar.log"
FUENTES = [f"{HOME}/.claude/projects", f"{HOME}/.codex/sessions", f"{HOME}/.local/share/opencode"]
DIAS = 31
VENTANA_H = 5               # ventana de 5 horas de las suscripciones
VERSION_ESTADO = 9          # subir cuando cambie el formato del cache: obliga a reescanear

# tarifas USD/1M: (entrada, salida, cache_leida, escritura_cache) — solo para el equivalente API
TARIFAS = {
    "claude-opus-5": (5, 25, .50, 6.25), "claude-sonnet-5": (3, 15, .30, 3.75),
    "claude-fable-5": (10, 50, 1, 12.50), "claude-haiku": (1, 5, .10, 1.25),
    "claude-opus-4": (15, 75, 1.5, 18.75), "claude-sonnet-4": (3, 15, .30, 3.75),
    "gpt-5.6-astra": (10, 50, 1, 12.50), "gpt-5.6-sol": (5, 30, .50, 6.25),
    "gpt-5.6-terra": (2.5, 15, .25, 3.125), "gpt-5.6-luna": (1, 6, .10, 1.25),
    "gpt-5.6": (5, 30, .50, 6.25),
}
CLIFF = 272_000


def corto(m):
    """claude-sonnet-5 -> sonnet-5 · gpt-5.6-terra -> terra · claude-haiku-4-5-20251001 -> haiku-4-5"""
    m = re.sub(r"-\d{8}$", "", (m or "?"))
    m = m.split("/")[-1]                      # anthropic/claude-sonnet-5 -> claude-sonnet-5
    return m.replace("claude-", "").replace("gpt-5.6-", "")


PROVEEDORES_DEFECTO = [
    {"nombre": "Claude", "modelos": ["claude"]},
    {"nombre": "OpenAI", "modelos": ["gpt", "o3", "o4", "codex"]},
]


ALIAS_DEFECTO = {
    "zai-coding-plan": "z.ai (coding plan)", "zai": "z.ai (coding plan)", "z-ai": "z.ai (coding plan)",
    "opencode-go": "OpenCode Go", "opencode": "OpenCode Go",
    "nan.builders": "NaN", "nan": "NaN", "openrouter": "OpenRouter",
    "anthropic": "Claude (Max)", "openai": "OpenAI (Codex)",
    "codex": "OpenAI (Codex)", "google": "Google", "gemini": "Google",
}


def alias_de(fuente):
    """zai-coding-plan -> z.ai (coding plan). Los nombres mas largos primero, para que
    'opencode-go' no lo capture 'opencode'."""
    if not fuente:
        return None
    al = dict(ALIAS_DEFECTO)
    al.update(cfg().get("aliases") or {})
    f = str(fuente).lower()
    for k in sorted(al, key=len, reverse=True):
        if k.lower() in f:
            return al[k]
    return None


NOMBRE_URL = (("cheaperinference", "CheaperInference"), ("nan.builders", "NaN"), ("openrouter", "OpenRouter"),
              ("z.ai", "z.ai"), ("zhipu", "z.ai"), ("bigmodel", "z.ai"), ("nodeclub", "nodeclub.ai"),
              ("opencode", "OpenCode Go"), ("openai", "OpenAI (Codex)"), ("anthropic", "Claude (Max)"),
              ("deepseek", "DeepSeek"), ("chutes", "Chutes"), ("together", "Together"), ("groq", "Groq"))


def nombre_de_url(url, perfil=""):
    u = (url or "").lower()
    for trozo, nombre in NOMBRE_URL:
        if trozo in u:
            return nombre
    return alias_de(perfil) or perfil or "sin identificar"


def linea_ccs():
    """Linea de tiempo de CCS (Claude Code Switch): que proveedor tenia activo y cuando.

    CCS deja un settings.json por cada cambio en ~/.ccs/backups/<perfil>.<fecha>.settings.json,
    y ahi dentro va ANTHROPIC_BASE_URL. Con eso cada sesion de Claude se atribuye a su proveedor real.
    """
    traza = []
    for b in glob.glob(f"{HOME}/.ccs/backups/*.settings.json"):
        m = re.match(r"^([^.]+)\.(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})", os.path.basename(b))
        if not m:
            continue
        perfil = m.group(1)
        try:
            cuando = time.mktime(time.strptime(f"{m.group(2)} {m.group(3)}:{m.group(4)}:{m.group(5)}", "%Y-%m-%d %H:%M:%S"))
        except Exception:
            continue
        url = ""
        try:
            d = json.load(open(b, encoding="utf-8"))
            url = ((d.get("env") or {}).get("ANTHROPIC_BASE_URL") or "")
        except Exception:
            pass
        traza.append((cuando, nombre_de_url(url, perfil), perfil, url))
    # el perfil activo ahora mismo
    ahora = f"{HOME}/.claude/settings.json"
    if os.path.exists(ahora):
        try:
            d = json.load(open(ahora, encoding="utf-8"))
            url = ((d.get("env") or {}).get("ANTHROPIC_BASE_URL") or "")
            traza.append((os.path.getmtime(ahora), nombre_de_url(url, "ahora"), "ahora", url))
        except Exception:
            pass
    return sorted(traza)


_TRAZA_CCS = None


def plan_ccs(ts):
    """Proveedor activo en CCS en ese momento (epoch)."""
    global _TRAZA_CCS
    if _TRAZA_CCS is None:
        _TRAZA_CCS = linea_ccs()
    anterior = None
    for cuando, nombre, perfil, url in _TRAZA_CCS:
        if cuando <= (ts or 0):
            anterior = (cuando, nombre, perfil, url)
        else:
            break
    return anterior[1] if anterior else None


def familia_de(m):
    """glm5.3-flash -> glm · qwen3.8-27b -> qwen · <synthetic> -> synthetic · sin-modelo -> otros"""
    m = re.sub(r"[^a-z0-9_.-]", "", corto(m or "").lower())
    g = re.match(r"[a-z]+", m)
    fam = g.group(0) if g else "otros"
    return "otros" if fam in ("sin", "the", "") else fam


def proveedores_de(c, modelos=()):
    """Proveedores declarados en config.json: nombre, patrones de modelo y limites (0 = sin declarar).

    Cada plan es una cuota distinta (y una suscripcion distinta), asi que se miden por separado.
    """
    lista = c.get("proveedores") if c.get("proveedores") else PROVEEDORES_DEFECTO
    salida = []
    for p in lista:
        if not isinstance(p, dict):
            continue
        salida.append({"nombre": str(p.get("nombre") or "?"),
                       "modelos": [str(m).lower() for m in (p.get("modelos") or [])],
                       "limite_5h_tokens": p.get("limite_5h_tokens") or 0,
                       "limite_semana_tokens": p.get("limite_semana_tokens") or 0})
    # lo que no este declarado en config.json aparece solo, agrupado por familia de modelo
    auto = collections.defaultdict(set)
    for m in modelos:
        mm = (m or "").lower()
        if not mm or mm == "sin-modelo":
            continue
        if any(pat and pat in mm for p in salida for pat in p["modelos"]):
            continue
        auto[familia_de(m)].add(corto(m))
    for fam, ms in sorted(auto.items()):
        salida.append({"nombre": fam, "modelos": sorted(ms), "auto": True,
                       "limite_5h_tokens": 0, "limite_semana_tokens": 0})
    return salida


NATIVOS = {"Claude (Max)": ("claude", "sonnet", "opus", "haiku", "fable"),
           "OpenAI (Codex)": ("gpt", "o3", "o4", "codex")}


def modelo_nativo(plan, modelo):
    """¿Este modelo es de la casa de ese plan? GLM usado desde Claude Code NO es de Claude."""
    patrones = NATIVOS.get(plan)
    if not patrones:
        return True
    m = (modelo or "").lower()
    return any(p in m for p in patrones)


def plan_final(nombre, declarados):
    """Evita duplicados tontos: 'Openrouter' y 'OpenRouter' son el mismo plan."""
    for d in (declarados or ()):
        if (d or "").strip().lower() == (nombre or "").strip().lower():
            return d
    return nombre


def proveedor_de(modelo, provs, plan=None, fiable=True):
    """plan = proveedor real segun el log (providerID de OpenCode, URL base de Hermes...).

    Orden: origen fiable del log -> alias del nombre de modelo -> patrones de config -> familia.
    """
    m = (modelo or "").lower()
    if plan and fiable:
        return alias_de(plan) or plan
    al = dict(ALIAS_DEFECTO)
    al.update(cfg().get("aliases") or {})
    for k in sorted(al, key=len, reverse=True):
        if k.lower() in m:
            return al[k]
    for p in provs:
        if any(pat and pat in m for pat in p["modelos"]):
            return p["nombre"]
    if plan:
        return alias_de(plan) or plan
    for p in provs:
        if any(pat and pat in m for pat in p["modelos"]):
            return p["nombre"]
    return "otros"


def tarifa(m):
    m = (m or "").lower()
    for k, v in TARIFAS.items():
        if k in m:
            return v
    return None


def fmt_tok(n):
    """18300000 -> 18,3 M · 820000 -> 820 K"""
    n = float(n or 0)
    for corte, sufijo in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= corte:
            return f"{n / corte:.1f} {sufijo}".replace(".", ",")
    return str(int(n))


def color_magnitud(v):
    """Tono segun el tamano: miles de millones mas fuerte que millones."""
    v = abs(v or 0)
    if v >= 1e9:
        return (255, 0, 90)        # miles de millones: rojo fuerte de marca
    if v >= 1e6:
        return (174, 0, 255)       # millones: violeta de marca
    if v >= 1e3:
        return (110, 90, 200)      # miles: violeta suave
    return (120, 120, 140)


def icono_png(destino, fraccion, texto="", color=None, alerta=False):
    """Barra de la barra de menus: barra de progreso de la ventana de 5 h + el numero coloreado."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    W, H = 62, 22
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 1, W - 1, H - 2], radius=6, fill=(150, 140, 175, 45),
                        outline=((255, 0, 90, 255) if alerta else None), width=2)
    relleno = int((W - 4) * max(0.0, min(fraccion, 1.0)))
    for x in range(max(relleno, 0)):
        f = x / max(relleno - 1, 1)
        c = tuple(int(a + (b - a) * f) for a, b in ((0xAE, 0xFF), (0x00, 0x00), (0xFF, 0x8C)))
        d.line([(2 + x, 4), (2 + x, H - 5)], fill=c + (255,))
    if texto:
        try:
            fuente = ImageFont.load_default(size=13)
        except Exception:
            fuente = ImageFont.load_default()
        col = color or (60, 60, 70)
        caja = d.textbbox((0, 0), texto, font=fuente)
        d.text((W - (caja[2] - caja[0]) - 5, (H - (caja[3] - caja[1])) / 2 - 2), texto, font=fuente, fill=col + (255,))
    im.save(destino)
    return destino


def _color_ns(rgb):
    from AppKit import NSColor
    return NSColor.colorWithSRGBRed_green_blue_alpha_(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, 1.0)


def _pinta_magnitudes(item):
    """Colorea una entrada del menu: etiqueta en rosa de marca y los numeros segun su tamano.

    Los menues de macOS son texto plano; hay que ir al NSMenuItem de debajo. Si algo falla,
    se queda como estaba: nunca debe romper la barra.
    """
    try:
        from AppKit import NSMutableAttributedString, NSForegroundColorAttributeName
        from Foundation import NSMakeRange
        ns = getattr(item, "_menuitem", None)
        if ns is None:
            return False
        txt = str(ns.title())
        if not txt:
            return False
        a = NSMutableAttributedString.alloc().initWithString_(txt)
        i = txt.find(":")
        if 0 < i < 40 and txt[:i].lower() not in ("5 h", "semana"):
            a.addAttribute_value_range_(NSForegroundColorAttributeName, _color_ns((255, 0, 140)), NSMakeRange(0, i))
        for m in re.finditer(r"(\d[\d.,]*)\s*([KMB])\b", txt):
            n = 0.0
            try:
                n = float(m.group(1).replace(".", "").replace(",", "."))
            except Exception:
                continue
            n *= {"K": 1e3, "M": 1e6, "B": 1e9}[m.group(2)]
            a.addAttribute_value_range_(NSForegroundColorAttributeName, _color_ns(color_magnitud(n)), NSMakeRange(m.start(), len(m.group(0))))
        ns.setAttributedTitle_(a)
        return True
    except Exception as e:
        log(f"menu sin color: {type(e).__name__}: {e}")
        return False


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def cfg():
    d = {"limite_hora_tokens": 8_000_000, "limite_5h_tokens": 0, "limite_semana_tokens": 0}
    if os.path.exists(CONFIG):
        try:
            d.update(json.load(open(CONFIG)))
        except Exception:
            pass
    return d


def leer_estado():
    if os.path.exists(STATE):
        try:
            s = json.load(open(STATE))
            if s.get("version") == VERSION_ESTADO:
                return s
        except Exception:
            pass
    return {"version": VERSION_ESTADO, "ficheros": {}}


def guardar_estado(s):
    s["version"] = VERSION_ESTADO
    tmp = f"{STATE}.{os.getpid()}.tmp"      # unico por proceso: si la app y un informe coinciden, no chocan
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.replace(tmp, STATE)


def _hallar_uso(o, p=0):
    """Busca el bloque de uso de un turno. Descarta los acumulados de Codex (llevan total_tokens)."""
    if p > 6 or not isinstance(o, (dict, list)):
        return None
    if isinstance(o, dict):
        if "input_tokens" in o and "total_tokens" not in o:
            return o
        if isinstance(o.get("last_token_usage"), dict):
            return o["last_token_usage"]
        for v in o.values():
            r = _hallar_uso(v, p + 1)
            if r:
                return r
    elif o:
        return _hallar_uso(o[0], p + 1)
    return None


def parsear(ruta):
    """Devuelve {dia: {modelo: [gi,go,cr,cw]}, turnos: {dia: n}, horas: {'YYYY-MM-DDTHH': {'tok','usd','fam'}}, proyecto: slug}

    Dos formatos, y confundirlos infla los totales:
      · Codex (rollouts): cada turno se escribe DOS veces (token_usage_record y event_msg/token_count,
        con el mismo segundo) y el fichero lleva ademas total_token_usage, que es ACUMULADO. Se usa el
        uso del turno (last_token_usage) y se descarta el acumulado. En Codex cached_input_tokens esta
        DENTRO de input_tokens, asi que la entrada fresca es input - cached.
      · Claude Code: una linea por respuesta con message.usage (el input ya excluye la cache).
    """
    dia = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0, 0]))
    fecha_ini = None
    fecha_ini = None
    turnos = collections.defaultdict(int)
    horas = collections.defaultdict(lambda: {"tok": 0, "usd": 0.0, "mod": collections.defaultdict(int)})
    vistos = set()
    proy = "codex/otros"
    if "/projects/" in ruta:
        trozos = [x for x in urllib.parse.unquote(ruta.split("/projects/")[-1].split("/")[0]).split("-") if x]
        proy = "/".join(trozos[-3:]) if trozos else "otros"
    modelo = None
    try:
        fh = open(ruta, encoding="utf-8", errors="ignore")
    except Exception:
        return None
    for linea in fh:
        if '"model"' in linea or "\"model\"" in linea:
            m = re.search(r'"model"\s*:\s*"([^"]+)"', linea)
            if m:
                modelo = m.group(1)
        if "input_tokens" not in linea and "inputTokens" not in linea:
            continue
        try:
            d = json.loads(linea)
        except Exception:
            continue
        ts = str(d.get("timestamp") or d.get("ts") or "")
        uso = _hallar_uso(d)
        if not uso:
            continue
        if "cached_input_tokens" in uso or "total_tokens" in uso:      # Codex: la cache va dentro de la entrada
            cr = uso.get("cached_input_tokens", 0) or 0
            gi = max((uso.get("input_tokens", 0) or 0) - cr, 0)
            cw = uso.get("cache_write_input_tokens", 0) or 0
        else:                                                          # Claude: entrada y cache separadas
            gi = uso.get("input_tokens", uso.get("inputTokens", 0)) or 0
            cr = uso.get("cache_read_input_tokens", uso.get("cachedInputTokens", 0)) or 0
            cw = uso.get("cache_creation_input_tokens", uso.get("cacheWriteInputTokens", 0)) or 0
        go = uso.get("output_tokens", uso.get("outputTokens", 0)) or 0
        # el doble registro de Codex comparte el segundo: incluir el segundo en la clave lo descarta
        clave = f"{modelo}|{ts[:19]}|{gi}|{go}|{cr}|{cw}"
        if clave in vistos:
            continue
        vistos.add(clave)
        k = modelo or "sin-modelo"
        tok = gi + go + cr + cw
        if len(ts) >= 10:
            a = dia[ts[:10]][k]
            a[0] += gi
            a[1] += go
            a[2] += cr
            a[3] += cw
            turnos[ts[:10]] += 1
        if len(ts) >= 13:
            horas[ts[:13]]["tok"] += tok
            horas[ts[:13]]["mod"][k] += tok
            t = tarifa(k)
            if t:
                over = gi > CLIFF
                horas[ts[:13]]["usd"] += (max(gi - cr, 0) * t[0] * (2 if over else 1)
                                          + cr * t[2] * (2 if over else 1) + cw * t[3] * (2 if over else 1)
                                          + go * t[1] * (1.5 if over else 1)) / 1e6
    fh.close()
    plan = None
    if "/.claude/" in ruta:
        plan = "Claude (Max)"
    elif "/.codex/" in ruta:
        plan = "OpenAI (Codex)"
    ccs = plan_ccs(fecha_ini) if "/.claude/" in ruta else None
    return {"dia": {k: {m: v for m, v in d.items()} for k, d in dia.items()},
            "turnos": dict(turnos), "horas": dict(horas), "proyecto": proy, "plan": ccs or plan}


def leer_bases():
    """OpenCode y Hermes no escriben .jsonl: guardan el consumo en SQLite.

    Devuelve {proyecto: {dia, turnos, horas}} para que cada aplicacion y cada proyecto cuenten
    por separado (Hermes entra como proyecto, no como plan).
    """
    proyectos = {}

    def saca(proyecto):
        if proyecto not in proyectos:
            proyectos[proyecto] = {
                "dia": collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0, 0])),
                "turnos": collections.defaultdict(int),
                "horas": collections.defaultdict(lambda: {"tok": 0, "usd": 0.0, "mod": collections.defaultdict(int)}),
            }
        return proyectos[proyecto]

    nombres = []

    def limpia_modelo(m):
        m = m or ""
        if m.strip().startswith("{"):
            try:
                d = json.loads(m)
                m = d.get("id") or d.get("modelID") or m
            except Exception:
                pass
        return m.strip()

    def mete(proyecto, plan, ts, modelo, gi, go, cr, cw):
        if not ts or len(str(ts)) < 10:
            return
        ts = str(ts)
        proy = saca((proyecto or "Hermes/OpenCode", plan or ""))
        k = limpia_modelo(modelo) or "sin-modelo"
        a = proy["dia"][ts[:10]][k]
        a[0] += gi; a[1] += go; a[2] += cr; a[3] += cw
        proy["turnos"][ts[:10]] += 1
        if len(ts) >= 13:
            tok = gi + go + cr + cw
            proy["horas"][ts[:13]]["tok"] += tok
            proy["horas"][ts[:13]]["mod"][k] += tok
            tr = tarifa(k)
            if tr:
                over = gi > CLIFF
                proy["horas"][ts[:13]]["usd"] += (max(gi - cr, 0) * tr[0] * (2 if over else 1)
                                                  + cr * tr[2] * (2 if over else 1) + cw * tr[3] * (2 if over else 1)
                                                  + go * tr[1] * (1.5 if over else 1)) / 1e6

    def hora(v):
        v = v or 0
        if v > 1e11:            # milisegundos
            v /= 1000
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(v)) if v else ""

    def ultimos(partes, n=2):
        trozos = [x for x in (partes or "").split("/") if x]
        return "/".join(trozos[-n:]) if trozos else ""

    # OpenCode: cada sesion lleva su directorio
    for ruta in (f"{HOME}/.local/share/opencode/opencode.db", f"{HOME}/.local/share/opencode/opencode-.db"):
        if not os.path.exists(ruta):
            continue
        try:
            con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
            filas = list(con.execute("select model, tokens_input, tokens_output, tokens_cache_read, tokens_cache_write, time_created, directory from session"))
            con.close()
            for m, ti, to, tcr, tcw, creado, direc in filas:
                prov = ""
                if (m or "").strip().startswith("{"):
                    try:
                        prov = json.loads(m).get("providerID") or ""
                    except Exception:
                        prov = ""
                mete(f"OpenCode · {ultimos(direc) or 'sin proyecto'}", prov, hora(creado), m, ti or 0, to or 0, tcr or 0, tcw or 0)
            nombres.append(f"OpenCode ({len(filas)})")
            break
        except Exception as e:
            log(f"opencode {ruta}: {type(e).__name__}: {e}")
    # Hermes: su cwd es el proyecto (Hermes no es un plan, es la app)
    for db in sorted(glob.glob(f"{HOME}/.hermes/profiles/*/state.db")):
        try:
            perfil = db.split("/profiles/")[-1].split("/")[0]
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            filas = list(con.execute("select model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, started_at, cwd, billing_provider, billing_base_url from sessions"))
            con.close()
            for m, i, o, cr, cw, ini, cwd, bprov, burl in filas:
                mete(f"Hermes/{perfil}" + (f" · {ultimos(cwd, 1)}" if cwd else ""), burl or bprov or "", hora(ini), m, i or 0, o or 0, cr or 0, cw or 0)
            nombres.append(f"Hermes/{perfil} ({len(filas)})")
        except Exception as e:
            log(f"hermes {db}: {type(e).__name__}: {e}")
    return proyectos, nombres


def escanear():
    estado = leer_estado()
    vistos, nuevos = {}, 0
    for base in FUENTES:
        ficheros = glob.glob(f"{base}/**/*.jsonl", recursive=True) + glob.glob(f"{base}/**/*.json", recursive=True)
        for f in ficheros:
            try:
                st = os.stat(f)
            except OSError:
                continue
            clave = f"{int(st.st_mtime)}:{st.st_size}"
            prev = estado["ficheros"].get(f)
            if prev and prev.get("clave") == clave:
                vistos[f] = prev
            else:
                r = parsear(f)
                if r is None:
                    continue
                r["clave"] = clave
                vistos[f] = r
                nuevos += 1
    proyectos_base, nombres_bases = leer_bases()
    for (proy, plan), datos in proyectos_base.items():
        datos["proyecto"] = proy
        datos["plan"] = plan
        datos["clave"] = "bases"
        vistos[f"sqlite:{proy}|{plan}"] = datos
    estado["ficheros"] = vistos
    guardar_estado(estado)
    r = agregar(vistos, nuevos)
    r["fuentes"] = r["fuentes"] + len(nombres_bases)
    r["bases"] = nombres_bases
    return r


def vacio():
    return {"gi": 0, "go": 0, "cr": 0, "cw": 0, "tok": 0, "turnos": 0, "usd": 0.0}


def suma(dest, v):
    dest["gi"] += v[0]; dest["go"] += v[1]; dest["cr"] += v[2]; dest["cw"] += v[3]
    dest["tok"] += v[0] + v[1] + v[2] + v[3]


def agregar(vistos, nuevos=0):
    hoy = time.strftime("%Y-%m-%d")
    ayer = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    desde = time.strftime("%Y-%m-%d", time.localtime(time.time() - DIAS * 86400))
    hace7 = time.strftime("%Y-%m-%d", time.localtime(time.time() - 6 * 86400))
    hace30 = time.strftime("%Y-%m-%d", time.localtime(time.time() - 29 * 86400))
    dias = collections.defaultdict(vacio)
    modelos_hoy = collections.defaultdict(vacio)
    modelos_mes = collections.defaultdict(vacio)
    modelo_dia = collections.defaultdict(lambda: collections.defaultdict(int))
    proys = collections.defaultdict(vacio)
    ritmo = {"tok": 0, "usd": 0.0}
    cinco = {"tok": 0, "usd": 0.0, "fam": collections.defaultdict(int)}
    modelos_vistos = {m for r in vistos.values() for mods in r.get("dia", {}).values() for m in mods}
    provs = proveedores_de(cfg(), modelos_vistos)
    por_prov = collections.defaultdict(lambda: {"cinco_h": 0, "semana": 0, "hoy": 0, "mes": 0, "modelos": set()})
    cruce = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(int)))
    proy_prov = collections.defaultdict(lambda: collections.defaultdict(int))
    modelo_prov = {}
    origenes = collections.defaultdict(int)
    ahora = time.time()
    ignora = [str(x).lower() for x in (cfg().get("ignorar") or ["synthetic", "<synthetic>", "sin-modelo"])]
    for f, r in vistos.items():
        for d, mods in r.get("dia", {}).items():
            if not d or d < desde:
                continue
            for m, v in mods.items():
                if any(x in (m or "").lower() for x in ignora):
                    continue
                suma(dias[d], v)
                t = tarifa(m)
                if t:
                    over = v[0] > CLIFF
                    dias[d]["usd"] += (max(v[0] - v[2], 0) * t[0] * (2 if over else 1)
                                       + v[2] * t[2] * (2 if over else 1) + v[3] * t[3] * (2 if over else 1)
                                       + v[1] * t[1] * (1.5 if over else 1)) / 1e6
                if d >= hace30:
                    suma(modelos_mes[m], v)
                    modelo_dia[m][d] += v[0] + v[1] + v[2] + v[3]
                if d == hoy:
                    suma(modelos_hoy[m], v)
                    if t:
                        over = v[0] > CLIFF
                        modelos_hoy[m]["usd"] += (max(v[0] - v[2], 0) * t[0] * (2 if over else 1)
                                                  + v[2] * t[2] * (2 if over else 1) + v[3] * t[3] * (2 if over else 1)
                                                  + v[1] * t[1] * (1.5 if over else 1)) / 1e6
                suma(proys[r.get("proyecto", "otros")], v)
                plan_f = r.get("plan")
                fiable = bool(plan_f) and modelo_nativo(plan_f, m)
                pn = plan_final(proveedor_de(m, provs, plan_f, fiable), [x["nombre"] for x in provs])
                pp = por_prov[pn]
                pp["modelos"].add(corto(m))
                modelo_prov[corto(m)] = pn
                tokv = v[0] + v[1] + v[2] + v[3]
                cruce[pn][corto(m)][d] += tokv
                origenes[str(r.get("plan") or "(sin origen)")] += tokv
                proy_prov[pn][r.get("proyecto", "otros")] += tokv
                if d >= hace7:
                    pp["semana"] += tokv
                if d >= hace30:
                    pp["mes"] += tokv
                if d == hoy:
                    pp["hoy"] += tokv
        for d, n in r.get("turnos", {}).items():
            if d and d >= desde:
                dias[d]["turnos"] += n
        for h, v in r.get("horas", {}).items():
            if len(h) < 13:
                continue
            try:
                hm = time.mktime(time.strptime(h, "%Y-%m-%dT%H"))
            except ValueError:
                continue
            edad = ahora - hm
            if edad < 3600:
                ritmo["tok"] += v.get("tok", 0)
                ritmo["usd"] += v.get("usd", 0.0)
            if edad < VENTANA_H * 3600:            # ventana de 5 horas de la suscripcion
                cinco["tok"] += v.get("tok", 0)
                cinco["usd"] += v.get("usd", 0.0)
                for k2, t2 in (v.get("mod") or {}).items():
                    cinco["fam"][proveedor_de(k2, provs)] += t2

    def pct(bloque):
        entrada_total = bloque["gi"] + bloque["cr"] + bloque["cw"]
        return (bloque["cr"] / entrada_total * 100) if entrada_total else 0.0

    for d in dias:
        dias[d]["cache_pct"] = pct(dias[d])
    c = cfg()
    lim5, limsem = c.get("limite_5h_tokens") or 0, c.get("limite_semana_tokens") or 0
    for n, t2 in cinco["fam"].items():          # la ventana de 5 h, por proveedor
        por_prov[n]["cinco_h"] = t2
    lim_prov = {p["nombre"]: (p["limite_5h_tokens"], p["limite_semana_tokens"]) for p in provs}
    provs_out = {}
    for n, x in sorted(por_prov.items(), key=lambda kv: -kv[1]["cinco_h"]):
        l5, ls = lim_prov.get(n, (0, 0))
        provs_out[n] = {"cinco_h": x["cinco_h"], "semana": x["semana"], "hoy": x["hoy"], "mes": x["mes"],
                        "modelos": sorted(x["modelos"]),
                        "pct_5h": (x["cinco_h"] / l5 * 100) if l5 else 0.0,
                        "pct_semana": (x["semana"] / ls * 100) if ls else 0.0}
    def agrega(filtro):
        acc = collections.defaultdict(float)
        for d, x in dias.items():
            if d and filtro(d):
                for k in ("gi", "go", "cr", "cw", "tok", "turnos", "usd"):
                    acc[k] += x[k]
        return dict(acc)
    semana = agrega(lambda d: d >= hace7)
    mes = agrega(lambda d: d >= hace30)
    # Un modelo puede servir a dos planes (deepseek por NaN y por OpenRouter): se muestra
    # el plan con el que mas se consume, para que la etiqueta no mienta.
    _tok = collections.defaultdict(lambda: collections.defaultdict(int))
    for _pn, _mods in cruce.items():
        for _m, _ds in _mods.items():
            _tok[_m][_pn] = sum(_ds.values())
    for _m, _d in _tok.items():
        if _d:
            modelo_prov[_m] = max(_d, key=_d.get)
    for _n, _pr in (provs_out or {}).items():
        _cf = next((x for x in (cfg().get("proveedores") or []) if x["nombre"] == _n), {})
        _t5 = next((v for kk, v in _pr.items() if isinstance(v, (int, float)) and ("5h" in kk.lower() or "cinco" in kk.lower())), 0)
        _ts = next((v for kk, v in _pr.items() if isinstance(v, (int, float)) and "semana" in kk.lower()), 0)
        _l5, _ls = _cf.get("limite_5h_tokens") or 0, _cf.get("limite_semana_tokens") or 0
        _pr["tipo"] = _cf.get("tipo") or ("suscripcion" if (_l5 or _ls) else "pago_por_uso")
        _pr["pct_5h"] = round(100.0 * _t5 / _l5, 1) if _l5 else 0
        _pr["pct_semana"] = round(100.0 * _ts / _ls, 1) if _ls else 0
    return {"hoy": dias.get(hoy, vacio()), "ayer": dias.get(ayer, vacio()), "semana": semana, "mes": mes,
            "modelos_mes": dict(modelos_mes),
            "modelo_dia": {m: dict(v) for m, v in modelo_dia.items()},
            "ritmo": ritmo, "cinco_h": {"tok": cinco["tok"], "usd": cinco["usd"], "fam": dict(cinco["fam"])},
            "pct_5h": (cinco["tok"] / lim5 * 100) if lim5 else 0.0,
            "pct_semana": (semana["tok"] / limsem * 100) if limsem else 0.0,
            "dias": {d: dias[d] for d in dias if d},
            "modelos_hoy": dict(modelos_hoy), "proyectos": dict(proys),
            "proveedores": provs_out,
            "cruce": {n: {m: dict(v) for m, v in d2.items()} for n, d2 in cruce.items()},
            "proy_prov": {n: dict(v) for n, v in proy_prov.items()},
        "modelo_prov": modelo_prov,
        "origenes": dict(origenes),
        "config": cfg(),
            "cache_pct_hoy": pct(dias.get(hoy, vacio())), "nuevos": nuevos,
            "limites": c, "fuentes": len(vistos)}


def informa(r):
    h, c5 = r["hoy"], r["cinco_h"]
    fam = " · ".join(f"{k}: {fmt_tok(v)}" for k, v in sorted(c5["fam"].items(), key=lambda x: -x[1]) if v)
    L = [f"fuentes: " + " · ".join(r.get("bases") or []) ,
         f"VENTANA 5 H  {fmt_tok(c5['tok'])} tokens" + (f"   {fam}" if fam else ""),
         f"SEMANA       {fmt_tok(r['semana']['tok'])} tokens ({int(r['semana']['turnos'])} turnos)",
         f"MES (30 d)   {fmt_tok(r['mes']['tok'])} tokens ({int(r['mes']['turnos'])} turnos)",
         f"HOY          {fmt_tok(h['tok'])} tokens ({int(h['turnos'])} turnos)   ritmo ultima hora {fmt_tok(r['ritmo']['tok'])} tokens",
         f"             entrada {fmt_tok(h['gi'])} · salida {fmt_tok(h['go'])} · cache leida {fmt_tok(h['cr'])} ({r['cache_pct_hoy']:.0f} %) · cache escrita {fmt_tok(h['cw'])}",
         "por dia: " + " · ".join(f"{d[5:]}: {fmt_tok(v['tok'])}" for d, v in sorted(r["dias"].items())),
         "modelos hoy: " + " · ".join(f"{corto(k)}: {fmt_tok(v['tok'])}" for k, v in sorted(r["modelos_hoy"].items(), key=lambda x: -x[1]["tok"])[:6]),
         "planes: " + " · ".join(f"{n}: 5h {fmt_tok(x['cinco_h'])}" + (f" ({x['pct_5h']:.0f} %)" if x["pct_5h"] else "") + f" · sem {fmt_tok(x['semana'])}" for n, x in r["proveedores"].items() if x["cinco_h"] or x["semana"]),
         "proyectos (mes): " + " · ".join(f"{k}: {fmt_tok(v['tok'])}" for k, v in sorted(r["proyectos"].items(), key=lambda x: -x[1]["tok"])[:5]),
         f"equivalente API (informativo): {h['usd']:.2f} USD hoy · {r['semana']['usd']:.0f} USD en 7 dias"]
    return "\n".join(L)


def servir():
    """Sirve el panel en http://127.0.0.1:8765 y guarda la configuracion que envies desde el.

    Asi no hay que editar ficheros a mano: el propio panel tiene el configurador.
    """
    import http.server, socketserver, threading

    base = os.path.dirname(os.path.abspath(__file__))
    cfg_ini = json.dumps(cfg(), indent=2, ensure_ascii=False)

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _manda(self, codigo, cuerpo, tipo):
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def do_GET(self):
            ruta = self.path.split("?")[0]
            if ruta in ("/", "/panel.html"):
                try:
                    self._manda(200, open(f"{base}/panel.html", "rb").read(), "text/html; charset=utf-8")
                except Exception as e:
                    self._manda(500, str(e).encode(), "text/plain")
            elif ruta == "/origenes":
                try:
                    r = escanear()
                    self._manda(200, json.dumps({"origenes": r.get("origenes", {}),
                                                 "planes": [p["nombre"] for p in (cfg().get("proveedores") or [])],
                                                 "config": cfg()}, ensure_ascii=False).encode(), "application/json; charset=utf-8")
                except Exception as e:
                    self._manda(500, json.dumps({"error": str(e)}).encode(), "application/json")
            elif ruta == "/config.json":
                self._manda(200, open(f"{base}/config.json", "rb").read(), "application/json; charset=utf-8")
            else:
                self._manda(404, b"no", "text/plain")

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                nuevo = json.loads(self.rfile.read(n).decode("utf-8"))
                if not isinstance(nuevo, dict):
                    raise ValueError("no es un objeto")
                if "proveedores" not in nuevo or "aliases" not in nuevo:
                    raise ValueError("faltan campos (proveedores / aliases)")
                if os.path.exists(f"{base}/config.json"):
                    import shutil as _sh
                    _sh.copy(f"{base}/config.json", f"{base}/config.json.bak")
                json.dump(nuevo, open(f"{base}/config.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
                try:
                    est = leer_estado()
                    est["version"] = 0                 # fuerza reescaneo con la config nueva
                    escribir_estado(est)
                except Exception:
                    pass
                log("configuracion guardada desde el panel")
                self._manda(200, b'{"ok":true}', "application/json")
            except Exception as e:
                self._manda(400, json.dumps({"ok": False, "error": str(e)}).encode(), "application/json")

    class S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    try:
        srv = S(("127.0.0.1", 8765), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        log(f"configurador listo en http://127.0.0.1:8765")
    except OSError as e:
        log(f"configurador no disponible: {e}")


def main():
    servir()
    if "--proveedores" in sys.argv:
        r = escanear()
        print("Modelos usados en los ultimos 7 dias, agrupados por proveedor:")
        for n, x in r["proveedores"].items():
            print(f"  {n:<14} {', '.join(x['modelos']) or '-'}")
        print("\nCopia esto en config.json y pon tus cuotas (0 = sin declarar):\n")
        prop = [{"nombre": n, "modelos": x["modelos"], "limite_5h_tokens": 0, "limite_semana_tokens": 0}
                for n, x in r["proveedores"].items() if n != "otros"]
        print(json.dumps({"proveedores": prop}, indent=2, ensure_ascii=False))
        print("\nLas cuotas de cada plan las sabe el proveedor, no los logs: ponlas cuando las tengas")
        print("y veras el % de la ventana de 5 h y de la semana en el icono y en el panel.")
        return
    if "--print" in sys.argv:
        print(informa(escanear()))
        return
    try:
        import rumps
    except ImportError:
        print("rumps no está instalado (solo hace falta para el icono de la barra en macOS).")
        print("Sin él sí funcionan:  python3 analizar_gasto.py   y   python3 panel.py")
        return

    class CostBar(rumps.App):
        def __init__(self):
            super().__init__("CostBar", title="…", quit_button=None)
            self.r = None
            self.refrescar(None)
            rumps.Timer(self.refrescar, 60).start()

        def abrir_panel(self, _):
            try:
                sys.path.insert(0, AQUI)
                import panel
                ruta, _, _ = panel.main()
                subprocess.Popen(["open", ruta])
            except Exception as e:
                log(f"ERROR panel: {type(e).__name__}: {e}")

        def copia(self, texto):
            def _f(_):
                try:
                    subprocess.run(["pbcopy"], input=texto.encode())
                except Exception:
                    pass
            return _f

        def _item(self, titulo):
            it = rumps.MenuItem(titulo, callback=self.copia(titulo))
            self.menu.add(it)
            return it

    _titulo = ""

    def refrescar(self, _):
            try:
                r = escanear()
                self.r = r
                h, c5 = r["hoy"], r["cinco_h"]
                lim5 = r["limites"].get("limite_5h_tokens") or 0
                alto = r["ritmo"]["tok"] >= r["limites"]["limite_hora_tokens"]
                lim5v = lim5 or 0
                fraccion = (c5["tok"] / lim5v) if lim5v else (r["ritmo"]["tok"] / (r["limites"]["limite_hora_tokens"] or 1) or 0.34)
                self._titulo = fmt_tok(c5["tok"])
                icono = icono_png(os.path.join(AQUI, "icono.png"), fraccion, self._titulo, color_magnitud(c5["tok"]), alto)
                if icono:
                    self.icon = icono
                self._titulo = fmt_tok(c5["tok"])
                self.title = ("" if self.icon else self._titulo) + (" !" if (alto and not self.icon) else "")
                self.menu.clear()
                m = self._item
                # la ventana de 5 horas es el dato que se agota en las suscripciones
                m(f"Ultimas 5 h: {fmt_tok(c5['tok'])} tokens" + (f"  ({r['pct_5h']:.0f} % del limite)" if lim5 else ""))
                for k, v in sorted(c5["fam"].items(), key=lambda x: -x[1]):
                    if v:
                        m(f"   {k}: {fmt_tok(v)}")
                m(f"Semana: {fmt_tok(r['semana']['tok'])} tokens · {r['semana']['turnos']} turnos")
                pv = rumps.MenuItem("Por proveedor (plan)")
                for nombre, x in r["proveedores"].items():
                    if not x["cinco_h"] and not x["semana"]:
                        continue
                    txt = f"{nombre}: 5 h {fmt_tok(x['cinco_h'])}"
                    if x["pct_5h"]:
                        txt += f" ({x['pct_5h']:.0f} %)"
                    txt += f" · semana {fmt_tok(x['semana'])}"
                    if x["pct_semana"]:
                        txt += f" ({x['pct_semana']:.0f} %)"
                    pv.add(rumps.MenuItem(txt))
                self.menu.add(pv)
                self.menu.add(rumps.separator)
                m(f"Hoy: {fmt_tok(h['tok'])} tokens · {h['turnos']} turnos")
                m(f"   entrada {fmt_tok(h['gi'])} · salida {fmt_tok(h['go'])}")
                m(f"   cache leida {fmt_tok(h['cr'])} ({r['cache_pct_hoy']:.0f} % de la entrada) · escrita {fmt_tok(h['cw'])}")
                m(f"Ritmo ultima hora: {fmt_tok(r['ritmo']['tok'])} tokens" + ("  ALTO" if alto else ""))
                self.menu.add(rumps.separator)
                m(f"Ayer: {fmt_tok(r['ayer']['tok'])} tokens · {r['ayer']['turnos']} turnos")
                for d, v in sorted(r["dias"].items())[-5:]:
                    m(f"   {d[5:]}: {fmt_tok(v['tok'])} ({v['turnos']} turnos)")
                mm = rumps.MenuItem("Por modelo (hoy)")
                for k, v in sorted(r["modelos_hoy"].items(), key=lambda x: -x[1]["tok"])[:8]:
                    mm.add(rumps.MenuItem(f"{corto(k)}: {fmt_tok(v['tok'])} · {v['turnos']} turnos"))
                self.menu.add(mm)
                pp = rumps.MenuItem("Por proyecto (7 dias)")
                for k, v in sorted(r["proyectos"].items(), key=lambda x: -x[1]["tok"])[:8]:
                    pp.add(rumps.MenuItem(f"{k}: {fmt_tok(v['tok'])}"))
                self.menu.add(pp)
                self.menu.add(rumps.separator)
                m(f"Equivalente API: {h['usd']:.1f} USD hoy (informativo)")
                m("Con suscripcion no se paga por token: mira tokens y turnos.")
                self.menu.add(rumps.separator)
                self.menu.add(rumps.MenuItem("Abrir panel", callback=self.abrir_panel))
                self.menu.add(rumps.MenuItem("Abrir la guia", callback=lambda _: subprocess.Popen(["open", os.path.join(AQUI, "GUIA.md")])))
                self.menu.add(rumps.MenuItem("Refrescar ahora", callback=self.refrescar))
                self.menu.add(rumps.MenuItem("Salir", callback=rumps.quit_application))
                if alto:
                    rumps.notification("CostBar", f"Ritmo alto: {fmt_tok(r['ritmo']['tok'])} tokens/h",
                                       f"5 h: {fmt_tok(c5['tok'])} · hoy {fmt_tok(h['tok'])}. Baja turnos o contexto.")
                try:
                    import panel
                    panel.main()
                except Exception as e:
                    log(f"panel: {type(e).__name__}: {e}")
                log(f"refresco ok: 5h {fmt_tok(c5['tok'])} · hoy {fmt_tok(h['tok'])} tok · {h['turnos']} turnos · nuevos {r['nuevos']}")
            except Exception as e:
                log(f"ERROR {type(e).__name__}: {e}")
                self.title = "!"

    CostBar().run()


if __name__ == "__main__":
    main()
