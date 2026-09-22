#!/usr/bin/env python3
"""Genera panel.html: panel de consumo en tokens, con modo claro/oscuro y filtros.

Todo se calcula aqui (Python) y se embute como JSON: el HTML no necesita red ni dependencias.
Se refresca solo cada 60 s.
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costbar

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = f"{AQUI}/panel.html"


def datos():
    r = costbar.escanear()
    def bloque(x):
        return {k: x[k] for k in ("gi", "go", "cr", "cw", "tok", "usd")} | {"turnos": int(x["turnos"])}
    dias = {d: {"tok": int(v["tok"]), "turnos": int(v["turnos"]), "usd": round(v["usd"], 2),
                "cr": int(v["cr"]), "gi": int(v["gi"])} for d, v in sorted(r["dias"].items())}
    provs = {n: {"cinco_h": int(x["cinco_h"]), "hoy": int(x["hoy"]), "semana": int(x["semana"]),
                 "mes": int(x["mes"]), "pct_5h": round(x["pct_5h"], 1), "pct_semana": round(x["pct_semana"], 1),
                 "modelos": x["modelos"]} for n, x in r["proveedores"].items()}
    mods = {periodo: {m: int(dic["tok"]) for m, dic in sorted(bl.items(), key=lambda kv: -kv[1]["tok"])}
            for periodo, bl in (("hoy", r["modelos_hoy"]), ("mes", r["modelos_mes"]))}
    proyectos = {n: int(x["tok"]) for n, x in sorted(r["proyectos"].items(), key=lambda kv: -kv[1]["tok"])}
    return {"generado": time.strftime("%d/%m/%Y %H:%M:%S"),
            "cinco_h": int(r["cinco_h"]["tok"]), "hoy": bloque(r["hoy"]), "ayer": bloque(r["ayer"]),
            "semana": bloque(r["semana"]), "mes": bloque(r["mes"]),
            "ritmo": int(r["ritmo"]["tok"]), "cache_pct": round(r["cache_pct_hoy"], 1),
            "limites": {k: int(v) if isinstance(v, int) else v for k, v in r["limites"].items() if k in ("limite_hora_tokens", "limite_5h_tokens", "limite_semana_tokens")},
            "dias": dias, "proveedores": provs, "modelos": mods,
            "modelo_dia": {m: {d: int(t) for d, t in v.items()} for m, v in r["modelo_dia"].items()},
            "proyectos": proyectos, "fuentes": r["fuentes"]}


PLANTILLA = """<!doctype html><html lang="es" data-tema="claro"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="60">
<title>Consumo de tokens</title>
<style>
 :root[data-tema="claro"]{--fondo:#f4f2f8;--panel:#fff;--borde:#e4dff0;--texto:#1c1726;--suave:#6d6485;--pista:#ece7f5;--sombra:0 1px 3px rgba(30,20,60,.07)}
 :root[data-tema="oscuro"]{--fondo:#0e0b14;--panel:#171226;--borde:#241a3a;--texto:#efeaf7;--suave:#9d93b5;--pista:#0b0813;--sombra:none}
 :root{--marca:#ae00ff;--marca2:#ff008c;--ok:#12b886;--aviso:#ff5c8a}
 *{box-sizing:border-box}
 body{margin:0;padding:26px;background:var(--fondo);color:var(--texto);font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text",Segoe UI,sans-serif}
 .cab{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:18px}
 h1{font-size:20px;margin:0 0 4px}
 .sub{color:var(--suave);font-size:12.5px}
 button{font:inherit;font-size:13px;padding:7px 13px;border-radius:10px;border:1px solid var(--borde);background:var(--panel);color:var(--texto);cursor:pointer}
 button:hover{border-color:var(--marca)}
 button.on{background:linear-gradient(90deg,var(--marca),var(--marca2));color:#fff;border-color:transparent}
 .filtros{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 18px}
 .rejilla{display:grid;grid-template-columns:repeat(auto-fit,minmax(196px,1fr));gap:13px;margin-bottom:14px}
 .tarjeta{background:var(--panel);border:1px solid var(--borde);border-radius:16px;padding:16px 18px;box-shadow:var(--sombra)}
 .tarjeta .rot{font-size:11.5px;text-transform:uppercase;letter-spacing:.8px;color:var(--suave)}
 .tarjeta .num{font-size:30px;font-weight:680;margin-top:5px;background:linear-gradient(90deg,var(--marca),var(--marca2));-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
 .tarjeta.g .num{font-size:36px}
 .tarjeta .pie{font-size:12px;color:var(--suave);margin-top:3px}
 section{background:var(--panel);border:1px solid var(--borde);border-radius:16px;padding:16px 18px;margin-bottom:14px;box-shadow:var(--sombra)}
 h2{font-size:13px;margin:0 0 12px;color:var(--suave);text-transform:uppercase;letter-spacing:.9px}
 .fila{display:grid;grid-template-columns:minmax(130px,34%) 1fr 118px;align-items:center;gap:12px;padding:3.5px 0}
 .et{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .pista{background:var(--pista);border-radius:7px;height:15px;overflow:hidden}
 .barra{display:block;height:100%;border-radius:7px;background:linear-gradient(90deg,var(--marca),var(--marca2))}
 .val{text-align:right;font-variant-numeric:tabular-nums;color:var(--suave);font-size:12.5px}
 table{width:100%;border-collapse:collapse;font-size:13px}
 th,td{text-align:right;padding:6px 8px;border-bottom:1px solid var(--borde);font-variant-numeric:tabular-nums}
 th{color:var(--suave);font-weight:500;font-size:11.5px;text-transform:uppercase;letter-spacing:.6px}
 th:first-child,td:first-child{text-align:left}
 .punto{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:8px;vertical-align:middle}
 .mat{overflow-x:auto}
 .mat table{font-size:12px}
 .mat td.celda{color:#fff}
 .mat td.cero{color:var(--suave);background:transparent!important}
 footer{color:var(--suave);font-size:12px;margin-top:16px}
 .vacio{color:var(--suave)}
</style></head><body>
<div class="cab">
  <div><h1>Consumo de tokens</h1>
  <div class="sub" id="sub"></div></div>
  <div><button id="tema" onclick="cambiarTema()">Modo oscuro</button></div>
</div>
<div class="filtros" id="filtros"></div>
<div class="rejilla" id="kpis"></div>
<div class="rejilla" id="detalle"></div>
<div id="tablas"></div>
<footer id="pie"></footer>
<script>
const D = __DATOS__;
let periodo = "mes", proveedor = "todos";

function fmt(n){
  n = Number(n)||0;
  if (n>=1e9) return (n/1e9).toFixed(1).replace(".",",")+" B";
  if (n>=1e6) return (n/1e6).toFixed(1).replace(".",",")+" M";
  if (n>=1e3) return (n/1e3).toFixed(0)+" K";
  return String(Math.round(n));
}
const dias = Object.keys(D.dias);
const color = (i) => ["#ae00ff","#ff008c","#12b886","#4c6ef5","#f59f00","#e8590c","#0ca678","#7048e8","#d6336c","#1098ad"][i%10];

function cambiarTema(){
  const r = document.documentElement;
  const nuevo = r.getAttribute("data-tema")==="claro" ? "oscuro":"claro";
  r.setAttribute("data-tema", nuevo);
  localStorage.setItem("tema", nuevo);
  document.getElementById("tema").textContent = nuevo==="claro" ? "Modo oscuro":"Modo claro";
}
if (localStorage.getItem("tema")) document.documentElement.setAttribute("data-tema", localStorage.getItem("tema"));

function total(periodo){
  if (periodo==="5h") return D.cinco_h;
  return D[periodo] ? D[periodo].tok : 0;
}
function turnos(periodo){ return (D[periodo] && D[periodo].turnos) || 0; }

function pintarKpis(){
  const t5 = D.limites.limite_5h_tokens ? " · " + (D.cinco_h/D.limites.limite_5h_tokens*100).toFixed(0) + " % de su cuota" : "";
  const tarjetas = [
    ["Ultimas 5 horas", fmt(D.cinco_h), "tokens" + t5, true],
    ["Hoy", fmt(D.hoy.tok), turnos("hoy")+" turnos", true],
    ["Semana", fmt(D.semana.tok), turnos("semana")+" turnos", false],
    ["Mes (30 dias)", fmt(D.mes.tok), turnos("mes")+" turnos", false],
  ];
  document.getElementById("kpis").innerHTML = tarjetas.map(
    t => `<div class="tarjeta${t[3]?" g":""}"><div class="rot">${t[0]}</div><div class="num">${t[1]}</div><div class="pie">${t[2]}</div></div>`).join("");
  const h = D.hoy;
  document.getElementById("detalle").innerHTML = [
    ["Cache leida", D.cache_pct+" %", fmt(h.cr)+" de la entrada"],
    ["Entrada / salida", fmt(h.gi)+" / "+fmt(h.go), "hoy"],
    ["Cache escrita", fmt(h.cw), "hoy"],
    ["Ritmo ultima hora", fmt(D.ritmo), "limite "+fmt(D.limites.limite_hora_tokens)+"/h"],
  ].map(t => `<div class="tarjeta"><div class="rot">${t[0]}</div><div class="num">${t[1]}</div><div class="pie">${t[2]}</div></div>`).join("");
}

function pintarFiltros(){
  const per = [["5h","5 h"],["hoy","Hoy"],["mes","Mes 30 d"]];
  const provs = ["todos"].concat(Object.keys(D.proveedores));
  document.getElementById("filtros").innerHTML =
    per.map(p => `<button class="${periodo===p[0]?"on":""}" onclick="setPeriodo('${p[0]}')">${p[1]}</button>`).join("") +
    "<span style='width:14px'></span>" +
    provs.map((p,i) => `<button class="${proveedor===p?"on":""}" onclick="setProveedor('${p}')">${p==="todos"?"Todos los planes":p}</button>`).join("");
}
function setPeriodo(p){ periodo = p; pintar(); }
function setProveedor(p){ proveedor = p; pintar(); }

function barras(datos, unidad){
  if (!datos.length) return "<p class='vacio'>sin datos</p>";
  const tope = Math.max(...datos.map(d => d[1])) || 1;
  return datos.map(d => `<div class="fila"><span class="et">${d[0]}</span>
    <span class="pista"><span class="barra" style="width:${Math.max(d[1]/tope*100,1.5).toFixed(1)}%"></span></span>
    <span class="val">${fmt(d[1])}${unidad||""}</span></div>`).join("");
}

function tablaProveedores(){
  const p5 = D.limites.limite_5h_tokens, ps = D.limites.limite_semana_tokens;
  const filas = Object.entries(D.proveedores).filter(([n]) => proveedor==="todos" || n===proveedor).map(([n,x],i) =>
    `<tr><td><span class="punto" style="background:${color(i)}"></span>${n}</td>
     <td>${fmt(x.cinco_h)}${x.pct_5h?" ("+x.pct_5h+" %)":""}</td>
     <td>${fmt(x.semana)}${x.pct_semana?" ("+x.pct_semana+" %)":""}</td>
     <td>${fmt(x.mes)}</td>
     <td style="color:var(--suave)">${x.modelos.join(", ")||"-"}</td></tr>`).join("");
  return `<section><h2>Por plan (proveedor)</h2><table><tr><th>Plan</th><th>5 h</th><th>Semana</th><th>Mes</th><th>Modelos</th></tr>${filas}</table>
  ${(p5||ps)?"":"<p class='sub' style='margin:10px 0 0'>Pon tus cuotas en config.json (limite_5h_tokens, limite_semana_tokens) y aqui saldra el % consumido de cada ventana.</p>"}</section>`;
}

function tablaModelos(){
  const fuente = periodo==="hoy" ? D.modelos.hoy : D.modelos.mes;
  const titulo = periodo==="hoy" ? "Por modelo (hoy)" : "Por modelo (mes)";
  const filas = Object.entries(fuente).map(([m,t],i) =>
    `<tr><td><span class="punto" style="background:${color(i)}"></span>${m}</td><td>${fmt(t)}</td></tr>`).join("");
  return `<section><h2>${titulo}</h2><table><tr><th>Modelo</th><th>Tokens</th></tr>${filas||"<tr><td colspan=2>sin datos</td></tr>"}</table></section>`;
}

function tablaModeloDia(){
  const ultimos = dias.slice(-21);
  const orden = Object.entries(D.modelos.mes).slice(0,8).map(m => m[0]);
  if (!orden.length) return "";
  let max = 0;
  orden.forEach(m => ultimos.forEach(d => { max = Math.max(max, (D.modelo_dia[m]||{})[d]||0); }));
  const cab = "<tr><th>Modelo</th>" + ultimos.map(d => `<th>${d.slice(8)}</th>`).join("") + "<th>Total</th></tr>";
  const filas = orden.map(m => {
    const serie = D.modelo_dia[m]||{};
    const celdas = ultimos.map(d => {
      const v = serie[d]||0, f = max ? v/max : 0;
      const c = f ? `background:rgba(174,0,255,${(0.12+f*0.8).toFixed(2)});color:${f>0.55?"#fff":"var(--texto)"}` : "";
      return `<td class="celda ${v?"":"cero"}" style="${c}" title="${fmt(v)}">${v?fmt(v).replace(" ",''):"-"}</td>`;
    }).join("");
    return `<tr><td>${m}</td>${celdas}<td>${fmt(D.modelos.mes[m])}</td></tr>`;
  }).join("");
  return `<section><h2>Que modelo consume cada dia (ultimas 3 semanas)</h2><div class="mat"><table>${cab}${filas}</table></div></section>`;
}

function tablaProyectos(){
  const datos = Object.entries(D.proyectos).slice(0,12);
  return `<section><h2>Por proyecto (mes)</h2>${barras(datos)}</section>`;
}

function pintar(){
  pintarFiltros(); pintarKpis();
  const serie = dias.slice(-30).map(d => [d.slice(5), D.dias[d].tok]);
  document.getElementById("tablas").innerHTML =
    tablaProveedores() +
    `<section><h2>Por dia (tokens, 30 dias)</h2>${barras(serie)}</section>` +
    tablaModelos() + tablaModeloDia() + tablaProyectos();
  document.getElementById("sub").textContent = `Generado ${D.generado} · se actualiza solo cada minuto · logs locales de Claude Code, Codex y OpenCode (${D.fuentes} ficheros), turnos repetidos descartados`;
  document.getElementById("pie").innerHTML = `Los <b>turnos</b> son idas y vueltas con el modelo: cada uno reenvia el contexto, y por eso la mayor parte es <b>cache leida</b>.
   Menos turnos y menos contexto pesan mas que bajar el <i>effort</i>. El equivalente API (${D.hoy.usd.toFixed(0)} USD hoy · ${D.mes.usd.toFixed(0)} USD en 30 dias) es informativo: con suscripcion se agota la cuota, no el monedero.
   Los planes que corren en la nube no dejan log aqui.`;
}
pintar();
</script></body></html>"""


def main():
    d = datos()
    html = PLANTILLA.replace("__DATOS__", json.dumps(d, ensure_ascii=False))
    open(SALIDA, "w", encoding="utf-8").write(html)
    return SALIDA, d["hoy"]["tok"], d["ritmo"]


if __name__ == "__main__":
    p, tok, ritmo = main()
    print(f"panel: {p} ({os.path.getsize(p)} bytes) · hoy {costbar.fmt_tok(tok)} · ritmo {costbar.fmt_tok(ritmo)}")
