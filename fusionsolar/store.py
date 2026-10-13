"""Persistencia: CSV maestro (siempre) + Excel (plantilla Indicadores_FusionSolar.xlsx) con cola si esta bloqueado."""
from __future__ import annotations

import csv
import logging
import os
from datetime import datetime
from pathlib import Path

from . import excel_indicadores
from .common import COLUMNAS, STATE_DIR

log = logging.getLogger("fusionsolar.store")

# En la nube (GitHub Actions) el CSV maestro vive en la rama "data"; en la PC, en la carpeta de estado local.
CSV_MAESTRO = Path(os.environ.get("HISTORICO_CSV") or (STATE_DIR / "historico.csv"))
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


def escribir_excel(filas: list[dict], inversores: list[dict]) -> Path:
    return excel_indicadores.escribir(filas, inversores)


def guardar(filas: list[dict], inversores: list[dict]) -> dict:
    """CSV maestro siempre; Excel con cola de pendientes si esta bloqueado (abierto / OneDrive)."""
    anexar_csv(filas)
    pend = _leer_csv(PENDIENTES)
    total = pend + filas
    try:
        ruta = escribir_excel(total, inversores)
        if PENDIENTES.exists():
            PENDIENTES.unlink()
        return {"excel": str(ruta), "pendientes": 0}
    except (PermissionError, OSError) as e:
        log.warning("Excel bloqueado/no disponible (%s). %s filas quedan pendientes.", e, len(total))
        _escribir_csv(PENDIENTES, total, anexar=False)
        return {"excel": None, "pendientes": len(total), "error": str(e)}


def leer_historico() -> list[dict]:
    return _leer_csv(CSV_MAESTRO)
