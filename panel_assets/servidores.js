/* Pestaña Servidores: de un servidor, sus webs, sus contenedores y servicios y lo que ha pasado (línea de tiempo). Todo se construye con nodos del DOM (nunca con HTML armado a mano):
   los nombres vienen del servidor. Los estados salen de un mapa cerrado a clases fijas; lo que no se conoce se pinta como «desconocido», nunca como correcto.
   Nada se enseña como bueno si su última lectura es vieja: la respuesta trae la ventana de frescura y cada fila su última lectura. */
(function(){
  const {h, post, ERRORES_PANEL} = window.CG;
  const $ = id => document.getElementById(id);
  const MAX_FILAS = 500;
  // estado → [clase fija, texto]
  const ESTADO_WEB = {ok: ["sv-ok", "Responde"], mal: ["sv-aviso", "Con error o lenta"], caida: ["sv-mal", "Caída"]};
  const ESTADO_RECURSO = {ok: ["sv-ok", "En marcha"], mal: ["sv-aviso", "Con problemas"], caido: ["sv-mal", "Caído"], reiniciando: ["sv-mal", "Reiniciando"],
                          terminado: ["sv-neutro", "Terminado"], otro: ["sv-neutro", "Otro estado"]};
  const ESTADO_GLOBAL = {verde: ["sv-ok", "verde"], ambar: ["sv-aviso", "ámbar"], rojo: ["sv-mal", "rojo"], gris: ["sv-neutro", "sin dato"], error: ["sv-mal", "con error"]};
  let servidores = [], elegido = null, peticion = 0, temporizador = null, verAltas = false, idosAbierto = false, probando = false, ultimoAviso = null;

  const hace = (ts, ahora) => {
    if (!ts) return "—";
    const s = Math.max(0, ahora - ts);
    return s < 90 ? "hace un momento" : s < 5400 ? "hace " + Math.round(s / 60) + " min" : s < 172800 ? "hace " + Math.round(s / 3600) + " h" : "hace " + Math.round(s / 86400) + " días";
  };
  const hora = ts => new Date(ts * 1000).toLocaleTimeString("es-ES", {hour: "2-digit", minute: "2-digit"});
  const dia = ts => new Date(ts * 1000).toLocaleDateString("es-ES", {weekday: "long", day: "numeric", month: "long"});
  const clase = (mapa, clave) => Object.prototype.hasOwnProperty.call(mapa, clave) ? mapa[clave] : ["sv-neutro", "Desconocido"];
  const celda = (t, extra) => h("td", extra || {}, t);
  // una web se abre en una pestaña nueva, solo si es una dirección http(s) de caracteres normales (nunca esquemas raros ni nada que venga extraño del servidor)
  const URL_OK = /^https?:\/\/[A-Za-z0-9._~:\/?#@!$&*+,;=%-]{1,300}$/;
  const corta = u => u.replace(/^https?:\/\//, "").replace(/\/$/, "");
  const enlaceWeb = u => URL_OK.test(u) ? h("a", {href: u, target: "_blank", rel: "noopener noreferrer", class: "sv-nombre", title: "Abrir " + u + " en una pestaña nueva"}, corta(u)) : h("span", {class: "sv-nombre", title: u}, corta(u));
  const insignia = (cl, texto, titulo) => h("span", {class: "sv-ins " + cl, title: titulo || ""}, texto);

  function disponibilidad(w, k){
    const d = w["disp_" + k], n = w["n_" + k], mal = w["mal_" + k];
    if (d == null || !n) return h("span", {class: "sv-suave"}, "—");
    return h("span", {title: "Sondeos que respondieron, entre " + n + " (" + mal + " con error o lentos). Medido desde este Mac."},
      (d === 100 || d === 0 ? String(d) : (Math.floor(d * 10) / 10).toFixed(1).replace(".", ",")) + " %", h("span", {class: "sv-suave"}, " de " + n), n < 30 ? h("span", {class: "sv-suave"}, " · pocos datos") : null);
  }

  function tablaWebs(d, fresco){
    if (!d.webs.length) return h("p", {class: "vacio"}, "Este servidor no tiene webs vigiladas. Se añaden en Ajustes.");
    const filas = d.webs.map(w => {
      let est;
      if (!w.sondeada) est = insignia("sv-neutro", "Sin sondear todavía");
      else if (!fresco(w.ultima)) est = insignia("sv-neutro", "Sin medir " + hace(w.ultima, d.generado_en).replace("hace ", "desde hace "));
      else { const [cl, tx] = clase(ESTADO_WEB, w.estado); est = insignia(cl, tx); }
      const cert = w.cert_dias == null ? "—" : w.cert_dias + " d";
      const certCl = w.cert_dias == null ? "" : w.cert_dias <= 7 ? "sv-mal" : w.cert_dias <= 14 ? "sv-aviso" : "";
      return h("tr", {}, celda(enlaceWeb(w.url)), celda(est),
        celda(w.sondeada && w.codigo != null ? String(w.codigo) : "—"), celda(w.sondeada && w.ms != null ? w.ms + " ms" : "—"),
        celda(w.sondeada ? disponibilidad(w, "24h") : "—"), celda(w.sondeada ? disponibilidad(w, "7d") : "—"), celda(w.sondeada ? disponibilidad(w, "30d") : "—"),
        celda(h("span", {class: certCl}, cert)));
    });
    return h("div", {class: "sv-tabla"}, h("table", {class: "sv"}, h("thead", {}, h("tr", {}, ...["Web", "Estado", "Código", "Respuesta", "Últimas 24 h", "7 días", "30 días", "Certificado"].map(t => h("th", {}, t)))), h("tbody", {}, ...filas)),
      h("p", {class: "ayuda"}, "La disponibilidad es el porcentaje de sondeos que respondieron (los de «error o lenta» cuentan como respuesta) y solo mide mientras este Mac está encendido: no es un «uptime»."));
  }

  function agrupa(recursos){
    const prefijos = {};
    for (const r of recursos) if (r.tipo === "servicio" && r.nombre.includes("_")) { const p = r.nombre.split("_")[0]; prefijos[p] = (prefijos[p] || 0) + 1; }
    const grupos = new Map();
    for (const r of recursos) {
      let g = "Sueltos", corto = r.nombre;
      if (r.tipo === "compose" && r.nombre.includes("/")) { g = r.nombre.split("/")[0]; corto = r.nombre.slice(g.length + 1); }
      else if (r.tipo === "servicio" && r.nombre.includes("_") && prefijos[r.nombre.split("_")[0]] >= 2) { g = r.nombre.split("_")[0]; corto = r.nombre.slice(g.length + 1); }
      if (!grupos.has(g)) grupos.set(g, []);
      grupos.get(g).push(Object.assign({}, r, {corto}));
    }
    return grupos;
  }

  function filaRecurso(r, d, fresco){
    let est, reinicios = r.reinicios == null ? "—" : String(r.reinicios);
    if (r.activo && fresco(r.ultima)) { const [cl, tx] = clase(ESTADO_RECURSO, r.estado); est = insignia(cl, tx); }
    else if (r.activo) est = insignia("sv-neutro", "Sin lectura " + hace(r.ultima, d.generado_en).replace("hace ", "desde hace "));
    else if (r.visto_n > 0 && fresco(r.ultima)) est = insignia("sv-neutro", "Pendiente de confirmar");
    else est = insignia("sv-neutro", "Ya no está · visto " + hace(r.ultima, d.generado_en));
    return h("tr", {}, celda(h("span", {class: "sv-nombre", title: r.nombre}, r.corto)), celda(est), celda(h("span", {title: "Reinicios desde que se creó el contenedor"}, reinicios)),
      celda(r.tipo === "compose" ? "Compose" : r.tipo === "servicio" ? "Swarm" : "Contenedor"));
  }

  function bloqueRecursos(d, fresco){
    if (!d.recursos.length) return h("p", {class: "vacio"}, "Todavía no hay contenedores ni servicios leídos (hace falta la lectura de root con Docker).");
    const vivos = d.recursos.filter(r => r.activo || (r.visto_n > 0 && fresco(r.ultima)));
    const idos = d.recursos.filter(r => !vivos.includes(r));
    const gravedad = r => r.activo && fresco(r.ultima) ? ({caido: 0, reiniciando: 0, mal: 1, ok: 3}[r.estado] ?? 2) : 4;
    const piezas = [];
    const grupos = [...agrupa(vivos)].sort((a, b) => (a[0] === "Sueltos") - (b[0] === "Sueltos") || Math.min(...a[1].map(gravedad)) - Math.min(...b[1].map(gravedad)) || a[0].localeCompare(b[0]));
    for (const [g, rs] of grupos) {
      rs.sort((a, b) => gravedad(a) - gravedad(b) || a.corto.localeCompare(b.corto));
      const peor = Math.min(...rs.map(gravedad));
      piezas.push(h("div", {class: "sv-grupo"}, h("div", {class: "sv-grupo-cab"}, h("strong", {}, g), h("span", {class: "sv-suave"}, rs.length + (rs.length === 1 ? " elemento" : " elementos")), peor <= 1 ? insignia(peor === 0 ? "sv-mal" : "sv-aviso", "!", "Hay algo que mirar") : null),
        h("table", {class: "sv sv-rec"}, h("tbody", {}, ...rs.map(r => filaRecurso(r, d, fresco))))));
    }
    if (idos.length) piezas.push(h("details", {open: idosAbierto || null, ontoggle: ev => { idosAbierto = ev.target.open; }}, h("summary", {}, idos.length + (idos.length === 1 ? " que ya no está" : " que ya no están") + " (últimos 30 días)"),
      h("table", {class: "sv sv-rec"}, h("tbody", {}, ...idos.map(r => filaRecurso(Object.assign({}, r, {corto: r.nombre}), d, fresco))))));
    if (d.recursos_truncado) piezas.push(h("p", {class: "ayuda"}, "Hay más de " + d.recursos.length + " elementos: se enseñan los más recientes."));
    piezas.push(h("p", {class: "ayuda"}, "Agrupado según el nombre que da el servidor, no verificado. «Reinicios» cuenta desde que se creó el contenedor; el aviso de bucle sale del evento, no del contador."));
    return h("div", {}, ...piezas);
  }

  const ESTADO_DESPLIEGUE = {done: ["sv-ok", "Correcto"], error: ["sv-mal", "Falló"], running: ["sv-neutro", "En curso"], idle: ["sv-neutro", "Sin despliegues"]};
  const ESTADO_COPIA = {al_dia: ["sv-ok", "Al día"], antigua: ["sv-aviso", "Sin hacer desde hace más de lo normal"], sin_buena: ["sv-neutro", "Sin copia buena registrada"]};
  const dias = s => s >= 86400 * 2 ? "cada " + Math.round(s / 86400) + " días" : s >= 7200 ? "cada " + Math.round(s / 3600) + " h" : "";

  function cabeceraPanel(d, p, frescoPanel){
    const piezas = [];
    if (p.error) piezas.push(insignia(p.error === "alcance" ? "sv-aviso" : "sv-mal", p.error === "alcance" ? "El alcance de la clave ha cambiado" : (ERRORES_PANEL[p.error] || "No se pudo leer el panel"),
      p.error === "alcance" ? "Dokploy devolvió muchos menos servicios que antes (¿se le quitaron proyectos al usuario?). Se conserva lo anterior y no se evalúa nada nuevo." : ""));
    piezas.push(h("span", {class: "sv-suave"}, (piezas.length ? " " : "") + (p.ok_en ? "Última lectura buena del panel " + hace(p.ok_en, d.generado_en) : "Aún sin lectura buena del panel") + (p.version ? " · Dokploy " + p.version : "") + (p.version_no_probada ? " (más nueva que las que he probado)" : "") + "."));
    return h("p", {class: "sv-estado"}, ...piezas);
  }

  function tablaCopias(d, p, frescoPanel){
    if (!p.copias.length) return h("p", {class: "vacio"}, "Todavía no hay copias registradas: Dokploy no ha ejecutado ninguna que este usuario pueda ver.");
    const filas = p.copias.map(c => {
      let est;
      if (!frescoPanel) est = insignia("sv-neutro", "Sin lectura del panel");
      else { const [cl, tx] = clase(ESTADO_COPIA, c.evaluacion); est = insignia(cl, tx); }
      const ultima = c.ultima_ok ? hace(c.ultima_ok, d.generado_en) : "—";
      const fallo = c.ultimo_estado === "error" ? " · la última ejecución falló" : c.ultimo_estado === "running" ? " · en curso" : "";
      return h("tr", {}, celda(h("span", {class: "sv-nombre", title: c.etiqueta}, c.etiqueta)), celda(c.tipo === "volumeBackup" ? "Volumen" : "Base de datos"), celda(est),
        celda(h("span", {}, ultima, h("span", {class: "sv-suave"}, (c.intervalo_s ? " · " + dias(c.intervalo_s) : "") + fallo))));
    });
    return h("div", {class: "sv-tabla"}, h("table", {class: "sv"}, h("thead", {}, h("tr", {}, ...["Copia", "Tipo", "Estado", "Última buena"].map(t => h("th", {}, t)))), h("tbody", {}, ...filas)),
      h("p", {class: "ayuda"}, "«Buena» es una ejecución que terminó sin error (done): no garantiza que la copia se pueda restaurar. La cadencia se aprende de las copias vistas, así que una semanal no salta a los 3 días."));
  }

  function arbolProyectos(d, p, frescoPanel){
    if (!p.servicios.length) return h("p", {class: "vacio"}, "El panel no devuelve servicios a este usuario (¿se le asignaron proyectos?).");
    const porProyecto = new Map();
    for (const s of p.servicios) { const k = s.proyecto || "(sin proyecto)"; if (!porProyecto.has(k)) porProyecto.set(k, []); porProyecto.get(k).push(s); }
    const gravedad = s => s.estado === "error" ? 0 : s.estado == null ? 1 : 2;
    const piezas = [];
    for (const [nombre, ss] of [...porProyecto].sort((a, b) => Math.min(...a[1].map(gravedad)) - Math.min(...b[1].map(gravedad)) || a[0].localeCompare(b[0]))) {
      ss.sort((a, b) => gravedad(a) - gravedad(b) || String(a.nombre || a.slug).localeCompare(String(b.nombre || b.slug)));
      const filas = ss.map(s => {
        const [cl, tx] = frescoPanel ? clase(ESTADO_DESPLIEGUE, s.estado) : ["sv-neutro", "Sin lectura del panel"];
        const doms = s.dominios.map(x => x.vigilado ? enlaceWeb("https://" + x.host + "/") : h("span", {class: "sv-nombre sv-suave", title: "El panel lo sirve, pero no lo vigilas"}, x.host));
        return h("tr", {}, celda(h("span", {class: "sv-nombre", title: s.nombre || s.slug || s.id}, s.nombre || s.slug || s.id)), celda(({application: "Aplicación", compose: "Compose"})[s.tipo] || "Base de datos"),
          celda(s.tipo === "application" || s.tipo === "compose" ? h("span", {}, insignia(cl, tx, "Estado del último despliegue, no de si el contenedor está en marcha"), s.despliegue ? h("span", {class: "sv-suave"}, " " + hace(s.despliegue, d.generado_en)) : null) : h("span", {class: "sv-suave"}, "—")),
          celda(h("span", {}, ...doms.flatMap((x, i) => i ? [", ", x] : [x]))));
      });
      piezas.push(h("div", {class: "sv-grupo"}, h("div", {class: "sv-grupo-cab"}, h("strong", {}, nombre), h("span", {class: "sv-suave"}, ss.length + (ss.length === 1 ? " elemento" : " elementos"))),
        h("table", {class: "sv"}, h("tbody", {}, ...filas))));
    }
    piezas.push(h("p", {class: "ayuda"}, "El estado es el del último despliegue según el panel; si un contenedor está o no en marcha sale de la lectura por SSH (más arriba). Los dominios en gris los sirve el panel pero no están entre tus webs vigiladas."));
    return h("div", {}, ...piezas);
  }

  function textoEvento(e){
    if (e.origen === "web") {
      if (e.evento === "cayo") return ["sv-mal", "Una web cayó" + (e.codigo ? " (código " + e.codigo + ")" : "")];
      if (e.evento === "volvio") return e.codigo >= 400 ? ["sv-aviso", "Volvió a responder, pero con error " + e.codigo] : ["sv-ok", "Volvió a responder"];
      return ["sv-aviso", "Una web empezó a ir mal" + (e.codigo ? " (código " + e.codigo + ")" : "")];
    }
    const mapa = {aparecio: ["sv-neutro", "Apareció"], desaparecio: ["sv-aviso", "Desapareció"], reinicios_en_bucle: ["sv-mal", "En bucle de reinicios"],
                  inestable: ["sv-aviso", "Inestable (cambia de estado a menudo)"], cambio_estado: e.severidad === "aviso" || e.severidad === "critica" ? ["sv-aviso", "Empeoró"] : ["sv-neutro", "Cambió de estado"]};
    return mapa[e.evento] || ["sv-neutro", "Evento"];
  }

  function lineaTiempo(d){
    const ev = d.eventos.filter(e => verAltas || !(e.origen === "recurso" && e.tipo === "contenedor" && (e.evento === "aparecio" || e.evento === "desaparecio")));
    const control = h("label", {class: "sv-check"}, h("input", {type: "checkbox", checked: verAltas || null, onchange: ev2 => { verAltas = ev2.target.checked; pinta(d); }}), " Mostrar altas y bajas de contenedores sueltos");
    const vigil = d.servidor.vigilado_desde ? "Vigilancia desde " + new Date(d.servidor.vigilado_desde * 1000).toLocaleDateString("es-ES") : "";
    const cobertura = h("p", {class: "ayuda"}, vigil + (vigil ? " · " : "") + "con datos " + d.horas.con_datos + " de las " + d.horas.total + " horas de los últimos 14 días. Un hueco es «sin datos» (Mac apagado o sin red), no «todo bien».");
    if (!ev.length) return h("div", {}, control, h("p", {class: "vacio"}, "Sin eventos en los últimos 14 días."), cobertura);
    // un mismo evento del mismo elemento en el mismo día se junta: «cayó 4 veces»
    const porDia = new Map();
    for (const e of ev) {
      const k = dia(e.ts), clave = [e.origen, e.tipo || "", e.evento, e.severidad || "", e.nombre || "", e.codigo || ""].join("|");
      if (!porDia.has(k)) porDia.set(k, new Map());
      const m = porDia.get(k);
      if (m.has(clave)) m.get(clave).veces++; else m.set(clave, {e, veces: 1});
    }
    const piezas = [];
    for (const [k, m] of porDia) {
      piezas.push(h("div", {class: "sv-dia"}, k));
      for (const {e, veces} of m.values()) {
        const [cl, tx] = textoEvento(e);
        piezas.push(h("div", {class: "sv-evento"}, h("span", {class: "sv-hora"}, hora(e.ts)), insignia(cl, tx), e.nombre ? (e.origen === "web" ? enlaceWeb(e.nombre) : h("span", {class: "sv-nombre", title: e.nombre}, e.nombre)) : h("span", {class: "sv-nombre"}, "(ya olvidado)"),
          veces > 1 ? h("span", {class: "sv-suave"}, "× " + veces) : null));
      }
    }
    if (d.truncado) piezas.push(h("p", {class: "ayuda"}, "Hay más eventos antiguos que no se enseñan (se muestran los " + d.eventos.length + " más recientes)."));
    return h("div", {}, control, ...piezas, cobertura);
  }

  // iconos del kit (cadenas fijas de graficos.js, nunca datos) convertidos en nodos sin interpretar HTML
  const iconoDom = n => new DOMParser().parseFromString(icono(n).replace("<svg ", "<svg xmlns=\"http://www.w3.org/2000/svg\" "), "image/svg+xml").documentElement;
  const COLOR = {verde: "--verde", ambar: "--ambar", rojo: "--rojo"};
  const punto = est => h("i", {class: "p", style: "background:var(" + (COLOR[est] || "--gris") + ")"});
  const gb = b => b == null ? "—" : (b / 1e9).toFixed(1).replace(".", ",");
  const titulo = (t, nota) => h("div", {class: "sec"}, h("span", {}, t), nota != null ? h("span", {class: "nota"}, nota) : null);
  const bloque = (t, nota, ...cuerpo) => h("div", {class: "col-f"}, titulo(t, nota), h("div", {class: "tj"}, ...cuerpo));
  function tarjeta(ic, rot, valor, sub, est, pct, clase_){
    return h("div", {class: "tj " + (clase_ || "c3")}, h("div", {class: "et"}, iconoDom(ic), rot, h("span", {class: "der"}, punto(est))),
      h("div", {class: "v"}, ...[].concat(valor)), h("div", {class: "s"}, sub),
      pct != null ? h("div", {class: "prog abajo"}, h("i", {class: est === "rojo" ? "rojo" : est === "ambar" ? "ambar" : "", style: "width:" + Math.max(0, Math.min(100, pct)).toFixed(1) + "%"})) : null);
  }

  // la última lectura del ciclo (la misma del desplegable): procesador, memoria, disco, webs y seguridad
  function tarjetasVivo(d, fresco){
    const v = d.vivo, t = (v && v.tarjetas) || {};
    if (!v) return h("p", {class: "vacio"}, "Todavía sin lectura en vivo de este servidor: llega con el próximo ciclo.");
    const viejo = !fresco(v.ok_en), est = k => viejo ? "gris" : t[k];
    const webs = d.webs.filter(w => w.sondeada && fresco(w.ultima)), bien = webs.filter(w => w.estado === "ok"), ms = webs.filter(w => w.ms != null);
    const media = ms.length ? Math.round(ms.reduce((a, w) => a + w.ms, 0) / ms.length) : null;
    const cpu = v.cpu.pct, m = v.memoria, k = v.disco;
    return h("div", {class: "rej"},
      tarjeta("web", "Webs", d.webs.length ? bien.length + " de " + d.webs.length : "—", d.webs.length ? "responden" + (media != null ? " · " + media + " ms de media" : "") : "sin webs vigiladas", d.webs.length ? (bien.length === d.webs.length ? "verde" : bien.length ? "ambar" : "rojo") : "gris"),
      tarjeta("cpu", "Procesador", cpu == null ? "—" : Math.round(cpu) + " %", (cpu == null ? "" : cpu < 30 ? "Tranquilo" : cpu < 80 ? "Con trabajo" : "Al límite") + (v.cpu.nucleos ? " · " + v.cpu.nucleos + " núcleos" : ""), est("cpu"), cpu),
      tarjeta("mem", "Memoria", [gb(m.usada), h("small", {}, " / " + gb(m.total) + " GB")], est("memoria") === "verde" ? "Va holgada" : "Va justa", est("memoria"), m.total ? m.usada / m.total * 100 : null),
      tarjeta("disco", "Disco", [gb(k.usado), h("small", {}, " / " + gb(k.total) + " GB")], est("disco") === "verde" ? "Espacio de sobra" : "Se está llenando", est("disco"), k.total ? k.usado / k.total * 100 : null));
  }

  function seguridad(d, fresco){
    const v = d.vivo; if (!v) return null;
    const t = v.tarjetas || {}, viejo = !fresco(v.ok_en), est = k => viejo ? "gris" : t[k], b = v.bastionado || {}, a = v.actualizaciones || {}, f = v.fail2ban || {};
    const tj = (ic, rot, val, sub, e) => tarjeta(ic, rot, val, sub, e, null, "c6");
    const cartas = h("div", {class: "rej", style: "gap:10px"},
      tj("persona", "Dentro ahora", v.sesiones ? v.sesiones + (v.sesiones === 1 ? " sesión" : " sesiones") : "Nadie", v.sesiones ? "sesiones SSH abiertas" : "Sin sesiones SSH", est("sesiones")),
      tj("mano", "Intentos 1 h", v.intentos ? String(v.intentos.hora ?? 0) : "—", v.intentos ? (v.intentos.total ?? 0) + " en 24 h" : "el servidor no registra intentos", est("intentos")),
      tj("prohibido", "Bloqueadas", f.instalado ? String(f.ahora ?? 0) : "—", f.instalado ? (f.total ?? 0) + " en total (fail2ban)" : "sin fail2ban", est("bloqueadas")),
      tj("puerta", "Puertas abiertas", puertas(v), v.raros.length ? "¡" + v.raros.join(", ") + " expuesto!" : (v.correo ? "+ correo (" + v.correo + ")" : "abiertas a Internet"), est("puertos")),
      tj("escudo", "Protección", b.total ? b.hechos + " de " + b.total : "—", b.estado === "verde" ? "todo en su sitio" : "ajustes de seguridad", viejo ? "gris" : b.estado),
      tj("llave", "Mantenimiento", a.pendientes ? a.pendientes + " paquetes" : "Al día", a.reinicio ? "reinicio pendiente" : (a.en_espera ? a.en_espera + " en espera" : "paquetes del sistema"), est("mantenimiento")));
    const falta = (b.falta || []);
    const lista = h("div", {class: "lista"}, ...(falta.length ? falta.map(c => h("div", {class: "f", style: "display:block"}, h("div", {style: "font-weight:600"}, c.texto || ""), c.arreglo ? h("div", {class: "mono tenue", style: "white-space:normal;overflow-wrap:anywhere;margin-top:2px"}, c.arreglo) : null))
      : [h("div", {class: "f"}, h("div", {class: "n"}, punto("verde"), h("span", {}, "Todo en su sitio")), h("span", {class: "mono tenue"}, (b.hechos || 0) + " de " + (b.total || 0)))]));
    return h("div", {class: "rej"}, h("div", {class: "c6 col-f"}, titulo("Seguridad", b.total ? b.hechos + " de " + b.total : null), cartas),
      h("div", {class: "c6 col-f"}, titulo("Qué falta para protegerlo", falta.length ? String(falta.length) : null), lista));
  }

  // la cifra grande: con correo aceptado sus puertos van al texto pequeño, y con más de 4 se cuenta (si no, se sale de la tarjeta)
  const CORREO = [25, 465, 587, 993, 995];
  function puertas(v){
    if (!v.expuestos.length) return "—";
    const grandes = v.correo ? v.expuestos.filter(p => !CORREO.includes(p)) : v.expuestos;
    return grandes.length > 4 ? v.expuestos.length + " abiertas" : (grandes.join(" · ") || "—");
  }

  function servicios(d){
    // primero los caídos, luego los parados a mano (0/0, en gris), luego los que van bien
    const peso = s => !s.ok ? 0 : (s.parado ? 1 : 2);
    const ss = ((d.vivo && d.vivo.servicios) || []).slice().sort((a, b) => peso(a) - peso(b) || String(a.nombre).localeCompare(String(b.nombre)));
    if (!ss.length) return null;
    const caidos = ss.filter(s => !s.ok).length, parados = ss.filter(s => s.ok && s.parado).length;
    const resumen = [caidos ? caidos + " caído" + (caidos > 1 ? "s" : "") : "", parados ? parados + " parado" + (parados > 1 ? "s" : "") : ""].filter(Boolean).join(" · ");
    return h("div", {class: "col-f"}, titulo("Servicios", resumen ? resumen + " de " + ss.length : ss.length + " en marcha"),
      h("div", {class: "pils"}, ...ss.map(s => h("span", {class: "pil vidrio", title: s.nombre}, punto(!s.ok ? "rojo" : (s.parado ? "gris" : "verde")), h("span", {}, s.nombre)))));
  }

  // procesador y memoria de las últimas 24 h (porcentajes máximos por hora, de /servidores/historia)
  function graficaRecursos(cont){
    const hoy = new Date(), ayer = new Date(hoy); ayer.setDate(ayer.getDate() - 1);
    const iso = x => x.getFullYear() + "-" + String(x.getMonth() + 1).padStart(2, "0") + "-" + String(x.getDate()).padStart(2, "0");
    fetch("/servidores/historia?" + new URLSearchParams({servidor: elegido, desde: iso(ayer), hasta: iso(hoy), paso: "hora"})).then(r => r.ok ? r.json() : null).then(r => {
      const pts = ((r && r.puntos) || []).filter(p => p.carga_max != null || p.mem_max != null).slice(-24);
      if (pts.length < 2) { cont.replaceChildren(h("p", {class: "vacio"}, "Todavía no hay horas suficientes para dibujar la gráfica.")); return; }
      const et = p => String(p.t).slice(11, 16);
      grafico(cont, {tipo: "area", alto: 170, unidad: "%", fmt: x => Math.round(x) + " %",
        series: [{nombre: "Memoria", color: "--s3", valores: pts.map(p => p.mem_max || 0)}, {nombre: "Procesador", color: "--s2", valores: pts.map(p => p.carga_max || 0)}],
        etiquetas: pts.map(et), titulos: pts.map(p => (String(p.t).slice(0, 10) === iso(hoy) ? "Hoy" : "Ayer") + " a las " + et(p))});
    }).catch(() => cont.replaceChildren(h("p", {class: "vacio"}, "No se pudo leer el historial del servidor.")));
  }

  function cabecera(d, fresco){
    const s = d.servidor, partes = [];
    const [cl, tx] = clase(ESTADO_GLOBAL, s.estado);
    partes.push(fresco(s.ok_en) ? h("span", {}, "Estado: ", insignia(cl, tx), " ") : h("span", {}, insignia("sv-neutro", "Sin lectura " + hace(s.ok_en, d.generado_en).replace("hace ", "desde hace ")), " "));
    partes.push(h("span", {class: "sv-suave"}, "último dato bueno " + hace(s.ok_en, d.generado_en) + " · a las " + hora(d.generado_en)));
    return h("p", {class: "sv-estado", style: "margin:0 0 0 auto"}, ...partes);
  }

  let aviso = null;
  function probarAviso(boton){
    if (probando) return;
    probando = true; boton.disabled = true;
    post("/servidores/avisos/probar", {servidor: elegido}).then(r => {
      let t, ok = false;
      if (r.espera_s) t = "Espera " + r.espera_s + " s antes de volver a probar.";
      else if (r.motivo === "solo_macos") t = "Las notificaciones de escritorio solo funcionan en macOS: aquí los avisos quedan solo en pantalla.";
      else if (r.ok) { ok = true; t = "Enviada. Si no la ves, revisa Notificaciones de macOS (permiso de la app) y el modo No molestar."; }
      else t = r.motivo === "no_aceptado" ? "macOS no aceptó la notificación: revisa sus permisos de notificaciones." : "No se pudo probar.";
      if (r.apagados && r.apagados.length) t += " Ojo: los avisos reales están apagados " + (r.apagados[0] === "global" ? "en general" : "para este servidor") + " (Ajustes); esta prueba no lo respeta.";
      ultimoAviso = {ok, t};
    }).catch(() => { ultimoAviso = {ok: false, t: "No se pudo hablar con la barra."}; }).finally(() => { probando = false; if (aviso) { aviso.className = "msg " + (ultimoAviso.ok ? "ok" : "err"); aviso.textContent = ultimoAviso.t; } boton.disabled = false; });
  }

  // la barra (selector, actualizar, probar aviso) sale siempre, también con errores: así nunca se queda la pestaña sin forma de cambiar de servidor
  function barra(estado){
    const cambia = id => { elegido = id; ultimoAviso = null; try { sessionStorage.setItem("sv-elegido", elegido); } catch (e) {} carga(); };
    const selector = servidores.length <= 5
      ? h("div", {class: "seg vidrio", role: "group", "aria-label": "Servidor"}, ...servidores.map(s => h("button", {class: s.id === elegido ? "on" : "", onclick: () => cambia(s.id)}, s.nombre)))
      : h("select", {"aria-label": "Servidor", onchange: ev => cambia(ev.target.value)}, ...servidores.map(s => h("option", {value: s.id, selected: s.id === elegido || null}, s.nombre)));
    const probar = h("button", {class: "vidrio", disabled: probando || null, onclick: ev => probarAviso(ev.currentTarget)}, "Probar aviso");
    aviso = h("span", {class: "msg" + (ultimoAviso ? (ultimoAviso.ok ? " ok" : " err") : ""), role: "status"}, ultimoAviso ? ultimoAviso.t : "");
    return h("div", {class: "filtros"}, h("div", {class: "grupo"}, h("span", {class: "etq"}, "Servidor"), selector),
      h("button", {class: "vidrio", onclick: () => carga()}, "Actualizar"), probar, aviso, estado || null);
  }

  function pinta(d){
    const raiz = $("vista-servidores");
    const fresco = ts => ts != null && d.generado_en - ts <= d.ventana_s && d.generado_en - ts >= -60;
    if (!d.disponible) { raiz.replaceChildren(barra(), h("p", {class: "vacio"}, d.motivo || "Sin datos todavía.")); return; }
    const p = d.panel, frescoPanel = !!p && p.ok_en != null && d.generado_en - p.ok_en <= p.ventana_s && d.generado_en - p.ok_en >= -60 && p.error !== "alcance";
    const graf = h("div", {class: "graf"}, h("p", {class: "vacio"}, "Cargando…"));
    raiz.replaceChildren(...[barra(cabecera(d, fresco)), tarjetasVivo(d, fresco),
      h("div", {class: "rej"}, h("div", {class: "c7 col-f"}, titulo("Webs vigiladas", "disponibilidad medida desde este Mac"), h("div", {class: "tj"}, tablaWebs(d, fresco))),
        h("div", {class: "c5 col-f"}, titulo("Qué ha pasado", "últimos 14 días"), h("div", {class: "lista"}, lineaTiempo(d)))),
      bloque("Procesador y memoria", "máximo de cada hora · últimas 24 h", graf, h("div", {class: "leyenda"}, h("span", {}, h("i", {class: "p", style: "background:var(--s2)"}), "Procesador"), h("span", {}, h("i", {class: "p", style: "background:var(--s3)"}), "Memoria"))),
      servicios(d), seguridad(d, fresco),
      bloque("Contenedores y servicios", "lectura por SSH", bloqueRecursos(d, fresco)),
      p ? bloque("Copias de seguridad", "panel " + (p.version ? "Dokploy " + p.version : "Dokploy"), cabeceraPanel(d, p, frescoPanel), tablaCopias(d, p, frescoPanel)) : null,
      p ? bloque("Proyectos del panel", "estado del último despliegue", arbolProyectos(d, p, frescoPanel)) : null].filter(Boolean));
    graficaRecursos(graf);
  }

  function mensaje(txt){ $("vista-servidores").replaceChildren(...[servidores.length ? barra() : null, h("div", {class: "tj"}, h("p", {class: "vacio"}, txt))].filter(Boolean)); }

  function carga(){
    if (!elegido) return;
    const mio = ++peticion;                        // una respuesta tardía de otro servidor no se pinta bajo el nombre del actual
    const pedido = elegido;
    fetch("/servidores/detalle?servidor=" + encodeURIComponent(pedido)).then(r => r.status === 429 ? new Promise(res => setTimeout(res, 2000)).then(() => fetch("/servidores/detalle?servidor=" + encodeURIComponent(pedido))) : r)
      .then(r => { if (r.status === 401) throw new Error("sesion"); if (r.status === 404) throw new Error("desconocido"); if (!r.ok && r.status !== 200) throw new Error("http"); return r.json(); })
      .then(d => { if (mio !== peticion || pedido !== elegido) return; if (d.disponible && d.servidor.id !== pedido) return; pinta(d); })
      .catch(e => { if (mio !== peticion) return;
        if (e.message === "desconocido") {          // el servidor no está entre los válidos de la configuración: se saca de la lista y se pasa al siguiente (no se queda uno atascado)
          servidores = servidores.filter(s => s.id !== pedido);
          try { if (sessionStorage.getItem("sv-elegido") === pedido) sessionStorage.removeItem("sv-elegido"); } catch (e2) {}
          if (servidores.length) { elegido = servidores[0].id; carga(); } else mensaje("Ese servidor ya no está en la configuración.");
          return;
        }
        mensaje(e.message === "sesion" ? "La sesión ha caducado: abre el panel desde el icono de la barra de menús." : "No se pudo leer el detalle ahora; vuelve a intentarlo."); });
  }

  window.cargarServidores = function(){
    fetch("/ajustes").then(r => { if (!r.ok) throw new Error("http"); return r.json(); }).then(a => {
      servidores = ((a.config && a.config.servidores) || []).filter(s => s && /^[0-9a-f]{32}$/.test(s.id || "") && typeof s.nombre === "string");
      if (!servidores.length) { mensaje("Aún no hay servidores. Da de alta uno en Ajustes."); return; }
      let guardado = null; try { guardado = sessionStorage.getItem("sv-elegido"); } catch (e) {}
      elegido = servidores.some(s => s.id === elegido) ? elegido : servidores.some(s => s.id === guardado) ? guardado : servidores[0].id;
      carga();
    }).catch(() => mensaje("No pude leer la lista de servidores. Abre el panel desde el icono de la barra de menús."));
    clearInterval(temporizador);
    temporizador = setInterval(() => { if (!$("vista-servidores").hidden && document.visibilityState === "visible") carga(); else if ($("vista-servidores").hidden) clearInterval(temporizador); }, 60000);
  };
  if (location.protocol !== "file:" && location.hash === "#servidores") window.vista("servidores");
})();
