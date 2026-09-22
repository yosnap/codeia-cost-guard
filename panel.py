#!/usr/bin/env python3
"""Genera panel.html: consumo en tokens, con modo claro/oscuro, filtros por plan y periodo,
tabla ordenable y selector de dias. Todo el dato se calcula en Python y se embute como JSON."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costbar

AQUI = os.path.dirname(os.path.abspath(__file__))
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
        "proyectos": {n: int(x["tok"]) for n, x in sorted(r["proyectos"].items(), key=lambda kv: -kv[1]["tok"])},
        "bases": r.get("bases") or [], "fuentes": r["fuentes"],
    }


PLANTILLA = """<!doctype html><html lang="es" data-tema="claro"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="60"><title>Consumo de tokens</title>
<style>
 :root[data-tema="claro"]{--fondo:#f4f2f8;--panel:#fff;--borde:#e4dff0;--texto:#1c1726;--suave:#6d6485;--pista:#ece7f5;--sombra:0 1px 3px rgba(30,20,60,.07)}
 :root[data-tema="oscuro"]{--fondo:#0e0b14;--panel:#171226;--borde:#241a3a;--texto:#efeaf7;--suave:#9d93b5;--pista:#0b0813;--sombra:none}
 :root{--marca:#ae00ff;--marca2:#ff008c}
 *{box-sizing:border-box}
 body{margin:0;padding:26px;background:var(--fondo);color:var(--texto);font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text",Segoe UI,sans-serif}
 .cab{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:flex-start;margin-bottom:12px}
 #tema{font-size:12.5px;padding:6px 12px;color:var(--suave)}
 #tema:hover{color:var(--texto)}
 th .f{opacity:.35;font-size:10px} th.act{color:var(--marca)} th.act .f{opacity:1}
 h1{font-size:20px;margin:0 0 4px} .sub{color:var(--suave);font-size:12.5px}
 button{font:inherit;font-size:13px;padding:7px 12px;border-radius:10px;border:1px solid var(--borde);background:var(--panel);color:var(--texto);cursor:pointer}
 button:hover{border-color:var(--marca)} button.on{background:linear-gradient(90deg,var(--marca),var(--marca2));color:#fff;border-color:transparent}
 .fila-b{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
 .etq{color:var(--suave);font-size:12px;text-transform:uppercase;letter-spacing:.7px;margin-right:2px}
 .rejilla{display:grid;grid-template-columns:repeat(auto-fit,minmax(196px,1fr));gap:13px;margin-bottom:14px}
 .tarjeta{background:var(--panel);border:1px solid var(--borde);border-radius:16px;padding:16px 18px;box-shadow:var(--sombra)}
 .tarjeta .rot{font-size:11.5px;text-transform:uppercase;letter-spacing:.8px;color:var(--suave)}
 .tarjeta .num{font-size:30px;font-weight:680;margin-top:5px;background:linear-gradient(90deg,var(--marca),var(--marca2));-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
 .tarjeta.g .num{font-size:36px} .tarjeta .pie{font-size:12px;color:var(--suave);margin-top:3px}
 section{background:var(--panel);border:1px solid var(--borde);border-radius:16px;padding:16px 18px;margin-bottom:14px;box-shadow:var(--sombra)}
 h2{font-size:13px;margin:0 0 12px;color:var(--suave);text-transform:uppercase;letter-spacing:.9px}
 .fila{display:grid;grid-template-columns:minmax(130px,34%) 1fr 118px;align-items:center;gap:12px;padding:3.5px 0}
 .et{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .pista{background:var(--pista);border-radius:7px;height:15px;overflow:hidden}
 .barra{display:block;height:100%;border-radius:7px;background:linear-gradient(90deg,var(--marca),var(--marca2))}
 .val{text-align:right;font-variant-numeric:tabular-nums;color:var(--suave);font-size:12.5px}
 table{width:100%;border-collapse:collapse;font-size:13px}
 th,td{text-align:right;padding:6px 8px;border-bottom:1px solid var(--borde);font-variant-numeric:tabular-nums}
 th{color:var(--suave);font-weight:500;font-size:11.5px;text-transform:uppercase;letter-spacing:.6px;cursor:pointer;user-select:none}
 th:first-child,td:first-child{text-align:left}
 th:hover{color:var(--marca)}
 .punto{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:8px;vertical-align:middle}
 .auto{font-size:10.5px;color:var(--suave);border:1px solid var(--borde);border-radius:6px;padding:0 5px;margin-left:6px}
 .mat{overflow-x:auto} .mat table{font-size:11.5px}
 footer{color:var(--suave);font-size:12px;margin-top:16px} .vacio{color:var(--suave)}
</style></head><body>
<div class="cab"><div><h1>Consumo de tokens</h1><div class="sub" id="sub"></div></div>
<div><button id="tema" onclick="cambiarTema()">Modo oscuro</button></div></div>
<div class="fila-b" id="f-periodo"></div>
<div class="fila-b" id="f-plan"></div>
<div class="rejilla" id="kpis"></div>
<div class="rejilla" id="detalle"></div>
<div id="fuentes"></div>
<div id="tablas"></div>
<footer id="pie"></footer>
<script>
const D = __DATOS__;
let periodo = "semana", plan = "todos", diasVer = 30, orden = ["mes", -1];

function fmt(n){n=Number(n)||0;
  if(n>=1e9)return (n/1e9).toFixed(1).replace(".",",")+" B";
  if(n>=1e6)return (n/1e6).toFixed(1).replace(".",",")+" M";
  if(n>=1e3)return (n/1e3).toFixed(0)+" K"; return String(Math.round(n));}
const dias = Object.keys(D.dias);
const DIASEM = ["dom","lun","mar","mie","jue","vie","sab"];
function etiquetaDia(d){ const hoy=dias[dias.length-1], ayer=dias[dias.length-2];
  if(d===hoy) return "hoy";
  if(d===ayer) return "ayer";
  const x = new Date(d+"T12:00:00");
  return DIASEM[x.getDay()]+" "+d.slice(8); }
const col = i => ["#ae00ff","#ff008c","#12b886","#4c6ef5","#f59f00","#e8590c","#0ca678","#7048e8","#d6336c","#1098ad"][i%10];
const planes = () => Object.keys(D.proveedores);
function cambiarTema(){const r=document.documentElement,n=r.getAttribute("data-tema")==="claro"?"oscuro":"claro";
  r.setAttribute("data-tema",n);localStorage.setItem("tema",n);document.getElementById("tema").textContent=n==="claro"?"Modo oscuro":"Modo claro";}
if(localStorage.getItem("tema"))document.documentElement.setAttribute("data-tema",localStorage.getItem("tema"));

/* --- que dias entran en el periodo elegido --- */
function diasPeriodo(){
  if(periodo==="hoy") return dias.slice(-1);
  if(periodo==="semana") return dias.slice(-7);
  if(periodo==="mes") return dias.slice(-30);
  return dias.slice(-diasVer);
}
/* --- cruce del plan y periodo elegidos: {modelo: tok} --- */
function porModelo(){
  const ds = new Set(diasPeriodo()), out = {};
  const planes_ = plan==="todos" ? planes() : [plan];
  planes_.forEach(p => {
    const mm = (D.cruce[p]||{});
    Object.entries(mm).forEach(([m, dd]) => {
      let t=0; Object.entries(dd).forEach(([d,v]) => { if(ds.has(d)) t+=v; });
      if(t) out[m]=(out[m]||0)+t;
    });
  });
  return out;
}
function porDia(){
  const ds = diasPeriodo(), out = {};
  const planes_ = plan==="todos" ? planes() : [plan];
  ds.forEach(d => out[d]=0);
  planes_.forEach(p => Object.values(D.cruce[p]||{}).forEach(dd => Object.entries(dd).forEach(([d,v]) => { if(out[d]!==undefined) out[d]+=v; })));
  return out;
}
function tokPeriodo(objeto){return Object.values(objeto).reduce((a,b)=>a+b,0);}
function totalesPlan(){ /* {plan: {hoy,semana,mes,tok}} del plan elegido */
  const ds = new Set(diasPeriodo()), out = {};
  const planes_ = plan==="todos" ? planes() : [plan];
  planes_.forEach(p => {
    let t=0; Object.values(D.cruce[p]||{}).forEach(dd => Object.entries(dd).forEach(([d,v]) => { if(ds.has(d)) t+=v; }));
    out[p] = {tok: t, cinco_h: (D.proveedores[p]||{}).cinco_h||0, semana: (D.proveedores[p]||{}).semana||0,
              mes: (D.proveedores[p]||{}).mes||0, pct_5h: (D.proveedores[p]||{}).pct_5h||0, pct_semana: (D.proveedores[p]||{}).pct_semana||0};
  });
  return out;
}

function pintarFiltros(){
  document.getElementById("f-periodo").innerHTML = "<span class='etq'>Periodo</span>" +
    [["hoy","Hoy"],["semana","7 dias"],["mes","30 dias"]].map(p =>
      `<button class="${periodo===p[0]?"on":""}" onclick="setPeriodo('${p[0]}')">${p[1]}</button>`).join("");
  document.getElementById("f-plan").innerHTML = "<span class='etq'>Plan</span>" +
    [`<button class="${plan==="todos"?"on":""}" onclick="setPlan('todos')">Todos</button>`].concat(
      planes().map(p => `<button class="${plan===p?"on":""}" onclick="setPlan('${p.replace(/'/g,"")}')">${p}</button>`)).join("");
}
function opcionesDias(){ if(periodo==="hoy") return [1]; if(periodo==="semana") return [5,7]; return [5,10,15,30]; }
function setPeriodo(p){ periodo=p; diasVer=Math.min(diasVer, opcionesDias().slice(-1)[0]); pintar(); }
function setPlan(p){plan=p;pintar();}
function setDias(n){diasVer=n;pintar();}
function setOrden(k){orden = (orden[0]===k) ? [k,-orden[1]] : [k,-1]; pintar();}

function pintarKpis(){
  const t = totalesPlan(), glo = Object.values(t).reduce((a,b)=>({tok:a.tok+b.tok,cinco_h:a.cinco_h+b.cinco_h}),{tok:0,cinco_h:0});
  const nom = plan==="todos" ? (periodo==="hoy"?"Hoy":"Ultimos "+diasPeriodo().length+" dias") : plan;
  const k5 = plan==="todos" ? D.cinco_h : (D.proveedores[plan]||{}).cinco_h||0;
  const lim5 = D.limites.limite_5h_tokens;
  const c5 = (plan!=="todos" && (D.proveedores[plan]||{}).pct_5h) ? " · "+(D.proveedores[plan].pct_5h)+" % de su cuota"
           : (lim5 && plan==="todos" ? " · "+(k5/lim5*100).toFixed(0)+" % de su cuota" : "");
  const tarjetas = [
    ["Ultimas 5 horas", fmt(k5), "tokens"+c5, true],
    [nom, fmt(glo.tok), (plan==="todos"?"todos los planes":plan), true],
    ["Cache leida hoy", D.cache_pct+" %", fmt(D.hoy.cr)+" de la entrada", false],
    ["Dias con actividad", String(diasPeriodo().length), "de "+dias.length+" guardados", false]];
  document.getElementById("kpis").innerHTML = tarjetas.map(x =>
    `<div class="tarjeta${x[3]?" g":""}"><div class="rot">${x[0]}</div><div class="num">${x[1]}</div><div class="pie">${x[2]}</div></div>`).join("");
  document.getElementById("detalle").innerHTML = [
    ["Hoy", fmt(D.hoy.tok), D.hoy.turnos+" turnos"], ["Semana", fmt(D.semana.tok), D.semana.turnos+" turnos"],
    ["Mes (30 d)", fmt(D.mes.tok), D.mes.turnos+" turnos"], ["Ritmo ultima hora", fmt(D.ritmo), "limite "+fmt(D.limites.limite_hora_tokens)+"/h"],
  ].map(x => `<div class="tarjeta"><div class="rot">${x[0]}</div><div class="num">${x[1]}</div><div class="pie">${x[2]}</div></div>`).join("");
}

function barras(datos, extra){
  if(!datos.length) return "<p class='vacio'>sin datos</p>";
  const tope = Math.max.apply(null, datos.map(d=>d[1]))||1;
  return datos.map(d => `<div class="fila"><span class="et">${d[0]}</span>
    <span class="pista"><span class="barra" style="width:${Math.max(d[1]/tope*100,1.5).toFixed(1)}%"></span></span>
    <span class="val">${fmt(d[1])}</span></div>`).join("");
}

function tablaPlanes(){
  let filas = Object.entries(D.proveedores).filter(([n])=>plan==="todos"||n===plan);
  const k = orden[0], s = orden[1];
  filas.sort((a,b) => {
    const v = x => k==="nombre" ? x[0].toLowerCase() : (x[1][k]||0);
    return (v(a) > v(b) ? 1 : v(a) < v(b) ? -1 : 0) * s; });
  const cab = [["nombre","Plan"],["cinco_h","5 h"],["semana","Semana"],["mes","Mes"],["hoy","Hoy"]];
  return `<section><h2>Por plan (proveedor) <span style="text-transform:none;letter-spacing:0">· pulsa una cabecera para ordenar</span></h2><table>
  <tr>${cab.map(c=>{const act=orden[0]===c[0];const f=act?(orden[1]<0?"▼":"▲"):"↕";
    return `<th class="${act?"act":""}" onclick="setOrden('${c[0]}')">${c[1]} <span class="f">${f}</span></th>`;}).join("")}<th>Modelos</th></tr>
  ${filas.map(([n,x],i)=>`<tr><td><span class="punto" style="background:${col(i)}"></span>${n}${x.auto?"<span class='auto'>auto</span>":""}</td>
    <td>${fmt(x.cinco_h)}${x.pct_5h?" ("+x.pct_5h+" %)":""}</td><td>${fmt(x.semana)}${x.pct_semana?" ("+x.pct_semana+" %)":""}</td>
    <td>${fmt(x.mes)}</td><td>${fmt(x.hoy)}</td><td style="color:var(--suave);font-size:11.5px">${(x.modelos||[]).join(", ")||"-"}</td></tr>`).join("")}
  </table>${(D.limites.limite_5h_tokens||D.limites.limite_semana_tokens)?"":"<p class='sub' style='margin:10px 0 0'>Los planes con etiqueta <span class='auto'>auto</span> salen solos de tus logs. Para agrupar modelos o poner cuota, edita config.json; con la cuota aparece el % consumido.</p>"}</section>`;
}

function tablaModelos(){
  const m = porModelo(), total = tokPeriodo(m);
  const filas = Object.entries(m).sort((a,b)=>b[1]-a[1]);
  return `<section><h2>Por modelo · ${plan==="todos"?"todos los planes":plan} · ultimos ${diasPeriodo().length} dias</h2><table>
  <tr><th>Modelo</th><th>Tokens</th><th>% del total</th><th>Plan</th></tr>
  ${filas.map(([k,v])=>`<tr><td>${k}</td><td>${fmt(v)}</td><td>${total?(v/total*100).toFixed(1):0} %</td>
    <td style="color:var(--suave)">${D.modelo_prov[k]||"-"}</td></tr>`).join("")||"<tr><td colspan=4>sin datos</td></tr>"}</table></section>`;
}

function tablaMatriz(){
  const ds = diasPeriodo(), ult = ds.slice(-Math.min(diasVer,21));
  const mm = porModelo();
  const modelos = Object.entries(mm).sort((a,b)=>b[1]-a[1]).slice(0,8).map(x=>x[0]);
  if(!modelos.length) return "";
  let max = 0;
  modelos.forEach(m => ult.forEach(d => { max = Math.max(max, ((D.cruce[plan==="todos"?"__":plan]||{})[m]||{})[d] || diaModelo(m,d)); }));
  function diaModelo(m,d){
    let t=0; const ps = plan==="todos"?planes():[plan];
    ps.forEach(p => { const v=((D.cruce[p]||{})[m]||{})[d]; if(v) t+=v; }); return t;
  }
  const cab = "<tr><th>Modelo</th>"+ult.map(d=>`<th>${d.slice(8)}</th>`).join("")+`<th>Total ${plan==="todos"?"":"("+plan+")"}</th></tr>`;
  const filas = modelos.map(m => {
    const celdas = ult.map(d => { const v=diaModelo(m,d), f=max?v/max:0;
      const st = v?`background:rgba(174,0,255,${(0.12+f*0.8).toFixed(2)});color:${f>0.55?"#fff":"var(--texto)"}`:"";
      return `<td class="${v?"":"cero"}" style="${st}" title="${fmt(v)}">${v?fmt(v).replace(" ",""):"-"}</td>`; }).join("");
    return `<tr><td>${m}</td>${celdas}<td>${fmt(mm[m])}</td></tr>`; }).join("");
  return `<section><h2>Que modelo consume cada dia</h2><div class="mat"><table>${cab}${filas}</table></div></section>`;
}

function tablaProyectos(){
  const ds = new Set(diasPeriodo());
  const acc = {};
  const ps = plan==="todos"?planes():[plan];
  if(plan==="todos"){
    Object.entries(D.proyectos).forEach(([p,v])=>{ acc[p]=v; });
  } else {
    Object.entries(D.proy_prov[plan]||{}).forEach(([p,v])=>{ acc[p]=v; });
  }
  const datos = Object.entries(acc).sort((a,b)=>b[1]-a[1]).slice(0,12);
  return `<section><h2>Por proyecto · ${plan==="todos"?"todos":plan}${plan==="todos"?" (30 dias)":" (30 dias)"}</h2>${barras(datos)}</section>`;
}

function pintarFuentes(){
  const b = D.bases || [];
  document.getElementById("fuentes").innerHTML = `<section><h2>De donde salen los datos</h2>
    <p class="sub" style="margin:0 0 8px">${D.fuentes} ficheros de log (Claude Code, Codex) + estas bases de datos:</p>
    ${b.length ? b.map(x=>`<div class="fila"><span class="et">${x.split(" (")[0]}</span><span class="pista"><span class="barra" style="width:100%;opacity:.25"></span></span><span class="val">${(x.match(/\((\d+)\)/)||[])[1]||""} sesiones</span></div>`).join("")
      : "<p class='vacio'>sin bases de datos detectadas</p>"}
    </section>`;
}
function pintar(){
  pintarFiltros(); pintarKpis(); pintarFuentes();
  const nver = Math.min(diasVer, diasPeriodo().length);
  const serie = Object.entries(porDia()).sort().slice(-nver).map(([d,v])=>[etiquetaDia(d),v]);
  const ops = opcionesDias();
  const control = `<div class="fila-b"><span class="etq">Dias en el grafico</span>` +
    (ops.length===1 ? `<button class="on" disabled>solo hoy</button>`
      : ops.map(n=>`<button class="${nver===n?"on":""}" onclick="setDias(${n})">${n}</button>`).join("")) +
    `<span class="sub" style="margin-left:6px">(el periodo elige el maximo)</span></div>`;
  document.getElementById("tablas").innerHTML = tablaPlanes() +
    `<section>${control}<h2>Por dia (tokens)</h2>${barras(serie)}</section>` +
    tablaModelos() + tablaMatriz() + tablaProyectos();
  document.getElementById("sub").textContent = `Generado ${D.generado} · se actualiza solo cada minuto · ${D.fuentes} ficheros + ${(D.bases||[]).join(", ")} · turnos repetidos descartados`;
  document.getElementById("pie").innerHTML = `Los <b>turnos</b> son idas y vueltas con el modelo: cada uno reenvia el contexto, y por eso la mayor parte es <b>cache leida</b>.
   Menos turnos y menos contexto pesan mas que bajar el <i>effort</i>. Equivalente API (${D.mes.usd.toFixed(0)} USD en 30 dias) solo informativo: con suscripcion se agota la cuota.
   Fuentes: ficheros de Claude Code y Codex, y las bases de datos de <b>OpenCode</b> y <b>Hermes</b>. Los planes que corren en la nube no dejan log aqui.`;
}
pintar();
</script></body></html>"""


def main():
    d = datos()
    open(SALIDA, "w", encoding="utf-8").write(_con_cfg(PLANTILLA.replace("__DATOS__", json.dumps(d, ensure_ascii=False))))
    return SALIDA, d["hoy"]["tok"], d["ritmo"]


if __name__ == "__main__":
    p, tok, ritmo = main()
    print(f"panel: {p} ({os.path.getsize(p)} bytes) · hoy {costbar.fmt_tok(tok)} · ritmo {costbar.fmt_tok(ritmo)}")
