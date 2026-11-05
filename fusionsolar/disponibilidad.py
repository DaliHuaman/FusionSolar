"""Disponibilidad diaria de los inversores.

Horas operativas = «Duración de conexión a la red (h)» del Informe de inversores de FusionSolar (Informes > Informe de
inversores, granularidad «Por día»). Es lo ÚNICO que se extrae del portal; la disponibilidad se calcula con la misma fórmula
de la hoja de Excel «Disponibilidad»:

    disponibilidad = SI(horas >= 12; 1; horas / 12)        (12 h = horas de sol de referencia, `horas_referencia`)

Cada día se guarda una fila por inversor en `disponibilidad.csv` (rama «data»). `disponibilidad_estado.json` recuerda hasta qué día
ya se revisó el portal: la primera corrida recorre todo el historial desde `inicio_busqueda` (2025) y las siguientes solo
los últimos días, así que un día que falló se recupera solo en la corrida siguiente.
"""
from __future__ import annotations

import csv
import logging
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .common import COLUMNAS_DISP, FMT_TS, archivo_datos, guardar_json, leer_json

log = logging.getLogger("fusionsolar.disponibilidad")

HORAS_REF = 12.0
INICIO_BUSQUEDA = "2025-01-01"      # FusionSolar tiene datos desde 2025; antes responde «sin datos»
GUARDAR_CADA = 60                   # días consultados entre guardados parciales (por si la corrida se corta)


def disponibilidad(horas: float | None, ref: float = HORAS_REF) -> float | None:
    """La fórmula del Excel: =IF(horas>=12,1,horas/12). None si no hay horas."""
    if horas is None:
        return None
    return 1.0 if horas >= ref else horas / ref


def horas_efectivas(horas: float | None, rend: float | None) -> float | None:
    """Horas operativas del día a partir de lo que trae el informe.

    · Con «Duración de conexión a la red» → ese valor.
    · Sin ella pero con «Rendimiento (kWh)» (el inversor sí aparece ese día en el informe, p. ej. con 0 kWh) → 0 h: no se conectó
      a la red en todo el día, es decir, estuvo no disponible.
    · Sin ninguna de las dos → None: el portal no tiene datos (antes de la puesta en marcha o sin comunicación).
    """
    if horas is not None:
        return horas
    return 0.0 if rend is not None else None


# ---------------------------------------------------------------------------------------------- archivos
def ruta_csv() -> Path:
    return archivo_datos("disponibilidad.csv")


def ruta_estado() -> Path:
    return archivo_datos("disponibilidad_estado.json")


def leer_filas() -> list[dict]:
    p = ruta_csv()
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _clave(f: dict) -> tuple[str, str]:
    return f["Fecha"], f"{f['Planta']}-{f['Inversor']}"


def guardar_filas(nuevas: list[dict]) -> list[dict]:
    """Mezcla las filas nuevas con las existentes (una por día e inversor; la más reciente gana) y reescribe el CSV de forma atómica."""
    todas = {_clave(f): f for f in leer_filas()}
    todas.update({_clave(f): f for f in nuevas})
    orden = sorted(todas.values(), key=lambda f: (f["Fecha"], f["Planta"], f["Grupo"], f["Inversor"]))
    p = ruta_csv()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_DISP)
        w.writeheader()
        w.writerows(orden)
    os.replace(tmp, p)
    return orden


def por_inversor(filas: list[dict]) -> dict[str, dict[str, float]]:
    """{id_inversor: {fecha_iso: horas}} a partir de las filas del CSV (descarta las que no tienen horas)."""
    out: dict[str, dict[str, float]] = {}
    for f in filas:
        try:
            h = float(f["Horas"])
        except (TypeError, ValueError):
            continue
        out.setdefault(f"{f['Planta']}-{f['Inversor']}", {})[f["Fecha"]] = h
    return out


# ---------------------------------------------------------------------------------------------- emparejar con la configuración
def emparejar(filas_informe: list[dict], cfg: dict) -> dict[str, tuple[float | None, float | None]]:
    """Asocia cada fila del informe (planta + nombre del dispositivo) con un inversor de la configuración.

    Usa los mismos criterios que la lectura en vivo: la planta por `plantas[].buscar` y el dispositivo por la expresión
    `inversores[].dispositivo`. Devuelve {id_inversor: (horas, rendimiento_kWh)}.
    """
    buscar = {p["codigo"]: [b.lower() for b in p["buscar"]] for p in cfg["plantas"]}
    out: dict[str, tuple[float | None, float | None]] = {}
    for inv in cfg["inversores"]:
        rx = re.compile(inv["dispositivo"])
        for f in filas_informe:
            if any(b in (f["planta"] or "").lower() for b in buscar[inv["planta"]]) and rx.search(f["dispositivo"] or ""):
                previo = out.get(inv["id"])
                if previo is None or (previo[0] is None and f["horas"] is not None):
                    out[inv["id"]] = (f["horas"], f["rend"])
    return out


def _fila(dia: date, inv: dict, horas: float, ts: datetime) -> dict:
    return {"Fecha": dia.isoformat(), "Planta": inv["planta"], "Grupo": inv["grupo"], "Inversor": inv["nombre"],
            "Horas": round(horas, 3), "Leido": ts.strftime(FMT_TS)}


# ---------------------------------------------------------------------------------------------- corrida
def actualizar(sc, cfg: dict, ahora: datetime, desde: date | None = None, hasta: date | None = None,
               presupuesto_s: float | None = None) -> dict:
    """Consulta al portal los días pendientes (hasta AYER) y guarda las horas operativas de cada inversor.

    `sc` es un Scraper con el Informe de inversores ya abierto. Primera corrida: desde `inicio_busqueda` (se detecta sola la
    primera fecha con datos). Siguientes: desde el día siguiente a la última revisión y, siempre, los últimos `releer_dias`
    días (el portal puede completar o corregir datos tardíos). Un fallo en un día detiene la corrida SIN avanzar la revisión.
    """
    dcfg = cfg.get("disponibilidad") or {}
    releer = max(1, int(dcfg.get("releer_dias", 3)))
    presupuesto_s = presupuesto_s if presupuesto_s is not None else float(dcfg.get("presupuesto_segundos", 900))
    ayer = hasta or (ahora.date() - timedelta(days=1))
    inicio = date.fromisoformat(dcfg.get("inicio_busqueda", INICIO_BUSQUEDA))
    estado = leer_json(ruta_estado(), {})
    revisado = date.fromisoformat(estado["revisado_hasta"]) if estado.get("revisado_hasta") else None
    if desde:
        ini = desde
    elif revisado:
        ini = min(revisado + timedelta(days=1), ayer - timedelta(days=releer - 1))
    else:
        ini = inicio
    info: dict = {"desde": ini.isoformat(), "hasta": ayer.isoformat(), "dias": 0, "filas": 0}
    if ini > ayer:
        return info
    invs = {i["id"]: i for i in cfg["inversores"]}
    limite = time.monotonic() + presupuesto_s
    pendientes: list[dict] = []
    ultimo_ok = revisado
    d = ini
    try:
        while d <= ayer:
            if time.monotonic() > limite:
                log.warning("Presupuesto de tiempo agotado en %s; la próxima corrida continúa desde ahí", d)
                info["incompleto"] = True
                break
            lectura = emparejar(sc.horas_dia(d), cfg)
            for iid, (h, rend) in lectura.items():
                ef = horas_efectivas(h, rend)
                if ef is not None:
                    pendientes.append(_fila(d, invs[iid], ef, ahora))
            info["dias"] += 1
            if (ultimo_ok is None and ini <= inicio) or (ultimo_ok is not None and d > ultimo_ok and d <= ultimo_ok + timedelta(days=1)):
                ultimo_ok = d                       # la revisión solo avanza sin dejar días sin consultar en medio
            if d == ayer or info["dias"] % GUARDAR_CADA == 0:
                _guardar(pendientes, ultimo_ok)
                info["filas"] += len(pendientes)
                pendientes = []
            d += timedelta(days=1)
    except Exception as e:
        log.exception("fallo consultando la disponibilidad del %s: %s", d, e)
        info["error"] = f"{d}: {e}"
    if pendientes or ultimo_ok:
        _guardar(pendientes, ultimo_ok)
        info["filas"] += len(pendientes)
    info["revisado_hasta"] = ultimo_ok.isoformat() if ultimo_ok else None
    log.info("Disponibilidad: %s día(s) consultados desde %s, %s fila(s) con horas", info["dias"], ini, info["filas"])
    return info


def _guardar(filas: list[dict], revisado: date | None) -> None:
    todas = guardar_filas(filas)
    estado = leer_json(ruta_estado(), {})
    if revisado:
        estado["revisado_hasta"] = revisado.isoformat()
    if todas:
        estado["primer_dato"] = todas[0]["Fecha"]
        estado["ultimo_dato"] = todas[-1]["Fecha"]
    guardar_json(ruta_estado(), estado)
