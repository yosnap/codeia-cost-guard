#!/usr/bin/env python3
"""Genera panel.html: el panel amigable de gasto, se refresca solo cada 60 s."""
import html, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costbar

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = f"{AQUI}/panel.html"

def barras(datos, unidad="USD", maximo=None):
    if not datos: return "<p class='vacio'>sin datos</p>"
    m = maximo or max(v for _, v in datos) or 1
    filas = []
    for k, v in datos:
        pct = max(v / m * 100, 1.5)
        filas.append(f"""<div class="fila"><span class="et">{html.escape(str(k))}</span>
        <span class="pista"><span class="barra" style="width:{pct:.1f}%"></span></span>
        <span class="val">{v:,.2f} {unidad}</span></div>""")
    return "\n".join(filas)

def main():
    r = costbar.escanear()
    hoy, ritmo, semana = r["hoy"], r["ritmo"], r["semana"]
    ayer = r["ayer"]
    dias = sorted(r["dias"].items())[-14:]
    mods = sorted(r["modelos"].items(), key=lambda x: -x[1])[:8]
    proys = sorted(r["proyectos"].items(), key=lambda x: -x[1])[:10]
    lim = r["limites"]
    aviso = ""
    if ritmo >= lim["limite_hora_usd"]:
        aviso = f"<p class='alerta'>Ritmo alto: {ritmo:,.0f} USD/h (limite {lim['limite_hora_usd']:.0f}). Baja el fan-out o compacta el contexto.</p>"
    html_txt = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="60">
<title>Gasto de tokens</title>
<style>
 :root {{ --marca:#ae00ff; --marca2:#ff008c; --fondo:#0e0b14; --panel:#171226; --texto:#efeaf7; --suave:#9d93b5; }}
 * {{ box-sizing:border-box; }}
 body {{ margin:0; padding:28px; background:var(--fondo); color:var(--texto);
        font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text",Segoe UI,sans-serif; }}
 h1 {{ font-size:19px; margin:0 0 4px; letter-spacing:.2px; }}
 .sub {{ color:var(--suave); font-size:13px; margin-bottom:22px; }}
 .rejilla {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:14px; margin-bottom:22px; }}
 .tarjeta {{ background:linear-gradient(160deg,var(--panel),#120e1d); border:1px solid #241a3a;
            border-radius:16px; padding:18px 20px; }}
 .tarjeta .rot {{ color:var(--suave); font-size:12px; text-transform:uppercase; letter-spacing:.8px; }}
 .tarjeta .num {{ font-size:34px; font-weight:650; margin-top:6px;
                 background:linear-gradient(90deg,var(--marca),var(--marca2)); -webkit-background-clip:text;
                 -webkit-text-fill-color:transparent; }}
 .tarjeta .pie {{ color:var(--suave); font-size:12px; margin-top:4px; }}
 section {{ background:var(--panel); border:1px solid #241a3a; border-radius:16px; padding:18px 20px; margin-bottom:16px; }}
 h2 {{ font-size:14px; margin:0 0 14px; color:var(--suave); text-transform:uppercase; letter-spacing:.9px; }}
 .fila {{ display:grid; grid-template-columns:minmax(120px,34%) 1fr 116px; align-items:center; gap:12px; padding:4px 0; }}
 .et {{ font-size:13px; color:var(--texto); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
 .pista {{ background:#0b0813; border-radius:7px; height:16px; overflow:hidden; }}
 .barra {{ display:block; height:100%; background:linear-gradient(90deg,var(--marca),var(--marca2)); }}
 .val {{ text-align:right; font-variant-numeric:tabular-nums; color:var(--suave); font-size:13px; }}
 .alerta {{ background:#3a0f26; border:1px solid var(--marca2); padding:12px 14px; border-radius:12px; margin:0 0 18px; }}
 .vacio {{ color:var(--suave); }}
 footer {{ color:var(--suave); font-size:12px; margin-top:18px; }}
</style></head><body>
<h1>Gasto de los agentes</h1>
<p class="sub">Generado {time.strftime('%d/%m/%Y %H:%M:%S')} · se actualiza solo cada minuto · fuente: logs locales de Claude Code, Codex y OpenCode (turnos duplicados por forks descartados)</p>
{aviso}
<div class="rejilla">
  <div class="tarjeta"><div class="rot">Hoy</div><div class="num">{hoy:,.2f} $</div><div class="pie">equivalente API</div></div>
  <div class="tarjeta"><div class="rot">Ultima hora</div><div class="num">{ritmo:,.2f} $</div><div class="pie">limite {lim['limite_hora_usd']:.0f} $/h</div></div>
  <div class="tarjeta"><div class="rot">Ayer</div><div class="num">{ayer:,.2f} $</div><div class="pie">mismo estilo de jornada</div></div>
  <div class="tarjeta"><div class="rot">7 dias</div><div class="num">{semana:,.2f} $</div><div class="pie">{len(dias)} dias con actividad</div></div>
</div>
<section><h2>Por dia</h2>{barras([(d[5:], v) for d, v in dias])}</section>
<section><h2>Por modelo (hoy)</h2>{barras(mods)}</section>
<section><h2>Por proyecto (7 dias)</h2>{barras(proys)}</section>
<footer>Los dolares son <b>equivalente API</b>: con suscripcion no se paga por token, lo que se agota es la cuota por turnos x contexto.
El gasto lo manda el contexto releido en cada turno y el numero de agentes en paralelo, no el effort del modelo.</footer>
</body></html>"""
    open(SALIDA, "w", encoding="utf-8").write(html_txt)
    return SALIDA, hoy, ritmo

if __name__ == "__main__":
    p, h, r = main()
    print(f"panel: {p} ({os.path.getsize(p)} bytes) · hoy {h:.2f} USD · ritmo {r:.2f}/h")
