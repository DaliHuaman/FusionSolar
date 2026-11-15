"""BESS carga-descarga: ¿en qué parte del periodo se concentra el aumento de temperatura de cada BESS?

Se calcula con las temperaturas que la corrida de 30 min ya guarda en `historico_bess.csv` («Temperatura más alta del grupo»,
`Temp_Max_Grupo_C`), así que no hace falta otra consulta al portal ni puede fallar por la red.

Por día y por BESS se toman las temperaturas de cada media hora (la lectura más cercana a cada hh:00 / hh:30, con tolerancia):

    HP  (hora punta, descarga):      18:00, 18:30, 19:00, 19:30, 20:00, 20:30        -> 5 incrementos de 30 min
    HFP (fuera de punta, carga):     00:00 … 17:30 (no incluye lo posterior a la descarga en HP)

Incrementos = diferencia entre temperaturas consecutivas. Se compara el promedio de los 2 primeros incrementos (en HP, 18:00–19:00)
con el de los 2 últimos (en HP, 19:30–20:30):

    «Al inicio»       si el promedio inicial es mayor
    «Al final»        si el promedio final es mayor
    «Sin predominio»  si la diferencia es menor a `umbral_c` (0,1 °C por 30 min)
    «Sin datos»       si falta algún grupo de incrementos (no hay lecturas suficientes ese día)

Un dato faltante nunca rompe nada: el incremento que lo necesita se omite y el promedio usa los que sí existen.
Cada día se guarda UNA vez por BESS en `bess_carga_descarga.csv` (rama «data»; no se acota a 90 días como el historial). Un día se
cierra y se agrega a las `hora_extraccion` (12:00 de Lima) del día siguiente; los últimos `recalcular_dias` se recalculan por si
llegaron lecturas tardías. Es idempotente: ejecutarlo varias veces no duplica filas.
"""
from __future__ import annotations

import csv
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from .common import COLUMNAS_BCD, archivo_datos

log = logging.getLogger("fusionsolar.bess_cd")

COL_TEMP = "Temp_Max_Grupo_C"
SLOTS_HP = [18 * 60 + 30 * k for k in range(6)]          # 18:00 … 20:30 (minutos desde 00:00)
SLOTS_HFP = [30 * k for k in range(36)]                  # 00:00 … 17:30
N_INC = 2                                                # incrementos que forman el grupo «inicial» y el «final»
UMBRAL_C = 0.1                                           # °C por 30 min
TOLERANCIA_MIN = 10                                      # una lectura vale para un horario si está a ≤ 10 min
HORA_EXTRACCION = 12
RECALCULAR_DIAS = 7

AL_INICIO, AL_FINAL, SIN_PRED, SIN_DATOS = "Al inicio", "Al final", "Sin predominio", "Sin datos"


def ruta_csv() -> Path:
    return archivo_datos("bess_carga_descarga.csv")


# ---------------------------------------------------------------------------------------------- cálculo (puro)
def _num(v) -> float | None:
    try:
        return None if v in ("", None) else float(v)
    except (TypeError, ValueError):
        return None


def temperaturas(lecturas: list[tuple[int, float]], slots: list[int], tol: int = TOLERANCIA_MIN) -> list[float | None]:
    """Temperatura de cada horario: la lectura (minuto del día, °C) más cercana, a ≤ `tol` min; None si no hay ninguna."""
    out: list[float | None] = []
    for s in slots:
        mejor = min(lecturas, key=lambda x: abs(x[0] - s), default=None)
        out.append(mejor[1] if mejor is not None and abs(mejor[0] - s) <= tol else None)
    return out


def incrementos(temps: list[float | None]) -> list[float | None]:
    """Diferencias consecutivas (cada 30 min). None si falta alguna de las dos temperaturas."""
    return [None if a is None or b is None else round(b - a, 4) for a, b in zip(temps, temps[1:])]


def clasificar(incs: list[float | None], n: int = N_INC, umbral: float = UMBRAL_C) -> str:
    """Compara el promedio de los `n` primeros incrementos con el de los `n` últimos (solo cuentan los que existen)."""
    ini = [x for x in incs[:n] if x is not None]
    fin = [x for x in incs[-n:] if x is not None]
    if not ini or not fin:
        return SIN_DATOS
    dif = round(sum(fin) / len(fin) - sum(ini) / len(ini), 6)
    if abs(dif) < umbral:
        return SIN_PRED
    return AL_FINAL if dif > 0 else AL_INICIO


def resumen_grupo(lecturas: list[tuple[int, float]], slots: list[int], tol: int, umbral: float) -> dict:
    t = temperaturas(lecturas, slots, tol)
    con = [x for x in t if x is not None]
    return {"min": min(con) if con else None, "max": max(con) if con else None,
            "conc": clasificar(incrementos(t), N_INC, umbral) if con else SIN_DATOS, "n": len(con)}


def resumen_dia(filas_bess: list[dict], dia: str, bess: str, tol: int = TOLERANCIA_MIN, umbral: float = UMBRAL_C) -> dict | None:
    """Fila del día para un BESS (None si ese día no tiene ninguna lectura de temperatura)."""
    lect: list[tuple[int, float]] = []
    for f in filas_bess:
        if f.get("BESS") != bess or f.get("Fecha") != dia:
            continue
        v, h = _num(f.get(COL_TEMP)), f.get("Hora") or ""
        if v is None or len(h) < 5 or not (h[:2].isdigit() and h[3:5].isdigit()):
            continue
        lect.append((int(h[:2]) * 60 + int(h[3:5]), v))
    if not lect:
        return None
    hfp, hp = resumen_grupo(lect, SLOTS_HFP, tol, umbral), resumen_grupo(lect, SLOTS_HP, tol, umbral)
    fmt = lambda v: "" if v is None else v
    return {"Fecha": dia, "BESS": bess,
            "HFP_Min": fmt(hfp["min"]), "HFP_Max": fmt(hfp["max"]), "HFP_Concentracion": hfp["conc"],
            "HP_Min": fmt(hp["min"]), "HP_Max": fmt(hp["max"]), "HP_Concentracion": hp["conc"],
            "Lecturas_HFP": hfp["n"], "Lecturas_HP": hp["n"]}


# ---------------------------------------------------------------------------------------------- archivo
def leer_filas() -> list[dict]:
    p = ruta_csv()
    if not p.exists():
        return []
    try:
        with open(p, newline="", encoding="utf-8-sig") as f:
            return [r for r in csv.DictReader(f) if r.get("Fecha") and r.get("BESS")]
    except (OSError, ValueError, csv.Error) as e:                    # un archivo ilegible no debe tumbar la corrida: se recalcula desde el historial
        log.warning("no se pudo leer %s: %s", p, e)
        return []


def _guardar(filas: list[dict]) -> None:
    p = ruta_csv()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_BCD, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(filas, key=lambda x: (x["Fecha"], x["BESS"])))
    os.replace(tmp, p)


def dias_cerrados(ahora: datetime, fechas: set[str], hora: int = HORA_EXTRACCION) -> list[str]:
    """Días con datos ya cerrados: el día D se cierra a las `hora` del día D+1 (hora local de `ahora`)."""
    return sorted(d for d in fechas if datetime.fromisoformat(d) + timedelta(days=1, hours=hora) <= ahora.replace(tzinfo=None))


def actualizar(filas_bess: list[dict], ahora: datetime, cfg: dict) -> list[dict]:
    """Agrega al archivo los días cerrados que falten (y recalcula los últimos días). Devuelve todas las filas guardadas.

    Nunca lanza: ante cualquier fallo devuelve lo que ya estaba guardado.
    """
    bcfg = (cfg.get("bess") or {}).get("carga_descarga") or {}
    previas = leer_filas()
    if not bcfg.get("activo", True):
        return previas
    try:
        tol = int(bcfg.get("tolerancia_min", TOLERANCIA_MIN))
        umbral = float(bcfg.get("umbral_c", UMBRAL_C))
        hora = int(bcfg.get("hora_extraccion", HORA_EXTRACCION))
        recalcular = int(bcfg.get("recalcular_dias", RECALCULAR_DIAS))
        ids = [e["id"] for e in (cfg.get("bess") or {}).get("equipos", [])]
        # El primer día del historial (o el recortado por la ventana de N días) puede empezar a media tarde: no se calcula con datos
        # parciales. Y nunca se pisa una fila ya guardada con otra que tenga menos lecturas.
        primera = min((f["Fecha_Hora"] for f in filas_bess if f.get("Fecha_Hora")), default=None)
        dia_parcial = primera[:10] if primera and int(primera[11:13]) * 60 + int(primera[14:16]) > 30 + tol else None
        hoy = ahora.date()
        guardadas = {(f["Fecha"], f["BESS"]): f for f in previas}
        nuevas: dict[tuple[str, str], dict] = {}
        for dia in dias_cerrados(ahora, {f["Fecha"] for f in filas_bess if f.get("Fecha")}, hora):
            if dia == dia_parcial:
                continue
            reciente = (hoy - date.fromisoformat(dia)).days <= recalcular
            for bid in ids:
                if (dia, bid) in guardadas and not reciente:
                    continue
                fila = resumen_dia(filas_bess, dia, bid, tol, umbral)
                if fila is None:
                    continue
                previa = guardadas.get((dia, bid))
                if previa and _n(previa) > _n(fila):                # lo guardado tiene más lecturas (el historial ya se recortó)
                    continue
                nuevas[(dia, bid)] = fila
        if not nuevas:
            return previas
        todas = {**guardadas, **nuevas}
        _guardar(list(todas.values()))
        log.info("BESS carga-descarga: %s fila(s) nuevas o actualizadas", len(nuevas))
        return sorted(todas.values(), key=lambda x: (x["Fecha"], x["BESS"]))
    except Exception as e:
        log.exception("BESS carga-descarga fallida (se conserva lo guardado): %s", e)
        return previas


def _n(f: dict) -> int:
    try:
        return int(f.get("Lecturas_HFP") or 0) + int(f.get("Lecturas_HP") or 0)
    except (TypeError, ValueError):
        return 0
