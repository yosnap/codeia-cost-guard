#!/usr/bin/env python3
"""Genera el desplegable de CostBar con el estilo del diseno (Open Design) y datos en vivo.

El estilo es el de OpenDesign (costbar.css): papel violeta-gris, cifras monoespaciadas y el
color reservado a codificar la magnitud (k / m / b). Los datos salen de costbar.escanear(),
importado como modulo (no se parsea texto): da acceso directo a los porcentajes de limite,
el historial (ayer, hace 2-5 dias) y los turnos que el informe de texto no lleva.
"""
import os, re, sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import costbar

SALIDA = f"{AQUI}/popover.html"


def cifra(n, grande=False):
    """Numero crudo (18300000) -> HTML con la clase de su magnitud (k/m/b)."""
    txt = costbar.fmt_tok(n)
    m = re.match(r"([\d.,]+)\s*([KMB]?)$", txt)
    num, u = (m.group(1), m.group(2)) if m else (txt, "")
    clases = f"val{' val-lg' if grande else ''} {(u or 'k').lower()}"
    unidad = f'<span class="u"> {u}</span>' if u else ""
    return f'<span class="{clases}">{num}{unidad}</span>'


def entero(n):
    """Un recuento (turnos): separador de miles con punto, escala K (violeta suave)."""
    txt = f"{int(n or 0):,}".replace(",", ".")
    return f'<span class="val k">{txt}</span>'


def sin_dato():
    return '<span class="val">—</span>'


def fila(etiqueta, valor_html):
    return f'<div class="row"><span class="row-lab">{etiqueta}</span>{valor_html}</div>'


def fila_plan(nombre, x):
    """Una fila de la seccion Planes: cabecera 5h, barra de % de limite, subfila 5h/semana."""
    pct = x["pct_5h"] or x["pct_semana"]
    aria = f"{nombre}: {pct:.0f} % del limite" if pct else f"{nombre}: sin datos de porcentaje"
    return (f'<div class="plan"><div class="plan-top"><span class="plan-name">{nombre}</span>'
            f'{cifra(x["cinco_h"])}</div>'
            f'<div class="bar" role="img" aria-label="{aria}"><i style="width:{min(100, pct):.0f}%"></i></div>'
            f'<div class="plan-sub"><span>5 h {costbar.fmt_tok(x["cinco_h"])} '
            f'· semana {costbar.fmt_tok(x["semana"])}</span>'
            f'<span>{f"{pct:.0f} %" if pct else "—"}</span></div></div>')


def fila_mini(nombre, valor):
    return (f'<div class="mini"><div class="mini-top"><span class="mini-name">{nombre}</span>'
            f'{cifra(valor)}</div></div>')


def informe():
    """costbar.escanear() ya trae porcentajes, historial y turnos: nada que parsear."""
    return costbar.escanear()


def render(r, oscuro=False):
    tema = " theme-dark" if oscuro else ""
    lim5 = r["limites"].get("limite_5h_tokens") or 0
    pct5 = r["pct_5h"]

    hero_lado, hero_bar = "", ""
    if lim5:
        hero_lado = (f'<div class="hero-side"><span class="hero-pct">{pct5:.0f} %</span><br>'
                     f'<span class="hero-lab">del limite</span></div>')
        hero_bar = (f'<div class="bar" role="img" aria-label="{pct5:.0f} % del limite de 5 horas">'
                    f'<i style="width:{min(100, pct5):.0f}%"></i></div>')

    planes = [(n, x) for n, x in r["proveedores"].items() if x["cinco_h"] or x["semana"]]

    ayer = r["ayer"]["tok"]
    dias_2a5 = [costbar.time.strftime("%Y-%m-%d", costbar.time.localtime(costbar.time.time() - i * 86400))
                for i in (2, 3, 4, 5)]
    hay_2a5 = any(d in r["dias"] for d in dias_2a5)
    tok_2a5 = sum(r["dias"].get(d, {}).get("tok", 0) for d in dias_2a5)

    tema_cfg = (r["limites"].get("tema") or "sistema").strip().lower()
    chips_tema = "".join(f'<span class="chip{" on" if k == tema_cfg else ""}">{et}</span>'
                         for k, et in (("claro", "Claro"), ("oscuro", "Oscuro"), ("sistema", "Sistema")))

    modelos = sorted(r["modelos_hoy"].items(), key=lambda x: -x[1]["tok"])[:4]
    proyectos = sorted(r["proyectos"].items(), key=lambda x: -x[1]["tok"])[:4]

    html = f"""<!doctype html>
<html lang="es"><meta charset="utf-8"><title>CostBar</title>
<link rel="stylesheet" href="costbar.css">
<style>
/* fondo transparente y tarjeta plana: el PNG se recorta por alfa a la tarjeta exacta y el marco
   (esquinas, sombra) lo pone el popover nativo de macOS */
html,body{{margin:0;background:transparent}}
.stage{{padding:0;justify-content:flex-start}}
.popover{{margin:0;border:0;border-radius:0;box-shadow:none}}
/* selector de tema: una fila de chips pegada al pie de acciones */
.tema{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:10px 16px 0;background:var(--surface);border-top:1px solid var(--border)}}
.tema .chip{{min-height:26px;display:inline-flex;align-items:center;justify-content:center;border-radius:13px;border:1px solid var(--border);background:var(--bg);color:var(--muted);font:500 11px/1 -apple-system,"SF Pro Text",sans-serif}}
.tema .chip.on{{border-color:var(--violet);color:var(--violet);font-weight:600}}
.actions{{border-top:0}}
</style>
<div class="stage"><div class="popover{tema}">
  <section class="sec">
    <div class="sec-head"><span class="sec-title">Últimas 5 h</span><span class="sec-note">ventana en curso</span></div>
    <div class="hero">
      <div>{cifra(r["cinco_h"]["tok"])}</div>
      {hero_lado}
    </div>
    {hero_bar}
    <div class="pad"><div class="rows">
      {fila("Semana", cifra(r["semana"]["tok"], grande=True))}
      {fila("Turnos semana", entero(r["semana"]["turnos"]))}
    </div></div>
  </section>
  <section class="sec">
    <div class="sec-head"><span class="sec-title">Planes</span><span class="sec-note">5 h · semana · % límite</span></div>
    <div class="pad">{"".join(fila_plan(n, x) for n, x in planes)}</div>
  </section>
  <section class="sec">
    <div class="sec-head"><span class="sec-title">Hoy</span><span class="sec-note">desglose</span></div>
    <div class="pad">
      <div class="rows">
        {fila("Total", cifra(r["hoy"]["tok"], grande=True))}
        {fila("Turnos", entero(r["hoy"]["turnos"]))}
      </div>
      <div class="split"><div class="rows">
        {fila("Entrada", cifra(r["hoy"]["gi"]))}
        {fila("Salida", cifra(r["hoy"]["go"]))}
        {fila(f'Caché leída · {r["cache_pct_hoy"]:.0f} %', cifra(r["hoy"]["cr"]))}
        {fila("Caché escrita", cifra(r["hoy"]["cw"]))}
      </div></div>
    </div>
  </section>
  <section class="sec">
    <div class="sec-head"><span class="sec-title">Historial</span><span class="sec-note">ayer + últimos 5 días</span></div>
    <div class="pad"><div class="rows">
      {fila("Ayer", cifra(ayer, grande=True) if r["dias"].get(costbar.time.strftime("%Y-%m-%d", costbar.time.localtime(costbar.time.time() - 86400))) else sin_dato())}
      {fila("Hace 2 a 5 días", cifra(tok_2a5) if hay_2a5 else sin_dato())}
    </div></div>
  </section>
  <div class="duo">
    <section class="sec">
      <div class="sec-head"><span class="sec-title">Modelos</span></div>
      <div class="pad">{"".join(fila_mini(costbar.corto(n), v["tok"]) for n, v in modelos)}</div>
    </section>
    <section class="sec">
      <div class="sec-head"><span class="sec-title">Proyectos</span></div>
      <div class="pad">{"".join(fila_mini(n, v["tok"]) for n, v in proyectos)}</div>
    </section>
  </div>
  <div class="tema">{chips_tema}</div>
  <footer class="actions">
    <button type="button" class="btn btn-primary">Abrir panel</button>
    <button type="button" class="btn">Guía</button>
    <button type="button" class="btn">Refrescar</button>
    <button type="button" class="btn">Salir</button>
  </footer>
</div></div></html>"""
    return html


def main():
    """Un solo escaneo, los dos temas: popover.html (claro) y popover_oscuro.html."""
    r = informe()
    for oscuro in (False, True):
        salida = SALIDA.replace(".html", "_oscuro.html") if oscuro else SALIDA
        open(salida, "w", encoding="utf-8").write(render(r, oscuro=oscuro))
    return SALIDA


if __name__ == "__main__":
    print(main())
