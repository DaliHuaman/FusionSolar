"""Importa el Excel manual antiguo ("Medición de Impedancia desde el Fusión Solar") al historico.

Uso:  python -m fusionsolar.importar_manual "ruta\\Medicion.xlsx" [--excel]

El archivo manual tiene bloques de 10 columnas por dia (LC Inv1, Inv2 | PL A.1-A.3, B.1-B.3 | VES Inv1, Inv2)
y una fila por hora ("1 hr" ... "24 hr"). Cada celda con valor se convierte en una fila del historico
(Fuente = "Excel manual"). Evita duplicados contra el CSV maestro.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta

import openpyxl

from . import store
from .common import classify, load_config

ORDEN = ["LC-Inv1", "LC-Inv2", "PL-InvA.1", "PL-InvA.2", "PL-InvA.3",
         "PL-InvB.1", "PL-InvB.2", "PL-InvB.3", "VES-Inv1", "VES-Inv2"]


def leer(path: str, cfg: dict) -> list[dict]:
    ws = openpyxl.load_workbook(path, data_only=True)["Datos"]
    rows = list(ws.iter_rows(values_only=True))
    fila_fechas = next(i for i, r in enumerate(rows[:8]) if any(isinstance(c, datetime) for c in r))
    horas = [(i, int(m.group(1))) for i, r in enumerate(rows)
             if isinstance(r[1], str) and (m := re.fullmatch(r"\s*(\d+)\s*hr\s*", r[1]))]
    invs = {i["id"]: i for i in cfg["inversores"]}
    filas = []
    for c0 in range(2, len(rows[fila_fechas]), 10):
        dia = rows[fila_fechas][c0]
        if not isinstance(dia, datetime):
            continue
        for k, iid in enumerate(ORDEN):
            inv = invs[iid]
            for i, h in horas:
                v = rows[i][c0 + k] if c0 + k < len(rows[i]) else None
                if not isinstance(v, (int, float)):
                    continue
                ts = dia + timedelta(hours=h)
                est = classify(float(v), inv["umbral"], cfg["margen_advertencia"])
                filas.append(store.construir_fila(
                    ts, inv, {"resistencia": float(v), "potencia_txt": "", "potencia_pct": None,
                              "estado_inversor": ""}, est, fuente="Excel manual"))
    return filas


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("archivo")
    ap.add_argument("--excel", action="store_true", help="tambien escribirlas en el Excel historico")
    a = ap.parse_args()
    cfg = load_config()
    nuevas = leer(a.archivo, cfg)
    existentes = {(f["Fecha_Hora"], f["Planta"], f["Inversor"]) for f in store.leer_historico()}
    nuevas = [f for f in nuevas if (f["Fecha_Hora"], f["Planta"], f["Inversor"]) not in existentes]
    nuevas.sort(key=lambda f: f["Fecha_Hora"])
    store.anexar_csv(nuevas)
    if a.excel and nuevas:
        store.escribir_excel(nuevas, cfg["inversores"])
    print(f"{len(nuevas)} filas importadas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
