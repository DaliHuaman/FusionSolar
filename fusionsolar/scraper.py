"""Extraccion desde el portal FusionSolar (Playwright).

Flujo (igual al procedimiento manual del Word "Aislamiento"):
  login -> pestaña Control -> expandir arbol de plantas -> por cada inversor:
  leer "Resistencia de aislamiento" y "Estado de ajuste de potencia activa".
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from .common import STATE_DIR, parse_kw, parse_mohm, parse_pct

LOGIN_URL = "https://la5.fusionsolar.huawei.com/pvmswebsite/login/build/index.html"
log = logging.getLogger("fusionsolar.scraper")

# Etiquetas del portal (español / ingles) -> el valor esta en el <div title=...> hermano.
ETIQ_RESISTENCIA = ["Resistencia de aislamiento", "Insulation resistance"]
ETIQ_POT_ACTIVA = ["Estado de ajuste de potencia activa", "Active power adjustment status"]
ETIQ_ESTADO_INV = ["Estado del inversor", "Inverter status"]
ETIQ_PNOM = ["Potencia nominal del inversor", "Rated power"]
ETIQ_NOMBRE = ["Nombre del dispositivo", "Device name"]

TEXTO_LOGIN = ["Iniciar sesión", "Log In", "Login"]

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


class ErrorPortal(RuntimeError):
    pass


class Scraper:
    def __init__(self, usuario: str, clave: str, headless: bool = True, depuracion: Path | None = None):
        self.usuario, self.clave = usuario, clave
        self.headless = headless
        self.dbg = depuracion or (STATE_DIR / "debug")
        self.dbg.mkdir(parents=True, exist_ok=True)
        self._pw = self._browser = self.page = None

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

    def _cerrar_avisos(self) -> None:
        for txt in ("Aceptar", "Accept", "OK"):
            try:
                self.page.get_by_text(txt, exact=True).first.click(timeout=800)
            except Exception:
                pass

    def abrir_control(self) -> None:
        pg = self.page
        for intento in range(3):
            try:
                pg.get_by_text("Control", exact=True).first.hover()
                pg.wait_for_timeout(800)
                visibles = pg.locator('text="Control" >> visible=true')
                visibles.last.click()
                pg.wait_for_selector('[id^="monitor-layout-node-name-"]', timeout=30000)
                pg.wait_for_timeout(1500)
                return
            except (PWTimeout, PWError):
                log.warning("abrir_control intento %s fallo", intento + 1)
                pg.wait_for_timeout(2000)
        self.captura("control_fallo")
        raise ErrorPortal("No se pudo abrir la pestaña Control")

    def expandir_arbol(self) -> list[dict]:
        pg = self.page
        for _ in range(8):
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

    # ------------------------------------------------------------------ lectura de un inversor
    def leer_inversor(self, ne: str, nombre: str) -> dict:
        pg = self.page
        pg.click(f'[id="monitor-layout-node-name-{ne}"]')
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
        txt_res = (res or {}).get("texto", "")
        txt_pot = ((pot or {}).get("titulo") or (pot or {}).get("texto") or "").strip()
        return {
            "resistencia_txt": txt_res,
            "resistencia": parse_mohm(txt_res),
            "potencia_txt": txt_pot,
            "potencia_pct": _pct_static(txt_pot, (pnom or {}).get("texto")),
            "estado_inversor": ((est or {}).get("texto") or "").strip(),
            "campos_encontrados": res is not None,
        }


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
