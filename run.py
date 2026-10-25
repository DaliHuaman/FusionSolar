"""Corrida completa: extraer de FusionSolar -> guardar (CSV + Excel) -> generar data.json del dashboard.

Uso:
    python run.py                 # corrida normal (la que ejecuta el Programador de tareas)
    python run.py --visible       # abre el navegador para ver / depurar el flujo
    python run.py --descubrir     # solo lista el arbol de plantas/inversores del portal
    python run.py --probar-correo # envia un correo de prueba (requiere SMTP_USER / SMTP_PASSWORD) y termina
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

from fusionsolar import alertas, sitedata, store, umbrales
from fusionsolar.common import (CAMPOS_BESS, STATE_DIR, ROOT, classify, credenciales, load_config, load_env, now_local)
from fusionsolar.scraper import ErrorPortal, Scraper, emparejar_bess, emparejar_inversores

log = logging.getLogger("fusionsolar")
LOCK = STATE_DIR / "run.lock"
ESTADO = STATE_DIR / "ultima_extraccion.json"
PRESUPUESTO_BESS_S = 240      # tope de tiempo para toda la fase BESS; si se agota se omite lo que falte (los inversores ya se leyeron)


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


def extraer_bess(sc: Scraper, nodos: list[dict], cfg: dict, ts) -> tuple[list[dict], dict[str, str]]:
    """Lee las temperaturas de cada BESS reutilizando la sesion ya abierta. Nunca lanza: un fallo aqui no afecta a los inversores."""
    filas: list[dict] = []
    errores: dict[str, str] = {}
    try:
        encontrados, faltan = emparejar_bess(nodos, cfg)
        if faltan:
            log.warning("BESS no encontrados en el portal: %s", ", ".join(faltan))
            errores.update({b: "no visible en el portal" for b in faltan})
        limite = time.monotonic() + PRESUPUESTO_BESS_S
        for eq in cfg["bess"]["equipos"]:
            nodo = encontrados.get(eq["id"])
            if not nodo:
                continue
            if time.monotonic() > limite:
                log.warning("Presupuesto de tiempo BESS agotado; se omite %s", eq["id"])
                errores[eq["id"]] = "omitido por tiempo"
                continue
            try:
                lec = sc.leer_bess(nodo["ne"], nodo["nombre"])
                if all(lec[k] is None for k in CAMPOS_BESS):
                    sc.captura(f"sin_campos_{eq['id']}")
                    errores[eq["id"]] = "sin temperaturas"
                    continue                                 # no se guarda una fila vacia
                filas.append(store.construir_fila_bess(ts, eq["id"], lec))
                log.info("%-8s max=%s | agua bat %s/%s | agua pot %s/%s | ext %s", eq["id"], lec["temp_max"],
                         lec["agua_bat_sum"], lec["agua_bat_ret"], lec["agua_pot_sum"], lec["agua_pot_ret"], lec["temp_ext"])
            except Exception as e:
                log.exception("fallo leyendo %s: %s", eq["id"], e)
                sc.captura(f"error_{eq['id']}")
                errores[eq["id"]] = str(e)
    except Exception as e:
        log.exception("fase BESS fallida: %s", e)
        errores["general"] = str(e)
    return filas, errores


def extraer(cfg: dict, visible: bool, solo_descubrir: bool = False, extra: dict | None = None):
    cred = credenciales()
    ts = now_local(cfg)
    filas: list[dict] = []
    filas_bess: list[dict] = []
    errores_bess: dict[str, str] = {}
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
            return [], [], ts, {}, [], {}
        encontrados, faltan = emparejar_inversores(nodos, cfg)
        if faltan:
            log.warning("Inversores no encontrados en el portal: %s", ", ".join(faltan))
        margen = cfg["margen_advertencia"]
        res_umbrales = umbrales.actualizar(sc, encontrados, cfg, ts)   # cada 8 h; no lanza y deja el panel en "Detalles"
        if res_umbrales and extra is not None:
            extra["umbrales"] = res_umbrales
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
        if (cfg.get("bess") or {}).get("activo"):          # despues de los inversores: nada de aqui los puede romper
            filas_bess, errores_bess = extraer_bess(sc, nodos, cfg, ts)
    return filas, faltan, ts, detalle, filas_bess, errores_bess


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--visible", action="store_true")
    ap.add_argument("--descubrir", action="store_true")
    ap.add_argument("--probar-correo", action="store_true")
    a = ap.parse_args()

    load_env()
    configurar_logs()
    cfg = load_config()
    if a.probar_correo:
        alertas.enviar_prueba(cfg, now_local(cfg))
        return 0
    umbrales.aplicar(cfg)            # umbrales leidos del portal en corridas anteriores (si los hay) sobre los del JSON

    if not tomar_lock():
        log.warning("Hay otra corrida en curso; se omite esta.")
        return 0
    try:
        if a.descubrir:
            extraer(cfg, a.visible, solo_descubrir=True)
            return 0

        faltan: list[str] = []
        info = {"ok": True}
        extra: dict = {}
        ts = now_local(cfg)
        if True:
            try:
                filas, faltan, ts, errores, filas_bess, errores_bess = extraer(cfg, a.visible, extra=extra)
            except Exception as e:
                log.exception("corrida fallida: %s", e)
                filas, errores, filas_bess, errores_bess = [], {"general": str(e)}, [], {}
                info = {"ok": False, "error": str(e)}
            historial_bess: list[dict] = []
            if (cfg.get("bess") or {}).get("activo"):
                try:                                       # historial BESS acotado; un fallo aqui no tumba lo demas
                    historial_bess = (store.guardar_bess(filas_bess, ts, cfg["bess"].get("dias_historial", 90))
                                      if filas_bess else store.leer_bess())
                except Exception as e:
                    log.exception("no se pudo guardar el historial BESS: %s", e)
                    errores_bess["guardado"] = str(e)
            if filas or filas_bess:
                info.update(store.guardar(filas, cfg["inversores"], historial_bess or None))
            info["leidos"] = len(filas)
            info.update(extra)
            if filas:                                      # aviso por correo; no lanza y nunca altera la corrida
                res_alertas = alertas.procesar(cfg, filas, ts)
                if res_alertas.get("pendientes"):
                    info["alertas"] = res_alertas
            info["bess_leidos"] = len(filas_bess)
            if errores_bess:
                info["errores_bess"] = errores_bess
            ESTADO.write_text(json.dumps({"faltan": faltan, "ts": ts.isoformat()}), encoding="utf-8")
            if errores:
                info["errores"] = errores

        data = sitedata.construir(cfg, store.leer_historico(), faltan, ts, info, historial_bess)
        sitedata.escribir(data)
        return 0 if info.get("ok", True) else 1
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
