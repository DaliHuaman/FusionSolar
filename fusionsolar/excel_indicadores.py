"""Escritura en la plantilla "Indicadores_FusionSolar.xlsx".

Hoja "Indicadores" (la tuya): una fila por corrida, hora en A y pares MΩ / % por inversor.
    B:C LC Inv1 · D:E LC Inv2 · F:G VES Inv1 · H:I VES Inv2 · J:U PL A1, A2, A3, B1, B2, B3
    (la tabla de umbrales que estaba en T:U se mueve a W:X para dejar sitio a los 6 inversores de PL).
Hoja "Historico" (nueva): formato plano, una fila por lectura e inversor. Es la mejor fuente para Power BI.
"""
from __future__ import annotations

import os
import shutil
from copy import copy
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from .common import COLUMNAS, ROOT

HOJA_WIDE = "Indicadores"
HOJA_PLANA = "Historico"
PLANTILLA = ROOT / "plantilla" / "Indicadores_FusionSolar.xlsx"
LIMITE_FILAS = 1_000_000
FMT = "%Y-%m-%d %H:%M:%S"

# inversor -> columna de MΩ en la hoja ancha (el % va en la siguiente) ; fila del umbral en la tabla W:X
ORDEN = ["LC-Inv1", "LC-Inv2", "VES-Inv1", "VES-Inv2",
         "PL-InvA.1", "PL-InvA.2", "PL-InvA.3", "PL-InvB.1", "PL-InvB.2", "PL-InvB.3"]
NOMBRES_PL = ["INVERSOR A1", "INVERSOR A2", "INVERSOR A3", "INVERSOR B1", "INVERSOR B2", "INVERSOR B3"]
PRIMERA_FILA_DATOS = 4
COL_UMBRALES = 23          # W
FILA_UMBRAL_0 = 5          # primera fila de umbrales (Inv1 LC)


def col_mohm(iid: str) -> int:
    return 2 + 2 * ORDEN.index(iid)


def _copiar_estilo(origen, destino) -> None:
    destino._style = copy(origen._style)


def _preparar(wb: Workbook, umbrales: dict[str, float]) -> None:
    """Adapta la plantilla (una sola vez): encabezados de PL, tabla de umbrales a W:X y hoja plana."""
    ws = wb[HOJA_WIDE]
    # 1) mover la tabla/leyenda T1:U14 -> W1:X14 (conserva estilos y la combinacion T4:U4)
    for rng in list(ws.merged_cells.ranges):
        if str(rng) in ("T4:U4", "J1:O1"):
            ws.unmerge_cells(str(rng))
    for r in range(1, 15):
        for dc in (0, 1):
            o, d = ws.cell(r, 20 + dc), ws.cell(r, COL_UMBRALES + dc)
            d.value = o.value
            _copiar_estilo(o, d)
            o.value = None
            o._style = copy(ws.cell(r, 22)._style)      # estilo "limpio"
    ws.merge_cells(start_row=4, start_column=COL_UMBRALES, end_row=4, end_column=COL_UMBRALES + 1)
    ws.column_dimensions[get_column_letter(COL_UMBRALES)].width = 30
    ws.column_dimensions[get_column_letter(COL_UMBRALES + 1)].width = 28
    # 2) encabezados de PL en J:U
    estilo_pl1, estilo_inv, estilo_u = ws["J1"], ws["F2"], ws["B3"]
    for c in range(10, 22):
        for r, ref in ((1, estilo_pl1), (2, estilo_inv), (3, estilo_u)):
            _copiar_estilo(ref, ws.cell(r, c))
    ws.merge_cells("J1:U1")
    ws["J1"] = "PLANTA SOLAR Y BESS - BSF PL"
    for k, nombre in enumerate(NOMBRES_PL):
        c = 10 + 2 * k
        ws.merge_cells(start_row=2, start_column=c, end_row=2, end_column=c + 1)
        ws.cell(2, c).value = nombre
        ws.cell(3, c).value, ws.cell(3, c + 1).value = "MΩ", "%"
        ws.column_dimensions[get_column_letter(c)].width = 9
        ws.column_dimensions[get_column_letter(c + 1)].width = 7
    # 3) umbrales: se reescriben desde la configuracion (orden = ORDEN)
    nombres_umbral = {"LC-Inv1": "Inv1 LC", "LC-Inv2": "Inv2 LC", "VES-Inv1": "Inv1 VES", "VES-Inv2": "Inv2 VES",
                      "PL-InvA.1": "Inv A1", "PL-InvA.2": "Inv A2", "PL-InvA.3": "Inv A3",
                      "PL-InvB.1": "Inv B1", "PL-InvB.2": "Inv B2", "PL-InvB.3": "Inv B3"}
    for k, iid in enumerate(ORDEN):
        ws.cell(FILA_UMBRAL_0 + k, COL_UMBRALES).value = nombres_umbral[iid]
        ws.cell(FILA_UMBRAL_0 + k, COL_UMBRALES + 1).value = umbrales.get(iid)
    # 4) formato condicional: MΩ por debajo del umbral en rojo
    rojo = PatternFill("solid", bgColor="F8CBAD")
    for k, iid in enumerate(ORDEN):
        col = get_column_letter(col_mohm(iid))
        ref = f"${get_column_letter(COL_UMBRALES + 1)}${FILA_UMBRAL_0 + k}"
        ws.conditional_formatting.add(
            f"{col}{PRIMERA_FILA_DATOS}:{col}1048576",
            CellIsRule(operator="lessThan", formula=[ref], fill=rojo, font=Font(color="9C0006", bold=True)))
    ws.column_dimensions["A"].width = 17
    ws.freeze_panes = "B4"
    # 5) hoja plana
    wp = wb.create_sheet(HOJA_PLANA)
    wp.append(COLUMNAS)
    for c in wp[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E79")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    wp.freeze_panes = "A2"
    for i, w in enumerate([19, 11, 7, 8, 11, 10, 15, 13, 10, 18, 52, 14, 22, 14], 1):
        wp.column_dimensions[get_column_letter(i)].width = w
    rng = "J2:J1048576"
    for txt, bg, fg in (("ALARMA", "F8CBAD", "9C0006"), ("ADVERTENCIA", "FFE699", "7F6000"),
                        ("OK", "C6EFCE", "006100"), ("SIN_DATO", "EDEDED", "595959")):
        wp.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=[f'"{txt}"'],
                                      fill=PatternFill("solid", bgColor=bg), font=Font(color=fg)))


def _num(v):
    return None if v in ("", None) else float(v)


def _valor_plano(col: str, v):
    if v in ("", None):
        return None
    if col == "Fecha_Hora":
        return datetime.strptime(v, FMT)
    if col == "Fecha":
        return datetime.strptime(v, "%Y-%m-%d")
    if col in ("Resistencia_MOhm", "Umbral_MOhm", "Margen_%", "Potencia_Activa_%"):
        return float(v)
    return v


def ruta_excel() -> Path:
    base = Path(os.environ.get("EXCEL_DIR") or (ROOT / "state" / "excel"))
    base.mkdir(parents=True, exist_ok=True)
    nombre = os.environ.get("EXCEL_NOMBRE", "Indicadores_FusionSolar.xlsx")
    stem, ext = os.path.splitext(nombre)
    parte = 1
    while True:
        p = base / (nombre if parte == 1 else f"{stem}_parte{parte}{ext}")
        if not p.exists():
            return p
        wb = load_workbook(p, read_only=True)
        n = wb[HOJA_PLANA].max_row if HOJA_PLANA in wb.sheetnames else 0
        wb.close()
        if n < LIMITE_FILAS:
            return p
        parte += 1


def escribir(filas: list[dict], inversores: list[dict]) -> Path:
    """Anexa las lecturas al Excel. Lanza PermissionError/OSError si el archivo esta bloqueado."""
    umbrales = {i["id"]: i["umbral"] for i in inversores}
    ruta = ruta_excel()
    if ruta.exists():
        wb = load_workbook(ruta)
    elif PLANTILLA.exists():
        wb = load_workbook(PLANTILLA)
    else:
        raise FileNotFoundError(f"No existe la plantilla {PLANTILLA}")
    if HOJA_PLANA not in wb.sheetnames:
        _preparar(wb, umbrales)

    # --- hoja plana
    wp = wb[HOJA_PLANA]
    for f in filas:
        wp.append([_valor_plano(c, f.get(c)) for c in COLUMNAS])
        r = wp.max_row
        wp.cell(r, 1).number_format = "yyyy-mm-dd hh:mm:ss"
        wp.cell(r, 2).number_format = "yyyy-mm-dd"
        wp.cell(r, 7).number_format = wp.cell(r, 8).number_format = "0.000"
    ref = f"A1:{get_column_letter(len(COLUMNAS))}{wp.max_row}"
    if wp.tables:
        wp.tables["tHistorico"].ref = ref
        wp.tables["tHistorico"].autoFilter.ref = ref
    else:
        t = Table(displayName="tHistorico", ref=ref)
        t.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=True)
        wp.add_table(t)

    # --- hoja ancha: una fila por marca de tiempo
    ws = wb[HOJA_WIDE]
    por_ts: dict[str, dict[str, dict]] = {}
    for f in filas:
        por_ts.setdefault(f["Fecha_Hora"], {})[f"{f['Planta']}-{f['Inversor']}"] = f
    fila = PRIMERA_FILA_DATOS
    while ws.cell(fila, 1).value is not None:
        fila += 1
    for ts in sorted(por_ts):
        ws.cell(fila, 1).value = datetime.strptime(ts, FMT).replace(second=0)
        ws.cell(fila, 1).number_format = "dd/mm/yyyy hh:mm"
        for iid, f in por_ts[ts].items():
            if iid not in ORDEN:
                continue
            c = col_mohm(iid)
            ws.cell(fila, c).value = _num(f["Resistencia_MOhm"])
            ws.cell(fila, c).number_format = "0.000"
            ws.cell(fila, c + 1).value = _num(f["Potencia_Activa_%"])
            ws.cell(fila, c + 1).number_format = "0.0"
        fila += 1

    tmp = ruta.with_name(ruta.stem + ".tmp.xlsx")
    wb.save(tmp)
    os.replace(tmp, ruta)        # atomico; falla con PermissionError si Excel tiene el archivo abierto
    return ruta
