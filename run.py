"""Corrida completa: extraer de FusionSolar -> guardar (CSV + Excel) -> generar data.json del dashboard.

Uso:
    python run.py                 # corrida normal (la que ejecuta el Programador de tareas)
    python run.py --visible       # abre el navegador para ver / depurar el flujo
    python run.py --descubrir     # solo lista el arbol de plantas/inversores del portal
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path

from fusionsolar import sitedata, store
from fusionsolar.common import (STATE_DIR, ROOT, classify, credenciales, load_config, load_env, now_local)
from fusionsolar.scraper import ErrorPortal, Scraper, emparejar_inversores

log = logging.getLogger("fusionsolar")
LOCK = STATE_DIR / "run.lock"
ESTADO = STATE_DIR / "ultima_extraccion.json"


def configurar_logs() -> None:
    (STATE_DIR / "logs").mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(STATE_DIR / "logs" / "collector.log", maxBytes=2_000_000,
                                             backupCount=5, encoding="utf-8")
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    h.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[h, sh])


def tomar_lock() -> bool:
    """Evita corridas solapadas (si una corrida anterior sigue viva, esta se omite)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 25 * 60:
        return False
    LOCK.write_text(str(os.getpid()))
    return True


def extraer(cfg: dict, visible: bool, solo_descubrir: bool = False):
    cred = credenciales()
    ts = now_local(cfg)
    filas: list[dict] = []
    detalle: dict[str, str] = {}
    with Scraper(cred.usuario, cred.clave, headless=not visible) as sc:
        for intento in (1, 2):
            try:
                sc.login()
                sc.abrir_control()
                break
            except ErrorPortal:
                if intento == 2:
                    raise
                log.warning("reintentando login...")
        nodos = sc.expandir_arbol()
        if solo_descubrir:
            for n in nodos:
                print("  " * n["nivel"], n["ne"], n["nombre"], n["icono"])
            return [], [], ts, {}
        encontrados, faltan = emparejar_inversores(nodos, cfg)
        if faltan:
            log.warning("Inversores no encontrados en el portal: %s", ", ".join(faltan))
        margen = cfg["margen_advertencia"]
        for inv in cfg["inversores"]:
            nodo = encontrados.get(inv["id"])
            if not nodo:
                continue
            try:
                lec = sc.leer_inversor(nodo["ne"], nodo["nombre"])
                if not lec["campos_encontrados"]:
                    sc.captura(f"sin_campos_{inv['id']}")
                estado = classify(lec["resistencia"], inv["umbral"], margen)
                filas.append(store.construir_fila(ts, inv, lec, estado))
                log.info("%-10s R=%s MΩ (umbral %s) %s | %s", inv["id"], lec["resistencia"], inv["umbral"],
                         estado, lec["potencia_txt"])
            except Exception as e:                       # un inversor con error no tumba a los demas
                log.exception("fallo leyendo %s: %s", inv["id"], e)
                sc.captura(f"error_{inv['id']}")
                detalle[inv["id"]] = str(e)
    return filas, faltan, ts, detalle


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--visible", action="store_true")
    ap.add_argument("--descubrir", action="store_true")
    a = ap.parse_args()

    load_env()
    configurar_logs()
    cfg = load_config()

    if not tomar_lock():
        log.warning("Hay otra corrida en curso; se omite esta.")
        return 0
    try:
        if a.descubrir:
            extraer(cfg, a.visible, solo_descubrir=True)
            return 0

        faltan: list[str] = []
        info = {"ok": True}
        ts = now_local(cfg)
        if True:
            try:
                filas, faltan, ts, errores = extraer(cfg, a.visible)
            except Exception as e:
                log.exception("corrida fallida: %s", e)
                filas, errores = [], {"general": str(e)}
                info = {"ok": False, "error": str(e)}
            if filas:
                info.update(store.guardar(filas, cfg["inversores"]))
            info["leidos"] = len(filas)
            ESTADO.write_text(json.dumps({"faltan": faltan, "ts": ts.isoformat()}), encoding="utf-8")
            if errores:
                info["errores"] = errores

        data = sitedata.construir(cfg, store.leer_historico(), faltan, ts, info)
        sitedata.escribir(data)
        return 0 if info.get("ok", True) else 1
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
