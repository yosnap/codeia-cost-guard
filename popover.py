#!/usr/bin/env python3
"""Genera el desplegable de CostBar con el estilo del diseno (brand-spec) y datos en vivo.

El estilo es el de OpenDesign (costbar.css): papel violeta-gris, cifras monoespaciadas y el
color reservado a codificar la magnitud (k / m / b).
"""
import json, os, re, subprocess, sys

AQUI = os.path.dirname(os.path.abspath(__file__))
PY = f"{AQUI}/venv/bin/python"
SALIDA = f"{AQUI}/popover.html"


def informe():
    r = subprocess.run([PY, f"{AQUI}/costbar.py", "--print"], capture_output=True, text=True, timeout=300)
    return r.stdout or ""


def trocea(txt):
    """Acepta '338,9 M' o '338900000' y devuelve ('338,9', 'M')."""
    m = re.match(r"\s*([\d.,]+)\s*([KMB]?)", str(txt))
    if not m:
        return ("0", "")
    n = float(m.group(1).replace(".", "").replace(",", "."))
    u = m.group(2)
    if u:
        return (f"{n:.1f}".replace(".", ","), u)
    for u2, f in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= f:
            return (f"{n / f:.1f}".replace(".", ","), u2)
    return (f"{n:.0f}", "")


def cifra(txt):
    """Devuelve el HTML de una cifra con la clase de su magnitud."""
    num, u = trocea(txt)
    return f'<span class="val {(u or "k").lower()}">{num}<span class="u">{u}</span></span>'


def main():
    tema = ""
    if "--tema" in sys.argv:
        i = sys.argv.index("--tema")
        if i + 1 < len(sys.argv) and sys.argv[i + 1] == "oscuro":
            tema = " theme-dark"
    t = informe()
    lineas = t.split("\n")

    def busca(pat, defecto=""):
        m = re.search(pat, t)
        return m.group(1).strip() if m else defecto

    cinco = busca(r"VENTANA 5 H\s+([\d.,]+\s*[KMB]?)")
    semana = busca(r"SEMANA\s+([\d.,]+\s*[KMB]?)")
    sem_turnos = busca(r"SEMANA\s+[\d.,]+\s*[KMB]? tokens \(([\d]+)")
    hoy = busca(r"HOY\s+([\d.,]+\s*[KMB]?)")
    hoy_turnos = busca(r"HOY\s+[\d.,]+\s*[KMB]? tokens \(([\d]+)")
    ent = busca(r"entrada ([\d.,]+\s*[KMB]?)")
    sal = busca(r"salida ([\d.,]+\s*[KMB]?)")
    clec = busca(r"cache leida ([\d.,]+\s*[KMB]?)")
    cporc = busca(r"cache leida [\d.,]+\s*[KMB]? \(([\d]+) %\)")
    cesc = busca(r"cache escrita ([\d.,]+\s*[KMB]?)")

    planes = []
    for mp in re.finditer(r"([^:\u00b7]+): 5h ([\d.,]+\s*[KMB]?) \u00b7 sem ([\d.,]+\s*[KMB]?)", t):
        planes.append((mp.group(1).strip(), mp.group(2).strip(), mp.group(3).strip()))

    proyectos = []
    mp2 = re.search(r"proyectos \(mes\): (.*)", t)
    if mp2:
        for parte in mp2.group(1).split(" \u00b7 ")[:4]:
            mm = re.match(r"([^:]+): ([\d.,]+\s*[KMB]?)", parte.strip())
            if mm:
                proyectos.append((mm.group(1).strip(), mm.group(2).strip()))

    modelos = []
    m = re.search(r"modelos hoy: (.*)", t)
    if m:
        for parte in m.group(1).split(" \u00b7 ")[:4]:
            mm = re.match(r"([^:]+): ([\d.,]+\s*[KMB]?)", parte.strip())
            if mm:
                modelos.append((mm.group(1).strip(), mm.group(2).strip()))

    maxp = 1.0
    for _, _, s in planes:
        v = s.replace(".", "").replace(",", ".")
        try:
            maxp = max(maxp, float(v))
        except Exception:
            pass

    def fila_plan(n, c5, s):
        try:
            pct = min(100.0, float(s.replace(".", "").replace(",", ".")) / maxp * 100)
        except Exception:
            pct = 0
        return (f'<div class="plan"><div class="plan-top"><span class="plan-name">{n}</span>'
                f'{cifra(s)}</div>'
                f'<div class="bar"><i style="width:{pct:.0f}%"></i></div>'
                f'<div class="plan-sub">5 h {c5}</div></div>')

    filas_mod = "".join(f'<div class="row"><span class="row-lab">{n}</span>{cifra(v)}</div>' for n, v in modelos)

    html = f"""<!doctype html>
<html lang="es"><meta charset="utf-8"><title>CostBar</title>
<link rel="stylesheet" href="costbar.css">

html,body{{margin:0;background:#ffffff}}
.stage{{padding:0}}
</style>
<div class="stage"><div class="popover{tema}">
  <section class="sec">
    <div class="sec-head"><span class="sec-title">ultimas 5 h</span><span class="sec-note">semana {sem_turnos} turnos</span></div>
    <div class="hero">
      <span class="hero-val m">{trocea(cinco)[0]}<span class="u">{trocea(cinco)[1]}</span></span>
      <div class="hero-side"><span class="hero-lab">semana</span><span class="hero-pct">{trocea(semana)[0]} {trocea(semana)[1]}</span></div>
    </div>
  </section>
  <section class="sec">
    <div class="sec-head"><span class="sec-title">por plan</span><span class="sec-note">semana</span></div>
    <div class="pad">{"".join(fila_plan(n, c, s) for n, c, s in planes)}</div>
  </section>
  <section class="sec">
    <div class="sec-head"><span class="sec-title">hoy</span><span class="sec-note">{hoy_turnos} turnos</span></div>
    <div class="pad">
      <div class="row"><span class="row-lab">total</span>{cifra(hoy)}</div>
      <div class="row"><span class="row-lab">entrada</span>{cifra(ent)}</div>
      <div class="row"><span class="row-lab">salida</span>{cifra(sal)}</div>
      <div class="row"><span class="row-lab">cache leida ({cporc} %)</span>{cifra(clec)}</div>
      <div class="row"><span class="row-lab">cache escrita</span>{cifra(cesc)}</div>
    </div>
  </section>
  <section class="sec">
    <div class="sec-head"><span class="sec-title">modelos hoy</span></div>
    <div class="pad">{filas_mod}</div>
  </section>
  <section class="sec">
    <div class="sec-head"><span class="sec-title">proyectos (mes)</span></div>
    <div class="pad">{"".join(f'<div class="row"><span class="row-lab">{n}</span>{cifra(v)}</div>' for n, v in proyectos)}</div>
  </section>
  <div class="actions"><span class="btn btn-primary">Abrir panel</span><span class="btn">Refrescar</span><span class="btn">Salir</span></div>
</div></div></html>"""
    salida = SALIDA if not tema else SALIDA.replace(".html", "_oscuro.html")
    open(salida, "w", encoding="utf-8").write(html)
    return salida
    return SALIDA


if __name__ == "__main__":
    print(main())
