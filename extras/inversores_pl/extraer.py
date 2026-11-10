"""Extracción puntual (fuera del flujo de 30 min): detalle de los 6 inversores de la planta PL.

Para cada inversor (Control > inversor > Detalles) guarda en la carpeta de salida:
  - <id>.json : tabla PV (Planta PV / PV1..PVn, tensión y corriente de entrada) y los datos en tiempo real como texto
  - <id>.png  : captura del bloque «Datos del dispositivo en tiempo real» + «Información básica» (sin la tabla PV)
  - debug/    : captura completa del panel y HTML de la tabla PV, por si hay que ajustar algo

No toca el Excel ni el dashboard de producción. Uso:
  python extras/inversores_pl/extraer.py --salida salida
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fusionsolar.common import credenciales, load_config  # noqa: E402
from fusionsolar.scraper import ETIQ_NOMBRE, ErrorPortal, Scraper, emparejar_inversores  # noqa: E402

log = logging.getLogger("inversores_pl")

ETIQ_TENSION = ["Tensión de entrada", "Input voltage"]
ETIQ_CORRIENTE = ["Corriente de entrada", "Input current"]
ETIQ_ESTADO = ["Estado del inversor", "Inverter status"]
ETIQ_INFO_BASICA = ["Información básica", "Basic information", "Basic Information"]

# Hojas visibles (sin hijos, con texto) del panel: base de todo lo demás. Se trabaja por geometría porque la tabla PV
# puede venir partida en varias <table> (cabecera fija, columna fija, scroll horizontal).
JS_COMUN = r"""
const norm = s => (s || "").replace(/\s+/g, " ").trim().toLowerCase();
const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
  return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
const hojas = raiz => [...raiz.querySelectorAll("*")].filter(e => e.children.length === 0 && norm(e.textContent) && vis(e));
const buscar = (etiquetas, raiz) => hojas(raiz || document.body).find(e => etiquetas.some(l => norm(e.textContent).startsWith(norm(l))));
"""

JS_TABLA_PV = r"""
([etqV, etqI]) => {""" + JS_COMUN + r"""
  const lv = buscar(etqV);
  if (!lv) return null;
  // contenedor = primer ancestro que tenga también la corriente y la cabecera PV1
  let cont = lv.parentElement;
  const ok = c => etqI.some(l => norm(c.textContent).includes(norm(l))) && hojas(c).some(h => /^pv\s*0*1$/i.test(norm(h.textContent)));
  while (cont && !ok(cont)) cont = cont.parentElement;
  if (!cont) return null;
  const celdas = hojas(cont).map(e => { const r = e.getBoundingClientRect();
    return {t: e.textContent.replace(/\s+/g, " ").trim(), x: r.left, y: r.top + r.height / 2}; });
  celdas.sort((a, b) => a.y - b.y || a.x - b.x);
  const filas = [];
  for (const c of celdas) {
    const f = filas.find(f => Math.abs(f.y - c.y) < 8);
    if (f) { if (!f.c.some(d => Math.abs(d.x - c.x) < 4 && d.t === c.t)) f.c.push(c); }
    else filas.push({y: c.y, c: [c]});
  }
  const r = cont.getBoundingClientRect();
  return {filas: filas.map(f => f.c.sort((a, b) => a.x - b.x).map(c => c.t)),
          rect: {x: r.left, y: r.top, w: r.width, h: r.height}, html: cont.outerHTML};
}
"""

# Recuadro rojo: desde debajo de la tabla PV («Estado del inversor») hasta el final de «Información básica».
JS_RECUADRO = r"""
([etqEstado, etqInfo, topeMin]) => {""" + JS_COMUN + r"""
  const le = buscar(etqEstado), li = buscar(etqInfo);
  if (!le || !li) return null;
  let cont = le.parentElement;
  while (cont && !cont.contains(li)) cont = cont.parentElement;
  const top = Math.max(topeMin || 0, le.getBoundingClientRect().top - 24);
  const dentro = hojas(cont).map(e => e.getBoundingClientRect()).filter(r => r.top >= top - 2);
  const izq = Math.min(...dentro.map(r => r.left)), der = Math.max(...dentro.map(r => r.right));
  const abajo = Math.max(...dentro.map(r => r.bottom));
  const datos = {};                                    // etiqueta -> valor (div[title] + hermano), igual que el scraper
  for (const d of cont.querySelectorAll("div[title]")) {
    const sib = d.nextElementSibling;
    if (!sib || !vis(d)) continue;
    const v = sib.querySelector("div") || sib;
    const k = (d.getAttribute("title") || "").trim();
    if (k && !(k in datos)) datos[k] = (v.getAttribute("title") || v.textContent || "").trim();
  }
  return {clip: {x: izq - 40, y: top, width: der - izq + 60, height: abajo - top + 16}, datos};
}
"""


class ScraperNitido(Scraper):
    """El mismo login y navegación del repo, pero con pantalla alta y a doble resolución (capturas nítidas en el Excel)."""

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless, args=["--no-sandbox"])
        ctx = self._browser.new_context(viewport={"width": 1900, "height": 2600}, device_scale_factor=2, locale="es-ES")
        ctx.set_default_timeout(20000)
        self.page = ctx.new_page()
        return self


def _esperar_inversor(sc: Scraper, nombre: str) -> None:
    """Espera a que el panel muestre ESTE inversor y a que la tabla PV y los datos tengan valores."""
    pg = sc.page
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
            arg=[ETIQ_NOMBRE, nombre], timeout=25000)
    except Exception:
        log.warning("%s: el panel no confirmó el nombre del dispositivo", nombre)
    for _ in range(15):                                   # hasta ~12 s a que la tabla PV traiga números
        t = pg.evaluate(JS_TABLA_PV, [ETIQ_TENSION, ETIQ_CORRIENTE])
        if t and len(t["filas"]) >= 3 and any(re.search(r"\d", x) for x in t["filas"][1][1:]):
            break
        pg.wait_for_timeout(800)
    pg.wait_for_timeout(2500)                             # que terminen de pintarse los valores restantes


def _num(txt: str):
    s = (txt or "").strip()
    if not re.fullmatch(r"-?[\d.,]+", s):
        return s or None
    s = s.replace(".", "").replace(",", ".") if "," in s else s
    try:
        return float(s)
    except ValueError:
        return txt


def leer(sc: Scraper, nodo: dict, salida: Path, dbg: Path) -> dict:
    pg, nombre = sc.page, nodo["nombre"]
    sc._clic_nodo(nodo["ne"])
    sc.volver_a_detalles()
    _esperar_inversor(sc, nombre)
    pg.screenshot(path=str(dbg / f"{nombre}_pantalla.png"), full_page=True)

    tabla = pg.evaluate(JS_TABLA_PV, [ETIQ_TENSION, ETIQ_CORRIENTE])
    pv = None
    if tabla:
        (dbg / f"{nombre}_tabla.html").write_text(tabla["html"], encoding="utf-8")
        filas = [f for f in tabla["filas"] if f]
        cab = filas[0]
        pv = {"cabecera": cab, "filas": [[f[0]] + [_num(x) for x in f[1:]] for f in filas[1:]]}
    else:
        log.warning("%s: no se encontró la tabla PV", nombre)

    tope = (tabla["rect"]["y"] + tabla["rect"]["h"] + 2) if tabla else 0
    rec = pg.evaluate(JS_RECUADRO, [ETIQ_ESTADO, ETIQ_INFO_BASICA, tope])
    png = None
    if rec:
        c = rec["clip"]
        png = salida / f"{_archivo(nombre)}.png"
        pg.screenshot(path=str(png), clip={k: max(0, v) for k, v in c.items()}, full_page=True)
    else:
        log.warning("%s: no se encontró el bloque de datos en tiempo real / información básica", nombre)
    return {"nombre": nombre, "pv": pv, "captura": png.name if png else None, "datos": (rec or {}).get("datos", {})}


def _archivo(nombre: str) -> str:
    return re.sub(r"[^\w.-]+", "_", nombre).strip("_")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default="salida")
    ap.add_argument("--visible", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config()
    salida = Path(a.salida)
    dbg = salida / "debug"
    dbg.mkdir(parents=True, exist_ok=True)
    cred = credenciales()
    ts = datetime.now(ZoneInfo(cfg["zona_horaria"])).strftime("%Y-%m-%d %H:%M")
    planta = next(p["nombre"] for p in cfg["plantas"] if p["codigo"] == "PL")
    resultado = {"extraido": ts, "planta": planta, "inversores": []}

    # pantalla alta para que el panel quepa completo sin scroll interno
    with ScraperNitido(cred.usuario, cred.clave, headless=not a.visible, depuracion=dbg) as sc:
        for intento in (1, 2):
            try:
                sc.login()
                sc.abrir_control()
                break
            except ErrorPortal:
                if intento == 2:
                    raise
        nodos = sc.expandir_arbol()
        encontrados, faltan = emparejar_inversores(nodos, cfg)
        for inv in [i for i in cfg["inversores"] if i["planta"] == "PL"]:
            nodo = encontrados.get(inv["id"])
            if not nodo:
                resultado["inversores"].append({"id": inv["id"], "nombre": inv["nombre"], "error": "no encontrado en el árbol"})
                continue
            try:
                r = leer(sc, nodo, salida, dbg)
                log.info("%s: %s columnas PV, captura %s", nodo["nombre"],
                         len(r["pv"]["cabecera"]) - 1 if r["pv"] else 0, "ok" if r["captura"] else "NO")
            except Exception as e:
                log.exception("%s: %s", nodo["nombre"], e)
                sc.captura(f"error_{inv['id']}")
                r = {"nombre": nodo["nombre"], "error": str(e)}
            resultado["inversores"].append({"id": inv["id"]} | r)

    (salida / "inversores_pl.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if any(i.get("pv") for i in resultado["inversores"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
