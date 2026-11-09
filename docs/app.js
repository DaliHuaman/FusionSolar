/* Seguimiento de indicadores de las plantas de BSF (inversores y BESS) — lee data/data.json (generado cada 30 min). */
(function () {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v; else if (k === "text") n.textContent = v; else n.setAttribute(k, v);
    }
    kids.flat().forEach(k => n.append(k instanceof Node ? k : document.createTextNode(k)));
    return n;
  };
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  const ESTADOS = {
    OK: { txt: "Normal", ico: "✓", cls: "ok" },
    ADVERTENCIA: { txt: "Advertencia", ico: "!", cls: "warn" },
    ALARMA: { txt: "Alarma", ico: "✕", cls: "bad" },
    SIN_DATO: { txt: "Sin dato", ico: "–", cls: "nd" },
  };
  const pill = e => { const s = ESTADOS[e] || ESTADOS.SIN_DATO; return el("span", { class: "pill " + s.cls }, s.ico + " " + s.txt); };
  const fR = v => v == null ? "—" : v.toLocaleString("es-PE", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
  const fP = v => v == null ? "—" : v.toLocaleString("es-PE", { maximumFractionDigits: 1 }) + " %";

  let D = null;
  const S = { planta: "TODAS", dias: 7, zmode: "x", tabla: "inv", dg: "dia", dr: 30 };   // zmode: eje(s) que acerca el zoom ("x" horizontal, "y" vertical, "xy" ambos)
  let charts = [];
  const zoomGuardado = new Map();                       // clave de gráfica -> {x:[min,max], y:[min,max]} (se conserva al refrescar datos)
  let soltarZoom = false;                               // true al cambiar planta/rango: se parte de la vista completa

  // ---------------------------------------------------------------- tiempo (hora de Lima "ingenua")
  const naive = s => { const m = /(\d+)-(\d+)-(\d+)[ T](\d+):(\d+)(?::(\d+))?/.exec(s); return Date.UTC(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)); };
  const ahoraNaive = tz => {
    const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })
      .formatToParts(new Date()).map(x => [x.type, x.value]));
    return Date.UTC(+p.year, p.month - 1, +p.day, +p.hour, +p.minute, +p.second);
  };
  const MES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  const pad = n => String(n).padStart(2, "0");
  const fHora = s => { const d = new Date(s * 1000); return pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()); };
  const fDia = s => { const d = new Date(s * 1000); return pad(d.getUTCDate()) + " " + MES[d.getUTCMonth()]; };
  const fFull = s => fDia(s) + " " + fHora(s);
  const fT = v => v == null ? "—" : v.toLocaleString("es-PE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  // hora de inicio del inversor: solo la hora si es de hoy (día de la última corrida), con fecha si es de otro día
  const fInicio = s => { const t = naive(s) / 1000; return Math.floor(t / 86400) === Math.floor(tMax() / 86400) ? fHora(t) : fFull(t); };

  // ---------------------------------------------------------------- carga
  async function cargar() {
    try {
      const r = await fetch("data/data.json?_=" + Date.now(), { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      const nuevo = await r.json();
      if (D && nuevo.generado === D.generado) return;   // sin datos nuevos: no repintar (así no se pierde el zoom del usuario)
      D = nuevo;
    } catch (e) {
      $("#frescura").className = "chip bad";
      $("#frescura-txt").textContent = "No se pudo cargar data.json";
      return;
    }
    pintar();
  }

  function frescura() {
    if (!D) return;
    const diff = (ahoraNaive(D.zona_horaria) - naive(D.generado)) / 60000;
    const chip = $("#frescura");
    const lim = D.intervalo_min || 30;
    chip.className = "chip " + (diff <= lim * 1.5 ? "ok" : diff <= lim * 3 ? "warn" : "bad");
    const hace = diff < 1 ? "hace instantes" : diff < 90 ? `hace ${Math.round(diff)} min` : diff < 2880 ? `hace ${(diff / 60).toFixed(1)} h` : `hace ${Math.round(diff / 1440)} días`;
    $("#frescura-txt").textContent = `Actualizado ${hace} · ${fFull(naive(D.generado) / 1000)} (Lima)`;
  }

  // ---------------------------------------------------------------- helpers de datos
  const invsVisibles = () => D.inversores.filter(i => S.planta === "TODAS" || i.planta === S.planta);
  const grupos = (invs = invsVisibles()) => {
    const m = new Map();
    invs.forEach(i => { const k = i.planta + "|" + i.grupo; if (!m.has(k)) m.set(k, { planta: i.planta, grupo: i.grupo, invs: [] }); m.get(k).invs.push(i); });
    return [...m.values()];
  };
  const nombrePlanta = c => (D.plantas.find(p => p.codigo === c) || {}).nombre || c;
  const tMax = () => naive(D.generado) / 1000;
  const tMin = () => S.dias ? tMax() - S.dias * 86400 : 0;
  const serie = id => (D.serie[id] || []);
  // Una lectura es "vigente" si es mas reciente que 3 intervalos de la ultima corrida.
  const edadMin = u => (naive(D.generado) - naive(u.t)) / 60000;
  const vigente = u => !!u && edadMin(u) <= (D.intervalo_min || 30) * 3;
  const estadoDe = i => { const u = D.ultima[i.id]; return vigente(u) ? u.estado : "SIN_DATO"; };
  const fEdad = m => m < 90 ? `hace ${Math.round(m)} min` : m < 2880 ? `hace ${(m / 60).toFixed(1)} h` : `hace ${Math.round(m / 1440)} días`;
  const colorInv = (i, grp) => css("--s" + (grp.invs.indexOf(i) + 1));

  // ---------------------------------------------------------------- render
  function pintar() {
    frescura();
    $("#f-int").textContent = D.intervalo_min;
    $("#mrg").textContent = Math.round(D.margen_advertencia * 100);
    $("#f-gen").textContent = "Última corrida del recolector: " + D.generado + (D.corrida && D.corrida.ok === false ? " · con errores: " + (D.corrida.error || "") : "");
    pintarAviso(); pintarFiltroPlantas();
    // "BESS" y "Disponibilidad" son vistas propias (como una planta más) para no alargar la página
    const vista = S.planta === "BESS" ? "bess" : S.planta === "DISP" ? "disp" : "inv";
    document.querySelectorAll("[data-vista=inv]").forEach(n => { n.hidden = vista !== "inv"; });
    $("#kpis").hidden = vista === "bess";
    $("#f-rango").hidden = vista === "disp";         // la disponibilidad tiene su propio selector de periodo
    $("#sec-disp").hidden = vista !== "disp";
    if (vista === "bess") { destruirCharts(); pintarBess(); return; }
    $("#sec-bess").hidden = true;
    if (vista === "disp") { destruirCharts(); pintarDisp(); return; }
    pintarKpis(); pintarCards(); pintarGraficas(); pintarTemperatura(); pintarInicio(); pintarDiario(); pintarExcel(); pintarAlarmas(); pintarPotencia();
  }

  function pintarAviso() {
    const a = $("#aviso");
    const f = D.no_encontrados || [];
    if (f.length) {
      a.hidden = false;
      a.textContent = `Sin lectura (no visibles en el portal con el usuario del recolector): ${f.join(", ")}.`;
    } else a.hidden = true;
  }

  function pintarFiltroPlantas() {
    const box = $("#f-planta");
    box.replaceChildren();
    const hayBess = !!(D.bess && D.bess.activo && D.bess.equipos.length);
    const hayDisp = !!(D.disponibilidad && D.disponibilidad.activo);
    [["TODAS", "Todas"], ...D.plantas.map(p => [p.codigo, p.codigo + " · " + p.nombre.replace("BSF ", "")]), ...(hayBess ? [["BESS", "BESS · Temperaturas"]] : []),
      ...(hayDisp ? [["DISP", "Disponibilidad"]] : [])].forEach(([v, t]) => {
      const b = el("button", { type: "button", "data-v": v, class: S.planta === v ? "on" : "" }, t);
      b.onclick = () => { S.planta = v; soltarZoom = true; pintar(); };
      box.append(b);
    });
  }

  function pintarKpis() {
    const vis = invsVisibles();
    const est = vis.map(estadoDe);
    const n = e => est.filter(x => x === e).length;
    const lim = vis.filter(i => { const u = D.ultima[i.id]; return vigente(u) && u.p != null && u.p < 100; }).length;
    const con = vis.filter(i => { const u = D.ultima[i.id]; return vigente(u) && u.r != null; }).length;
    const k = (l, v, s, cls = "") => el("div", { class: "kpi " + cls }, el("div", { class: "l", text: l }), el("div", { class: "v", text: v }), el("div", { class: "s", text: s }));
    $("#kpis").replaceChildren(
      k("Inversores con lectura", `${con}/${vis.length}`, "última corrida"),
      k("En alarma", n("ALARMA"), "resistencia < umbral", n("ALARMA") ? "bad" : "ok"),
      k("En advertencia", n("ADVERTENCIA"), `a ≤ ${Math.round(D.margen_advertencia * 100)} % del umbral`, n("ADVERTENCIA") ? "warn" : "ok"),
      k("Potencia limitada", lim, "ajuste activo < 100 %", lim ? "warn" : "ok"),
      k("Sin dato", n("SIN_DATO"), "sin lectura / sin acceso", ""),
    );
  }

  function pintarCards() {
    const box = $("#cards");
    box.replaceChildren();
    invsVisibles().forEach(i => {
      const u = D.ultima[i.id];
      const est = estadoDe(i);
      const vieja = !!u && !vigente(u);
      const c = el("article", { class: "card " + est, "aria-label": `${i.planta} ${i.nombre}` });
      c.append(el("div", { class: "h" },
        el("div", {}, el("div", { class: "n", text: `${i.planta} · ${i.nombre}` }), el("div", { class: "g", text: `${nombrePlanta(i.planta).replace("BSF ", "")} · ${i.grupo}` })),
        pill(est)));
      c.append(el("div", { class: "val" }, u && u.r != null ? fR(u.r) : "—", el("small", { text: "MΩ" })));
      if (vieja) c.style.opacity = ".72";
      if (u && u.r != null) {
        const max = Math.max(i.umbral * 2.5, u.r * 1.1);
        const m = el("div", { class: "medidor " + (vieja ? "SIN_DATO" : est), role: "img", "aria-label": `Resistencia ${fR(u.r)} MΩ, umbral ${fR(i.umbral)} MΩ` },
          el("i", { style: `width:${Math.min(100, u.r / max * 100)}%` }), el("b", { style: `left:${i.umbral / max * 100}%` }));
        c.append(m, el("div", { class: "medidor-l" }, el("span", { text: "0" }), el("span", { text: "umbral " + fR(i.umbral) })));
      } else {
        c.append(el("div", { class: "meta", text: u ? "Lectura no disponible (inversor apagado o sin dato)." : "Sin lecturas registradas (inversor no visible en el portal)." }));
        c.append(el("div", { class: "meta", text: "Umbral " + fR(i.umbral) + " MΩ" }));
      }
      c.append(el("div", { class: "pot", title: u ? u.p_txt : "" }, "Pot. activa: ", el("b", { text: u && u.p != null ? fP(u.p) : (u && u.p_txt ? u.p_txt.slice(0, 28) : "—") })));
      c.append(el("div", { class: "pot" }, "Temp. interna: ", el("b", { text: u && u.temp != null && vigente({ t: u.t_temp }) ? fT(u.temp) + " °C" : "—" }),
        " · Inicio: ", el("b", { text: u && u.inicio ? fInicio(u.inicio) : "—" })));
      c.append(sparkline(i, est));
      c.append(el("div", { class: "meta", text: u ? `${vieja ? "Última lectura " + fEdad(edadMin(u)) + " · " : ""}${u.inv ? u.inv + " · " : ""}${fFull(naive(u.t) / 1000)}${u.fuente && u.fuente !== "FusionSolar" ? " · " + u.fuente : ""}` : "" }));
      box.append(c);
    });
  }

  function sparkline(inv, est) {
    const pts = serie(inv.id).filter(p => p[1] != null).slice(-60);
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "spark"); svg.setAttribute("viewBox", "0 0 200 34"); svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Tendencia reciente de la resistencia");
    if (pts.length < 2) return svg;
    const um = p => (p[3] != null ? p[3] : inv.umbral);     // umbral vigente en cada punto
    const ys = pts.map(p => p[1]).concat(pts.map(um));
    const lo = Math.min(...ys), hi = Math.max(...ys), sp = (hi - lo) || 1;
    const X = k => 2 + k / (pts.length - 1) * 196, Y = v => 31 - (v - lo) / sp * 28;
    const path = (d, attrs) => { const p = document.createElementNS(NS, "path"); p.setAttribute("d", d); for (const [k, v] of Object.entries(attrs)) p.setAttribute(k, v); svg.append(p); };
    path(pts.map((p, k) => (k ? "H" + X(k).toFixed(1) + "V" : "M" + X(k).toFixed(1) + " ") + Y(um(p)).toFixed(1)).join("") + "H198",
      { stroke: css("--fg-3"), "stroke-dasharray": "3 3", "stroke-width": 1, fill: "none", "vector-effect": "non-scaling-stroke" });
    const col = est === "ALARMA" ? css("--bad") : est === "ADVERTENCIA" ? css("--warn") : css("--accent");
    path(pts.map((p, k) => (k ? "L" : "M") + X(k).toFixed(1) + " " + Y(p[1]).toFixed(1)).join(""), { stroke: col, "stroke-width": 1.8, fill: "none", "stroke-linejoin": "round", "vector-effect": "non-scaling-stroke" });
    return svg;
  }

  // ---------------------------------------------------------------- gráficas
  function ticksTiempo(min, max) {
    const span = max - min, M = 60, H = 3600, D1 = 86400;
    const pasos = [5 * M, 10 * M, 15 * M, 30 * M, H, 2 * H, 3 * H, 6 * H, 12 * H, D1, 2 * D1, 3 * D1, 7 * D1, 14 * D1];
    const paso = pasos.find(p => span / p <= 7) || 14 * D1;
    const out = []; for (let t = Math.ceil(min / paso) * paso; t <= max; t += paso) out.push({ value: t });
    return { ticks: out, paso };
  }

  // Zoom (plugin chartjs-plugin-zoom): arrastrar = seleccionar un rango (según el modo Horizontal / Vertical / Ambos),
  // Ctrl + rueda = acercar/alejar, Mayús + arrastrar = desplazar, doble clic = restablecer.
  const hayZoom = () => !!(window.Chart && Chart.registry && Chart.registry.plugins.get("zoom"));
  function opcionesZoom() {
    return {
      pan: { enabled: true, mode: "xy", modifierKey: "shift" },
      zoom: {
        mode: S.zmode,
        wheel: { enabled: true, modifierKey: "ctrl" },
        pinch: { enabled: true },
        drag: { enabled: true, threshold: 6, backgroundColor: css("--accent-soft"), borderColor: css("--accent"), borderWidth: 1 },
      },
      limits: { x: { min: "original", max: "original", minRange: 600 }, y: { min: "original", max: "original" } },
    };
  }

  const MODOS_ZOOM = [["x", "↔ Horizontal", "Arrastra sobre la gráfica para acercar un rango de tiempo (eje horizontal)"],
                      ["y", "↕ Vertical", "Arrastra sobre la gráfica para acercar un rango de valores (eje vertical)"],
                      ["xy", "⤡ Ambos", "Arrastra un recuadro para acercar en horizontal y vertical a la vez"]];
  function barraZoom() {
    if (!hayZoom()) return null;                         // sin el plugin (p. ej. sin acceso al CDN) no se muestran controles
    const bar = el("div", { class: "zbar", role: "group", "aria-label": "Zoom de la gráfica" });
    const seg = el("div", { class: "seg mini" });
    MODOS_ZOOM.forEach(([m, t, ayuda]) => {
      const b = el("button", { type: "button", "data-m": m, title: ayuda, class: S.zmode === m ? "on" : "" }, t);
      b.onclick = () => {
        S.zmode = m;
        charts.forEach(c => { c.options.plugins.zoom.zoom.mode = m; });
        document.querySelectorAll(".zbar .seg button").forEach(x => x.classList.toggle("on", x.dataset.m === m));
      };
      seg.append(b);
    });
    bar.append(seg, el("button", { type: "button", class: "btn mini zr", title: "Restablecer zoom (también: doble clic en la gráfica)", "aria-label": "Restablecer zoom", hidden: "" }, "⟲ Restablecer"));
    return bar;
  }

  // Crea la gráfica, la registra (para destruirla al repintar) y le conecta el zoom: botón Restablecer, doble clic y recuperar el
  // zoom que el usuario tenía antes de un refresco de datos.
  function mk(clave, cnv, cfg, barra) {
    const ch = new Chart(cnv, cfg);
    ch.$clave = clave;
    charts.push(ch);
    if (!hayZoom()) return ch;
    const zr = barra && barra.querySelector(".zr");
    const marcar = () => { if (zr) zr.hidden = !ch.isZoomedOrPanned(); };
    ch.options.plugins.zoom.zoom.onZoomComplete = marcar;
    ch.options.plugins.zoom.pan.onPanComplete = marcar;
    const reset = () => { ch.resetZoom(); marcar(); };
    if (zr) zr.onclick = reset;
    cnv.addEventListener("dblclick", reset);
    const g = zoomGuardado.get(clave);
    if (g) {
      if (g.x) ch.zoomScale("x", { min: g.x[0], max: g.x[1] }, "none");
      if (g.y) ch.zoomScale("y", { min: g.y[0], max: g.y[1] }, "none");
      marcar();
    }
    return ch;
  }

  function destruirCharts() {
    zoomGuardado.clear();
    if (!soltarZoom) {
      charts.forEach(c => {
        if (c.$clave && c.isZoomedOrPanned && c.isZoomedOrPanned()) zoomGuardado.set(c.$clave, { x: [c.scales.x.min, c.scales.x.max], y: [c.scales.y.min, c.scales.y.max] });
      });
    }
    soltarZoom = false;
    charts.forEach(c => c.destroy());
    charts = [];
  }

  function baseOpts(xmin, xmax, ymin, ymax, ytitle) {
    const fg2 = css("--fg-2"), grid = css("--grid");
    return {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      layout: { padding: { right: 6 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          filter: it => !it.dataset._umbral,
          backgroundColor: css("--fg"), titleColor: css("--bg"), bodyColor: css("--bg"), padding: 8, displayColors: true, boxPadding: 3,
          callbacks: { title: its => its.length ? fFull(its[0].parsed.x) : "" },
        },
        zoom: opcionesZoom(),
      },
      scales: {
        x: {
          type: "linear", min: xmin, max: xmax, grid: { color: grid }, border: { color: grid },
          afterBuildTicks: sc => { sc.ticks = ticksTiempo(sc.min, sc.max).ticks; },
          // con pasos menores a un día se muestra la hora (y la fecha en el primer tick y a medianoche); depende del rango VISIBLE, así que cambia al hacer zoom
          ticks: { color: fg2, maxRotation: 0, callback(v, i) { return ticksTiempo(this.min, this.max).paso < 86400 ? fHora(v) + (i === 0 || Math.abs(v % 86400) < 1 ? "\n" + fDia(v) : "") : fDia(v); } },
        },
        y: { min: ymin, max: ymax, grid: { color: grid }, border: { display: false }, ticks: { color: fg2 }, title: { display: !!ytitle, text: ytitle, color: fg2 } },
      },
    };
  }

  function tarjetaGrafica(titulo, sub, leyenda, vacioMsg) {
    const box = el("div", { class: "chart" + (vacioMsg ? " vacio" : "") });
    const barra = vacioMsg ? null : barraZoom();
    box.append(el("h3", {}, titulo, el("span", { text: sub }), barra || ""));
    if (leyenda) box.append(leyenda);
    const cv = el("div", { class: "cv" });
    if (vacioMsg) cv.append(vacioMsg);
    box.append(cv);
    return { box, cv, barra };
  }

  function leyenda(items) {
    return el("div", { class: "leg" }, items.map(([c, t, d]) => el("span", {}, el("i", { class: d ? "d" : "", style: `background:${d ? "none" : c};color:${c}` }), t)));
  }

  // Umbral como serie ESCALONADA en el tiempo: cada lectura guarda el umbral vigente (p[3]); la línea punteada salta cuando el
  // umbral cambia en el portal. Solo se dibujan los puntos de cambio, más el inicio y el fin de la ventana visible.
  function puntosUmbral(inv, xmin, xmax) {
    const cambios = []; let ant;
    serie(inv.id).forEach(p => { if (p[3] != null && p[3] !== ant) { cambios.push({ x: p[0], y: p[3] }); ant = p[3]; } });
    if (!cambios.length) return [{ x: xmin, y: inv.umbral }, { x: xmax, y: inv.umbral }];   // data.json antiguo (sin historial de umbral)
    const previos = cambios.filter(c => c.x <= xmin), dentro = cambios.filter(c => c.x > xmin);
    return [{ x: xmin, y: (previos.length ? previos[previos.length - 1] : cambios[0]).y }, ...dentro, { x: xmax, y: cambios[cambios.length - 1].y }];
  }

  const xminGlobal = () => S.dias ? tMin() : Math.min(tMax() - 86400, ...Object.values(D.serie).filter(s => s.length).map(s => s[0][0]));

  function pintarGraficas() {
    destruirCharts();
    const rBox = $("#charts-r"), pBox = $("#charts-p");
    rBox.replaceChildren(); pBox.replaceChildren();
    const xmin = xminGlobal(), xmax = tMax();
    grupos().forEach(g => {
      const conDatos = g.invs.some(i => serie(i.id).length);
      const sub = `${nombrePlanta(g.planta).replace("BSF ", "")} · ${g.grupo}`;
      const items = g.invs.map(i => [colorInv(i, g), i.nombre]);
      const umb = g.invs.map(i => [colorInv(i, g), `umbral ${i.nombre} ${fR(i.umbral)}`, true]);
      // Resistencia
      {
        const msg = conDatos ? null : el("span", { text: "Sin lecturas todavía para este grupo (los inversores no son visibles en el portal con el usuario actual)." });
        const { box, cv, barra } = tarjetaGrafica(g.planta + " · " + g.grupo, "MΩ", conDatos ? leyenda([...items, ...(g.invs.length <= 3 ? umb : [])]) : null, msg);
        rBox.append(box);
        if (conDatos) {
          const cnv = el("canvas", { role: "img", "aria-label": "Resistencia de aislamiento " + sub }); cv.append(cnv);
          const ds = []; let umbMax = 0;
          g.invs.forEach(i => {
            const col = colorInv(i, g);
            const pu = puntosUmbral(i, xmin, xmax);
            umbMax = Math.max(umbMax, ...pu.map(q => q.y));
            ds.push({ label: i.nombre, data: serie(i.id).map(p => ({ x: p[0], y: p[1] })).filter(p => p.x >= xmin), borderColor: col, backgroundColor: col, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0, spanGaps: false });
            ds.push({ label: "umbral " + i.nombre, _umbral: true, data: pu, stepped: "before", borderColor: col, borderDash: [5, 4], borderWidth: 1.4, pointRadius: 0, pointHitRadius: 0 });
          });
          const o = baseOpts(xmin, xmax, 0, undefined);
          o.scales.y.suggestedMax = umbMax * 2;
          mk("r|" + g.planta + "|" + g.grupo, cnv, { type: "line", data: { datasets: ds }, options: o }, barra);
        }
      }
      // Potencia activa
      {
        const conP = g.invs.some(i => serie(i.id).some(p => p[2] != null));
        const msg = conP ? null : el("span", { text: "Aún no hay lecturas de potencia activa para este grupo." });
        const { box, cv, barra } = tarjetaGrafica(g.planta + " · " + g.grupo, "% potencia activa", conP ? leyenda(items) : null, msg);
        pBox.append(box);
        if (conP) {
          const cnv = el("canvas", { role: "img", "aria-label": "Ajuste de potencia activa " + sub }); cv.append(cnv);
          const ds = g.invs.map(i => {
            const col = colorInv(i, g);
            return { label: i.nombre, data: serie(i.id).filter(p => p[2] != null && p[0] >= xmin).map(p => ({ x: p[0], y: p[2] })), borderColor: col, backgroundColor: col, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, stepped: "before" };
          });
          const o = baseOpts(xmin, xmax, 0, 105);
          o.scales.y.ticks.callback = v => v + " %";
          mk("p|" + g.planta + "|" + g.grupo, cnv, { type: "line", data: { datasets: ds }, options: o }, barra);
        }
      }
    });
  }

  // Temperatura interna de cada inversor (Real-Time Device Data), una lectura por corrida.
  function pintarTemperatura() {
    const box0 = $("#charts-t");
    box0.replaceChildren();
    const xmin = xminGlobal(), xmax = tMax();
    grupos().forEach(g => {
      const sub = `${nombrePlanta(g.planta).replace("BSF ", "")} · ${g.grupo}`;
      const pts = i => serie(i.id).filter(p => p[0] >= xmin).map(p => ({ x: p[0], y: p[4] }));       // sin lectura (y = null) = hueco, no se une
      const hay = g.invs.some(i => pts(i).some(q => q.y != null));
      const { box, cv, barra } = tarjetaGrafica(g.planta + " · " + g.grupo, "°C temperatura interna", hay ? leyenda(g.invs.map(i => [colorInv(i, g), i.nombre])) : null,
        hay ? null : el("span", { text: "Aún no hay lecturas de temperatura interna en este rango (se registran desde la corrida con esta mejora)." }));
      box0.append(box);
      if (!hay) return;
      const cnv = el("canvas", { role: "img", "aria-label": "Temperatura interna " + sub }); cv.append(cnv);
      const ds = g.invs.map(i => { const col = colorInv(i, g); return { label: i.nombre, data: pts(i), borderColor: col, backgroundColor: col, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0, spanGaps: false }; });
      const o = baseOpts(xmin, xmax, undefined, undefined, "°C");
      o.scales.y.ticks.callback = v => v + " °C";
      o.plugins.tooltip.callbacks.label = c => `${c.dataset.label}: ${fT(c.parsed.y)} °C`;
      mk("t|" + g.planta + "|" + g.grupo, cnv, { type: "line", data: { datasets: ds }, options: o }, barra);
    });
  }

  // Hora de inicio: un punto por arranque (D.inicio[id] = instantes de arranque); el eje vertical es la hora del día.
  const horaDia = t => (((t % 86400) + 86400) % 86400) / 3600;
  const fHoraDec = h => { const s = Math.round(h * 3600); return pad(Math.floor(s / 3600) % 24) + ":" + pad(Math.floor(s % 3600 / 60)); };
  const fHoraSeg = h => { const s = Math.round(h * 3600); return pad(Math.floor(s / 3600) % 24) + ":" + pad(Math.floor(s % 3600 / 60)) + ":" + pad(s % 60); };
  function pintarInicio() {
    const box0 = $("#charts-i");
    box0.replaceChildren();
    const xmin = xminGlobal(), xmax = tMax();
    grupos().forEach(g => {
      const sub = `${nombrePlanta(g.planta).replace("BSF ", "")} · ${g.grupo}`;
      const pts = i => ((D.inicio || {})[i.id] || []).filter(t => t >= xmin && t <= xmax + 86400).map(t => ({ x: t, y: horaDia(t) }));
      const hay = g.invs.some(i => pts(i).length);
      const { box, cv, barra } = tarjetaGrafica(g.planta + " · " + g.grupo, "hora de inicio (hh:mm)", hay ? leyenda(g.invs.map(i => [colorInv(i, g), i.nombre])) : null,
        hay ? null : el("span", { text: "Aún no hay horas de inicio registradas en este rango (se registran desde la corrida con esta mejora)." }));
      box0.append(box);
      if (!hay) return;
      const cnv = el("canvas", { role: "img", "aria-label": "Hora de inicio de los inversores " + sub }); cv.append(cnv);
      const ds = g.invs.map(i => { const col = colorInv(i, g); return { label: i.nombre, data: pts(i), borderColor: col, backgroundColor: col, borderWidth: 1.6, pointRadius: 3, pointHoverRadius: 5, tension: 0 }; });
      const o = baseOpts(xmin, xmax, undefined, undefined, "hora del día");
      const ys = ds.flatMap(d => d.data.map(p => p.y));
      o.scales.y.suggestedMin = Math.min(...ys) - 0.25; o.scales.y.suggestedMax = Math.max(...ys) + 0.25;   // ±15 min alrededor de los datos
      o.scales.y.ticks.callback = v => fHoraDec(v);
      o.plugins.tooltip.callbacks.title = its => its.length ? "Inicio del " + fDia(its[0].parsed.x) : "";
      o.plugins.tooltip.callbacks.label = c => `${c.dataset.label}: ${fHoraSeg(c.parsed.y)}`;
      mk("i|" + g.planta + "|" + g.grupo, cnv, { type: "line", data: { datasets: ds }, options: o }, barra);
    });
  }

  // ---------------------------------------------------------------- BESS (temperaturas)
  // Orden de los valores en D.bess.serie[id] = [t, max, bat_sum, bat_ret, pot_sum, pot_ret, ext] (ver sitedata._bess)
  const BESS_CAMPOS = [
    { k: "temp_max", l: "Máx. del grupo", c: "--s2", w: 2.6 },
    { k: "agua_bat_sum", l: "Agua suministrada · lado batería", c: "--s1", w: 1.6 },
    { k: "agua_bat_ret", l: "Agua de retorno · lado batería", c: "--s5", w: 1.6 },
    { k: "agua_pot_sum", l: "Agua suministrada · lado potencia", c: "--s3", w: 1.6 },
    { k: "agua_pot_ret", l: "Agua de retorno · lado potencia", c: "--s4", w: 1.6 },
    { k: "temp_ext", l: "Temperatura en exteriores", c: "--s6", w: 1.6 },
  ];
  const DIAS_BESS = 7;

  function pintarBess() {
    const sec = $("#sec-bess");
    const B = D.bess;
    const visible = !!B && B.activo && B.equipos.length && S.planta === "BESS";
    sec.hidden = !visible;
    const box = $("#bess"), comp = $("#bess-comp");
    box.replaceChildren(); comp.replaceChildren();
    if (!visible) return;
    const av = $("#aviso-bess"), errs = (D.corrida && D.corrida.errores_bess) || {};
    const ks = Object.keys(errs);
    av.hidden = !ks.length;
    av.textContent = ks.length ? "Última corrida sin lectura BESS: " + ks.map(k => `${k} (${errs[k]})`).join(", ") + "." : "";

    const xmax = tMax(), xmin = xmax - Math.min(S.dias || DIAS_BESS, DIAS_BESS) * 86400;
    const serieB = id => ((B.serie || {})[id] || []).filter(p => p[0] >= xmin);
    const bessVigente = u => !!u && (naive(D.generado) - naive(u.t)) / 60000 <= (D.intervalo_min || 30) * 3;

    // comparación: temperatura más alta del grupo de los 5 BESS
    {
      const hay = B.equipos.some(e => serieB(e.id).some(p => p[1] != null));
      const items = B.equipos.map((e, k) => [css("--s" + (k + 1)), e.nombre]);
      const { box: cb, cv } = tarjetaGrafica("Temperatura más alta del grupo", "°C · comparación entre BESS", hay ? leyenda(items) : null,
        hay ? null : el("span", { text: "Aún no hay lecturas de temperatura BESS." }));
      comp.className = "chart" + (hay ? "" : " vacio");
      const barraComp = cb.querySelector(".zbar");
      comp.append(...cb.childNodes);
      if (hay) {
        const cnv = el("canvas", { role: "img", "aria-label": "Temperatura más alta del grupo por BESS" });
        comp.querySelector(".cv").append(cnv);
        const ds = B.equipos.map((e, k) => ({ label: e.nombre, data: serieB(e.id).filter(p => p[1] != null).map(p => ({ x: p[0], y: p[1] })), borderColor: css("--s" + (k + 1)), backgroundColor: css("--s" + (k + 1)), borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0 }));
        const o = baseOpts(xmin, xmax, undefined, undefined, "°C");
        o.scales.y.ticks.callback = v => v + " °C";
        mk("bess|comp", cnv, { type: "line", data: { datasets: ds }, options: o }, barraComp);
      }
    }

    B.equipos.forEach(e => {
      const u = (B.ultima || {})[e.id];
      const vig = bessVigente(u);
      const c = el("article", { class: "bess " + (vig ? "OK" : "SIN_DATO"), "aria-label": e.nombre });
      c.append(el("div", { class: "h" },
        el("div", {}, el("div", { class: "n", text: e.nombre }), el("div", { class: "g", text: nombrePlanta(B.planta).replace("BSF ", "") })),
        vig ? el("span", { class: "pill ok" }, "✓ En línea") : pill("SIN_DATO")));
      c.append(el("div", { class: "max" }, el("span", { class: "val" }, fT(u && u.temp_max), el("small", { text: "°C" })), el("span", { class: "lab" }, el("i", { style: `background:${css("--s2")}` }), "Temperatura más alta del grupo")));
      c.append(el("div", { class: "temps" }, BESS_CAMPOS.slice(1).map(f => el("div", { class: "t" },
        el("span", { class: "l" }, el("i", { style: `background:${css(f.c)}` }), f.l), el("span", { class: "v", text: fT(u && u[f.k]) + (u && u[f.k] != null ? " °C" : "") })))));
      const pts = serieB(e.id);
      if (pts.length) {
        const barra = barraZoom(); if (barra) c.append(barra);
        const cv = el("div", { class: "cv" }); const cnv = el("canvas", { role: "img", "aria-label": "Temperaturas " + e.nombre }); cv.append(cnv); c.append(cv);
        const ds = BESS_CAMPOS.map((f, j) => ({ label: f.l, data: pts.filter(p => p[j + 1] != null).map(p => ({ x: p[0], y: p[j + 1] })), borderColor: css(f.c), backgroundColor: css(f.c), borderWidth: f.w, pointRadius: 0, pointHoverRadius: 4, tension: 0 }));
        const o = baseOpts(xmin, xmax, undefined, undefined, "°C");
        o.scales.y.ticks.callback = v => v + " °C";
        mk("bess|" + e.id, cnv, { type: "line", data: { datasets: ds }, options: o }, barra);
      }
      c.append(el("div", { class: "meta", text: u ? `${vig ? "" : "Última lectura " + fEdad((naive(D.generado) - naive(u.t)) / 60000) + " · "}${fFull(naive(u.t) / 1000)}` : "Sin lecturas registradas." }));
      box.append(c);
    });
  }

  // ---------------------------------------------------------------- Disponibilidad
  // D.disponibilidad.h[id] = horas operativas por día desde d0 (null = sin dato). Disponibilidad diaria de un inversor = la fórmula del
  // Excel: 1 si horas >= horas_ref (12), si no horas / horas_ref. Una planta = promedio de sus inversores; mes/año = promedio de los días con dato.
  const DIA_MS = 86400000;
  const dispRef = () => (D.disponibilidad && D.disponibilidad.horas_ref) || 12;
  const dispFrac = h => h == null ? null : h >= dispRef() ? 1 : h / dispRef();
  const fPct = v => v == null ? "—" : (v * 100).toLocaleString("es-PE", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + " %";
  const fPct2 = v => v == null ? "–" : (v * 100).toLocaleString("es-PE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " %";
  const fH3 = v => v == null ? "–" : v.toLocaleString("es-PE", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
  const isoDia = k => new Date(Date.parse(D.disponibilidad.d0 + "T00:00:00Z") + k * DIA_MS).toISOString().slice(0, 10);
  const fFechaIso = iso => iso.slice(8) + " " + MES[+iso.slice(5, 7) - 1] + " " + iso.slice(0, 4);
  const fEtiqueta = (modo, clave) => modo === "dia" ? clave.slice(8) + " " + MES[+clave.slice(5, 7) - 1] + (clave.slice(0, 4) !== D.disponibilidad.hasta.slice(0, 4) ? " " + clave.slice(2, 4) : "")
    : modo === "mes" ? MES[+clave.slice(5, 7) - 1] + " " + clave.slice(0, 4) : clave;
  const dispPlantas = () => D.plantas.filter(p => D.inversores.some(i => i.planta === p.codigo && (D.disponibilidad.h[i.id] || []).some(h => h != null)));
  const colorPlanta = c => css("--s" + (D.plantas.findIndex(p => p.codigo === c) + 1));

  // Periodos (día / mes / año) en orden cronológico: {clave, dias, pl: {planta: fracción}, inv: {id: fracción}, horas: {id: h} (solo día)}
  function dispPeriodos(modo) {
    const B = D.disponibilidad;
    const n = Math.max(0, ...Object.values(B.h).map(a => a.length));
    const per = new Map();
    for (let k = 0; k < n; k++) {
      const iso = isoDia(k), clave = modo === "dia" ? iso : modo === "mes" ? iso.slice(0, 7) : iso.slice(0, 4);
      D.inversores.forEach(i => {
        const h = (B.h[i.id] || [])[k], a = dispFrac(h);
        if (a == null) return;
        let p = per.get(clave);
        if (!p) per.set(clave, p = { clave, dias: new Set(), pl: {}, inv: {}, horas: {} });
        p.dias.add(iso);
        const x = (p.inv[i.id] = p.inv[i.id] || { s: 0, n: 0 }); x.s += a; x.n++;
        const y = (p.pl[i.planta] = p.pl[i.planta] || { s: 0, n: 0 }); y.s += a; y.n++;
        if (modo === "dia") p.horas[i.id] = h;
      });
    }
    return [...per.values()].map(p => ({ clave: p.clave, dias: p.dias.size, horas: p.horas,
      pl: Object.fromEntries(Object.entries(p.pl).map(([c, x]) => [c, x.s / x.n])),
      inv: Object.fromEntries(Object.entries(p.inv).map(([c, x]) => [c, x.s / x.n])) }));
  }

  function pintarKpisDisp() {
    const B = D.disponibilidad;
    const k = (l, v, s) => el("div", { class: "kpi" }, el("div", { class: "l", text: l }), el("div", { class: "v", text: v }), el("div", { class: "s", text: s }));
    if (!B.d0) { $("#kpis").replaceChildren(k("Disponibilidad", "—", "aún sin datos")); return; }
    const dias = dispPeriodos("dia"), meses = dispPeriodos("mes");
    const ult = dias[dias.length - 1], mes = meses[meses.length - 1];
    const tarjetas = dispPlantas().map(p => k(`${p.codigo} · ${p.nombre.replace("BSF ", "")}`, fPct(ult.pl[p.codigo]),
      `${fFechaIso(ult.clave)} · mes ${fPct(mes && mes.pl[p.codigo])}`));
    const todas = o => { const v = Object.values(o.pl); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
    $("#kpis").replaceChildren(...tarjetas, k("Promedio de las plantas", fPct(todas(ult)), `${fFechaIso(ult.clave)} · mes ${fPct(mes && todas(mes))}`));
  }

  function pintarDisp() {
    const B = D.disponibilidad;
    document.querySelectorAll("#disp-gran button").forEach(b => b.classList.toggle("on", b.dataset.v === S.dg));
    document.querySelectorAll("#disp-rango button").forEach(b => b.classList.toggle("on", +b.dataset.v === S.dr));
    $("#disp-rango").hidden = S.dg !== "dia";
    pintarKpisDisp();
    $("#nota-disp").textContent = `Disponibilidad diaria de un inversor = horas de conexión a la red ÷ ${dispRef()} h (máximo 100 %); la de una planta es el promedio de sus inversores, y la mensual/anual el promedio de sus días. ` +
      (B.d0 ? `Datos de FusionSolar del ${fFechaIso(B.d0)} al ${fFechaIso(B.hasta)}; cada día a las 06:00 (Lima) se agrega el día anterior. ` : "") +
      "Un día sin conexión a la red cuenta como 0 %; un día sin datos en el portal (antes de la puesta en marcha) no se grafica.";
    const box = $("#disp-chart"), tabla = $("#t-disp");
    box.replaceChildren(); box.className = "chart";
    if (!B.d0) {
      box.className = "chart vacio";
      box.append(el("div", { class: "cv" }, el("span", { text: "Aún no hay datos de disponibilidad: se cargan al ejecutar «Disponibilidad diaria» (lee el Informe de inversores de FusionSolar)." })));
      vacioTabla(tabla, "Sin datos de disponibilidad todavía.");
      return;
    }
    const modo = S.dg;
    let per = dispPeriodos(modo);
    if (modo === "dia" && S.dr) per = per.slice(-S.dr);
    const plantas = dispPlantas();
    const nombreMod = { dia: "por día", mes: "por mes", anio: "por año" }[modo];
    const barra = barraZoom();
    box.append(el("h3", {}, "Disponibilidad " + nombreMod, el("span", { text: "% · promedio de los inversores de cada planta" }), barra || ""));
    box.append(leyenda(plantas.map(p => [colorPlanta(p.codigo), `${p.codigo} · ${p.nombre.replace("BSF ", "")}`])));
    const cv = el("div", { class: "cv" }); const cnv = el("canvas", { role: "img", "aria-label": "Disponibilidad de las plantas " + nombreMod }); cv.append(cnv); box.append(cv);
    const ds = plantas.map(p => ({ label: `${p.codigo} · ${p.nombre.replace("BSF ", "")}`, _cod: p.codigo, data: per.map(x => x.pl[p.codigo] == null ? null : x.pl[p.codigo] * 100),
      backgroundColor: colorPlanta(p.codigo), borderColor: colorPlanta(p.codigo), borderWidth: 0, borderRadius: 2, maxBarThickness: 36 }));
    const o = baseOpts(undefined, undefined, 0, 100);
    o.scales.x = { type: "category", grid: { display: false }, border: { color: css("--grid") },
      ticks: { color: css("--fg-2"), maxRotation: 0, autoSkip: true, maxTicksLimit: 14, callback(v) { return fEtiqueta(modo, per[v] ? per[v].clave : ""); } } };
    o.scales.y.ticks.callback = v => v + " %";
    o.plugins.zoom.limits = { x: { min: "original", max: "original", minRange: 3 }, y: { min: "original", max: "original" } };
    o.plugins.tooltip.filter = () => true;
    o.plugins.tooltip.callbacks = {
      title: its => its.length ? (modo === "dia" ? fFechaIso(per[its[0].dataIndex].clave) : fEtiqueta(modo, per[its[0].dataIndex].clave)) : "",
      label: c => `${c.dataset.label}: ${c.parsed.y == null ? "—" : fPct(c.parsed.y / 100)}`,
      afterBody: its => {
        const x = its.length ? per[its[0].dataIndex] : null; if (!x) return [];
        const l = D.inversores.filter(i => x.inv[i.id] != null).map(i => `${i.planta} ${i.nombre}: ${fPct(x.inv[i.id])}` + (modo === "dia" ? ` · ${fH3(x.horas[i.id])} h` : ""));
        return [...(modo === "dia" ? [] : [`${x.dias} día${x.dias === 1 ? "" : "s"} con datos`]), ...(l.length ? ["", ...l] : [])];
      },
    };
    mk("disp|" + modo + "|" + S.dr, cnv, { type: "bar", data: { labels: per.map(x => x.clave), datasets: ds }, options: o }, barra);
    tablaDisp(tabla, ordenGrupos(D.inversores), null, 120);
  }

  // Tabla como la hoja «Disponibilidad» del Excel: por inversor, horas operativas y disponibilidad (fórmula). Un renglón por día, el más reciente arriba.
  function tablaDisp(t, gr, desde, max) {
    const B = D.disponibilidad;
    if (!B || !B.activo) { vacioTabla(t, "La disponibilidad no está activa."); return; }
    const invs = gr.flatMap(g => g.invs);
    const n = Math.max(0, ...invs.map(i => (B.h[i.id] || []).length));
    const dias = [];
    for (let k = n - 1; k >= 0; k--) { const iso = isoDia(k); if (desde && iso < desde) break; if (invs.some(i => (B.h[i.id] || [])[k] != null)) dias.push([k, iso]); if (dias.length >= max) break; }
    if (!dias.length) { vacioTabla(t, "Aún no hay datos de disponibilidad" + (desde ? " desde " + fFechaIso(desde) : "") + "."); return; }
    const h1 = el("tr", {}, el("th", { class: "g", text: "Fecha", rowspan: 3 })), h2 = el("tr"), h3 = el("tr");
    gr.forEach((g, k) => {
      if (k) h1.append(el("th", { class: "sep", rowspan: 3 }));
      h1.append(el("th", { class: "g", colspan: g.invs.length * 2, text: `${g.planta} · ${g.grupo}` }));
      g.invs.forEach(i => { h2.append(el("th", { class: "i", colspan: 2, text: i.nombre })); h3.append(el("th", { class: "u", text: "Horas operativas" }), el("th", { class: "u", text: "Disponibilidad" })); });
    });
    const body = el("tbody");
    dias.forEach(([k, iso]) => {
      const tr = el("tr", {}, el("td", { text: iso.slice(8) + "/" + iso.slice(5, 7) + "/" + iso.slice(0, 4) }));
      gr.forEach((g, j) => {
        if (j) tr.append(el("td", { class: "sep" }));
        g.invs.forEach(i => {
          const h = (B.h[i.id] || [])[k], a = dispFrac(h);
          tr.append(el("td", { class: "p", text: fH3(h) }), el("td", { class: "d" + (a == null ? "" : a === 0 ? " cero" : a < 0.9 ? " bajo" : ""), text: fPct2(a) }));
        });
      });
      body.append(tr);
    });
    t.replaceChildren(el("thead", {}, h1, h2, h3), body);
  }

  // ---------------------------------------------------------------- tablas
  function pintarDiario() {
    const t = $("#t-diario");
    const vis = invsVisibles();
    const dias = new Set();
    vis.forEach(i => (D.diario[i.id] || []).forEach(r => dias.add(r[0])));
    const lista = [...dias].sort().slice(-14);
    if (!lista.length) { t.replaceChildren(el("tbody", {}, el("tr", {}, el("td", { class: "vacio-t", text: "Sin datos diarios todavía." })))); return; }
    const head = el("thead", {}, el("tr", {}, el("th", { text: "Inversor" }), el("th", { text: "Umbral" }), lista.map(d => el("th", { text: d.slice(8) + " " + MES[+d.slice(5, 7) - 1] }))));
    const body = el("tbody");
    vis.forEach(i => {
      const m = Object.fromEntries((D.diario[i.id] || []).map(r => [r[0], r]));
      body.append(el("tr", {}, el("td", { class: "t-l", text: `${i.planta} · ${i.nombre}` }), el("td", { text: fR(i.umbral) }),
        lista.map(d => {
          const r = m[d]; if (!r || r[1] == null) return el("td", { class: "c-nd", text: "–" });
          const um = r[7] != null ? r[7] : i.umbral;            // umbral vigente ese día (puede haber cambiado)
          const v = r[1]; const e = v < um ? "bad" : v < um * (1 + D.margen_advertencia) ? "warn" : "ok";
          return el("td", { class: "c-" + e, title: `mín ${fR(r[1])} · prom ${fR(r[2])} · máx ${fR(r[3])} · alarmas ${r[4]}/${r[5]}` }, (e === "bad" ? "✕ " : e === "warn" ? "! " : "") + fR(v));
        })));
    });
    t.replaceChildren(head, body);
  }

  // "Datos del Excel" con pestañas, como las hojas del archivo: Indicadores (MΩ y %), BESS e Inicio y temperatura interna.
  const NOTAS_TABLA = {
    inv: "Misma vista de la hoja «Indicadores» del Excel, separada por planta. Hasta 300 filas del rango elegido; el Excel descargable tiene todo el histórico.",
    bess: "Misma vista de la hoja «BESS»: temperaturas en °C de cada BESS por corrida (últimos 7 días). Máx = temperatura más alta del grupo; Sum./Ret. = agua suministrada / de retorno; bat. = lado batería; pot. = lado potencia; Ext. = exterior.",
    dia: "Un renglón por día. «Inicio» es la hora a la que arrancó el inversor ese día (un valor por día); «T máx» es la temperatura interna más alta registrada ese día.",
    disp: "Misma vista de la hoja «Disponibilidad»: horas operativas del día (Duración de conexión a la red, de FusionSolar) y disponibilidad = si horas ≥ 12 → 100 %, si no horas ÷ 12. Un renglón por día, el más reciente arriba.",
  };
  const ordenGrupos = (invs) => { const o = ["LC", "VES", "PL"]; return grupos(invs).sort((x, y) => o.indexOf(x.planta) - o.indexOf(y.planta) || x.grupo.localeCompare(y.grupo)); };
  const vacioTabla = (t, msg) => t.replaceChildren(el("tbody", {}, el("tr", {}, el("td", { class: "vacio-t", text: msg }))));
  const fFechaCorta = ts => pad(new Date(ts * 1000).getUTCDate()) + "/" + pad(new Date(ts * 1000).getUTCMonth() + 1);
  const fHms = ts => { const d = new Date(ts * 1000); return pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()) + ":" + pad(d.getUTCSeconds()); };

  function pintarExcel() {
    document.querySelectorAll("#disp-gran button").forEach(b => b.addEventListener("click", () => {
    S.dg = b.dataset.v; soltarZoom = true;
    if (D) pintar();
  }));
  document.querySelectorAll("#disp-rango button").forEach(b => b.addEventListener("click", () => {
    S.dr = +b.dataset.v; soltarZoom = true;
    if (D) pintar();
  }));

  document.querySelectorAll("#tabs-excel button").forEach(b => b.classList.toggle("on", b.dataset.t === S.tabla));
    $("#nota-excel").textContent = NOTAS_TABLA[S.tabla];
    const t = $("#t-excel");
    if (S.tabla === "bess") tablaBess(t); else if (S.tabla === "dia") tablaDia(t);
    else if (S.tabla === "disp") tablaDisp(t, ordenGrupos(), (D.disponibilidad || {}).excel_desde, 300);
    else tablaInversores(t);
  }

  function tablaBess(t) {
    const B = D.bess;
    if (!B || !B.activo || !B.equipos.length) { vacioTabla(t, "Las temperaturas BESS no están activas."); return; }
    const TIT = ["Máx", "Sum. bat.", "Ret. bat.", "Sum. pot.", "Ret. pot.", "Ext."];
    const mapas = new Map(B.equipos.map(e => [e.id, new Map(((B.serie || {})[e.id] || []).map(p => [p[0], p]))]));
    const min = tMin();
    const tiempos = [...new Set([...mapas.values()].flatMap(m => [...m.keys()]))].filter(x => x >= min).sort((a, b) => b - a).slice(0, 300);
    if (!tiempos.length) { vacioTabla(t, "Sin lecturas BESS en el rango seleccionado."); return; }
    const h1 = el("tr", {}, el("th", { class: "g", text: "Horario", rowspan: 2 })), h2 = el("tr");
    B.equipos.forEach((e, k) => {
      if (k) h1.append(el("th", { class: "sep", rowspan: 2 }));
      h1.append(el("th", { class: "g", colspan: TIT.length, text: e.nombre }));
      TIT.forEach(x => h2.append(el("th", { class: "i", text: x })));
    });
    const body = el("tbody");
    tiempos.forEach(ts => {
      const tr = el("tr", {}, el("td", { text: fFechaCorta(ts) + " " + fHora(ts) }));
      B.equipos.forEach((e, k) => {
        if (k) tr.append(el("td", { class: "sep" }));
        const p = mapas.get(e.id).get(ts);
        TIT.forEach((_, j) => tr.append(el("td", { class: "p", text: p && p[j + 1] != null ? fT(p[j + 1]) : "–" })));
      });
      body.append(tr);
    });
    t.replaceChildren(el("thead", {}, h1, h2), body);
  }

  function tablaDia(t) {
    const gr = ordenGrupos(), invs = gr.flatMap(g => g.invs);
    const dia = ts => Math.floor(ts / 86400);
    const inicio = new Map(invs.map(i => [i.id, new Map(((D.inicio || {})[i.id] || []).map(ts => [dia(ts), ts]))]));
    const tmax = new Map(invs.map(i => {
      const m = new Map();
      serie(i.id).forEach(p => { if (p[4] != null) m.set(dia(p[0]), Math.max(m.get(dia(p[0])) ?? -Infinity, p[4])); });
      return [i.id, m];
    }));
    const minDia = dia(tMin());
    const dias = [...new Set(invs.flatMap(i => [...inicio.get(i.id).keys(), ...tmax.get(i.id).keys()]))].filter(d => d >= minDia).sort((a, b) => b - a).slice(0, 60);
    if (!dias.length) { vacioTabla(t, "Aún no hay horas de inicio ni temperaturas internas en el rango seleccionado."); return; }
    const h1 = el("tr", {}, el("th", { class: "g", text: "Día", rowspan: 3 })), h2 = el("tr"), h3 = el("tr");
    gr.forEach((g, k) => {
      if (k) h1.append(el("th", { class: "sep", rowspan: 3 }));
      h1.append(el("th", { class: "g", colspan: g.invs.length * 2, text: `${g.planta} · ${g.grupo}` }));
      g.invs.forEach(i => { h2.append(el("th", { class: "i", colspan: 2, text: i.nombre })); h3.append(el("th", { class: "u", text: "Inicio" }), el("th", { class: "u", text: "T máx °C" })); });
    });
    const body = el("tbody");
    dias.forEach(d => {
      const tr = el("tr", {}, el("td", { text: fFechaCorta(d * 86400) }));
      gr.forEach((g, k) => {
        if (k) tr.append(el("td", { class: "sep" }));
        g.invs.forEach(i => {
          const ini = inicio.get(i.id).get(d), tm = tmax.get(i.id).get(d);
          tr.append(el("td", { class: "c-ok", text: ini != null ? fHms(ini) : "–" }), el("td", { class: "p", text: tm != null ? fT(tm) : "–" }));
        });
      });
      body.append(tr);
    });
    t.replaceChildren(el("thead", {}, h1, h2, h3), body);
  }

  function tablaInversores(t) {
    const gr = ordenGrupos();
    const invs = gr.flatMap(g => g.invs);
    const mapas = new Map(invs.map(i => [i.id, new Map(serie(i.id).map(p => [p[0], p]))]));
    const min = tMin();
    const tiempos = [...new Set(invs.flatMap(i => serie(i.id).map(p => p[0])))].filter(x => x >= min).sort((a, b) => b - a).slice(0, 300);
    if (!tiempos.length) { vacioTabla(t, "Sin datos en el rango seleccionado."); return; }
    const h1 = el("tr", {}, el("th", { class: "g", text: "Horario", rowspan: 3 }));
    const h2 = el("tr"), h3 = el("tr");
    gr.forEach((g, k) => {
      if (k) h1.append(el("th", { class: "sep", rowspan: 3 }));
      h1.append(el("th", { class: "g", colspan: g.invs.length * 2, text: `${g.planta} · ${g.grupo}` }));
      g.invs.forEach(i => { h2.append(el("th", { class: "i", colspan: 2, text: i.nombre })); h3.append(el("th", { class: "u", text: "MΩ" }), el("th", { class: "u", text: "%" })); });
    });
    const body = el("tbody");
    tiempos.forEach(ts => {
      const tr = el("tr", {}, el("td", { text: pad(new Date(ts * 1000).getUTCDate()) + "/" + pad(new Date(ts * 1000).getUTCMonth() + 1) + " " + fHora(ts) }));
      gr.forEach((g, k) => {
        if (k) tr.append(el("td", { class: "sep" }));
        g.invs.forEach(i => {
          const p = mapas.get(i.id).get(ts);
          const r = p ? p[1] : null;
          const um = p && p[3] != null ? p[3] : i.umbral;       // umbral vigente en esa lectura
          const e = r == null ? "nd" : r < um ? "bad" : r < um * (1 + D.margen_advertencia) ? "warn" : "ok";
          tr.append(el("td", { class: "c-" + e, text: r == null ? "–" : (e === "bad" ? "✕ " : e === "warn" ? "! " : "") + fR(r) }),
                    el("td", { class: "p", text: p && p[2] != null ? p[2].toLocaleString("es-PE", { maximumFractionDigits: 1 }) : "–" }));
        });
      });
      body.append(tr);
    });
    t.replaceChildren(el("thead", {}, h1, h2, h3), body);
  }

  function pintarAlarmas() {
    const t = $("#t-alarmas");
    const ids = new Set(invsVisibles().map(i => i.id));
    const lim = S.dias ? naive(D.generado) - S.dias * 86400000 : 0;
    const rows = D.alarmas.filter(a => ids.has(a.id) && naive(a.t) >= lim).slice(0, 150);
    if (!rows.length) { t.replaceChildren(el("tbody", {}, el("tr", {}, el("td", { class: "vacio-t", text: "Sin alarmas ni advertencias en el rango seleccionado." })))); return; }
    t.replaceChildren(
      el("thead", {}, el("tr", {}, ["Fecha y hora", "Inversor", "Resistencia (MΩ)", "Umbral (MΩ)", "Diferencia", "Estado"].map(h => el("th", { text: h })))),
      el("tbody", {}, rows.map(a => {
        const dif = a.r != null && a.um ? ((a.r - a.um) / a.um * 100) : null;
        return el("tr", {}, el("td", { text: a.t }), el("td", { class: "t-l", text: a.id.replace("-", " · ") }), el("td", { text: fR(a.r) }), el("td", { text: fR(a.um) }),
          el("td", { text: dif == null ? "—" : (dif > 0 ? "+" : "") + dif.toFixed(0) + " %" }), el("td", {}, pill(a.estado)));
      })));
  }

  function pintarPotencia() {
    const t = $("#t-pot");
    const ids = new Set(invsVisibles().map(i => i.id));
    const lim = S.dias ? naive(D.generado) - S.dias * 86400000 : 0;
    const rows = D.eventos_potencia.filter(a => ids.has(a.id) && naive(a.t) >= lim).slice(0, 150);
    if (!rows.length) { t.replaceChildren(el("tbody", {}, el("tr", {}, el("td", { class: "vacio-t", text: "Sin limitaciones de potencia activa en el rango seleccionado." })))); return; }
    t.replaceChildren(
      el("thead", {}, el("tr", {}, ["Fecha y hora", "Inversor", "Potencia", "Estado reportado"].map(h => el("th", { text: h })))),
      el("tbody", {}, rows.map(a => el("tr", {}, el("td", { text: a.t }), el("td", { class: "t-l", text: a.id.replace("-", " · ") }), el("td", { text: fP(a.p) }), el("td", { class: "t-l", text: a.txt })))));
  }

  // ---------------------------------------------------------------- eventos
  document.querySelectorAll("#f-rango button").forEach(b => b.addEventListener("click", () => {
    S.dias = +b.dataset.v;
    soltarZoom = true;
    document.querySelectorAll("#f-rango button").forEach(x => x.classList.toggle("on", x === b));
    if (D) pintar();
  }));

  document.querySelectorAll("#tabs-excel button").forEach(b => b.addEventListener("click", () => { S.tabla = b.dataset.t; if (D) pintarExcel(); }));

  const tema = $("#tema");
  tema.addEventListener("click", () => {
    const root = document.documentElement;
    const oscuro = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = oscuro ? "light" : "dark";
    try { localStorage.setItem("tema", root.dataset.theme); } catch (e) { /* sin almacenamiento */ }
    if (D) pintar();
  });
  try { const t = localStorage.getItem("tema"); if (t) document.documentElement.dataset.theme = t; } catch (e) { /* sin almacenamiento */ }
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => D && pintar());

  cargar();
  setInterval(cargar, 5 * 60 * 1000);
  setInterval(frescura, 30 * 1000);
})();
