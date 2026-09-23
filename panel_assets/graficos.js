/* Kit de graficos: funciones puras, sin estado ni DOM propio.
   Reciben datos ya agregados ([[etiqueta, valor], ...]) y devuelven HTML/SVG.
   Los elementos clicables llevan data-k="<clave>"; quien los use (app.js) pone
   los listeners despues de insertar el HTML — aqui no hay onclick ni fetch. */

const COLORES = ["#ae00ff","#ff008c","#12b886","#4c6ef5","#f59f00","#e8590c","#0ca678","#7048e8","#d6336c","#1098ad"];
function colorI(i){ return COLORES[i % COLORES.length]; }

/* --- barras horizontales (la vista clasica, con divs) --- */
function _clave(d){ return d.length > 2 ? d[2] : d[0]; }

function barrasH(datos, opts={}){
  const fmt = opts.fmt || (n=>String(n));
  if(!datos.length) return "<p class='vacio'>sin datos</p>";
  const tope = Math.max.apply(null, datos.map(d=>d[1])) || 1;
  return datos.map(d => {
    const k = _clave(d);
    const sel = opts.seleccionadas && opts.seleccionadas.has(k) ? " sel" : "";
    return `<div class="fila${sel}" data-k="${k}" style="${opts.clicable?"cursor:pointer":""}">
      <span class="et">${d[0]}</span>
      <span class="pista"><span class="barra" style="width:${Math.max(d[1]/tope*100,1.5).toFixed(1)}%"></span></span>
      <span class="val">${fmt(d[1])}</span></div>`;
  }).join("");
}

/* --- barras verticales en SVG --- */
function barrasV(datos, opts={}){
  const fmt = opts.fmt || (n=>String(n));
  if(!datos.length) return "<p class='vacio'>sin datos</p>";
  const w = 640, h = 180, padB = 22, padT = 6;
  const tope = Math.max.apply(null, datos.map(d=>d[1])) || 1;
  const n = datos.length, bw = w / n;
  const barras = datos.map((d, i) => {
    const alto = Math.max((d[1] / tope) * (h - padB - padT), 2);
    const x = i * bw + bw * 0.15, bwr = bw * 0.7, y = h - padB - alto;
    const k = _clave(d);
    const sel = opts.seleccionadas && opts.seleccionadas.has(k) ? " sel" : "";
    return `<rect class="barra-v${sel}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bwr.toFixed(1)}" height="${alto.toFixed(1)}" data-k="${k}"><title>${d[0]}: ${fmt(d[1])}</title></rect>`;
  }).join("");
  const paso = Math.max(1, Math.ceil(n / 10));
  const etqs = datos.map((d, i) => (i % paso !== 0 && i !== n - 1) ? "" :
    `<text class="etq-eje" x="${(i * bw + bw / 2).toFixed(1)}" y="${h - 6}" text-anchor="middle">${d[0]}</text>`).join("");
  return `<svg class="grafico-svg" viewBox="0 0 ${w} ${h}">
    ${barras}${etqs}<line class="eje" x1="0" y1="${h - padB}" x2="${w}" y2="${h - padB}"/></svg>`;
}

/* --- linea con area, para tendencias largas --- */
function lineas(datos, opts={}){
  const fmt = opts.fmt || (n=>String(n));
  if(!datos.length) return "<p class='vacio'>sin datos</p>";
  const w = 640, h = 180, padB = 22, padT = 10, padL = 4, padR = 4;
  const tope = Math.max.apply(null, datos.map(d=>d[1])) || 1;
  const n = datos.length;
  const x = i => n === 1 ? w / 2 : padL + i * (w - padL - padR) / (n - 1);
  const y = v => padT + (1 - v / tope) * (h - padB - padT);
  const pts = datos.map((d, i) => `${x(i).toFixed(1)},${y(d[1]).toFixed(1)}`).join(" ");
  const area = `${x(0).toFixed(1)},${h - padB} ${pts} ${x(n - 1).toFixed(1)},${h - padB}`;
  const circulos = datos.map((d, i) => {
    const k = _clave(d);
    const sel = opts.seleccionadas && opts.seleccionadas.has(k) ? " sel" : "";
    return `<circle class="punto-linea${sel}" cx="${x(i).toFixed(1)}" cy="${y(d[1]).toFixed(1)}" r="3.5" data-k="${k}"><title>${d[0]}: ${fmt(d[1])}</title></circle>`;
  }).join("");
  const paso = Math.max(1, Math.ceil(n / 8));
  const etqs = datos.map((d, i) => (i % paso !== 0 && i !== n - 1) ? "" :
    `<text class="etq-eje" x="${x(i).toFixed(1)}" y="${h - 6}" text-anchor="middle">${d[0]}</text>`).join("");
  return `<svg class="grafico-svg" viewBox="0 0 ${w} ${h}">
    <polygon class="area" points="${area}"/><polyline class="linea" points="${pts}"/>
    ${circulos}${etqs}<line class="eje" x1="0" y1="${h - padB}" x2="${w}" y2="${h - padB}"/></svg>`;
}

/* --- tarta / donut: mismo dibujo, con o sin hueco central --- */
function _arco(cx, cy, r, a0, a1){
  return {
    x0: cx + r * Math.cos(a0), y0: cy + r * Math.sin(a0),
    x1: cx + r * Math.cos(a1), y1: cy + r * Math.sin(a1),
    largo: (a1 - a0) > Math.PI ? 1 : 0,
  };
}
function _sectores(datos, opts, huecoFrac){
  const fmt = opts.fmt || (n=>String(n));
  datos = datos.filter(d => d[1] > 0);
  if(!datos.length) return "<p class='vacio'>sin datos</p>";
  const total = datos.reduce((a, d) => a + d[1], 0) || 1;
  const w = 220, h = 220, cx = 110, cy = 110, r = 100, rh = r * huecoFrac;
  let ang = -Math.PI / 2;
  const partes = datos.map((d, i) => {
    const frac = d[1] / total, a0 = ang, a1 = ang + frac * 2 * Math.PI; ang = a1;
    const o = _arco(cx, cy, r, a0, a1);
    let dpath;
    if(huecoFrac > 0){
      const ih = _arco(cx, cy, rh, a0, a1);
      dpath = `M${o.x0.toFixed(1)},${o.y0.toFixed(1)} A${r},${r} 0 ${o.largo} 1 ${o.x1.toFixed(1)},${o.y1.toFixed(1)} L${ih.x1.toFixed(1)},${ih.y1.toFixed(1)} A${rh},${rh} 0 ${o.largo} 0 ${ih.x0.toFixed(1)},${ih.y0.toFixed(1)} Z`;
    } else {
      dpath = `M${cx},${cy} L${o.x0.toFixed(1)},${o.y0.toFixed(1)} A${r},${r} 0 ${o.largo} 1 ${o.x1.toFixed(1)},${o.y1.toFixed(1)} Z`;
    }
    return `<path class="sector" d="${dpath}" fill="${colorI(i)}" data-k="${d[0]}"><title>${d[0]}: ${fmt(d[1])} (${(frac * 100).toFixed(1)} %)</title></path>`;
  }).join("");
  const centro = huecoFrac > 0
    ? `<text class="etq-valor" x="${cx}" y="${cy}" text-anchor="middle" dominant-baseline="middle" style="font-size:15px;font-weight:600">${fmt(total)}</text>`
    : "";
  const leyenda = `<div class="leyenda-tarta">${datos.map((d, i) =>
    `<span class="it"><span class="pt" style="background:${colorI(i)}"></span><span>${d[0]} · ${(d[1] / total * 100).toFixed(1)} %</span></span>`).join("")}</div>`;
  return `<div class="tarta-envoltorio"><svg class="grafico-svg" viewBox="0 0 ${w} ${h}" style="max-width:220px">${partes}${centro}</svg>${leyenda}</div>`;
}
function tarta(datos, opts={}){ return _sectores(datos, opts, 0); }
function donut(datos, opts={}){ return _sectores(datos, opts, 0.58); }

/* --- calendario: mapa de calor, una celda por dia --- */
function calendario(dias, opts={}){
  const fmt = opts.fmt || (n=>String(n));
  if(!dias.length) return "<p class='vacio'>sin datos</p>";
  const max = Math.max.apply(null, dias.map(d => d[1])) || 1;
  const relleno = new Date(dias[0][0] + "T12:00:00").getDay();
  const vacias = Array(relleno).fill('<div class="celda vacia"></div>').join("");
  const celdas = dias.map(([f, v]) => {
    const frac = v / max;
    const sel = opts.seleccionadas && opts.seleccionadas.has(f) ? " sel" : "";
    const bg = v ? `background:rgba(174,0,255,${(0.12 + frac * 0.8).toFixed(2)})` : "";
    const dia = new Date(f + "T12:00:00").getDate();
    return `<div class="celda${sel}" style="${bg}" data-k="${f}" title="${f}: ${fmt(v)}">${dia}</div>`;
  }).join("");
  const cab = ["D", "L", "M", "X", "J", "V", "S"].map(d => `<div class="dow">${d}</div>`).join("");
  return `<div class="calendario">${cab}${vacias}${celdas}</div>`;
}

/* --- barras apiladas por dia, segmentadas por modelo --- */
function barrasApiladas(dias, series, opts={}){
  const fmt = opts.fmt || (n=>String(n));
  if(!dias.length || !series.length) return "<p class='vacio'>sin datos</p>";
  const totales = dias.map(d => series.reduce((s, [, vals]) => s + (vals[d] || 0), 0));
  const max = Math.max.apply(null, totales) || 1;
  const cols = dias.map(d => {
    const segs = series.map(([nombre, vals], si) => {
      const v = vals[d] || 0;
      if(!v) return "";
      return `<div style="height:${(v / max * 100).toFixed(2)}%;background:${colorI(si)}" title="${nombre}: ${fmt(v)}"></div>`;
    }).join("");
    const sel = opts.seleccionadas && opts.seleccionadas.has(d) ? " sel" : "";
    return `<div class="col${sel}" data-k="${d}">${segs}</div>`;
  }).join("");
  const paso = Math.max(1, Math.ceil(dias.length / 10));
  const etqs = dias.map((d, i) => `<span>${(i % paso === 0 || i === dias.length - 1) ? d.slice(8) : ""}</span>`).join("");
  const leyenda = `<div class="leyenda-tarta">${series.map(([n], i) =>
    `<span class="it"><span class="pt" style="background:${colorI(i)}"></span>${n}</span>`).join("")}</div>`;
  return `<div class="apiladas">${cols}</div><div class="etqs">${etqs}</div>${leyenda}`;
}

/* --- iconos de las pestañas: SVG minimo, monocromo (sigue el color del texto del boton) --- */
const ICONOS = {
  barrasH: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><line x1="2" y1="4" x2="13" y2="4"/><line x1="2" y1="8" x2="10" y2="8"/><line x1="2" y1="12" x2="12" y2="12"/></svg>',
  barrasV: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><line x1="3" y1="13" x2="3" y2="6"/><line x1="8" y1="13" x2="8" y2="2"/><line x1="13" y1="13" x2="13" y2="9"/></svg>',
  lineas: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><polyline points="2,12 6,6 9,9 14,3" stroke-linejoin="round" stroke-linecap="round"/></svg>',
  calendario: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3" width="12" height="11" rx="1.5"/><line x1="2" y1="7" x2="14" y2="7"/><line x1="5" y1="1.5" x2="5" y2="4"/><line x1="11" y1="1.5" x2="11" y2="4"/></svg>',
  tarta: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="6.2"/><path d="M8 8 L8 1.8 A6.2 6.2 0 0 1 13.4 11 Z" fill="currentColor" stroke="none"/></svg>',
  donut: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="6.2"/><circle cx="8" cy="8" r="3"/></svg>',
  matriz: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="2" y="2" width="12" height="12" rx="1"/><line x1="2" y1="6.7" x2="14" y2="6.7"/><line x1="2" y1="11.3" x2="14" y2="11.3"/><line x1="6.7" y1="2" x2="6.7" y2="14"/><line x1="11.3" y1="2" x2="11.3" y2="14"/></svg>',
  apiladas: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="2.5" y="7" width="3" height="6"/><rect x="2.5" y="3" width="3" height="3"/><rect x="6.5" y="9" width="3" height="4"/><rect x="6.5" y="5" width="3" height="3"/><rect x="10.5" y="4" width="3" height="9"/></svg>',
};
