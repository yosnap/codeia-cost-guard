#!/usr/bin/env python3
"""Genera panel.html: el panel de consumo, en tokens y en vivo (se refresca solo cada 60 s).

Pone en primer plano la ventana de 5 horas y la semana, que es lo que se agota en las
suscripciones, y deja los dolares solo como equivalente API informativo.
"""
import html, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costbar

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = f"{AQUI}/panel.html"


def barras(datos):
    if not datos:
        return "<p class='vacio'>sin datos</p>"
    m = max(v for _, v in datos) or 1
    filas = []
    for k, v in datos:
        pct = max(v / m * 100, 1.5)
        filas.append(f"""<div class="fila"><span class="et">{html.escape(str(k))}</span>
        <span class="pista"><span class="barra" style="width:{pct:.1f}%"></span></span>
        <span class="val">{costbar.fmt_tok(v)}</span></div>""")
    return "\n".join(filas)


def progreso(valor, limite, etiqueta):
    if not limite:
        return ""
    pct = min(valor / limite * 100, 100)
    return f"""<section><h2>{etiqueta}</h2>
      <div class="pista alta"><span class="barra" style="width:{pct:.1f}%"></span></div>
      <p class="sub2">{costbar.fmt_tok(valor)} de {costbar.fmt_tok(limite)} · {pct:.0f} %</p></section>"""


def main():
    r = costbar.escanear()
    h, c5, sem = r["hoy"], r["cinco_h"], r["semana"]
    dias = sorted(r["dias"].items())[-14:]
    mods = sorted(((costbar.corto(k), v["tok"]) for k, v in r["modelos_hoy"].items()), key=lambda x: -x[1])[:8]
    proys = sorted(((k, v["tok"]) for k, v in r["proyectos"].items()), key=lambda x: -x[1])[:10]
    lim = r["limites"]
    fams = " · ".join(f"{k}: {costbar.fmt_tok(v)}" for k, v in sorted(c5["fam"].items(), key=lambda x: -x[1]) if v)
    aviso = ""
    if r["ritmo"]["tok"] >= lim["limite_hora_tokens"]:
        aviso = (f"<p class='alerta'>Ritmo alto: {costbar.fmt_tok(r['ritmo']['tok'])} tokens en la ultima hora "
                 f"(limite {costbar.fmt_tok(lim['limite_hora_tokens'])}). Menos turnos, menos contexto o menos agentes a la vez.</p>")
    tarjetas = "\n".join(f"""<div class="tarjeta{' grande' if i < 2 else ''}">
        <div class="rot">{t}</div><div class="num">{v}</div><div class="pie">{p}</div></div>"""
        for i, (t, v, p) in enumerate([
            ("Ultimas 5 horas", costbar.fmt_tok(c5["tok"]), fams or "la ventana que se agota"),
            ("Semana", costbar.fmt_tok(sem["tok"]), f"{sem['turnos']} turnos"),
            ("Hoy", costbar.fmt_tok(h["tok"]), f"{h['turnos']} turnos"),
            ("Cache leida", f"{r['cache_pct_hoy']:.0f} %", f"{costbar.fmt_tok(h['cr'])} de la entrada"),
            ("Ultima hora", costbar.fmt_tok(r["ritmo"]["tok"]), f"limite {costbar.fmt_tok(lim['limite_hora_tokens'])}"),
            ("Ayer", costbar.fmt_tok(r["ayer"]["tok"]), f"{r['ayer']['turnos']} turnos"),
        ]))
    detalle = "\n".join(f"""<div class="mini"><span>{t}</span><b>{v}</b></div>""" for t, v in (
        ("Entrada hoy", costbar.fmt_tok(h["gi"])), ("Salida hoy", costbar.fmt_tok(h["go"])),
        ("Cache leida hoy", costbar.fmt_tok(h["cr"])), ("Cache escrita hoy", costbar.fmt_tok(h["cw"])),
    ))

    html_txt = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="60">
<title>Consumo de tokens</title>
<style>
 :root {{ --marca:#ae00ff; --marca2:#ff008c; --fondo:#0e0b14; --panel:#171226; --texto:#efeaf7; --suave:#9d93b5; }}
 * {{ box-sizing:border-box; }}
 body {{ margin:0; padding:28px; background:var(--fondo); color:var(--texto);
        font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text",Segoe UI,sans-serif; }}
 h1 {{ font-size:19px; margin:0 0 4px; }}
 .sub {{ color:var(--suave); font-size:13px; margin-bottom:20px; }}
 .sub2 {{ color:var(--suave); font-size:13px; margin:10px 0 0; }}
 .rejilla {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(205px,1fr)); gap:14px; margin-bottom:14px; }}
 .tarjeta {{ background:linear-gradient(160deg,var(--panel),#120e1d); border:1px solid #241a3a; border-radius:16px; padding:18px 20px; }}
 .tarjeta.grande .num {{ font-size:38px; }}
 .tarjeta .rot {{ color:var(--suave); font-size:12px; text-transform:uppercase; letter-spacing:.8px; }}
 .tarjeta .num {{ font-size:30px; font-weight:650; margin-top:6px;
                 background:linear-gradient(90deg,var(--marca),var(--marca2)); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
 .tarjeta .pie {{ color:var(--suave); font-size:12px; margin-top:4px; }}
 .mini {{ display:flex; justify-content:space-between; background:var(--panel); border:1px solid #241a3a;
         border-radius:12px; padding:12px 18px; margin-bottom:16px; }}
 .mini span {{ color:var(--suave); font-size:13px; }}
 section {{ background:var(--panel); border:1px solid #241a3a; border-radius:16px; padding:18px 20px; margin-bottom:16px; }}
 h2 {{ font-size:14px; margin:0 0 14px; color:var(--suave); text-transform:uppercase; letter-spacing:.9px; }}
 .fila {{ display:grid; grid-template-columns:minmax(120px,32%) 1fr 110px; align-items:center; gap:12px; padding:4px 0; }}
 .et {{ font-size:13px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
 .pista {{ background:#0b0813; border-radius:7px; height:16px; overflow:hidden; }}
 .pista.alta {{ height:20px; }}
 .barra {{ display:block; height:100%; background:linear-gradient(90deg,var(--marca),var(--marca2)); }}
 .val {{ text-align:right; font-variant-numeric:tabular-nums; color:var(--suave); font-size:13px; }}
 .alerta {{ background:#3a0f26; border:1px solid var(--marca2); padding:12px 14px; border-radius:12px; margin:0 0 18px; }}
 .vacio {{ color:var(--suave); }}
 footer {{ color:var(--suave); font-size:12px; margin-top:18px; }}
</style></head><body>
<h1>Consumo de tokens</h1>
<p class="sub">Generado {time.strftime('%d/%m/%Y %H:%M:%S')} · se actualiza solo cada minuto · logs locales de Claude Code, Codex y OpenCode ({r['fuentes']} ficheros), turnos duplicados por forks descartados</p>
{aviso}
<div class="rejilla">{tarjetas}</div>
{detalle}
{progreso(c5["tok"], lim.get("limite_5h_tokens"), "Ventana de 5 horas")}
{progreso(sem["tok"], lim.get("limite_semana_tokens"), "Semana")}
<section><h2>Por dia (tokens)</h2>{barras([(d[5:], v["tok"]) for d, v in dias])}</section>
<section><h2>Por modelo (hoy, tokens)</h2>{barras(mods)}</section>
<section><h2>Por proyecto (7 dias, tokens)</h2>{barras(proys)}</section>
<footer>Los <b>turnos</b> son idas y vueltas con el modelo: cada uno reenvia el contexto, y por eso la mayor parte del
consumo es <b>cache leida</b>. Menos turnos y menos contexto pesan mas que bajar el <i>effort</i>.
El equivalente API ({h['usd']:.1f} USD hoy · {sem['usd']:.0f} USD en 7 dias) es solo informativo: con suscripcion
no se paga por token, se agota la cuota. Para ver porcentajes en 5 h y semana, pon los limites en
<code>config.json</code> (<code>limite_5h_tokens</code>, <code>limite_semana_tokens</code>).</footer>
</body></html>"""
    open(SALIDA, "w", encoding="utf-8").write(html_txt)
    return SALIDA, h["tok"], r["ritmo"]["tok"]


if __name__ == "__main__":
    p, tok, ritmo = main()
    print(f"panel: {p} ({os.path.getsize(p)} bytes) · hoy {costbar.fmt_tok(tok)} tokens · ritmo {costbar.fmt_tok(ritmo)}")
