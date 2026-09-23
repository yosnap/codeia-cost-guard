/* Estado, agregacion y pintado. Los datos ya vienen resueltos en __DATOS__ (D);
   aqui solo se filtra/suma en cliente — sin fetch salvo el configurador (/origenes, /guardar),
   que ya existia. Los graficos (barras, lineas, tarta...) los pone panel_assets/graficos.js. */
const D = __DATOS__;

let periodo = "semana", plan = "todos", diasVer = 30, orden = ["mes", -1];
let periodoModelo = "semana", planModelo = "todos";
let periodoProyecto = "mes", planProyecto = "todos";
let vistaDia = "barrasH", vistaModelo = "barrasH", vistaProyecto = "barrasH", vistaMatriz = "matriz";
let seleccion = null;           // {desde, hasta}: indices dentro de `dias`
let arrastreDesde = null;       // indice donde empezo el mousedown, mientras se arrastra

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
const planes = () => Object.keys(D.proveedores);
function cambiarTema(){const r=document.documentElement,n=r.getAttribute("data-tema")==="claro"?"oscuro":"claro";
  r.setAttribute("data-tema",n);localStorage.setItem("tema",n);document.getElementById("tema").textContent=n==="claro"?"Modo oscuro":"Modo claro";}
if(localStorage.getItem("tema"))document.documentElement.setAttribute("data-tema",localStorage.getItem("tema"));

/* --- que dias entran en un periodo (hoy/semana/mes/todo/N) --- */
function diasParaPeriodo(p, verN){
  if(p==="hoy") return dias.slice(-1);
  if(p==="semana") return dias.slice(-7);
  if(p==="mes") return dias.slice(-30);
  if(p==="todo") return dias.slice();
  return dias.slice(-(verN||30));
}
function diasPeriodo(){ return diasParaPeriodo(periodo, diasVer); }

/* --- agregados: todos aceptan que plan y que dias, para poder reusarlos
   tanto en las secciones globales como en los filtros propios y en el detalle
   del dia/tramo seleccionado --- */
function porModelo(planSel, diasSel){
  const ds = new Set(diasSel), out = {};
  const planes_ = planSel==="todos" ? planes() : [planSel];
  planes_.forEach(p => {
    Object.entries(D.cruce[p]||{}).forEach(([m, dd]) => {
      let t=0; Object.entries(dd).forEach(([d,v]) => { if(ds.has(d)) t+=v; });
      if(t) out[m]=(out[m]||0)+t;
    });
  });
  return out;
}
function porProyecto(planSel, diasSel){
  const ds = new Set(diasSel), out = {};
  const planes_ = planSel==="todos" ? planes() : [planSel];
  planes_.forEach(p => {
    Object.entries(D.proy_cruce[p]||{}).forEach(([proy, dd]) => {
      let t=0; Object.entries(dd).forEach(([d,v]) => { if(ds.has(d)) t+=v; });
      if(t) out[proy]=(out[proy]||0)+t;
    });
  });
  return out;
}
function porDia(planSel, diasSel){
  const out = {}; diasSel.forEach(d => out[d]=0);
  const planes_ = planSel==="todos" ? planes() : [planSel];
  planes_.forEach(p => Object.values(D.cruce[p]||{}).forEach(dd =>
    Object.entries(dd).forEach(([d,v]) => { if(out[d]!==undefined) out[d]+=v; })));
  return out;
}
function tokPeriodo(objeto){return Object.values(objeto).reduce((a,b)=>a+b,0);}
function totalesPlan(diasSel, planFiltro){
  const ds = new Set(diasSel), out = {};
  const planes_ = (planFiltro||"todos")==="todos" ? planes() : [planFiltro];
  planes_.forEach(p => {
    let t=0; Object.values(D.cruce[p]||{}).forEach(dd => Object.entries(dd).forEach(([d,v]) => { if(ds.has(d)) t+=v; }));
    out[p] = {tok: t, cinco_h: (D.proveedores[p]||{}).cinco_h||0, semana: (D.proveedores[p]||{}).semana||0,
              mes: (D.proveedores[p]||{}).mes||0, pct_5h: (D.proveedores[p]||{}).pct_5h||0, pct_semana: (D.proveedores[p]||{}).pct_semana||0};
  });
  return out;
}

/* --- filtros: botones reusables (periodo y plan), globales y por seccion --- */
function botonesPeriodo(actual, fn, conTodo){
  const ops = [["hoy","Hoy"],["semana","7 dias"],["mes","30 dias"]].concat(conTodo?[["todo","Todo"]]:[]);
  return ops.map(p => `<button class="${actual===p[0]?"on":""}" onclick="${fn}('${p[0]}')">${p[1]}</button>`).join("");
}
function botonesPlan(actual, fn){
  return [`<button class="${actual==="todos"?"on":""}" onclick="${fn}('todos')">Todos</button>`]
    .concat(planes().map(p => `<button class="${actual===p?"on":""}" onclick="${fn}('${p.replace(/'/g,"")}')">${p}</button>`)).join("");
}
function botonesPestanas(opciones, actual, fn){
  return `<div class="pestanas">${opciones.map(o =>
    `<button class="${actual===o[0]?"on":""}" onclick="${fn}('${o[0]}')">${ICONOS[o[0]]||""}${o[1]}</button>`).join("")}</div>`;
}

function pintarFiltros(){
  document.getElementById("f-periodo").innerHTML = "<span class='etq'>Periodo</span>"+botonesPeriodo(periodo,"setPeriodo");
  document.getElementById("f-plan").innerHTML = "<span class='etq'>Plan</span>"+botonesPlan(plan,"setPlan");
}
function opcionesDias(){ if(periodo==="hoy") return [1]; if(periodo==="semana") return [5,7]; return [5,10,15,30]; }
function setPeriodo(p){ periodo=p; diasVer=Math.min(diasVer, opcionesDias().slice(-1)[0]); limpiarSeleccion(); pintar(); }
function setPlan(p){ plan=p; limpiarSeleccion(); pintar(); }
function setDias(n){ diasVer=n; limpiarSeleccion(); pintar(); }
function setOrden(k){ orden = (orden[0]===k) ? [k,-orden[1]] : [k,-1]; pintarTablas(); }
function setVistaDia(v){ vistaDia=v; pintarTablas(); }
function setVistaModelo(v){ vistaModelo=v; pintarTablas(); }
function setVistaProyecto(v){ vistaProyecto=v; pintarTablas(); }
function setVistaMatriz(v){ vistaMatriz=v; pintarTablas(); }
function setPeriodoModelo(p){ periodoModelo=p; pintarTablas(); }
function setPlanModelo(p){ planModelo=p; pintarTablas(); }
function setPeriodoProyecto(p){ periodoProyecto=p; pintarTablas(); }
function setPlanProyecto(p){ planProyecto=p; pintarTablas(); }

function pintarKpis(){
  const t = totalesPlan(diasPeriodo(), plan), glo = Object.values(t).reduce((a,b)=>({tok:a.tok+b.tok,cinco_h:a.cinco_h+b.cinco_h}),{tok:0,cinco_h:0});
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
  ${filas.map(([n,x],i)=>`<tr><td><span class="punto" style="background:${colorI(i)}"></span>${n}${x.auto?"<span class='auto'>auto</span>":""}</td>
    <td>${fmt(x.cinco_h)}${x.pct_5h?" ("+x.pct_5h+" %)":""}</td><td>${fmt(x.semana)}${x.pct_semana?" ("+x.pct_semana+" %)":""}</td>
    <td>${fmt(x.mes)}</td><td>${fmt(x.hoy)}</td><td style="color:var(--suave);font-size:11.5px">${(x.modelos||[]).join(", ")||"-"}</td></tr>`).join("")}
  </table>${(D.limites.limite_5h_tokens||D.limites.limite_semana_tokens)?"":"<p class='sub' style='margin:10px 0 0'>Los planes con etiqueta <span class='auto'>auto</span> salen solos de tus logs. Para agrupar modelos o poner cuota, edita config.json; con la cuota aparece el % consumido.</p>"}</section>`;
}

/* --- por dia: pestañas de vista + clic/arrastre para seleccionar un dia o tramo --- */
function seccionDia(){
  const nver = Math.min(diasVer, diasPeriodo().length);
  const diasMostrar = diasPeriodo().slice(-nver);
  const serieObj = porDia(plan, diasMostrar);
  const datosGrafico = diasMostrar.map(d => [etiquetaDia(d), serieObj[d]||0, d]);
  const seleccionadas = seleccion ? new Set(dias.slice(seleccion.desde, seleccion.hasta+1)) : null;
  const ops = opcionesDias();
  const controlDias = `<div class="fila-b"><span class="etq">Dias en el grafico</span>` +
    (ops.length===1 ? `<button class="on" disabled>solo hoy</button>`
      : ops.map(n=>`<button class="${nver===n?"on":""}" onclick="setDias(${n})">${n}</button>`).join("")) +
    `<span class="sub" style="margin-left:6px">(el periodo elige el maximo · pulsa o arrastra sobre el grafico)</span></div>`;
  let grafico;
  if(vistaDia==="barrasV") grafico = barrasV(datosGrafico, {fmt, seleccionadas});
  else if(vistaDia==="lineas") grafico = lineas(datosGrafico, {fmt, seleccionadas});
  else if(vistaDia==="calendario") grafico = calendario(diasMostrar.map(d=>[d, serieObj[d]||0]), {fmt, seleccionadas});
  else grafico = barrasH(datosGrafico, {fmt, seleccionadas, clicable:true});
  const pestanas = botonesPestanas([["barrasH","Barras"],["barrasV","Barras verticales"],["lineas","Lineas"],["calendario","Calendario"]], vistaDia, "setVistaDia");
  return `<section>${controlDias}<h2>Por dia (tokens)</h2>${pestanas}<div id="grafico-dia">${grafico}</div></section>`;
}

/* --- por modelo: filtro propio (periodo + plan) + pestañas de vista --- */
function seccionModelo(){
  const ds = diasParaPeriodo(periodoModelo);
  const m = porModelo(planModelo, ds), total = tokPeriodo(m);
  const datos = Object.entries(m).sort((a,b)=>b[1]-a[1]);
  const filtroPropio = `<div class="fila-b"><span class="etq">Periodo</span>${botonesPeriodo(periodoModelo,"setPeriodoModelo",true)}</div>
    <div class="fila-b"><span class="etq">Plan</span>${botonesPlan(planModelo,"setPlanModelo")}</div>`;
  const pestanas = botonesPestanas([["barrasH","Barras"],["tarta","Tarta"],["donut","Donut"]], vistaModelo, "setVistaModelo");
  let grafico;
  if(vistaModelo==="tarta") grafico = tarta(datos, {fmt});
  else if(vistaModelo==="donut") grafico = donut(datos, {fmt});
  else grafico = barrasH(datos, {fmt});
  const tabla = `<table style="margin-top:12px">
    <tr><th>Modelo</th><th>Tokens</th><th>% del total</th><th>Plan</th></tr>
    ${datos.map(([k,v])=>`<tr><td>${k}</td><td>${fmt(v)}</td><td>${total?(v/total*100).toFixed(1):0} %</td>
      <td style="color:var(--suave)">${D.modelo_prov[k]||"-"}</td></tr>`).join("")||"<tr><td colspan=4>sin datos</td></tr>"}</table>`;
  return `<section>${filtroPropio}<h2>Por modelo · ${planModelo==="todos"?"todos los planes":planModelo} · ${ds.length} dias</h2>${pestanas}${grafico}${tabla}</section>`;
}

function tablaMatriz(){
  const ds = diasPeriodo(), ult = ds.slice(-Math.min(diasVer,21));
  const mm = porModelo(plan, ds);
  const modelos = Object.entries(mm).sort((a,b)=>b[1]-a[1]).slice(0,8).map(x=>x[0]);
  if(!modelos.length) return "<p class='vacio'>sin datos</p>";
  function diaModelo(m,d){
    let t=0; const ps = plan==="todos"?planes():[plan];
    ps.forEach(p => { const v=((D.cruce[p]||{})[m]||{})[d]; if(v) t+=v; }); return t;
  }
  let max = 0;
  modelos.forEach(m => ult.forEach(d => { max = Math.max(max, diaModelo(m,d)); }));
  const cab = "<tr><th>Modelo</th>"+ult.map(d=>`<th>${d.slice(8)}</th>`).join("")+`<th>Total ${plan==="todos"?"":"("+plan+")"}</th></tr>`;
  const filas = modelos.map(m => {
    const celdas = ult.map(d => { const v=diaModelo(m,d), f=max?v/max:0;
      const st = v?`background:rgba(174,0,255,${(0.12+f*0.8).toFixed(2)});color:${f>0.55?"#fff":"var(--texto)"}`:"";
      return `<td class="${v?"":"cero"}" style="${st}" title="${fmt(v)}">${v?fmt(v).replace(" ",""):"-"}</td>`; }).join("");
    return `<tr><td>${m}</td>${celdas}<td>${fmt(mm[m])}</td></tr>`; }).join("");
  return `<div class="mat"><table>${cab}${filas}</table></div>`;
}
function seccionMatriz(){
  const ds = diasPeriodo(), ult = ds.slice(-Math.min(diasVer,12));
  const mm = porModelo(plan, ds);
  const modelos = Object.entries(mm).sort((a,b)=>b[1]-a[1]).slice(0,6);
  const pestanas = botonesPestanas([["matriz","Matriz"],["apiladas","Barras apiladas"]], vistaMatriz, "setVistaMatriz");
  let cuerpo;
  if(vistaMatriz==="apiladas"){
    // serie de cada modelo -> {dia:tok}, sumando los planes elegidos
    const planes_ = plan==="todos"?planes():[plan];
    const seriesAgg = modelos.map(([m]) => {
      const porDiaM = {};
      planes_.forEach(p => { const dd=(D.cruce[p]||{})[m]||{}; Object.entries(dd).forEach(([d,v])=>{ porDiaM[d]=(porDiaM[d]||0)+v; }); });
      return [m, porDiaM];
    });
    cuerpo = barrasApiladas(ult, seriesAgg, {fmt});
  } else {
    cuerpo = tablaMatriz();
  }
  return `<section><h2>Que modelo consume cada dia</h2>${pestanas}${cuerpo}</section>`;
}

/* --- por proyecto: filtro propio + pestañas de vista --- */
function seccionProyecto(){
  const ds = diasParaPeriodo(periodoProyecto);
  const acc = porProyecto(planProyecto, ds);
  const datos = Object.entries(acc).sort((a,b)=>b[1]-a[1]).slice(0,12);
  const filtroPropio = `<div class="fila-b"><span class="etq">Periodo</span>${botonesPeriodo(periodoProyecto,"setPeriodoProyecto",true)}</div>
    <div class="fila-b"><span class="etq">Plan</span>${botonesPlan(planProyecto,"setPlanProyecto")}</div>`;
  const pestanas = botonesPestanas([["barrasH","Barras"],["tarta","Tarta"],["donut","Donut"]], vistaProyecto, "setVistaProyecto");
  let grafico;
  if(vistaProyecto==="tarta") grafico = tarta(datos, {fmt});
  else if(vistaProyecto==="donut") grafico = donut(datos, {fmt});
  else grafico = barrasH(datos, {fmt});
  return `<section>${filtroPropio}<h2>Por proyecto · ${planProyecto==="todos"?"todos":planProyecto} · ${ds.length} dias</h2>${pestanas}${grafico}</section>`;
}

function pintarFuentes(){
  const b = D.bases || [];
  document.getElementById("fuentes").innerHTML = `<section><h2>Aplicaciones que registran datos</h2>
    <p class="sub" style="margin:0 0 8px">De que app sale cada sesion (${D.fuentes} ficheros/sesiones en total) — no de que proveedor de API.
    Eso esta en la tabla «Por plan» de mas abajo.</p>
    ${b.length ? b.map(x=>`<div class="fila"><span class="et">${x.split(" (")[0]}</span><span class="pista"><span class="barra" style="width:100%;opacity:.25"></span></span><span class="val">${(x.match(/\((\d+)\)/)||[])[1]||""} sesiones</span></div>`).join("")
      : "<p class='vacio'>sin bases de datos detectadas</p>"}
    </section>`;
}

/* --- detalle del dia/tramo seleccionado: se abre a la derecha de "Por dia" --- */
function limpiarSeleccion(){ seleccion = null; }
function seleccionarDia(indice){
  if(seleccion && seleccion.desde===indice && seleccion.hasta===indice){ seleccion = null; }
  else { seleccion = {desde: indice, hasta: indice}; }
  pintarTablas();
}
function seleccionarTramo(i0, i1){
  seleccion = {desde: Math.min(i0,i1), hasta: Math.max(i0,i1)};
  pintarTablas();
}
function pintarDetalleSeleccion(){
  const zona = document.getElementById("zona-dia");
  if(!seleccion){ zona.classList.remove("con-detalle"); document.getElementById("detalle-dia").innerHTML = ""; return; }
  zona.classList.add("con-detalle");
  const diasSel = dias.slice(seleccion.desde, seleccion.hasta+1);
  const titulo = diasSel.length===1 ? diasSel[0] : `${diasSel[0]} → ${diasSel[diasSel.length-1]}`;
  const totTok = tokPeriodo(porDia(plan, diasSel));
  const turnos = diasSel.reduce((a,d)=>a+(D.dias[d]?.turnos||0),0);
  const porM = Object.entries(porModelo(plan, diasSel)).sort((a,b)=>b[1]-a[1]).slice(0,6);
  const porP = Object.entries(porProyecto(plan, diasSel)).sort((a,b)=>b[1]-a[1]).slice(0,6);
  const porPlan = Object.entries(totalesPlan(diasSel, "todos")).filter(([,x])=>x.tok>0).sort((a,b)=>b[1].tok-a[1].tok);
  const listaLD = arr => arr.length ? arr.map(x => `<div class="linea-det"><span>${x[0]}</span><span>${fmt(x[1])}</span></div>`).join("")
    : "<div class='linea-det'><span class='vacio'>sin datos</span></div>";
  document.getElementById("detalle-dia").innerHTML = `
    <span class="cerrar" onclick="limpiarSeleccion();pintarTablas()">✕</span>
    <h3>${titulo}</h3>
    <div class="linea-det"><span>Total</span><span>${fmt(totTok)}</span></div>
    <div class="linea-det"><span>Turnos</span><span>${turnos}</span></div>
    <h4>Por modelo</h4>${listaLD(porM)}
    <h4>Por proyecto</h4>${listaLD(porP)}
    <h4>Por plan</h4>${listaLD(porPlan.map(([n,x])=>[n, x.tok]))}`;
}
function engancharClicsDia(){
  const cont = document.getElementById("grafico-dia");
  if(!cont) return;
  const claveDeEvento = e => { const el = e.target.closest("[data-k]"); return el ? el.dataset.k : null; };
  const indiceDeClave = k => dias.indexOf(k);
  cont.onclick = e => { const k = claveDeEvento(e); if(k && arrastreDesde===null) seleccionarDia(indiceDeClave(k)); };
  cont.onmousedown = e => { const k = claveDeEvento(e); if(k) arrastreDesde = indiceDeClave(k); };
  document.onmouseup = () => {
    if(arrastreDesde===null) return;
    const destino = cont.dataset.hoverIdx;
    if(destino!==undefined && destino!=="" && +destino!==arrastreDesde) seleccionarTramo(arrastreDesde, +destino);
    arrastreDesde = null;
  };
  cont.onmousemove = e => { const k = claveDeEvento(e); if(k) cont.dataset.hoverIdx = indiceDeClave(k); };
}

function pintarTablas(){
  document.getElementById("tablas").innerHTML = tablaPlanes() +
    `<div class="zona" id="zona-dia"><div>${seccionDia()}</div><div id="detalle-dia"></div></div>` +
    seccionModelo() + seccionMatriz() + seccionProyecto();
  pintarDetalleSeleccion();
  engancharClicsDia();
}

function pintar(){
  pintarFiltros(); pintarKpis(); pintarFuentes(); pintarTablas();
  document.getElementById("sub").textContent = `Generado ${D.generado} · se actualiza solo cada minuto · ${D.fuentes} ficheros + ${(D.bases||[]).join(", ")} · turnos repetidos descartados`;
  document.getElementById("pie").innerHTML = `Los <b>turnos</b> son idas y vueltas con el modelo: cada uno reenvia el contexto, y por eso la mayor parte es <b>cache leida</b>.
   Menos turnos y menos contexto pesan mas que bajar el <i>effort</i>. Equivalente API (${D.mes.usd.toFixed(0)} USD en 30 dias) solo informativo: con suscripcion se agota la cuota.
   Fuentes: ficheros de Claude Code y Codex, y las bases de datos de <b>OpenCode</b> y <b>Hermes</b>. Los planes que corren en la nube no dejan log aqui.`;
}
pintar();
