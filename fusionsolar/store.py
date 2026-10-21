"""Persistencia: CSV maestro (siempre) + Excel (plantilla Indicadores_FusionSolar.xlsx) con cola si esta bloqueado."""
from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from . import excel_indicadores
from .common import CAMPOS_BESS, COLUMNAS, COLUMNAS_BESS, STATE_DIR

log = logging.getLogger("fusionsolar.store")

# En la nube (GitHub Actions) el CSV maestro vive en la rama "data"; en la PC, en la carpeta de estado local.
CSV_MAESTRO = Path(os.environ.get("HISTORICO_CSV") or (STATE_DIR / "historico.csv"))
# Temperaturas BESS: CSV aparte (junto al maestro) y acotado a los ultimos N dias para no crecer sin limite.
CSV_BESS = Path(os.environ.get("HISTORICO_BESS_CSV") or CSV_MAESTRO.with_name("historico_bess.csv"))
PENDIENTES = STATE_DIR / "pendientes_excel.csv"
FMT = "%Y-%m-%d %H:%M:%S"


def _leer_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _escribir_csv(path: Path, filas: list[dict], anexar: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    nuevo = not path.exists() or not anexar
    with open(path, "a" if anexar else "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS)
        if nuevo:
            w.writeheader()
        w.writerows(filas)


def anexar_csv(filas: list[dict]) -> None:
    _escribir_csv(CSV_MAESTRO, filas, anexar=True)


def construir_fila(ts: datetime, inv: dict, lectura: dict, estado: str, fuente: str = "FusionSolar") -> dict:
    r = lectura.get("resistencia")
    um = inv["umbral"]
    return {
        "Fecha_Hora": ts.strftime(FMT),
        "Fecha": ts.strftime("%Y-%m-%d"),
        "Hora": ts.strftime("%H:%M"),
        "Planta": inv["planta"],
        "Grupo": inv["grupo"],
        "Inversor": inv["nombre"],
        "Resistencia_MOhm": "" if r is None else r,
        "Umbral_MOhm": um,
        "Margen_%": "" if r is None else round((r - um) / um * 100, 1),
        "Estado_Aislamiento": estado,
        "Estado_Potencia_Activa": lectura.get("potencia_txt", ""),
        "Potencia_Activa_%": "" if lectura.get("potencia_pct") is None else lectura["potencia_pct"],
        "Estado_Inversor": lectura.get("estado_inversor", ""),
        "Fuente": fuente,
    }


def construir_fila_bess(ts: datetime, bess_id: str, lectura: dict) -> dict:
    fila = {"Fecha_Hora": ts.strftime(FMT), "Fecha": ts.strftime("%Y-%m-%d"), "Hora": ts.strftime("%H:%M"), "BESS": bess_id}
    for clave, col in CAMPOS_BESS.items():
        v = lectura.get(clave)
        fila[col] = "" if v is None else v
    return fila


def leer_bess() -> list[dict]:
    return _leer_csv(CSV_BESS)


def guardar_bess(filas: list[dict], ahora: datetime, dias: int) -> list[dict]:
    """Agrega las lecturas al CSV BESS descartando lo mas antiguo que `dias`. Devuelve el historial resultante.

    Se reescribe completo (es pequeño: ~20 KB/dia) mediante un archivo temporal para que nunca quede a medias.
    """
    corte = (ahora - timedelta(days=dias)).strftime(FMT)
    todas = [f for f in leer_bess() if f.get("Fecha_Hora", "") >= corte] + [f for f in filas if f["Fecha_Hora"] >= corte]
    CSV_BESS.parent.mkdir(parents=True, exist_ok=True)
    tmp = CSV_BESS.with_name(CSV_BESS.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_BESS)
        w.writeheader()
        w.writerows(todas)
    os.replace(tmp, CSV_BESS)
    return todas


def escribir_excel(filas: list[dict], inversores: list[dict], filas_bess: list[dict] | None = None) -> Path:
    return excel_indicadores.escribir(filas, inversores, historico_completo=leer_historico, filas_bess=filas_bess)


def guardar(filas: list[dict], inversores: list[dict], filas_bess: list[dict] | None = None) -> dict:
    """CSV maestro siempre; Excel con cola de pendientes si esta bloqueado (abierto / OneDrive).

    `filas_bess` es el historial BESS completo (ya acotado): la hoja "BESS" se reconstruye entera desde el, asi que
    no necesita cola de pendientes (el CSV BESS ya lo tiene todo).
    """
    if filas:
        anexar_csv(filas)
    pend = _leer_csv(PENDIENTES)
    total = pend + filas
    try:
        ruta = escribir_excel(total, inversores, filas_bess)
        if PENDIENTES.exists():
            PENDIENTES.unlink()
        return {"excel": str(ruta), "pendientes": 0}
    except (PermissionError, OSError) as e:
        log.warning("Excel bloqueado/no disponible (%s). %s filas quedan pendientes.", e, len(total))
        _escribir_csv(PENDIENTES, total, anexar=False)
        return {"excel": None, "pendientes": len(total), "error": str(e)}


def leer_historico() -> list[dict]:
    return _leer_csv(CSV_MAESTRO)
