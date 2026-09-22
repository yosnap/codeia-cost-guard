#!/usr/bin/env python3
"""CostBar - gasto de tokens de la flota de agentes, en la barra de menus del Mac.

Lee los logs de Claude Code / Codex / OpenCode, desduplica los turnos copiados por los forks
(el mismo sesgo que inflaba ~15x el panel de Orca) y muestra el gasto de hoy y el ritmo por hora.

Uso:
    ./venv/bin/python costbar.py --print      # imprime el informe y sale (sin GUI)
    ./venv/bin/python costbar.py              # icono en la barra de menus
"""
import collections, glob, hashlib, json, os, re, subprocess, sys, time, urllib.parse

AQUI = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
STATE = f"{AQUI}/state.json"
CONFIG = f"{AQUI}/config.json"
LOG = f"{AQUI}/logs/costbar.log"
FUENTES = [f"{HOME}/.claude/projects", f"{HOME}/.codex/sessions", f"{HOME}/.local/share/opencode"]
DIAS = 7

# tarifas USD/1M: (entrada, salida, cache_leida, escritura_cache)
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
    m = re.sub(r"-\d{8}$", "", (m or "?")).replace("claude-", "").replace("gpt-5.6-", "")
    return m.split("/")[0]
def tarifa(m):
    m = (m or "").lower()
    for k, v in TARIFAS.items():
        if k in m: return v
    return None

def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

def cfg():
    d = {"limite_hora_usd": 25.0, "limite_dia_usd": 150.0}
    if os.path.exists(CONFIG):
        try: d.update(json.load(open(CONFIG)))
        except Exception: pass
    return d

def leer_estado():
    if os.path.exists(STATE):
        try: return json.load(open(STATE))
        except Exception: pass
    return {"ficheros": {}}

def guardar_estado(s):
    tmp = STATE + ".tmp"
    json.dump(s, open(tmp, "w"))
    os.replace(tmp, STATE)

def parsear(ruta):
    """Devuelve {dia: {modelo: [gi,go,cr,cw]}, horas: {'YYYY-MM-DDTHH': usd}, proyecto: slug}"""
    dia = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0, 0]))
    horas = collections.defaultdict(float)
    vistos = set()
    proy = "codex/otros"
    if "/projects/" in ruta:
        trozos = [x for x in urllib.parse.unquote(ruta.split("/projects/")[-1].split("/")[0]).split("-") if x]
        proy = "/".join(trozos[-3:]) if trozos else "otros"
    ult = None
    try: fh = open(ruta, encoding="utf-8", errors="ignore")
    except Exception: return None
    for linea in fh:
        if "input_tokens" not in linea and "inputTokens" not in linea: continue
        try: d = json.loads(linea)
        except Exception: continue
        ts = str(d.get("timestamp") or d.get("ts") or "")
        def walk(o, p=0):
            nonlocal ult
            if p > 6: return None
            if isinstance(o, dict):
                if isinstance(o.get("model"), str): ult = o["model"]
                if "input_tokens" in o or "inputTokens" in o: return o
                for v in o.values():
                    r = walk(v, p + 1)
                    if r: return r
            elif isinstance(o, list) and o: return walk(o[0], p + 1)
            return None
        u = walk(d)
        if not u: continue
        gi = u.get("input_tokens", u.get("inputTokens", 0)) or 0
        go = u.get("output_tokens", u.get("outputTokens", 0)) or 0
        cr = u.get("cache_read_input_tokens", u.get("cachedInputTokens", 0)) or 0
        cw = u.get("cache_creation_input_tokens", u.get("cacheWriteInputTokens", 0)) or 0
        h = hashlib.md5(f"{ult}|{ts}|{gi}|{go}|{cr}|{cw}".encode()).hexdigest()
        if h in vistos: continue          # fork duplicado
        vistos.add(h)
        k = ult or "sin-modelo"
        if len(ts) >= 10:
            a = dia[ts[:10]][k]; a[0] += gi; a[1] += go; a[2] += cr; a[3] += cw
        t = tarifa(k)
        if t and len(ts) >= 13:
            over = gi > CLIFF
            horas[ts[:13]] += (max(gi - cr, 0) * t[0] * (2 if over else 1)
                               + cr * t[2] * (2 if over else 1) + cw * t[3] * (2 if over else 1)
                               + go * t[1] * (1.5 if over else 1)) / 1e6
    fh.close()
    return {"dia": {k: {m: v for m, v in d.items()} for k, d in dia.items()},
            "horas": dict(horas), "proyecto": proy}

def escanear():
    estado = leer_estado()
    vistos, nuevos = {}, 0
    for base in FUENTES:
        for f in glob.glob(f"{base}/**/*.jsonl", recursive=True) + glob.glob(f"{base}/**/*.json", recursive=True):
            try: st = os.stat(f)
            except OSError: continue
            clave = f"{int(st.st_mtime)}:{st.st_size}"
            prev = estado["ficheros"].get(f)
            if prev and prev.get("clave") == clave:
                vistos[f] = prev
            else:
                r = parsear(f)
                if r is None: continue
                r["clave"] = clave
                vistos[f] = r; nuevos += 1
    estado["ficheros"] = vistos
    guardar_estado(estado)
    # agregar
    por_dia, por_hora, por_proy, por_modelo = (collections.defaultdict(float) for _ in range(4))
    hoy = time.strftime("%Y-%m-%d")
    desde = time.strftime("%Y-%m-%d", time.localtime(time.time() - DIAS * 86400))
    c = cfg()
    for f, r in vistos.items():
        for d, mods in r["dia"].items():
            if d and d < desde: continue
            for m, v in mods.items():
                t = tarifa(m)
                if not t: continue
                over = v[0] > CLIFF
                usd = (max(v[0] - v[2], 0) * t[0] * (2 if over else 1) + v[2] * t[2] * (2 if over else 1)
                       + v[3] * t[3] * (2 if over else 1) + v[1] * t[1] * (1.5 if over else 1)) / 1e6
                por_dia[d] += usd
                if d == hoy: por_modelo[m] += usd
                if d >= desde:
                    por_proy[r["proyecto"]] += usd
        for h, v in r["horas"].items():
            if len(h) < 13: continue
            try: hm = time.mktime(time.strptime(h, "%Y-%m-%dT%H"))
            except ValueError: continue
            if time.time() - hm < 3600: por_hora[h] = por_hora.get(h, 0) + v
    ritmo = sum(por_hora.values())
    return {"hoy": por_dia.get(hoy, 0.0), "ayer": por_dia.get(time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400)), 0.0),
            "semana": sum(por_dia.values()), "ritmo": ritmo, "dias": dict(por_dia),
            "modelos": dict(por_modelo), "proyectos": dict(por_proy), "nuevos": nuevos, "limites": c}

def informa(r):
    L = [f"HOY {r['hoy']:.2f} USD   ritmo ultima hora {r['ritmo']:.2f} USD/h   (limite {r['limites']['limite_hora_usd']:.0f}/h)",
         f"AYER {r['ayer']:.2f} USD   ULTIMOS 7 DIAS {r['semana']:.2f} USD",
         "por dia: " + " · ".join(f"{d[5:]}: {v:.0f}" for d, v in sorted(r["dias"].items()) if d),
         "modelos hoy: " + " · ".join(f"{corto(k)}: {v:.0f}" for k, v in sorted(r["modelos"].items(), key=lambda x: -x[1])[:5]),
         "proyectos 7d: " + " · ".join(f"{k[-34:]:<34}: {v:>6.0f}" for k, v in sorted(r["proyectos"].items(), key=lambda x: -x[1])[:5])]
    return "\n".join(L)

def main():
    if "--print" in sys.argv:
        r = escanear(); print(informa(r)); return
    try:
        import rumps
    except ImportError:
        print("rumps no está instalado (solo hace falta para el icono de la barra en macOS).")
        print("Sin él sí funcionan:  python3 analizar_gasto.py   y   python3 panel.py")
        return
    class CostBar(rumps.App):
        def __init__(self):
            super().__init__("CostBar", title="$…", quit_button=None)
            self.r = None; self.refrescar(None)
            rumps.Timer(self.refrescar, 60).start()
        def abrir_panel(self, _):
            try:
                sys.path.insert(0, AQUI)
                import panel
                ruta, _, _ = panel.main()
                subprocess.Popen(["open", ruta])
            except Exception as e:
                log(f"ERROR panel: {type(e).__name__}: {e}")
        def _item(self, titulo):
            it = rumps.MenuItem(titulo); self.menu.add(it); return it
        def refrescar(self, _):
            try:
                r = escanear(); self.r = r
                aviso = " · %.0f/h" % r["ritmo"] if r["ritmo"] >= r["limites"]["limite_hora_usd"] else ""
                self.title = f"${r['hoy']:.1f}{aviso}"
                self.menu.clear()
                self._item(f"Hoy: {r['hoy']:.2f} USD   (ritmo {r['ritmo']:.2f} USD/h)")
                self._item(f"Ayer: {r['ayer']:.2f} USD   ·   7 dias: {r['semana']:.2f} USD")
                for d, v in sorted(r["dias"].items())[-5:]:
                    self._item(f"   {d[5:]}: {v:.2f} USD")
                mods = rumps.MenuItem("Por modelo (hoy)")
                for k, v in sorted(r["modelos"].items(), key=lambda x: -x[1])[:6]:
                    mods.add(rumps.MenuItem(f"{corto(k)}: {v:.2f} USD"))
                self.menu.add(mods)
                proy = rumps.MenuItem("Por proyecto (7 dias)")
                for k, v in sorted(r["proyectos"].items(), key=lambda x: -x[1])[:8]:
                    proy.add(rumps.MenuItem(f"{k}: {v:.0f} USD"))
                self.menu.add(proy)
                self.menu.add(rumps.separator)
                self.menu.add(rumps.MenuItem("Abrir panel", callback=self.abrir_panel))
                self.menu.add(rumps.MenuItem("Abrir la guia", callback=lambda _: subprocess.Popen(["open", os.path.join(AQUI, "GUIA.md")])))
                self.menu.add(rumps.MenuItem("Refrescar ahora", callback=self.refrescar))
                self.menu.add(rumps.MenuItem("Salir", callback=rumps.quit_application))
                if r["ritmo"] >= r["limites"]["limite_hora_usd"]:
                    rumps.notification("CostBar", f"Ritmo alto: {r['ritmo']:.0f} USD/h", f"Hoy llevas {r['hoy']:.0f} USD. Revísalo antes de que se vaya el día.")
                try:
                    import panel
                    panel.main()
                except Exception as e:
                    log(f"panel: {type(e).__name__}: {e}")
                log(f"refresco ok: hoy {r['hoy']:.2f} ritmo {r['ritmo']:.2f} nuevos {r['nuevos']}")
            except Exception as e:
                log(f"ERROR {type(e).__name__}: {e}")
                self.title = "$!"
    CostBar().run()

if __name__ == "__main__":
    main()
