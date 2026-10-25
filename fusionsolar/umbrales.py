"""Umbral de proteccion de resistencia de aislamiento de cada inversor, leido del portal cada pocas horas.

El portal (Control > inversor > Configuración > Parámetros de protección) es la fuente de verdad. Los valores de
config/inversores.json son el valor inicial / de respaldo; los leidos se guardan en `umbrales.json` (rama "data" en la
nube) y las demas corridas lo reutilizan sin volver a abrir la pestaña Configuración.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from .common import FMT_TS, archivo_datos, guardar_json, leer_json, umbral_valido

log = logging.getLogger("fusionsolar.umbrales")
ARCHIVO = "umbrales.json"
MAX_CAMBIOS = 30          # historial de cambios que se conserva en el archivo
POR_DEFECTO = {"horas": [0, 8, 16], "reintento_minutos": 60, "presupuesto_segundos": 150}


def _cfg(cfg: dict) -> dict:
    return POR_DEFECTO | (cfg.get("umbrales") or {})


def cargar() -> dict:
    return leer_json(archivo_datos(ARCHIVO), {})


def _ts(txt: str | None) -> datetime | None:
    try:
        return datetime.strptime(txt, FMT_TS) if txt else None
    except ValueError:
        return None


def aplicar(cfg: dict, cache: dict | None = None) -> int:
    """Pone en cfg["inversores"] los umbrales guardados (los validos). Devuelve cuantos cambiaron respecto al JSON."""
    guardados = (cache if cache is not None else cargar()).get("umbrales") or {}
    cambiados = 0
    for inv in cfg["inversores"]:
        valor = (guardados.get(inv["id"]) or {}).get("valor")
        if isinstance(valor, (int, float)) and umbral_valido(valor) and valor != inv["umbral"]:
            inv["umbral"] = float(valor)
            cambiados += 1
    return cambiados


def inicio_ultima_ventana(cfg: dict, ahora: datetime) -> datetime:
    """Inicio de la ventana de lectura vigente: la hora programada mas reciente (00:00, 08:00, 16:00 por defecto)."""
    horas = sorted({int(h) for h in _cfg(cfg)["horas"]}) or [0]
    for h in reversed(horas):
        if h <= ahora.hour:
            return ahora.replace(hour=h, minute=0, second=0, microsecond=0)
    return (ahora - timedelta(days=1)).replace(hour=horas[-1], minute=0, second=0, microsecond=0)


def pendientes(cfg: dict, ahora: datetime, cache: dict) -> list[str]:
    """Ids de inversores cuyo umbral no se ha leido en la ventana vigente (vacio = nada que hacer en esta corrida)."""
    ventana = inicio_ultima_ventana(cfg, ahora)
    guardados = cache.get("umbrales") or {}
    falta = [i["id"] for i in cfg["inversores"]
             if (_ts((guardados.get(i["id"]) or {}).get("leido")) or datetime.min) < ventana]
    if not falta:
        return []
    intento = _ts(cache.get("intento"))
    if intento and intento >= ventana and ahora - intento < timedelta(minutes=_cfg(cfg)["reintento_minutos"]):
        return []                                    # ya se intento hace poco y fallo: no alargar cada corrida
    return falta


def actualizar(sc, encontrados: dict[str, dict], cfg: dict, ahora: datetime) -> dict:
    """Lee en el portal los umbrales pendientes, los guarda y los aplica a cfg. Nunca lanza.

    Devuelve un resumen para el log / data.json: {"leidos": n, "cambios": [...], "fallos": [...]} o {} si no tocaba.
    """
    cache: dict = {}
    try:
        cache = cargar()
        aplicar(cfg, cache)                           # los guardados valen aunque esta lectura falle
        falta = pendientes(cfg, ahora, cache)
        if not falta:
            return {}
        log.info("Leyendo umbrales de aislamiento (%s inversores)", len(falta))
        guardados = cache.setdefault("umbrales", {})
        cambios, fallos = [], []
        limite = time.monotonic() + _cfg(cfg)["presupuesto_segundos"]
        try:
            for inv in cfg["inversores"]:
                if inv["id"] not in falta:
                    continue
                nodo = encontrados.get(inv["id"])
                if not nodo:
                    fallos.append(inv["id"])
                    continue
                if time.monotonic() > limite:
                    log.warning("Presupuesto de tiempo de umbrales agotado; se omite %s", inv["id"])
                    fallos.append(inv["id"])
                    continue
                try:
                    valor = sc.leer_umbral(nodo["ne"], nodo["nombre"])
                except Exception as e:
                    log.warning("no se pudo leer el umbral de %s: %s", inv["id"], e)
                    valor = None
                if valor is None:
                    fallos.append(inv["id"])
                    continue
                antes = (guardados.get(inv["id"]) or {}).get("valor", inv["umbral"])
                if valor != antes:
                    cambios.append({"id": inv["id"], "antes": antes, "despues": valor, "fecha": ahora.strftime(FMT_TS)})
                    log.info("Umbral %s: %s -> %s MΩ", inv["id"], antes, valor)
                guardados[inv["id"]] = {"valor": valor, "leido": ahora.strftime(FMT_TS)}
                inv["umbral"] = valor
        finally:
            sc.volver_a_detalles()                    # leer_inversor busca sus campos en la pestaña "Detalles"
        cache["intento"] = ahora.strftime(FMT_TS)
        cache["cambios"] = (cache.get("cambios") or []) + cambios
        cache["cambios"] = cache["cambios"][-MAX_CAMBIOS:]
        guardar_json(archivo_datos(ARCHIVO), cache)
        if fallos:
            log.warning("Umbrales sin actualizar (se conserva el valor anterior): %s", ", ".join(fallos))
        return {"leidos": len(falta) - len(fallos), "cambios": cambios, "fallos": fallos}
    except Exception as e:                            # esto jamas debe impedir la lectura normal de resistencias
        log.exception("fallo en la lectura de umbrales: %s", e)
        return {"error": str(e)}
