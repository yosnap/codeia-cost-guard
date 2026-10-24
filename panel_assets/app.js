/* Vista Agentes: estado, agregación y pintado. Los datos ya vienen resueltos en D (incrustados abajo); aquí solo se
   filtra y se suma en cliente. Los gráficos los pone panel_assets/graficos.js; Histórico, Servidores y Ajustes viven
   en sus propios ficheros y cambian de vista con window.vista (ajustes.js). */
/* Todo texto que viene de los logs (modelos, proyectos, planes) se escapa una vez aqui, antes de que ninguna plantilla lo
   use: asi cabe en HTML y en atributos entre comillas. Los clics no llevan datos en codigo: botones con data-a/data-v. */
function esc(s){ return String(s).replace(/[&<>"'`\\]/g, c => "&#" + c.charCodeAt(0) + ";"); }
function escapar(v){
  if(typeof v === "string") return esc(v);
  if(Array.isArray(v)) return v.map(escapar);
  if(v && typeof v === "object"){ const o = {}; for(const k of Object.keys(v)) o[esc(k)] = escapar(v[k]); return o; }
  return v;
}
const D = escapar(__DATOS__);

/* el panel se recarga solo cada minuto (ajustes.js): lo elegido se guarda en el navegador para no volver a lo de
   fábrica en cada recarga; si el almacenamiento no está disponible, se usa lo de fábrica */
const PREF = (() => { try { return JSON.parse(localStorage.getItem("cg-panel") || "{}") || {}; } catch(e){ return {}; } })();
function recordar(){ try { localStorage.setItem("cg-panel", JSON.stringify({periodo, plan, vistaDia, orden, periodoModelo, periodoProyecto})); } catch(e){} }
let periodo = PREF.periodo || "semana", plan = PREF.plan || "todos", vistaDia = PREF.vistaDia || "auto", orden = Array.isArray(PREF.orden) ? PREF.orden : ["mes", -1];
let periodoModelo = PREF.periodoModelo || periodo, periodoProyecto = PREF.periodoProyecto || periodo;
if(plan !== "todos" && !Object.prototype.hasOwnProperty.call(D.proveedores || {}, plan)) plan = "todos";     // un plan renombrado o quitado
if(!["hoy", "semana", "mes"].includes(periodo)) periodo = "semana";
let seleccion = null;               // {desde, hasta}: índices dentro de `dias`
let _navegadorEnCurso = false;      // cooldown de "Ver en el navegador": ver ACCIONES.abrirNavegador
let conMayus = false;               // un clic con mayúsculas amplía la selección a un tramo

function fmt(n){ n = Number(n) || 0;
  if(n >= 1e9) return (n/1e9).toFixed(1).replace(".", ",") + " B";
  if(n >= 1e6) return (n/1e6).toFixed(1).replace(".", ",") + " M";
  if(n >= 1e3) return (n/1e3).toFixed(0) + " K";
  return String(Math.round(n)); }
/* cifra con el color de su magnitud, como el desplegable: K tenue, M verde terminal, B naranja Opus */
function cifra(n){ const t = fmt(n), u = (t.match(/[KMB]$/) || ["K"])[0].toLowerCase(); return `<span class="${u}">${t}</span>`; }
const pct = (a, b) => b ? (a / b * 100).toFixed(1).replace(".", ",") + " %" : "—";
const dias = Object.keys(D.dias || {});
const DIASEM = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"], MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"];
function etiquetaDia(d){ if(d === dias[dias.length-1]) return "hoy"; if(d === dias[dias.length-2]) return "ayer";
  const x = new Date(d + "T12:00:00"); return DIASEM[x.getDay()] + " " + x.getDate(); }
function tituloDia(d){ const x = new Date(d + "T12:00:00"); return (d === dias[dias.length-1] ? "Hoy, " : "") + DIASEM[x.getDay()] + " " + x.getDate() + " de " + MESES[x.getMonth()]; }
const planes = () => Object.keys(D.proveedores || {});
const colorPlan = p => colorSerie(Math.max(planes().indexOf(p), 0));
const punto = c => `<i class="p" style="background:var(${c})"></i>`;
const prog = (p, cls, color) => `<div class="prog"><i class="${cls || ""}" style="width:${Math.max(0, Math.min(100, p)).toFixed(1)}%${color ? `;background:var(${color})` : ""}"></i></div>`;
const suma = (arr, f) => arr.reduce((a, x) => a + f(x), 0);

const ACCIONES = {
  setPeriodo: v => { periodo = periodoModelo = periodoProyecto = v; seleccion = null; pintar(); },
  setPlan: v => { plan = v; seleccion = null; pintar(); },
  setVistaDia: v => { vistaDia = v; pintarDia(); },
  setDia: v => elegirDia(+v),
  setDiaCal: v => elegirDiaGuardado(+v),
  cerrarDetalle: () => { seleccion = null; pintarDia(); },
  setOrden: v => { orden = orden[0] === v ? [v, -orden[1]] : [v, -1]; pintarPlanes(); },
  setPeriodoModelo: v => { periodoModelo = v; pintarModelos(); },
  setPeriodoProyecto: v => { periodoProyecto = v; pintarProyectos(); },
  vista: v => { window.vista(v); pintarCab(v); },
  refrescar: () => { const v = location.hash.slice(1); if(v) window.vista(v); else location.reload(); },
  cambiarTema: () => cambiarTema(),
  // el navegador externo no comparte cookies con esta ventana: el servidor abre el suyo propio con
  // un ticket nuevo (el mismo mecanismo que usa el icono de la bandeja), no window.open() de esta URL.
  // Cooldown corto: un doble clic o un clic repetido no debe abrir un navegador por cada pulsacion.
  abrirNavegador: () => {
    if (_navegadorEnCurso) return;
    _navegadorEnCurso = true;
    setTimeout(() => { _navegadorEnCurso = false; }, 2000);
    fetch("/navegador").catch(() => {});
  }};
document.addEventListener("pointerdown", e => { conMayus = e.shiftKey; });
document.addEventListener("click", e => {
  const el = e.target.closest("[data-a]");
  const f = el && Object.prototype.hasOwnProperty.call(ACCIONES, el.dataset.a) ? ACCIONES[el.dataset.a] : null;
  if(f) f(esc(el.dataset.v === undefined ? "" : el.dataset.v));     // el atributo llega decodificado: se escapa igual que las claves de D
  if(f) recordar();
});

/* --- tema: tu elección si la hay; si no, la apariencia del sistema (como el desplegable de la barra) --- */
function botonTema(){ const b = document.getElementById("tema"), claro = document.documentElement.getAttribute("data-tema") === "claro";
  if(b) b.innerHTML = icono(claro ? "luna" : "sol") + (claro ? "Oscuro" : "Claro"); }
function cambiarTema(){ const r = document.documentElement, n = r.getAttribute("data-tema") === "claro" ? "oscuro" : "claro";
  r.setAttribute("data-tema", n); try { localStorage.setItem("tema", n); } catch(e){} botonTema(); }
let _temaGuardado = null; try { _temaGuardado = localStorage.getItem("tema"); } catch(e){}
document.documentElement.setAttribute("data-tema", _temaGuardado || (matchMedia("(prefers-color-scheme: dark)").matches ? "oscuro" : "claro"));

/* --- cabecera: título, estado e icono según la vista --- */
function pintarCab(v){
  const lim = (D.limites || {}).limite_hora_tokens || 0, alto = lim && D.ritmo >= lim;
  const C = {panel: ["Mis agentes", alto ? `Ritmo alto: ${fmt(D.ritmo)}/h · el límite es ${fmt(lim)}/h` : `Última hora: ${fmt(D.ritmo)} tokens`, alto ? "ambar" : "", "pulso"],
    historico: ["Histórico", "Consumo guardado por día, semana o mes", "", "cal"], servidores: ["Mis servidores", "Salud, webs y seguridad de tus VPS", "", "escudo"],
    ajustes: ["Ajustes", "Planes, claves, servidores y avisos · se guardan en tu equipo", "", "ajustes"]}[v] || null;
  if(!C) return;
  document.getElementById("titulo").textContent = C[0];
  document.getElementById("sub").textContent = C[1];
  const e = document.getElementById("escudo"); e.className = "escudo" + (C[2] ? " " + C[2] : ""); e.innerHTML = icono(C[3], 2);
}

/* --- qué días entran en un periodo --- */
function diasParaPeriodo(p){
  if(p === "hoy") return dias.slice(-1);
  if(p === "semana") return dias.slice(-7);
  if(p === "mes") return dias.slice(-30);
  return dias.slice();
}
/* --- agregados por plan y por días (los mismos que antes, para el panel y para el detalle del día) --- */
function planesDe(planSel){ return planSel === "todos" ? planes() : [planSel]; }
function porModelo(planSel, diasSel){
  const ds = new Set(diasSel), out = {};
  planesDe(planSel).forEach(p => Object.entries((D.cruce || {})[p] || {}).forEach(([m, dd]) => {
    let t = 0; Object.entries(dd).forEach(([d, v]) => { if(ds.has(d)) t += v; }); if(t) out[m] = (out[m] || 0) + t; }));
  return out;
}
function porProyecto(planSel, diasSel){
  const ds = new Set(diasSel), out = {};
  planesDe(planSel).forEach(p => Object.entries((D.proy_cruce || {})[p] || {}).forEach(([pr, dd]) => {
    let t = 0; Object.entries(dd).forEach(([d, v]) => { if(ds.has(d)) t += v; }); if(t) out[pr] = (out[pr] || 0) + t; }));
  return out;
}
function porDiaPlan(p, diasSel){            // tokens de un plan en cada día pedido
  const out = Object.fromEntries(diasSel.map(d => [d, 0]));
  Object.values((D.cruce || {})[p] || {}).forEach(dd => Object.entries(dd).forEach(([d, v]) => { if(d in out) out[d] += v; }));
  return diasSel.map(d => out[d]);
}
function totalDias(planSel, diasSel){ const t = diasSel.map(() => 0); planesDe(planSel).forEach(p => porDiaPlan(p, diasSel).forEach((v, i) => t[i] += v)); return t; }

/* --- filtros --- */
function pintarFiltros(){
  document.getElementById("f-periodo").innerHTML = `<span class="etq">Periodo</span><div class="seg vidrio">` +
    [["hoy", "Hoy"], ["semana", "7 días"], ["mes", "30 días"]].map(([k, t]) => `<button class="${periodo === k ? "on" : ""}" data-a="setPeriodo" data-v="${k}">${t}</button>`).join("") + "</div>";
  document.getElementById("f-plan").innerHTML = `<span class="etq">Plan</span>` +
    [`<button class="pil vidrio${plan === "todos" ? " on" : ""}" data-a="setPlan" data-v="todos">Todos</button>`]
      .concat(planes().map(p => `<button class="pil vidrio${plan === p ? " on" : ""}" data-a="setPlan" data-v="${p}">${punto(colorPlan(p))}${p}</button>`)).join("");
}

/* --- tarjetas de arriba --- */
function medidor(r){            // de 0 a 3× el límite, con su marca
  const tope = 3, p = Math.min(r / tope, 1) * 100, lim = 100 / tope, corte = p ? Math.min(lim / p * 100, 100) : 100;
  return `<div style="position:relative;height:8px;border-radius:4px;background:var(--pista);margin-top:14px">
    <i style="position:absolute;inset:0 auto 0 0;width:${p.toFixed(1)}%;border-radius:4px;background:linear-gradient(90deg,var(--marca) 0,var(--marca) ${corte.toFixed(0)}%,var(--ambar) ${Math.min(corte + 8, 100).toFixed(0)}%)"></i>
    <i style="position:absolute;left:${lim.toFixed(1)}%;top:-4px;bottom:-4px;width:2px;border-radius:1px;background:var(--texto);opacity:.5"></i></div>
    <div class="s tenue" style="display:flex;justify-content:space-between;margin-top:5px;font-size:11px"><span>0</span><span>límite</span><span>3×</span></div>`;
}
function pintarKpis(){
  const P = D.proveedores || {}, L = D.limites || {}, sel = planesDe(plan);
  // últimas 5 h: reparto por plan (la barra) y % de la cuota si la hay
  const cinco = plan === "todos" ? D.cinco_h : (P[plan] || {}).cinco_h || 0;
  const pct5 = plan !== "todos" ? (P[plan] || {}).pct_5h || 0 : (L.limite_5h_tokens ? cinco / L.limite_5h_tokens * 100 : 0);
  const partes = sel.map(p => [p, (P[p] || {}).cinco_h || 0]).filter(x => x[1] > 0), tot5 = suma(partes, x => x[1]) || 1;
  document.getElementById("k-5h").innerHTML = `<div class="et">${icono("reloj")}Últimas 5 horas<span class="der"><span class="chip">ventana en curso</span>${punto(pct5 >= 90 ? "--rojo" : pct5 >= 70 ? "--ambar" : "--verde")}</span></div>
    <div class="cifra">${cifra(cinco)}</div>
    <div class="s">${pct5 ? pct5.toFixed(0) + " % de su cuota de 5 h" : "tokens · entrada, salida y caché · sin cuota declarada"}</div>
    <div class="abajo">${pct5 ? prog(pct5, pct5 >= 90 ? "rojo" : pct5 >= 70 ? "ambar" : "") : ""}
      ${partes.length ? `<div class="mezcla">${partes.map(([p, v]) => `<i style="flex:${Math.max(v / tot5, .02)};background:var(${colorPlan(p)})" title="${p}: ${fmt(v)}"></i>`).join("")}</div>
      <div class="leyenda" style="margin-top:6px">${partes.map(([p, v]) => `<span>${punto(colorPlan(p))}${p} <span class="mono">${fmt(v)}</span></span>`).join("")}</div>` : '<div class="s tenue" style="margin-top:10px">sin consumo en la ventana</div>'}</div>`;
  // ritmo de la última hora contra el límite
  const lim = L.limite_hora_tokens || 0, r = lim ? D.ritmo / lim : 0, alto = lim && D.ritmo >= lim;
  document.getElementById("k-ritmo").innerHTML = `<div class="et">${icono("pulso")}Ritmo última hora<span class="der">${punto(alto ? "--ambar" : "--verde")}</span></div>
    <div class="v" style="font-size:26px">${cifra(D.ritmo)}<small> /h</small></div>
    <div class="s">${!lim ? "sin límite de ritmo (se pone en Ajustes)" : alto ? r.toFixed(1).replace(".", ",") + "× el límite de " + fmt(lim) + "/h" : "por debajo del límite de " + fmt(lim) + "/h"}</div>
    <div class="abajo">${lim ? medidor(r) : ""}</div>`;
  // caché leída hoy
  const cache = +D.cache_pct || 0;
  document.getElementById("k-cache").innerHTML = `<div class="et">${icono("capas")}Caché leída hoy<span class="der">${punto("--verde")}</span></div>
    <div style="display:flex;align-items:center;gap:14px;margin-top:10px">${anillo([[cache, "--marca"], [100 - cache, "--pista", 0]], 70, 7, "")}
    <div><div class="v">${cache.toFixed(1).replace(".", ",")}<small> %</small></div><div class="s">${fmt((D.hoy || {}).cr)} de la entrada</div></div></div>
    <div class="s abajo" style="white-space:normal;margin-top:10px">Contexto reenviado en cada turno: es lo que más pesa.</div>`;
  // fila del periodo elegido (Hoy / 7 días / 30 días) y del plan del filtro: total, media diaria y días con actividad
  const ds = diasParaPeriodo(periodo), n = ds.length, valores = totalDias(plan, ds), total = suma(valores, x => x);
  const previos = dias.length >= 2 * n ? totalDias(plan, dias.slice(-2 * n, -n)) : null, antes = previos ? suma(previos, x => x) : 0;
  const delta = (a, b) => b ? `<span class="delta ${a > b ? "sube" : "baja"}" title="frente al periodo anterior de la misma duración">${a > b ? "+" : ""}${Math.round((a - b) / b * 100)} %</span>` : "";
  const turnos = plan === "todos" ? suma(ds, d => (D.dias[d] || {}).turnos || 0) : null;
  const nombre = periodo === "hoy" ? "Hoy" : "Últimos " + n + " días", conActividad = valores.filter(v => v > 0).length;
  const contexto = periodo === "hoy" ? totalDias(plan, dias.slice(-10)) : valores;           // un solo día no hace curva: los últimos 10 de contexto
  const T = (id, ic, rot, der, valor, sub, abajo) => document.getElementById(id).innerHTML =
    `<div class="et">${icono(ic)}${rot}<span class="der">${der || ""}</span></div><div class="v">${valor}</div><div class="s">${sub}</div><div class="abajo">${abajo || ""}</div>`;
  T("k-hoy", periodo === "hoy" ? "pulso" : "cal", nombre, delta(total, antes), cifra(total),
    (turnos != null ? turnos.toLocaleString("es-ES") + " turnos · " : "") + (antes ? (periodo === "hoy" ? "vs. ayer" : "vs. " + n + " días anteriores") : (plan === "todos" ? "todos los planes" : plan)),
    chispa(contexto, plan === "todos" ? "--s1" : colorPlan(plan), 30));
  const media = periodo === "hoy" ? suma(totalDias(plan, dias.slice(-8, -1)), x => x) / 7 : total / Math.max(n, 1);
  T("k-semana", "caja", periodo === "hoy" ? "Media diaria de la semana" : "Media por día", "", cifra(media),
    periodo === "hoy" ? (media ? "hoy vas al " + Math.round(total / media * 100) + " % de un día normal" : "sin consumo la última semana") : "en " + n + " días" + (conActividad < n ? " · " + Math.round(total / Math.max(conActividad, 1) / 1e6).toLocaleString("es-ES") + " M los días con uso" : ""),
    periodo === "hoy" && media ? prog(total / media * 100, total > media ? "ambar" : "") : "");
  T("k-mes", "capas", "Días con actividad", "", `${conActividad}<small> / ${n}</small>`, `${dias.length} días guardados en los logs`, prog(conActividad / Math.max(n, 1) * 100, "", plan === "todos" ? "--s1" : colorPlan(plan)));
}

/* --- consumo por día, con detalle del día o tramo pulsado --- */
function diasGrafico(){ return periodo === "hoy" ? dias.slice(-7) : diasParaPeriodo(periodo); }
function elegirDia(i){ elegirDiaGuardado(dias.indexOf(diasGrafico()[i])); }
function elegirDiaGuardado(g){              // g: índice dentro de todos los días guardados
  if(!(g >= 0 && g < dias.length)) return;
  if(conMayus && seleccion) seleccion = {desde: Math.min(seleccion.desde, g), hasta: Math.max(seleccion.hasta, g)};
  else seleccion = seleccion && seleccion.desde === g && seleccion.hasta === g ? null : {desde: g, hasta: g};
  pintarDia();
}
function pintarDia(){
  const ds = diasGrafico(), sel = planesDe(plan);
  const series = sel.map(p => ({nombre: p, color: colorPlan(p), valores: porDiaPlan(p, ds)})).filter(s => s.valores.some(v => v));
  const tipo = vistaDia === "auto" ? (ds.length > 14 ? "area" : "barras") : vistaDia;
  const elegidos = new Set(); if(seleccion) ds.forEach((d, i) => { const g = dias.indexOf(d); if(g >= seleccion.desde && g <= seleccion.hasta) elegidos.add(i); });
  const caja = document.getElementById("sec-dia");
  caja.innerHTML = `<div class="sec"><span>Consumo por día <span class="nota">· ${fmt(suma(totalDias(plan, tipo === "calendario" ? diasParaPeriodo(periodo) : ds), x => x))}${periodo === "hoy" && tipo !== "calendario" ? " en 7 días" : ""}</span></span>
    <span class="nota"><span class="seg mini vidrio">${[["auto", "Auto"], ["barras", "Barras"], ["area", "Área"], ["calendario", "Calendario"]].map(([k, t]) => `<button class="${vistaDia === k ? "on" : ""}" data-a="setVistaDia" data-v="${k}">${t}</button>`).join("")}</span></span></div>
    <div class="tj"><div id="graf-dia"></div><div class="leyenda">${series.map(s => `<span>${punto(s.color)}${s.nombre}</span>`).join("")}<span class="tenue" style="margin-left:auto">pulsa un día para ver su detalle · con ⇧ eliges un tramo</span></div></div>`;
  const cont = document.getElementById("graf-dia");
  if(tipo === "calendario"){
    const todosDias = diasParaPeriodo(periodo), tot = totalDias(plan, todosDias), selCal = new Set();      // el calendario sigue al periodo: hoy, 7 o 30 días
    if(seleccion) todosDias.forEach((d, i) => { const g = dias.indexOf(d); if(g >= seleccion.desde && g <= seleccion.hasta) selCal.add(i); });
    cont.className = "graf";
    // cada celda lleva su índice dentro de todos los días guardados
    const desde = dias.length - todosDias.length;
    cont.innerHTML = calendario(todosDias.map((d, i) => [d, tot[i]]), {fmt, accion: "setDiaCal", indice: i => desde + i, sel: selCal, color: sel.length === 1 ? colorPlan(sel[0]) : "--s1", nombre: tituloDia});
    cont._datos = null;
  } else {
    grafico(cont, {tipo, series, etiquetas: ds.map(etiquetaDia), titulos: ds.map(tituloDia), fmt, accion: "setDia", sel: elegidos});
  }
  pintarDetalle();
}
function pintarDetalle(){
  const z = document.getElementById("detalle-dia");
  if(!seleccion){ z.innerHTML = ""; return; }
  const ds = dias.slice(seleccion.desde, seleccion.hasta + 1);
  const titulo = ds.length === 1 ? tituloDia(ds[0]) : `${tituloDia(ds[0])} → ${tituloDia(ds[ds.length-1])} · ${ds.length} días`;
  const total = suma(totalDias(plan, ds), x => x), turnos = suma(ds, d => (D.dias[d] || {}).turnos || 0);
  const lista = arr => arr.length ? arr.map(([n, v]) => `<div class="linea-det"><span>${n}</span><span>${fmt(v)}</span></div>`).join("") : '<div class="linea-det"><span class="vacio">sin datos</span></div>';
  const porPlan = planes().map(p => [p, suma(porDiaPlan(p, ds), x => x)]).filter(x => x[1] > 0).sort((a, b) => b[1] - a[1]);
  z.innerHTML = `<div class="cab-det"><b>${titulo}</b><span class="mono">${cifra(total)} · ${turnos} turnos</span><button data-a="cerrarDetalle" aria-label="Cerrar el detalle">✕</button></div>
    <div class="cols"><div><h4>Por modelo</h4>${lista(Object.entries(porModelo(plan, ds)).sort((a, b) => b[1] - a[1]).slice(0, 6))}</div>
    <div><h4>Por proyecto</h4>${lista(Object.entries(porProyecto(plan, ds)).sort((a, b) => b[1] - a[1]).slice(0, 6))}</div>
    <div><h4>Por plan</h4>${lista(porPlan)}</div></div>`;
}

/* --- planes y cuotas --- */
function pintarPlanes(){
  const P = D.proveedores || {}, k = orden[0], s = orden[1];
  const filas = Object.entries(P).filter(([n]) => plan === "todos" || n === plan)
    .sort((a, b) => { const v = x => k === "nombre" ? x[0].toLowerCase() : (x[1][k] || 0); return (v(a) > v(b) ? 1 : v(a) < v(b) ? -1 : 0) * s; });
  const maxMes = Math.max(...filas.map(([, x]) => x.mes || 0), 1);
  const btn = (c, t) => `<button class="orden${k === c ? " on" : ""}" data-a="setOrden" data-v="${c}" title="Ordenar por ${t}">${t}${k === c ? (s < 0 ? " ▾" : " ▴") : ""}</button>`;
  document.getElementById("sec-planes").innerHTML = `<div class="sec"><span>Planes</span><span class="nota">${btn("cinco_h", "5 h")}·${btn("semana", "semana")}·${btn("mes", "mes")}</span></div>
    <div class="lista">${filas.map(([n, x]) => { const c = colorPlan(n), q = Math.max(x.pct_5h || 0, x.pct_semana || 0);
      return `<div class="f"><div class="n">${punto(c)}<span>${n}</span>${x.auto ? '<span class="chip" title="Sale solo de tus logs">auto</span>' : ""}${(x.modelos_cuota || []).length ? '<span class="chip">cuota mensual</span>' : ""}</div>
        <div class="mono">${cifra(x.cinco_h)}${x.pct_5h ? ` <span class="tenue">${x.pct_5h} %</span>` : ""} <span class="tenue">·</span> ${fmt(x.semana)}${x.pct_semana ? ` <span class="tenue">${x.pct_semana} %</span>` : ""} <span class="tenue">·</span> ${cifra(x.mes)}</div>
        ${q ? prog(q, q >= 90 ? "rojo" : q >= 70 ? "ambar" : "") : prog((x.mes || 0) / maxMes * 100, "", c)}
        <div class="nota-f">${(x.modelos || []).join(", ") || "sin modelos con consumo"}</div></div>`; }).join("") || '<p class="vacio">sin planes</p>'}</div>`;
  const cuotas = [];
  filas.forEach(([n, x]) => (x.modelos_cuota || []).forEach(mc => cuotas.push({plan: n, fuente: x.cuota_fuente || "logs", ...mc})));
  cuotas.sort((a, b) => ((b.pct ?? -1) - (a.pct ?? -1)) || (b.mes - a.mes));
  const vivo = cuotas.filter(c => c.fuente === "api").map(c => c.plan);
  document.getElementById("sec-cuotas").innerHTML = `<div class="sec"><span>Cuotas del mes</span>${vivo.length ? `<span class="chip vivo"><i class="p"></i>en vivo · ${[...new Set(vivo)].join(", ")}</span>` : '<span class="nota">mes natural · desde el día 1</span>'}</div>
    <div class="lista">${cuotas.map(c => { const q = c.pct != null ? +c.pct : null, cls = q >= 90 ? "rojo" : q >= 70 ? "ambar" : "";
      return `<div class="f"><div class="n">${punto(q == null ? "--gris" : cls ? "--" + cls : "--verde")}<span>${c.modelo}</span></div>
        <div class="mono">${fmt(c.mes)} ${c.limite ? `<span class="tenue">/ ${fmt(c.limite)}</span> · ${String(q).replace(".", ",")} %` : '<span class="tenue">· sin tope</span>'}</div>
        ${q != null ? prog(q, cls) : `<div class="nota-f">${c.fuente === "api" ? "en vivo" : "solo de tus logs"} · ${c.plan} · ponle tope en Ajustes</div>`}</div>`; }).join("")
      || '<p class="vacio">Sin cuotas mensuales por modelo. Se ponen en Ajustes → Planes.</p>'}</div>`;
}

/* --- modelos y proyectos, cada uno con su periodo --- */
function segPeriodo(actual, accion){
  return `<div class="seg mini vidrio">${[["hoy", "Hoy"], ["semana", "7 d"], ["mes", "30 d"], ["todo", "Todo"]].map(([k, t]) => `<button class="${actual === k ? "on" : ""}" data-a="${accion}" data-v="${k}">${t}</button>`).join("")}</div>`;
}
function pintarModelos(){
  const ds = diasParaPeriodo(periodoModelo), m = Object.entries(porModelo(plan, ds)).sort((a, b) => b[1] - a[1]), total = suma(m, x => x[1]);
  const rango = {}, op = m.map(([n]) => { const p = (D.modelo_prov || {})[n] || ""; rango[p] = (rango[p] || 0) + 1; return [p, [1, .72, .5, .34][Math.min(rango[p] - 1, 3)]]; });
  const top = m.slice(0, 7), resto = suma(m.slice(7), x => x[1]);
  document.getElementById("sec-modelos").innerHTML = `<div class="sec"><span>Modelos</span><span class="nota">${segPeriodo(periodoModelo, "setPeriodoModelo")}</span></div><div class="tj">
    ${m.length ? `<div class="dona">${anillo(m.map(([, v], i) => [v, colorPlan(op[i][0]), op[i][1]]), 136, 14, fmt(total))}
      <div class="lista">${top.map(([n, v], i) => `<div class="f" style="padding:6px 0"><div class="n"><i class="p" style="background:var(${colorPlan(op[i][0])});opacity:${op[i][1]}"></i><span>${n}</span></div><div class="mono">${cifra(v)} <span class="tenue">${pct(v, total)}</span></div></div>`).join("")}
      ${resto ? `<div class="f" style="padding:6px 0"><div class="n tenue"><span>otros ${m.length - 7}</span></div><div class="mono">${fmt(resto)} <span class="tenue">${pct(resto, total)}</span></div></div>` : ""}</div></div>`
      : '<p class="vacio">sin consumo en este periodo</p>'}</div>`;
}
function pintarProyectos(){
  const ds = diasParaPeriodo(periodoProyecto), p = Object.entries(porProyecto(plan, ds)).sort((a, b) => b[1] - a[1]), total = suma(p, x => x[1]);
  const max = p.length ? p[0][1] : 1;
  document.getElementById("sec-proyectos").innerHTML = `<div class="sec"><span>Proyectos</span><span class="nota">${segPeriodo(periodoProyecto, "setPeriodoProyecto")}</span></div>
    <div class="lista">${p.slice(0, 8).map(([n, v]) => `<div class="f"><div class="n"><span class="tenue" style="display:inline-flex;width:15px;flex:none">${icono("carpeta")}</span><span>${n}</span></div>
      <div class="mono">${cifra(v)} <span class="tenue">${pct(v, total)}</span></div>${prog(v / max * 100, "", "--s1")}</div>`).join("") || '<p class="vacio">sin consumo en este periodo</p>'}</div>`;
}

/* --- matriz modelo × día como mapa de calor --- */
function pintarMatriz(){
  const ds = (periodo === "hoy" ? dias.slice(-7) : diasParaPeriodo(periodo)).slice(-14);
  const mm = Object.entries(porModelo(plan, ds)).sort((a, b) => b[1] - a[1]).slice(0, 8);
  const celda = (m, d) => suma(planesDe(plan), p => (((D.cruce || {})[p] || {})[m] || {})[d] || 0);
  let max = 0; mm.forEach(([m]) => ds.forEach(d => { max = Math.max(max, celda(m, d)); }));
  const caja = document.getElementById("sec-matriz");
  if(!mm.length){ caja.innerHTML = '<div class="sec"><span>Qué modelo consume cada día</span></div><div class="tj"><p class="vacio">sin datos</p></div>'; return; }
  caja.innerHTML = `<div class="sec"><span>Qué modelo consume cada día</span><span class="nota">últimos ${ds.length} días · intensidad = tokens del día</span></div>
    <div class="tj"><div class="mapa"><table><thead><tr><th>Modelo</th>${ds.map(d => `<th>${d === dias[dias.length-1] ? "hoy" : +d.slice(8)}</th>`).join("")}<th style="text-align:right">Total</th></tr></thead><tbody>
    ${mm.map(([m, t]) => { const c = colorPlan((D.modelo_prov || {})[m] || "");
      return `<tr><td class="nom">${punto(c)} ${m}</td>${ds.map(d => { const v = celda(m, d), o = v ? .14 + .86 * Math.sqrt(v / max) : 0;
        return `<td style="background:${v ? `color-mix(in srgb,var(${c}) ${Math.round(o*100)}%,transparent)` : "var(--pista)"}" title="${m} · ${tituloDia(d)}: ${fmt(v)}"></td>`; }).join("")}<td class="tot">${fmt(t)}</td></tr>`; }).join("")}
    </tbody></table></div><div class="escala">menos ${[.14, .35, .6, .85, 1].map(o => `<i style="background:color-mix(in srgb,var(--s1) ${o*100}%,transparent)"></i>`).join("")} más</div></div>`;
}

/* --- hoy en detalle y aplicaciones --- */
function pintarHoyYApps(){
  const h = D.hoy || {}, partes = [["Caché leída", h.cr, "--marca"], ["Caché escrita", h.cw, "--marca2"], ["Entrada", h.gi, "--s2"], ["Salida", h.go, "--s3"]], tot = suma(partes, x => x[1] || 0);
  document.getElementById("sec-hoy").innerHTML = `<div class="sec"><span>Hoy, en detalle</span><span class="nota">de dónde salen los tokens</span></div>
    <div class="tj">${tot ? `<div class="mezcla" style="height:10px;margin-top:2px">${partes.map(p => `<i style="flex:${Math.max((p[1] || 0) / tot, .015)};background:var(${p[2]})"></i>`).join("")}</div>
      <div class="rej" style="margin-top:14px;gap:10px">${partes.map(p => `<div class="c3"><div class="s" style="display:flex;align-items:center;gap:6px">${punto(p[2])}${p[0]}</div><div class="mono" style="font-size:16px;margin-top:2px">${cifra(p[1])}</div><div class="s tenue" style="font-size:11px">${pct(p[1] || 0, tot)}</div></div>`).join("")}</div>
      <div class="s abajo" style="white-space:normal;margin-top:12px">Cada turno reenvía el contexto: menos turnos y menos contexto pesan más que bajar el esfuerzo del modelo.</div>` : '<p class="vacio">todavía sin consumo hoy</p>'}</div>`;
  const apps = (D.bases || []).map(x => [String(x).split(" (")[0], +((String(x).match(/\((\d+)\)/) || [])[1] || 0)]), maxA = Math.max(...apps.map(a => a[1]), 1);
  document.getElementById("sec-apps").innerHTML = `<div class="sec"><span>Aplicaciones</span><span class="nota">${D.fuentes} sesiones leídas</span></div>
    <div class="lista">${apps.map(([n, v], i) => `<div class="f"><div class="n">${punto(colorSerie(i))}<span>${n}</span></div><div class="mono">${v ? v + " sesiones" : ""}</div>${prog(v / maxA * 100, "", colorSerie(i))}</div>`).join("")
      || '<p class="vacio">sin aplicaciones detectadas</p>'}</div>`;
}

function pintar(){
  pintarFiltros(); pintarKpis(); pintarDia(); pintarPlanes(); pintarModelos(); pintarProyectos(); pintarMatriz(); pintarHoyYApps();
  document.getElementById("pie").innerHTML = `<span>Generado ${D.generado} · se actualiza cada minuto · ${D.fuentes} sesiones${(D.bases || []).length ? " de " + D.bases.map(b => String(b).split(" (")[0]).join(", ") : ""} · turnos repetidos descartados</span>
    <span>Equivalente a precio de API: US$ ${Math.round((D.mes || {}).usd || 0).toLocaleString("es-ES")} en 30 días (solo informativo: con suscripción se gasta cuota, no dinero)</span>`;
}
addEventListener("DOMContentLoaded", () => { botonTema(); });
addEventListener("load", () => pintarCab(location.hash.slice(1) || "panel"));       // Histórico/Servidores/Ajustes pueden abrirse por #hash
botonTema(); pintarCab("panel");
pintar();
