"""Arma el Excel de los inversores PL a partir de lo que dejó extraer.py: una hoja por inversor con la tabla PV
(formato Kondu) y, debajo, la captura de «Datos del dispositivo en tiempo real» + «Información básica».

  python extras/inversores_pl/excel.py --salida salida
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# Paleta Kondu (docs/style.css, tema claro)
MORADO, MORADO_2 = "270D44", "3D1A66"
MAGENTA, NARANJA = "E11D73", "F37828"
FONDO, BANDA, BORDE, TEXTO, TEXTO_3 = "FFFFFF", "F6F4F9", "E6E1EC", "1A1325", "6B6479"
ACENTO_SUAVE = "FBE3EE"                                   # magenta al 12 % sobre blanco

ANCHO_CAPTURA_PX = 1150                                   # ancho con el que se ve la captura en Excel


def _fill(c):
    return PatternFill("solid", fgColor=c)


def hoja_inversor(wb: Workbook, inv: dict, planta: str, extraido: str, carpeta: Path) -> None:
    nombre = inv.get("nombre") or inv["id"]
    ws = wb.create_sheet(nombre.replace("INVERSOR - ", "INV ")[:31])
    ws.sheet_view.showGridLines = False
    fino = Side(style="thin", color=BORDE)
    caja = Border(left=fino, right=fino, top=fino, bottom=fino)

    pv = inv.get("pv")
    ncols = max(len(pv["cabecera"]) if pv else 2, 8)

    # Título
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    t = ws.cell(1, 1, nombre)
    t.font = Font(name="Calibri", size=16, bold=True, color="FFFFFF")
    t.fill = _fill(MORADO)
    t.alignment = Alignment(vertical="center", indent=1)
    ws.row_dimensions[1].height = 30
    for c in range(2, ncols + 1):
        ws.cell(1, c).fill = _fill(MORADO)
    for c in range(1, ncols + 1):                         # franja magenta -> naranja bajo el título
        ws.cell(2, c).fill = _fill(MAGENTA if c <= ncols / 2 else NARANJA)
    ws.row_dimensions[2].height = 4
    s = ws.cell(3, 1, f"{planta} · Control › Detalles · extraído {extraido} (hora Lima)")
    s.font = Font(italic=True, size=10, color=TEXTO_3)

    fila = 5
    sec = ws.cell(fila, 1, "Datos del dispositivo en tiempo real · Planta PV")
    sec.font = Font(size=12, bold=True, color=MORADO)
    fila += 1

    if pv:
        for j, txt in enumerate(pv["cabecera"], start=1):
            c = ws.cell(fila, j, txt)
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = _fill(MORADO_2 if j == 1 else MORADO)
            c.alignment = Alignment(horizontal="left" if j == 1 else "center", vertical="center", indent=1 if j == 1 else 0)
            c.border = Border(left=fino, right=fino, top=fino, bottom=Side(style="medium", color=MAGENTA))
        ws.row_dimensions[fila].height = 22
        for i, f in enumerate(pv["filas"], start=1):
            r = fila + i
            fondo = BANDA if i % 2 == 0 else FONDO
            corriente = "corriente" in str(f[0]).lower() or "current" in str(f[0]).lower()
            for j, v in enumerate(f, start=1):
                c = ws.cell(r, j, v)
                c.border = caja
                c.fill = _fill(ACENTO_SUAVE if j == 1 else fondo)
                if j == 1:
                    c.font = Font(bold=True, color=MORADO)
                    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
                else:
                    c.font = Font(color=TEXTO)
                    c.alignment = Alignment(horizontal="center", vertical="center")
                    if isinstance(v, (int, float)):
                        c.number_format = "0.00" if corriente else "0.0"
            ws.row_dimensions[r].height = 20
        fila += len(pv["filas"]) + 1
    else:
        c = ws.cell(fila, 1, f"Sin datos de la tabla PV ({inv.get('error') or 'no se encontró en el portal'})")
        c.font = Font(italic=True, color="C4271B")
        fila += 1

    ws.column_dimensions["A"].width = 26
    for j in range(2, ncols + 1):
        ws.column_dimensions[get_column_letter(j)].width = 9.5

    fila += 1
    sec = ws.cell(fila, 1, "Datos del dispositivo en tiempo real + Información básica (captura del portal)")
    sec.font = Font(size=12, bold=True, color=MORADO)
    fila += 1
    png = carpeta / inv["captura"] if inv.get("captura") else None
    if png and png.exists():
        img = XLImage(str(png))
        escala = ANCHO_CAPTURA_PX / img.width
        img.width, img.height = ANCHO_CAPTURA_PX, int(img.height * escala)
        ws.add_image(img, f"A{fila}")
    else:
        c = ws.cell(fila, 1, "Sin captura")
        c.font = Font(italic=True, color="C4271B")
    ws.freeze_panes = None


def armar(carpeta: Path, destino: Path) -> Path:
    datos = json.loads((carpeta / "inversores_pl.json").read_text(encoding="utf-8"))
    wb = Workbook()
    wb.remove(wb.active)
    for inv in datos["inversores"]:
        hoja_inversor(wb, inv, datos["planta"], datos["extraido"], carpeta)
    wb.save(destino)
    return destino


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default="salida")
    ap.add_argument("--archivo", default="")
    a = ap.parse_args()
    carpeta = Path(a.salida)
    datos = json.loads((carpeta / "inversores_pl.json").read_text(encoding="utf-8"))
    nombre = a.archivo or f"Inversores_PL_detalle_{datos['extraido'][:10]}_{datos['extraido'][11:].replace(':', '')}.xlsx"
    print(armar(carpeta, carpeta / nombre))


if __name__ == "__main__":
    main()
