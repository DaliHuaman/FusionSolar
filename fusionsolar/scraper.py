"""Extraccion desde el portal FusionSolar (Playwright).

Flujo (igual al procedimiento manual del Word "Aislamiento"):
  login -> pestaña Control -> expandir arbol de plantas -> por cada inversor:
  leer "Resistencia de aislamiento" y "Estado de ajuste de potencia activa".
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from .common import (CAMPOS_BESS, STATE_DIR, parse_inicio, parse_kw, parse_mohm, parse_num, parse_pct, parse_temp,
                     umbral_valido)

LOGIN_URL = "https://la5.fusionsolar.huawei.com/pvmswebsite/login/build/index.html"
log = logging.getLogger("fusionsolar.scraper")

# Etiquetas del portal (español / ingles) -> el valor esta en el <div title=...> hermano.
ETIQ_RESISTENCIA = ["Resistencia de aislamiento", "Insulation resistance"]
ETIQ_POT_ACTIVA = ["Estado de ajuste de potencia activa", "Active power adjustment status"]
ETIQ_ESTADO_INV = ["Estado del inversor", "Inverter status"]
ETIQ_PNOM = ["Potencia nominal del inversor", "Rated power"]
ETIQ_NOMBRE = ["Nombre del dispositivo", "Device name"]
# Real-Time Device Data del inversor: hora de inicio (ultimo arranque) y temperatura interna.
ETIQ_INICIO = ["Fecha y hora de inicio del inversor", "Hora de inicio del inversor", "Inverter startup time"]
ETIQ_TEMP_INT = ["Temperatura interna", "Internal temperature"]

# BESS (pestaña "Detalles"): tablas etiqueta/valor <tr class="detail-item">; se leen todas en una sola evaluacion.
ETIQ_BESS = {
    "temp_max": ["Temperatura más alta del grupo", "Highest temperature of the group"],
    "agua_bat_sum": ["Temperatura del agua suministrada en el lado de la batería"],
    "agua_bat_ret": ["Temperatura del agua de retorno en el lado de la batería"],
    "agua_pot_sum": ["Temperatura del agua suministrada en el lado de la potencia"],
    "agua_pot_ret": ["Temperatura del agua de retorno en el lado de la potencia"],
    "temp_ext": ["Temperatura en exteriores", "Outdoor temperature"],
}
TEXTO_DETALLES = ["Detalles", "Details"]

# Inversor (pestaña "Configuración", solo lectura): el umbral es un <input> numerico dentro del bloque de su etiqueta.
ETIQ_UMBRAL = ["Umbral de protección de resistencia de aislamiento", "Insulation resistance protection threshold"]
TEXTO_CONFIG = ["Configuración", "Settings", "Configuration"]

TEXTO_LOGIN = ["Iniciar sesión", "Log In", "Login"]

# Informes > Informe de inversores (granularidad "Por día"): la columna "Duración de conexión a la red (h)" son las horas operativas.
RUTA_INFORME = "#/reportv3/inverter"
API_INFORME = "/rest/pvms/web/report/v1/inverter/energy-generate-kpi-list"
ETIQ_HORAS_RED = ["duración de conexión a la red", "grid connection duration", "grid connected duration"]
ETIQ_RENDIMIENTO = ["rendimiento", "yield", "energy yield"]      # kWh del día: distingue "sin datos" de "no se conectó a la red"
BOTON_BUSCAR = re.compile(r"^(Buscar|Search)$")

JS_NODOS = """els => els.map(e => {
  const li = e.closest("li.node-line");
  const img = li.querySelector("img");
  let depth = 0, n = li;
  while (n) { n = n.parentElement && n.parentElement.closest("li.node-line"); if (n) depth++; }
  return {ne: e.id.replace("monitor-layout-node-name-", ""),
          nombre: e.textContent.trim(),
          icono: img ? img.getAttribute("src").split("/").pop() : "",
          nivel: depth};
})"""

JS_VALOR = """(labels) => {
  const norm = s => (s || "").replace(/\\s+/g, " ").trim().toLowerCase();
  for (const lab of labels) {
    for (const d of document.querySelectorAll("div[title]")) {
      if (norm(d.getAttribute("title")) === norm(lab)) {
        const sib = d.nextElementSibling;
        if (!sib) continue;
        const v = sib.querySelector("div") || sib;
        return {titulo: v.getAttribute("title") || "", texto: (v.textContent || "").trim()};
      }
    }
  }
  return null;
}"""

JS_DETALLES = """() => {
  const norm = s => (s || "").replace(/\\s+/g, " ").replace(/\\s*[:：]\\s*$/, "").trim().toLowerCase();
  const out = {};
  for (const tr of document.querySelectorAll("tr.detail-item")) {
    const l = tr.querySelector(".detail-data-label"), v = tr.querySelector(".detail-data-value");
    const k = l && norm(l.textContent);
    if (k && v && !(k in out)) out[k] = v.textContent.trim();
  }
  return out;
}"""


# El propio informe consulta este servicio REST (la tabla de la pantalla sale de aquí): se llama desde la sesión ya abierta, con el
# mismo token anti-CSRF ("roarand") que usa la página. statTime = medianoche UTC de la fecha; statTimeDim 2 = "Por día";
# startupTime = "Duración de conexión a la red (h)"; productPower = "Rendimiento (kWh)".
JS_INFORME_API = """async ([ne, ms, token]) => {
  const pedir = async pagina => {
    const r = await fetch(%r, {method: "POST", credentials: "include",
      headers: {"content-type": "application/json", "roarand": token},
      body: JSON.stringify({moList: [{moType: 20800, moString: ne}], stationDn: "", orderBy: "productPower", page: pagina, pageSize: 100,
                            sort: "desc", statTime: ms, statTimeDim: "2", timeZone: 0, mocId: 20822})});
    let j = null;
    try { j = await r.json(); } catch (e) { j = null; }
    return {status: r.status, d: (j && j.data) || null};
  };
  const filas = [];
  let r = await pedir(1), total = r.d ? r.d.total : null;
  for (let pagina = 1; r.status === 200 && r.d; pagina++) {
    (r.d.list || []).forEach(x => filas.push({planta: x.stationName, dispositivo: x.deviceName, horas: x.startupTime, rend: x.productPower}));
    if (filas.length >= total || !(r.d.list || []).length || pagina >= 20) break;
    r = await pedir(pagina + 1);
  }
  return {status: r.status, json: !!r.d, total, filas};
}""" % API_INFORME

# Respaldo por pantalla: lee la tabla del informe (se localiza la columna por su encabezado).
JS_TABLA_INFORME = """([etiquetas, etiquetasRend]) => {
  const norm = s => (s || "").replace(/\\s+/g, " ").trim().toLowerCase();
  const ths = [...document.querySelectorAll(".dpdesign-table-thead th")].map(t => norm(t.getAttribute("title") || t.textContent));
  const i = ths.findIndex(t => etiquetas.some(e => t.startsWith(e)));
  const j = ths.findIndex(t => etiquetasRend.some(e => t.startsWith(e)));
  const filas = [...document.querySelectorAll("tr.dpdesign-table-row")].map(tr => [...tr.querySelectorAll("td")].map(td => td.textContent.trim()))
    .filter(f => f.some(x => x));
  return {col: i, colRend: j, nombres: ths, filas};
}"""

JS_CONFIG = """([labelsUmbral, labelsNombre]) => {
  const norm = s => (s || "").replace(/\\s+/g, " ").replace(/\\s*[:：]\\s*$/, "").trim().toLowerCase();
  const input = labels => {
    for (const sp of document.querySelectorAll("span.signal-config-signal-name")) {
      const t = norm(sp.getAttribute("title") || sp.textContent).replace(/\\s*\\([^)]*\\)$/, "");
      if (!labels.some(l => norm(l) === t)) continue;
      const bloque = sp.closest(".device-signal-form-item");
      const inp = bloque && bloque.querySelector("input");
      if (inp) return inp;
    }
    return null;
  };
  const u = input(labelsUmbral), n = input(labelsNombre);
  return {nombre: n ? (n.getAttribute("title") || n.value || "") : null,
          texto: u ? (u.value || "") : null,
          numero: u ? u.getAttribute("aria-valuenow") : null};
}"""

# Cierra cualquier ventana modal visible (p. ej. "Smart Health Guard"): primero la X, luego un boton de rechazo/cierre
# ("Cancelar", "Cerrar", "Más tarde"...) y, solo para avisos informativos, "Aceptar". NUNCA pulsa "Habilitar"/"Enable".
JS_CERRAR_MODALES = """([cierres, avisos]) => {
  const norm = s => (s || "").replace(/\\s+/g, " ").trim().toLowerCase();
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.display !== "none" && s.visibility !== "hidden"; };
  const raices = [...document.querySelectorAll('[role="dialog"], [class*="modal"][class*="wrap"], .ant-modal, .dpdesign-modal')]
    .filter(vis);
  let cerrados = 0;
  for (const m of raices) {
    const x = m.querySelector('[class*="modal-close"], button[aria-label="Close"], button[aria-label="Cerrar"], .close-icon-wrap');
    const botones = [...m.querySelectorAll("button, [role=button], a.btn")].filter(vis);
    const por = lista => botones.find(b => lista.includes(norm(b.textContent)));
    const b = (x && vis(x) ? x : null) || por(cierres) || por(avisos);
    if (b) { b.click(); cerrados++; }
  }
  return {modales: raices.length, cerrados};
}"""
CIERRES_MODAL = ["cancelar", "cancel", "cerrar", "close", "más tarde", "mas tarde", "later", "omitir", "skip",
                 "ahora no", "not now", "no, gracias", "entendido", "got it"]
AVISOS_MODAL = ["aceptar", "accept", "ok"]


class ErrorPortal(RuntimeError):
    pass


class Scraper:
    def __init__(self, usuario: str, clave: str, headless: bool = True, depuracion: Path | None = None):
        self.usuario, self.clave = usuario, clave
        self.headless = headless
        self.dbg = depuracion or (STATE_DIR / "debug")
        self.dbg.mkdir(parents=True, exist_ok=True)
        self._pw = self._browser = self.page = None
        self._token = None                                    # token anti-CSRF ("roarand") de las llamadas de la propia página
        self._raiz_informe = None                             # nodo raíz de las plantas (NE=...) que usa el Informe de inversores

    # ------------------------------------------------------------------ ciclo de vida
    def __enter__(self):
        self._pw = sync_playwright().start()
        kw = {"headless": self.headless, "args": ["--no-sandbox"]}
        if os.environ.get("CHROMIUM_PATH"):
            kw["executable_path"] = os.environ["CHROMIUM_PATH"]
        self._browser = self._pw.chromium.launch(**kw)
        ctx = self._browser.new_context(viewport={"width": 1800, "height": 1000}, locale="es-ES")
        ctx.set_default_timeout(20000)
        self.page = ctx.new_page()
        return self

    def __exit__(self, *exc):
        try:
            self._browser.close()
        except Exception:
            pass
        try:
            self._pw.stop()
        except Exception:
            pass

    def captura(self, nombre: str) -> None:
        try:
            self.page.screenshot(path=str(self.dbg / f"{nombre}.png"))
            (self.dbg / f"{nombre}.html").write_text(self.page.content(), encoding="utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------------ login / navegacion
    def login(self) -> None:
        pg = self.page
        pg.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
        pg.fill("input[type=text]", self.usuario)
        pg.fill("input[type=password]", self.clave)
        for txt in TEXTO_LOGIN:
            btn = pg.get_by_text(txt, exact=True)
            if btn.count():
                btn.last.click()
                break
        else:
            pg.keyboard.press("Enter")
        try:
            pg.wait_for_url(re.compile(r"cloud\.html"), timeout=45000)
        except PWTimeout:
            self.captura("login_fallo")
            raise ErrorPortal("No se completo el login (credenciales, captcha o verificacion adicional). Ver state/debug/login_fallo.png")
        pg.wait_for_timeout(3000)
        self._cerrar_avisos()
        log.info("login correcto")

    def _cerrar_avisos(self, intentos: int = 4) -> int:
        """Descarta modales/avisos que bloquean los clics (Smart Health Guard, novedades, sesion...). Devuelve cuantos cerro.

        Es barato (una evaluacion JS) y se llama tras el login, tras abrir Control y antes de leer cada equipo.
        """
        pg, total = self.page, 0
        for i in range(intentos):
            try:
                r = pg.evaluate(JS_CERRAR_MODALES, [CIERRES_MODAL, AVISOS_MODAL])
            except Exception:
                break
            if not r["modales"]:
                break
            if r["cerrados"]:
                total += r["cerrados"]
                log.info("aviso/modal del portal cerrado (%s)", r["cerrados"])
            else:                                             # modal sin boton reconocible: ultimo recurso, tecla Esc
                if i == 0:
                    self.captura("modal_desconocido")
                try:
                    pg.keyboard.press("Escape")
                except Exception:
                    pass
            pg.wait_for_timeout(500)
        return total

    def abrir_control(self) -> None:
        pg = self.page
        for intento in range(3):
            try:
                self._cerrar_avisos()
                pg.get_by_text("Control", exact=True).first.hover()
                pg.wait_for_timeout(800)
                visibles = pg.locator('text="Control" >> visible=true')
                visibles.last.click()
                pg.wait_for_selector('[id^="monitor-layout-node-name-"]', timeout=30000)
                pg.wait_for_timeout(1500)
                self._cerrar_avisos()                         # el modal "Smart Health Guard" aparece al cargar Control
                return
            except (PWTimeout, PWError):
                log.warning("abrir_control intento %s fallo", intento + 1)
                pg.wait_for_timeout(2000)
        self.captura("control_fallo")
        raise ErrorPortal("No se pudo abrir la pestaña Control")

    def expandir_arbol(self) -> list[dict]:
        pg = self.page
        for _ in range(8):
            self._cerrar_avisos()
            carets = pg.query_selector_all(".anticon-caret-right.tree-icon")
            if not carets:
                break
            for c in carets:
                try:
                    c.click(timeout=1500)
                    pg.wait_for_timeout(500)
                except (PWTimeout, PWError):
                    pass
            pg.wait_for_timeout(1200)
        return pg.eval_on_selector_all('[id^="monitor-layout-node-name-"]', JS_NODOS)

    def _clic_nodo(self, ne: str) -> None:
        """Selecciona un equipo del arbol; si un modal tapa el clic, lo cierra y reintenta una vez."""
        sel = f'[id="monitor-layout-node-name-{ne}"]'
        self._cerrar_avisos(intentos=1)
        try:
            self.page.click(sel, timeout=8000)
        except (PWTimeout, PWError):
            log.warning("clic bloqueado en %s; cierro avisos y reintento", ne)
            self._cerrar_avisos()
            self.page.click(sel)

    # ------------------------------------------------------------------ lectura de un inversor
    def leer_inversor(self, ne: str, nombre: str) -> dict:
        """Lee el inversor; si el portal devuelve '--' (dato momentáneamente no disponible) reintenta una vez."""
        lec = self._leer_inversor_una_vez(ne, nombre)
        if lec["resistencia"] is None:
            log.info("%s: sin dato en la primera lectura, reintento", nombre)
            self.page.wait_for_timeout(6000)
            lec2 = self._leer_inversor_una_vez(ne, nombre)
            if lec2["resistencia"] is not None:
                return lec2
        return lec

    def _leer_inversor_una_vez(self, ne: str, nombre: str) -> dict:
        pg = self.page
        self._clic_nodo(ne)
        # esperar a que el panel muestre ESTE dispositivo (evita leer el valor del anterior)
        try:
            pg.wait_for_function(
                """([labels, esperado]) => {
                  const norm = s => (s||"").replace(/\\s+/g," ").trim().toLowerCase();
                  for (const lab of labels) for (const d of document.querySelectorAll("div[title]")) {
                    if (norm(d.getAttribute("title")) === norm(lab)) {
                      const v = d.nextElementSibling && d.nextElementSibling.querySelector("div");
                      if (v && norm(v.getAttribute("title")) === norm(esperado)) return true;
                    }}
                  return false; }""",
                arg=[ETIQ_NOMBRE, nombre], timeout=20000)
        except PWTimeout:
            log.warning("%s: el panel no confirmo el nombre del dispositivo", nombre)
        # esperar a que las lecturas dejen de estar vacias (puede tardar unos segundos)
        res = None
        for _ in range(10):
            res = pg.evaluate(JS_VALOR, ETIQ_RESISTENCIA)
            if res and (res["texto"] or res["titulo"]):
                break
            pg.wait_for_timeout(800)
        pot = pg.evaluate(JS_VALOR, ETIQ_POT_ACTIVA)
        est = pg.evaluate(JS_VALOR, ETIQ_ESTADO_INV)
        pnom = pg.evaluate(JS_VALOR, ETIQ_PNOM)
        ini = pg.evaluate(JS_VALOR, ETIQ_INICIO)
        tint = pg.evaluate(JS_VALOR, ETIQ_TEMP_INT)
        txt_res = (res or {}).get("texto", "")
        txt_pot = ((pot or {}).get("titulo") or (pot or {}).get("texto") or "").strip()
        return {
            "resistencia_txt": txt_res,
            "resistencia": parse_mohm(txt_res),
            "potencia_txt": txt_pot,
            "potencia_pct": _pct_static(txt_pot, (pnom or {}).get("texto")),
            "estado_inversor": ((est or {}).get("texto") or "").strip(),
            "inicio": parse_inicio((ini or {}).get("texto")),
            "temp_interna": parse_temp((tint or {}).get("texto")),
            "pnom_kw": parse_kw((pnom or {}).get("texto")),          # «Potencia nominal del inversor» (fija; ver potencia.py)
            "campos_encontrados": res is not None,
        }

    # ------------------------------------------------------------------ umbral de aislamiento (solo lectura)
    def _abrir_pestana(self, textos: list[str]) -> bool:
        for txt in textos:
            tab = self.page.get_by_text(txt, exact=True)
            if tab.count():
                tab.first.click()
                return True
        return False

    def leer_umbral(self, ne: str, nombre: str) -> float | None:
        """Umbral de proteccion de resistencia de aislamiento (MΩ) de un inversor (Control > inversor > Configuración).

        Solo lee: nunca pulsa Guardar ni toca ningun campo. Devuelve None si no se pudo leer con certeza.
        """
        pg = self.page
        self._clic_nodo(ne)
        esperado = " ".join(nombre.split()).lower()
        previo = None
        for i in range(25):                                  # hasta ~20 s a que el formulario muestre ESTE inversor
            datos = pg.evaluate(JS_CONFIG, [ETIQ_UMBRAL, ETIQ_NOMBRE])
            if datos["texto"] is not None and " ".join((datos["nombre"] or "").split()).lower() == esperado:
                num = datos["numero"]
                valor = float(num) if num not in (None, "") else parse_mohm(datos["texto"])
                if valor == previo:                          # mismo valor en dos lecturas seguidas: el formulario ya cargo
                    return valor if umbral_valido(valor) else None
                previo = valor
            else:
                previo = None
            if i % 4 == 0:                                   # al cambiar de nodo la vista puede volver a "Detalles"
                self._abrir_pestana(TEXTO_CONFIG)
            pg.wait_for_timeout(800)
        log.warning("%s: la pestaña Configuración no mostró el umbral de este inversor", nombre)
        return None

    def volver_a_detalles(self) -> None:
        """Deja el panel en 'Detalles' (donde leer_inversor busca sus campos) tras visitar 'Configuración'."""
        try:
            self._abrir_pestana(TEXTO_DETALLES)
            self.page.wait_for_timeout(800)
        except Exception:
            pass

    # ------------------------------------------------------------------ Informe de inversores (disponibilidad)
    def _capturar_token(self, req) -> None:
        t = req.headers.get("roarand")
        if t:
            self._token = t

    def abrir_informe_inversores(self) -> None:
        """Informes > Informe de inversores. Se navega por la ruta del portal (el menú con hover es poco fiable) y se espera la tabla."""
        pg = self.page
        if not getattr(self, "_escucha_token", False):
            pg.on("request", self._capturar_token)
            self._escucha_token = True
        for intento in range(3):
            try:
                self._cerrar_avisos()
                pg.evaluate(f"location.hash = {RUTA_INFORME!r}")
                pg.wait_for_selector("tr.dpdesign-table-row", timeout=30000)
                pg.wait_for_timeout(1500)
                m = re.search(r"reportv3/inverter/(NE=\d+)", pg.url)
                self._raiz_informe = m.group(1) if m else None
                self._cerrar_avisos()
                log.info("Informe de inversores abierto (%s)", self._raiz_informe or "sin nodo raíz")
                return
            except (PWTimeout, PWError):
                log.warning("abrir_informe_inversores intento %s fallo", intento + 1)
                pg.wait_for_timeout(2000)
        self.captura("informe_fallo")
        raise ErrorPortal("No se pudo abrir Informes > Informe de inversores")

    def horas_dia(self, dia: date) -> list[dict]:
        """Horas de conexión a la red de cada inversor en un día: [{planta, dispositivo, horas, rend}] (None = el portal no trae el valor).

        Primero por el servicio REST del informe (≈0,3 s por día); si falla, escribiendo la fecha en la pantalla y pulsando Buscar.
        Cada fila: horas (conexión a la red) y rend (kWh del día). Devuelve [] cuando el portal responde que no hay datos ese día; lanza ErrorPortal si no se pudo consultar.
        """
        try:
            return self._horas_dia_api(dia)
        except Exception as e:
            log.warning("informe %s por API fallo (%s); pruebo por pantalla", dia, e)
        return self._horas_dia_pantalla(dia)

    def _horas_dia_api(self, dia: date) -> list[dict]:
        if not (self._token and self._raiz_informe):
            raise ErrorPortal("sin token o nodo raíz del informe")
        ms = int(datetime(dia.year, dia.month, dia.day, tzinfo=timezone.utc).timestamp() * 1000)
        r = self.page.evaluate(JS_INFORME_API, [self._raiz_informe, ms, self._token])
        if r["status"] != 200 or not r["json"] or r["total"] is None:
            raise ErrorPortal(f"respuesta inesperada del informe (HTTP {r['status']})")
        return [{"planta": f["planta"] or "", "dispositivo": f["dispositivo"] or "",
                 "horas": _num_o_none(f["horas"]), "rend": _num_o_none(f["rend"])} for f in r["filas"]]

    def _horas_dia_pantalla(self, dia: date) -> list[dict]:
        pg = self.page
        self._cerrar_avisos()
        campo = pg.locator('input[value^="20"]:visible').first
        campo.click()
        pg.keyboard.press("Control+A")
        pg.keyboard.type(dia.isoformat())
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(400)
        try:
            with pg.expect_response(lambda r: API_INFORME in r.url, timeout=25000):
                pg.get_by_role("button", name=BOTON_BUSCAR).click()
        except PWTimeout:
            raise ErrorPortal("el informe no respondió al pulsar Buscar")
        pg.wait_for_timeout(1200)
        t = pg.evaluate(JS_TABLA_INFORME, [ETIQ_HORAS_RED, ETIQ_RENDIMIENTO])
        if t["filas"] and t["col"] < 0:
            self.captura("informe_sin_columna")
            raise ErrorPortal("no se encontró la columna «Duración de conexión a la red» en la tabla")
        if t["filas"] and t["colRend"] < 0:
            log.warning("informe: no se encontró la columna de rendimiento; un día sin conexión se tomará como «sin dato»")
        return [{"planta": f[0], "dispositivo": f[1], "horas": parse_num(f[t["col"]]),
                 "rend": parse_num(f[t["colRend"]]) if t["colRend"] >= 0 else None} for f in t["filas"]]

    # ------------------------------------------------------------------ lectura de un BESS
    def leer_bess(self, ne: str, nombre: str) -> dict:
        """Temperaturas de un BESS (Control > BESS > Detalles). Reintenta una vez si todo viene vacio."""
        lec = self._leer_bess_una_vez(ne, nombre)
        if all(lec[k] is None for k in CAMPOS_BESS):
            log.info("%s: sin temperaturas en la primera lectura, reintento", nombre)
            self.page.wait_for_timeout(5000)
            lec = self._leer_bess_una_vez(ne, nombre)
        return lec

    def _leer_bess_una_vez(self, ne: str, nombre: str) -> dict:
        pg = self.page
        self._clic_nodo(ne)
        esperado = " ".join(nombre.split()).lower()
        datos: dict = {}
        for i in range(25):                              # hasta ~20 s a que la tabla muestre ESTE BESS
            if i % 5 == 0:                               # la vista inicial es "Información general": abrir "Detalles"
                for txt in TEXTO_DETALLES:
                    tab = pg.get_by_text(txt, exact=True)
                    if tab.count():
                        tab.first.click()
                        break
            pg.wait_for_timeout(800)
            datos = pg.evaluate(JS_DETALLES)
            if " ".join(datos.get("nombre del dispositivo", "").split()).lower() == esperado:
                break
        else:
            log.warning("%s: el panel no confirmo el nombre del dispositivo", nombre)
            return {k: None for k in CAMPOS_BESS} | {"campos_encontrados": False}
        out: dict = {"campos_encontrados": True}
        for clave, etiquetas in ETIQ_BESS.items():
            txt = next((datos[e.lower()] for e in etiquetas if e.lower() in datos), None)
            out[clave] = parse_temp(txt)
        return out


def _num_o_none(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _pct_static(txt_pot, txt_pnom):
    """% de la potencia nominal: 'P=100.0%' tal cual; 'P=115.000kW' se divide por la potencia nominal."""
    pct = parse_pct(txt_pot)
    if pct is not None:
        return pct
    kw, nom = parse_kw(txt_pot), parse_kw(txt_pnom)
    return round(kw / nom * 100, 1) if kw is not None and nom else None


def emparejar_inversores(nodos: list[dict], cfg: dict) -> tuple[dict[str, dict], list[str]]:
    """Asocia cada inversor configurado con un nodo del arbol.

    Devuelve ({id_inversor: nodo}, [ids_no_encontrados]). Un inversor es un nodo con icono 20822
    que cuelga de la planta cuyo nombre contiene alguna de las cadenas 'buscar' de su planta.
    """
    plantas_cfg = {p["codigo"]: p for p in cfg["plantas"]}
    planta_actual = None
    por_planta: dict[str, list[dict]] = {}
    for n in nodos:
        if n["icono"].startswith("20801"):          # nodo planta
            planta_actual = n
            continue
        if n["icono"].startswith("20822") and planta_actual:   # nodo inversor
            por_planta.setdefault(planta_actual["nombre"], []).append(n)

    encontrados, faltan = {}, []
    for inv in cfg["inversores"]:
        patrones = plantas_cfg[inv["planta"]]["buscar"]
        candidatos = [n for nombre_pl, lst in por_planta.items()
                      if any(p.lower() in nombre_pl.lower() for p in patrones) for n in lst]
        rx = re.compile(inv["dispositivo"])
        nodo = next((n for n in candidatos if rx.search(n["nombre"])), None)
        if inv.get("ne"):
            nodo = next((n for n in nodos if n["ne"] == inv["ne"]), nodo)
        if nodo:
            encontrados[inv["id"]] = nodo
        else:
            faltan.append(inv["id"])
    return encontrados, faltan


def emparejar_bess(nodos: list[dict], cfg: dict) -> tuple[dict[str, dict], list[str]]:
    """Asocia cada BESS configurado (cfg["bess"]) con su nodo del arbol (icono 20835 bajo la planta indicada)."""
    bcfg = cfg.get("bess") or {}
    patrones = next((p["buscar"] for p in cfg["plantas"] if p["codigo"] == bcfg.get("planta")), [])
    planta_ok, candidatos = False, []
    for n in nodos:
        if n["icono"].startswith("20801"):
            planta_ok = any(p.lower() in n["nombre"].lower() for p in patrones)
        elif planta_ok and n["icono"].startswith("20835"):
            candidatos.append(n)
    encontrados, faltan = {}, []
    for eq in bcfg.get("equipos", []):
        rx = re.compile(eq["dispositivo"])
        nodo = next((n for n in candidatos if rx.search(n["nombre"])), None)
        if nodo:
            encontrados[eq["id"]] = nodo
        else:
            faltan.append(eq["id"])
    return encontrados, faltan
