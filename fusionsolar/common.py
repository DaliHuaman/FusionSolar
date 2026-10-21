"""Configuracion, parseo y clasificacion compartidos."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "inversores.json"


def _cargar_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:  # python-dotenv es opcional si las variables ya estan en el entorno
        pass


_cargar_env()
# Estado local (CSV maestro, logs, capturas, clon de gh-pages). Con el codigo dentro de OneDrive conviene
# dejarlo FUERA (FUSIONSOLAR_STATE=%LOCALAPPDATA%\FusionSolar\state) para que no se sincronicen miles de archivos.
STATE_DIR = Path(os.environ.get("FUSIONSOLAR_STATE") or (ROOT / "state"))

# Columnas del historico (Excel y CSV maestro)
COLUMNAS = [
    "Fecha_Hora", "Fecha", "Hora", "Planta", "Grupo", "Inversor",
    "Resistencia_MOhm", "Umbral_MOhm", "Margen_%", "Estado_Aislamiento",
    "Estado_Potencia_Activa", "Potencia_Activa_%", "Estado_Inversor", "Fuente",
]

# Historico de temperaturas BESS (una fila por BESS y corrida)
COLUMNAS_BESS = [
    "Fecha_Hora", "Fecha", "Hora", "BESS", "Temp_Max_Grupo_C",
    "Agua_Sum_Bateria_C", "Agua_Ret_Bateria_C", "Agua_Sum_Potencia_C", "Agua_Ret_Potencia_C", "Temp_Exterior_C",
]
# clave de la lectura -> columna del CSV BESS (mismo orden que en el Excel y en el dashboard)
CAMPOS_BESS = {
    "temp_max": "Temp_Max_Grupo_C",
    "agua_bat_sum": "Agua_Sum_Bateria_C",
    "agua_bat_ret": "Agua_Ret_Bateria_C",
    "agua_pot_sum": "Agua_Sum_Potencia_C",
    "agua_pot_ret": "Agua_Ret_Potencia_C",
    "temp_ext": "Temp_Exterior_C",
}


def load_env() -> None:
    _cargar_env()


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def now_local(cfg: dict) -> datetime:
    return datetime.now(ZoneInfo(cfg["zona_horaria"])).replace(tzinfo=None, microsecond=0)


def parse_mohm(txt: str | None) -> float | None:
    """'1,745 MΩ' -> 1.745 ; '1.234,5 MΩ' -> 1234.5 ; '--' -> None."""
    if not txt:
        return None
    m = re.search(r"-?\d[\d.,]*", txt.replace(" ", " "))
    if not m:
        return None
    s = m.group(0)
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_pct(txt: str | None) -> float | None:
    """'P=100.0% (Porcentaje ...)' -> 100.0"""
    if not txt:
        return None
    m = re.search(r"P\s*=\s*(-?\d+(?:[.,]\d+)?)\s*%", txt)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def parse_kw(txt: str | None) -> float | None:
    """'P=115.000kW (Valor fijo ...)' -> 115.0 ; '115,000 kW' -> 115.0"""
    if not txt:
        return None
    m = re.search(r"(-?\d+(?:[.,]\d+)?)\s*kW", txt)
    return float(m.group(1).replace(",", ".")) if m else None


def parse_temp(txt: str | None) -> float | None:
    """'27.90℃' -> 27.9 ; '-3,5 ℃' -> -3.5 ; '--' -> None. Descarta valores fuera de -40..150 °C (lectura corrupta)."""
    if not txt:
        return None
    m = re.search(r"-?\d+(?:[.,]\d+)?", txt)
    if not m:
        return None
    v = float(m.group(0).replace(",", "."))
    return v if -40 <= v <= 150 else None


def classify(valor: float | None, umbral: float, margen: float) -> str:
    """OK / ADVERTENCIA (dentro del margen sobre el umbral) / ALARMA (< umbral) / SIN_DATO."""
    if valor is None:
        return "SIN_DATO"
    if valor < umbral:
        return "ALARMA"
    if valor < umbral * (1 + margen):
        return "ADVERTENCIA"
    return "OK"


@dataclass
class Credenciales:
    usuario: str
    clave: str


def credenciales() -> Credenciales:
    u, p = os.environ.get("FUSIONSOLAR_USER", ""), os.environ.get("FUSIONSOLAR_PASSWORD", "")
    if not u or not p:
        raise SystemExit("Faltan FUSIONSOLAR_USER / FUSIONSOLAR_PASSWORD (archivo .env o variables de entorno).")
    return Credenciales(u, p)
