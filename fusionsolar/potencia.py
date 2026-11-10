"""Potencia nominal (kW) de cada inversor: dato fijo que se muestra junto al nombre en la sección/hoja «Disponibilidad».

Fuente: Control > inversor > Detalles, «Potencia nominal del inversor». La corrida de cada 30 min ya pasa por ese panel, así que
anota el valor en `potencia_nominal.json` (rama «data»). `potencia_nominal_kw` de config/inversores.json es el valor de respaldo
(se usa mientras el portal no haya dado uno).
"""
from __future__ import annotations

import logging
from datetime import datetime

from .common import FMT_TS, archivo_datos, guardar_json, leer_json

log = logging.getLogger("fusionsolar.potencia")
ARCHIVO = "potencia_nominal.json"


def _valida(v) -> bool:
    return isinstance(v, (int, float)) and 1 <= v <= 10_000


def aplicar(cfg: dict) -> None:
    """Deja en cada inversor de cfg la clave `pnom_kw`: la leída del portal o, si no hay, la de la configuración (o None)."""
    leidas = leer_json(archivo_datos(ARCHIVO), {}).get("kw") or {}
    for inv in cfg["inversores"]:
        portal = leidas.get(inv["id"])
        inv["pnom_kw"] = float(portal) if _valida(portal) else inv.get("potencia_nominal_kw")


def guardar(lecturas: dict[str, float | None], cfg: dict, ts: datetime) -> None:
    """Guarda las potencias leídas en esta corrida (solo las válidas; un fallo de lectura no borra el valor anterior). Nunca lanza."""
    try:
        nuevas = {k: float(v) for k, v in lecturas.items() if _valida(v)}
        if not nuevas:
            return
        p = archivo_datos(ARCHIVO)
        datos = leer_json(p, {})
        kw = datos.get("kw") or {}
        cambio = {k: v for k, v in nuevas.items() if kw.get(k) != v}
        if not cambio:
            return
        for inv in cfg["inversores"]:
            fija = inv.get("potencia_nominal_kw")
            if inv["id"] in cambio and fija is not None and fija != cambio[inv["id"]]:
                log.warning("%s: potencia nominal del portal %s kW ≠ config %s kW (se usa la del portal)", inv["id"], cambio[inv["id"]], fija)
        kw.update(cambio)
        guardar_json(p, {"kw": kw, "actualizado": ts.strftime(FMT_TS)})
        log.info("Potencia nominal actualizada: %s", cambio)
    except Exception as e:
        log.exception("no se pudo guardar la potencia nominal: %s", e)
