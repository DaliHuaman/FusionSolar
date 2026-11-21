"""BESS carga-descarga: cómo evoluciona la temperatura de cada BESS dentro de cada periodo (metodología «Lógica de Temperaturas»).

Se calcula con las temperaturas que la corrida de 30 min ya guarda en `historico_bess.csv` («Temperatura más alta del grupo»,
`Temp_Max_Grupo_C`), así que no hace falta otra consulta al portal ni puede fallar por la red.

Por día y por BESS se toman las temperaturas de cada media hora (la lectura más cercana a cada hh:00 / hh:30, con tolerancia):

    HP  (hora punta, descarga):   18:00, 18:30, 19:00, 19:30, 20:00, 20:30  -> 6 puntos T1…T6, 5 tramos incrementales
    HFP (fuera de punta, carga):  00:00 … 17:30                              -> 36 puntos T1…T36, 35 tramos incrementales

Tramo k = T(k+1) − T(k). Por periodo se obtiene:

    °C Min          HP: temperatura en el primer punto (T1; si falta, la primera lectura disponible).
                    HFP: la temperatura mínima del periodo, en cualquier punto (no necesariamente T1).
    °C Máx          HP: temperatura en el último punto (T6; si falta, la última lectura disponible).
                    HFP: la temperatura máxima del periodo, en cualquier punto (no necesariamente T36).
    Salto °C        el tramo incremental de mayor valor absoluto (subida o bajada, con su signo) si |salto| ≥ 3 °C; si no, «normal».
                    Si dos tramos empatan se toma el primero.
    Tramo           número del tramo donde ocurrió ese salto (1 = T1→T2, 2 = T2→T3, …); «normal» si el salto es normal.
    Fuera de Rango  SOLO en HP (en HFP no aplica y queda vacío). Temperatura de los puntos intermedios (ni el primero ni el último) que queda fuera del rango [°C Min, °C Máx]:
                    la más baja si queda por debajo (azul) o la más alta si queda por encima (rojo); «normal» si ninguna sale.
                    Si hay una por debajo y otra por encima, se toma la que más se aleja del rango (empate: la de arriba).

Un dato faltante nunca rompe nada: el tramo que lo necesita se omite. Cada día se guarda UNA vez por BESS en
`bess_carga_descarga.csv` (rama «data»; no se acota a 90 días como el historial). Un día se cierra y se agrega a las
`hora_extraccion` (12:00 de Lima) del día siguiente; los últimos `recalcular_dias` se recalculan por si llegaron lecturas tardías.
Si cambia la metodología (columna `Metodo`), todos los días que aún tengan lecturas en el historial se recalculan una vez.
Es idempotente: ejecutarlo varias veces no duplica filas.
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
SALTO_C = 3.0                                            # |tramo| ≥ 3 °C = salto
TOLERANCIA_MIN = 10                                      # una lectura vale para un horario si está a ≤ 10 min
HORA_EXTRACCION = 12
RECALCULAR_DIAS = 7

NORMAL, SIN_DATOS = "normal", "Sin datos"
ALTO, BAJO = "alto", "bajo"                               # Fuera de Rango: por encima (rojo) / por debajo (azul)
GRUPOS = ("HFP", "HP")
CAMPOS = ("Min", "Max", "Salto", "Tramo", "Fuera", "Fuera_Tipo")


def metodo() -> str:
    """Etiqueta de la metodología guardada en cada fila: si cambia, se recalcula el histórico."""
    return "tramos-v2"


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
    """Tramos incrementales T(k+1) − T(k) (cada 30 min). None si falta alguna de las dos temperaturas."""
    return [None if a is None or b is None else round(b - a, 4) for a, b in zip(temps, temps[1:])]


def salto_tramo(incs: list[float | None], umbral: float = SALTO_C) -> tuple[float | str, int | str]:
    """(Salto °C, Tramo): el tramo de mayor |valor| si |valor| ≥ umbral (empate: el primero); si no («normal», «normal»)."""
    validos = [(i + 1, d) for i, d in enumerate(incs) if d is not None]
    if not validos:
        return SIN_DATOS, SIN_DATOS
    tramo, d = max(validos, key=lambda x: (abs(x[1]), -x[0]))
    if round(abs(d), 2) + 1e-9 < umbral:
        return NORMAL, NORMAL
    return round(d, 1), tramo


def fuera_de_rango(temps: list[float | None], lo: float, hi: float) -> tuple[float | str, str]:
    """(valor, «alto»/«bajo»/"") de los puntos intermedios fuera de [lo, hi]; («normal», "") si ninguno sale."""
    con = [t for t in temps if t is not None]
    medio = con[1:-1]
    bajos = [t for t in medio if t < lo - 1e-9]
    altos = [t for t in medio if t > hi + 1e-9]
    b = min(bajos) if bajos else None
    a = max(altos) if altos else None
    if a is None and b is None:
        return NORMAL, ""
    if b is None or (a is not None and round(a - hi, 4) >= round(lo - b, 4)):
        return a, ALTO
    return b, BAJO


def evaluar(temps: list[float | None], extremos: str = "t1_tn", umbral: float = SALTO_C) -> dict:
    """°C Min, °C Máx, Salto °C, Tramo y Fuera de Rango de un periodo (lista de temperaturas T1…Tn, None = sin lectura)."""
    con = [t for t in temps if t is not None]
    if not con:
        return {"Min": "", "Max": "", "Salto": SIN_DATOS, "Tramo": SIN_DATOS, "Fuera": SIN_DATOS, "Fuera_Tipo": "", "n": 0}
    if extremos == "min_max":
        tmin, tmax = min(con), max(con)
    else:
        tmin, tmax = con[0], con[-1]
    salto, tramo = salto_tramo(incrementos(temps), umbral)
    fuera, tipo = fuera_de_rango(temps, min(tmin, tmax), max(tmin, tmax))
    return {"Min": tmin, "Max": tmax, "Salto": salto, "Tramo": tramo, "Fuera": fuera, "Fuera_Tipo": tipo, "n": len(con)}


def resumen_dia(filas_bess: list[dict], dia: str, bess: str, tol: int = TOLERANCIA_MIN, umbral: float = SALTO_C) -> dict | None:
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
    hfp = evaluar(temperaturas(lect, SLOTS_HFP, tol), "min_max", umbral)
    hfp.update({"Fuera": "", "Fuera_Tipo": ""})                  # en HFP no se evalúa «Fuera de Rango»
    hp = evaluar(temperaturas(lect, SLOTS_HP, tol), "t1_tn", umbral)
    fila = {"Fecha": dia, "BESS": bess}
    for g, r in (("HFP", hfp), ("HP", hp)):
        for c in CAMPOS:
            fila[f"{g}_{c}"] = r[c]
    fila.update({"Lecturas_HFP": hfp["n"], "Lecturas_HP": hp["n"], "Metodo": metodo()})
    return fila


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
        umbral = float(bcfg.get("salto_c", SALTO_C))
        met = metodo()
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
                previa = guardadas.get((dia, bid))
                vigente = previa is not None and previa.get("Metodo") == met     # otra metodología -> se recalcula siempre
                if vigente and not reciente:
                    continue
                fila = resumen_dia(filas_bess, dia, bid, tol, umbral)
                if fila is None:
                    continue
                if vigente and _n(previa) > _n(fila):               # lo guardado tiene más lecturas (el historial ya se recortó)
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
