"""Pruebas de BESS carga-descarga (metodología «Lógica de Temperaturas»): °C Min / °C Máx, Salto °C, Tramo y Fuera de Rango,
y el recálculo del histórico cuando cambia la metodología.   Ejecutar:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fusionsolar import bess_cd  # noqa: E402
from fusionsolar.common import COLUMNAS_BCD  # noqa: E402


class EvaluarHP(unittest.TestCase):
    """Los 6 ejemplos de la hoja «Evaluación» del Excel (T1…T6 de HP de BESS-01, 12 al 17/11/2026)."""

    def caso(self, temps, mn, mx, salto, tramo, fuera, tipo=""):
        r = bess_cd.evaluar(temps)
        self.assertEqual((r["Min"], r["Max"], r["Salto"], r["Tramo"], r["Fuera"], r["Fuera_Tipo"]), (mn, mx, salto, tramo, fuera, tipo))

    def test_17_nov_fuera_por_encima(self):
        self.caso([31, 32, 32, 33, 34, 33.3], 31, 33.3, "normal", "normal", 34, "alto")

    def test_16_nov_salto_y_fuera_por_debajo(self):
        self.caso([27.7, 27.5, 27.8, 32, 32.5, 33.1], 27.7, 33.1, 4.2, 3, 27.5, "bajo")

    def test_15_nov_todo_normal(self):
        self.caso([30.3, 30.35, 30.35, 30.35, 30.3, 30.4], 30.3, 30.4, "normal", "normal", "normal")

    def test_14_nov_salto_en_tramo_3(self):
        # El Excel de ejemplo dice tramo 2, pero el salto 30.6 -> 34.4 es T3 -> T4, que por la definición es el tramo 3.
        self.caso([30.6, 30.7, 30.6, 34.4, 34.5, 34.6], 30.6, 34.6, 3.8, 3, "normal")

    def test_13_nov_dos_fuera_de_rango_empate_gana_el_de_arriba(self):
        self.caso([30.9, 31.2, 31.5, 30.7, 33.8, 33.6], 30.9, 33.6, 3.1, 4, 33.8, "alto")

    def test_12_nov_fuera_por_debajo(self):
        self.caso([29.1, 29, 29.5, 30.5, 32, 33.5], 29.1, 33.5, "normal", "normal", 29, "bajo")


class Reglas(unittest.TestCase):
    def test_salto_de_bajada_conserva_signo(self):
        self.assertEqual(bess_cd.salto_tramo(bess_cd.incrementos([33, 33.5, 30.1, 30.5])), (-3.4, 2))

    def test_dos_saltos_gana_el_mayor_valor_absoluto(self):
        self.assertEqual(bess_cd.salto_tramo(bess_cd.incrementos([30, 33.1, 33, 29.0, 29.5])), (-4.0, 3))

    def test_exactamente_3_es_salto(self):
        self.assertEqual(bess_cd.salto_tramo(bess_cd.incrementos([30.1, 33.1])), (3.0, 1))

    def test_fuera_de_rango_mas_alejado(self):
        self.assertEqual(bess_cd.fuera_de_rango([30, 29.0, 32.5, 32], 30, 32), (29.0, "bajo"))

    def test_extremo_faltante_usa_primera_y_ultima_lectura(self):
        r = bess_cd.evaluar([None, 30, 31, 32, 33, None])
        self.assertEqual((r["Min"], r["Max"], r["n"]), (30, 33, 4))

    def test_hueco_omite_el_tramo(self):
        r = bess_cd.evaluar([30, None, 34, 34.5, 35, 35.2])   # 30 -> 34 cruza el hueco: no es un tramo
        self.assertEqual((r["Salto"], r["Tramo"]), ("normal", "normal"))

    def test_sin_lecturas(self):
        r = bess_cd.evaluar([None] * 6)
        self.assertEqual((r["Min"], r["Salto"], r["n"]), ("", "Sin datos", 0))

    def test_hfp_min_max_opcional(self):
        t = [29.0, 27.2, 26.4] + [28.0] * 32 + [27.4]
        self.assertEqual((bess_cd.evaluar(t)["Min"], bess_cd.evaluar(t)["Max"]), (29.0, 27.4))
        r = bess_cd.evaluar(t, "min_max")
        self.assertEqual((r["Min"], r["Max"], r["Fuera"]), (26.4, 29.0, "normal"))


class Recalculo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._env = os.environ.get("HISTORICO_CSV")
        os.environ["HISTORICO_CSV"] = str(Path(self.tmp.name) / "historico.csv")

    def tearDown(self):
        if self._env is None:
            os.environ.pop("HISTORICO_CSV", None)
        else:
            os.environ["HISTORICO_CSV"] = self._env
        self.tmp.cleanup()

    @staticmethod
    def historial(dia: str, hp: list[float]) -> list[dict]:
        filas = []
        for k in range(36):
            filas.append({"Fecha": dia, "Hora": f"{k // 2:02d}:{30 * (k % 2):02d}", "BESS": "BESS-01", "Temp_Max_Grupo_C": "27.0"})
        for k, t in enumerate(hp):
            filas.append({"Fecha": dia, "Hora": f"{18 + k // 2:02d}:{30 * (k % 2):02d}", "BESS": "BESS-01", "Temp_Max_Grupo_C": str(t)})
        for f in filas:
            f["Fecha_Hora"] = f"{f['Fecha']} {f['Hora']}:00"
        return filas

    def test_filas_de_la_metodologia_anterior_se_recalculan(self):
        dia = "2026-10-25"                                           # fuera de los 7 días que se recalculan siempre
        viejo = ["Fecha", "BESS", "HFP_Min", "HFP_Max", "HFP_Concentracion", "HP_Min", "HP_Max", "HP_Concentracion", "Lecturas_HFP", "Lecturas_HP"]
        with open(bess_cd.ruta_csv(), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=viejo)
            w.writeheader()
            w.writerow({"Fecha": dia, "BESS": "BESS-01", "HFP_Min": 27, "HFP_Max": 27, "HFP_Concentracion": "Sin predominio",
                        "HP_Min": 27.7, "HP_Max": 33.1, "HP_Concentracion": "Al final", "Lecturas_HFP": 36, "Lecturas_HP": 6})
        cfg = {"bess": {"equipos": [{"id": "BESS-01"}], "carga_descarga": {}}}
        filas = bess_cd.actualizar(self.historial(dia, [27.7, 27.5, 27.8, 32, 32.5, 33.1]), datetime(2026, 11, 19, 13), cfg)
        self.assertEqual(len(filas), 1)
        f = filas[0]
        self.assertEqual((f["HP_Salto"], f["HP_Tramo"], f["HP_Fuera"], f["HP_Fuera_Tipo"]), (4.2, 3, 27.5, "bajo"))
        with open(bess_cd.ruta_csv(), encoding="utf-8-sig") as fh:
            r = list(csv.DictReader(fh))
        self.assertEqual(list(r[0].keys()), COLUMNAS_BCD)
        self.assertEqual(r[0]["Metodo"], bess_cd.metodo())
        # segunda corrida: ya está con la metodología vigente y fuera de la ventana de recálculo -> no se toca
        mtime = bess_cd.ruta_csv().stat().st_mtime_ns
        bess_cd.actualizar(self.historial(dia, [27.7, 27.5, 27.8, 32, 32.5, 33.1]), datetime(2026, 11, 19, 14), cfg)
        self.assertEqual(bess_cd.ruta_csv().stat().st_mtime_ns, mtime)


if __name__ == "__main__":
    unittest.main()
