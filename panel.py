#!/usr/bin/env python3
"""Genera panel.html: consumo en tokens, con modo claro/oscuro, filtros por plan y periodo,
graficos (barras, lineas, tarta, calendario...) y detalle al clicar un dia o un tramo.

Todo el dato se calcula en Python (datos()) y se embute como JSON; el HTML/CSS/JS vive en
panel_assets/ (varios ficheros pequeños en vez de una sola plantilla gigante) pero el
resultado sigue siendo un unico panel.html autocontenido: sin red, sin dependencias."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costbar

AQUI = os.path.dirname(os.path.abspath(__file__))
ASSETS = f"{AQUI}/panel_assets"
SALIDA = f"{AQUI}/panel.html"
SECCION_CFG = """
<details class="tarjeta" id="caja-cfg" style="margin-top:18px">
  <summary style="cursor:pointer;font-weight:600;font-size:1.05rem">Configurar planes y límites</summary>
  <p style="opacity:.75;margin:.6rem 0 1rem">A la izquierda, lo que dice el log. A la derecha, con qué plan y con qué cuota lo cuentas tú.
  Guarda y la app lo aplica sola en el siguiente refresco.</p>
  <table id="tabla-cfg" style="width:100%;border-collapse:collapse"></table>
  <div style="margin-top:1rem;display:flex;gap:.6rem;align-items:center;flex-wrap:wrap">
    <button onclick="guardarCfg()" style="padding:.5rem 1.1rem;border-radius:8px;border:0;background:#ae00ff;color:#fff;font-weight:600;cursor:pointer">Guardar</button>
    <span id="aviso-cfg" style="font-size:.9rem"></span>
  </div>
  <h4 style="margin:1.4rem 0 .4rem">Cuotas por plan (en tokens; 0 = sin declarar)</h4>
  <table id="tabla-lim" style="width:100%;border-collapse:collapse"></table>
</details>
<script>
let CFG = null;
function fmtTok(v){ v=+v||0; const u=["","K","M","B"]; let i=0; while(v>=1000&&i<3){v/=1000;i++;} return (i?v.toFixed(1):v)+" "+u[i]; }
function pintaCfg(){
  fetch("/origenes").then(r=>r.json()).then(d=>{
    CFG = d.config || {aliases:{},proveedores:[]};
    CFG.aliases = CFG.aliases || {};
    const filas = Object.entries(d.origenes||{}).sort((a,b)=>b[1]-a[1]);
    document.getElementById("tabla-cfg").innerHTML =
      "<tr><th style='text-align:left'>Origen (según el log)</th><th style='text-align:right'>Tokens 30 d</th><th style='text-align:left'>Plan al que pertenece</th></tr>" +
      filas.map(([k,v])=>`<tr><td><code>${k}</code></td><td style="text-align:right">${fmtTok(v)}</td>
        <td><input class="cfg-in" data-k="${k}" value="${(CFG.aliases[k]||"").replace(/"/g,"&quot;")}" placeholder="p. ej. NaN, OpenCode Go, z.ai" style="width:100%;padding:.35rem;border-radius:6px;border:1px solid #bbb"></td></tr>`).join("");
    document.getElementById("tabla-lim").innerHTML =
      "<tr><th style='text-align:left'>Plan</th><th style='text-align:right'>Límite 5 h</th><th style='text-align:right'>Límite semanal</th></tr>" +
      (CFG.proveedores||[]).map(pr=>`<tr><td>${pr.nombre}</td>
        <td style="text-align:right"><input class="lim-in" data-p="${pr.nombre}" data-c="limite_5h_tokens" value="${pr.limite_5h_tokens||0}" style="width:9rem;padding:.3rem;border-radius:6px;border:1px solid #bbb"></td>
        <td style="text-align:right"><input class="lim-in" data-p="${pr.nombre}" data-c="limite_semana_tokens" value="${pr.limite_semana_tokens||0}" style="width:9rem;padding:.3rem;border-radius:6px;border:1px solid #bbb"></td></tr>`).join("");
  }).catch(e=>{ document.getElementById("aviso-cfg").textContent = "No pude leer la configuración: "+e; });
}
function guardarCfg(){
  if(!CFG){ return; }
  document.querySelectorAll(".cfg-in").forEach(i=>{ CFG.aliases[i.dataset.k] = i.value.trim(); });
  document.querySelectorAll(".lim-in").forEach(i=>{
    const pr = (CFG.proveedores||[]).find(x=>x.nombre===i.dataset.p); if(pr){ pr[i.dataset.c] = parseInt(i.value||0,10)||0; }
  });
  fetch("/guardar", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(CFG)})
    .then(r=>r.json()).then(j=>{ document.getElementById("aviso-cfg").textContent = j.ok ? "✓ guardado; se aplica en el próximo refresco" : ("Error: "+(j.error||"?")); })
    .catch(e=>{ document.getElementById("aviso-cfg").textContent = "Error al guardar: "+e; });
}
pintaCfg();
</script>
"""


def _con_cfg(cuerpo):
    """Mete el configurador al final del panel."""
    return cuerpo.replace("</body>", SECCION_CFG + "</body>")


def _leer(nombre):
    return open(f"{ASSETS}/{nombre}", encoding="utf-8").read()


def datos():
    r = costbar.escanear()
    def b(x):
        return {"gi": int(x["gi"]), "go": int(x["go"]), "cr": int(x["cr"]), "cw": int(x["cw"]),
                "tok": int(x["tok"]), "usd": round(x["usd"], 2), "turnos": int(x["turnos"])}
    return {
        "generado": time.strftime("%d/%m/%Y %H:%M:%S"),
        "cinco_h": int(r["cinco_h"]["tok"]), "ritmo": int(r["ritmo"]["tok"]),
        "cache_pct": round(r["cache_pct_hoy"], 1),
        "hoy": b(r["hoy"]), "ayer": b(r["ayer"]), "semana": b(r["semana"]), "mes": b(r["mes"]),
        "limites": {k: int(v) for k, v in r["limites"].items() if k in ("limite_hora_tokens", "limite_5h_tokens", "limite_semana_tokens") and isinstance(v, (int, float))},
        "dias": {d: {"tok": int(v["tok"]), "turnos": int(v["turnos"]), "usd": round(v["usd"], 2), "cr": int(v["cr"])} for d, v in sorted(r["dias"].items())},
        "proveedores": {n: {"cinco_h": int(x["cinco_h"]), "hoy": int(x["hoy"]), "semana": int(x["semana"]),
                            "mes": int(x["mes"]), "pct_5h": round(x["pct_5h"], 1), "pct_semana": round(x["pct_semana"], 1),
                            "modelos": x["modelos"], "auto": bool(x.get("auto", False))}
                        for n, x in r["proveedores"].items()},
        "modelo_prov": r["modelo_prov"],
        "cruce": {n: {m: {d: int(t) for d, t in dd.items()} for m, dd in mm.items()} for n, mm in r["cruce"].items()},
        "proy_prov": {n: {p: int(t) for p, t in pp.items()} for n, pp in r["proy_prov"].items()},
        "proy_cruce": {n: {p: {d: int(t) for d, t in dd.items()} for p, dd in mm.items()} for n, mm in r.get("proy_cruce", {}).items()},
        "proyectos": {n: int(x["tok"]) for n, x in sorted(r["proyectos"].items(), key=lambda kv: -kv[1]["tok"])},
        "bases": r.get("bases") or [], "fuentes": r["fuentes"],
    }


def construye(d):
    """Ensambla el unico panel.html a partir de panel_assets/ (fuente separada, salida inline)."""
    cuerpo = _leer("plantilla.html")
    estilos = _leer("estilos.css")
    app_js = _leer("app.js").replace("__DATOS__", json.dumps(d, ensure_ascii=False))
    graficos_js = _leer("graficos.js")
    html = f"""<!doctype html><html lang="es" data-tema="claro"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="60"><title>Consumo de tokens</title>
<style>
{estilos}
</style></head><body>
{cuerpo}
<script>
{graficos_js}
{app_js}
</script></body></html>"""
    return html


def main():
    d = datos()
    open(SALIDA, "w", encoding="utf-8").write(_con_cfg(construye(d)))
    return SALIDA, d["hoy"]["tok"], d["ritmo"]


if __name__ == "__main__":
    p, tok, ritmo = main()
    print(f"panel: {p} ({os.path.getsize(p)} bytes) · hoy {costbar.fmt_tok(tok)} · ritmo {costbar.fmt_tok(ritmo)}")
