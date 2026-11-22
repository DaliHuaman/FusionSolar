"""Pruebas de la energía exportada a la red (sin portal): histórico solo con valores > 0, relleno, aviso por correo una sola vez,
hoja del Excel y bloque del dashboard.   Ejecutar:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class PortalFalso:
    """Scraper simulado: {(planta, 'AAAA-MM-DD'): [(fecha_hora, kWh)]}."""

    def __init__(self, datos: dict, falla: set | None = None):
        self.datos, self.falla, self.planta, self.consultas = datos, falla or set(), None, []

    def elegir_planta_informe(self, buscar):
        self.planta = {"Logiscity": "LC", "Villa el Salvador": "VES", "Planta Solar y Bess": "PL"}[buscar[0]]
        return self.planta

    def energia_exportada_dia(self, dia: date):
        self.consultas.append((self.planta, dia.isoformat()))
        if (self.planta, dia.isoformat()) in self.falla:
            raise RuntimeError("portal caído")
        return self.datos.get((self.planta, dia.isoformat()), [])


def horas(dia: str, valores: dict[int, float]) -> list[tuple[str, float]]:
    return [(f"{dia} {h:02d}:00:00", valores.get(h, 0.0)) for h in range(6, 19)]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["HISTORICO_CSV"] = str(Path(self.tmp.name) / "historico.csv")
        os.environ["FUSIONSOLAR_STATE"] = self.tmp.name
        os.environ["EXCEL_DIR"] = self.tmp.name
        from fusionsolar import common
        importlib.reload(common)
        from fusionsolar import energia_exportada
        self.ee = importlib.reload(energia_exportada)
        self.cfg = common.load_config()
        self.cfg["energia_exportada"]["fechas_relleno"] = {"LC": ["2025-07-25"], "VES": ["2026-11-01"], "PL": []}
        self.enviados = []

    def tearDown(self):
        self.tmp.cleanup()
        for k in ("HISTORICO_CSV", "FUSIONSOLAR_STATE", "EXCEL_DIR"):
            os.environ.pop(k, None)

    def enviar(self, asunto, texto, para, html=None, cc=None):
        self.enviados.append({"asunto": asunto, "texto": texto, "para": para, "html": html, "cc": cc})


class TestLogica(Base):
    def test_solo_positivos_y_sin_duplicar(self):
        ahora = datetime(2026, 11, 18, 15, 15)
        todas, nuevas = self.ee.fusionar([], [("LC", "2026-11-18 11:00:00", 0.0), ("LC", "2026-11-18 12:00:00", 1.5),
                                              ("LC", "2026-11-18 13:00:00", None)], ahora)
        self.assertEqual([f["Fecha_Hora"] for f in nuevas], ["2026-11-18 12:00:00"])
        todas, nuevas = self.ee.fusionar(todas, [("LC", "2026-11-18 12:00:00", 1.75), ("LC", "2026-11-18 12:00:00", 0)], ahora)
        self.assertEqual(nuevas, [])
        self.assertEqual(len(todas), 1)
        self.assertEqual(todas[0]["Energia_kWh"], 1.75)          # corrección del portal (sigue > 0)

    def test_plan_hoy_ayer_y_relleno(self):
        p = self.ee.plan(self.cfg, datetime(2026, 11, 18, 14, 15), {"relleno_hecho": ["VES|2026-11-01"]})
        lc = [(d.isoformat(), r) for c, d, r in p if c == "LC"]
        self.assertEqual(lc, [("2026-11-18", False), ("2026-11-17", False), ("2025-07-25", True)])
        self.assertNotIn(("VES", date(2026, 11, 1), True), p)
        self.assertEqual([c for c, *_ in p][:2], ["LC", "LC"])  # agrupado por planta (orden LC, VES, PL)

    def test_corrida_relleno_sin_correo_y_nueva_con_correo(self):
        datos = {("LC", "2025-07-25"): horas("2025-07-25", {11: 2.5, 12: 4.25}),
                 ("VES", "2026-11-01"): horas("2026-11-01", {12: 7.0}),
                 ("PL", "2026-11-18"): horas("2026-11-18", {12: 0.84, 13: 1.2})}
        sc = PortalFalso(datos)
        info = self.ee.actualizar(sc, self.cfg, datetime(2026, 11, 18, 15, 15), enviar=self.enviar)
        self.assertEqual(info["nuevas"], 5)
        self.assertEqual(len(self.enviados), 1)                    # solo las de hoy (PL); el relleno no avisa
        m = self.enviados[0]
        self.assertIn("PL", m["asunto"])
        self.assertIn("destinatario@correo.invalido", m["para"])
        self.assertIn("gestiondesoluciones@kondu.pe", m["para"])
        self.assertIn("18/11/2026 12:00", m["html"])
        self.assertIn("Energía exportada a la red", m["html"])
        filas = self.ee.leer_filas()
        self.assertEqual(len(filas), 5)
        self.assertEqual({f["Alertado"] for f in filas if f["Planta"] != "PL"}, {"relleno"})
        # segunda corrida: nada nuevo → no se repite el correo ni se vuelve a consultar el relleno
        sc2 = PortalFalso(datos)
        self.ee.actualizar(sc2, self.cfg, datetime(2026, 11, 18, 16, 15), enviar=self.enviar)
        self.assertEqual(len(self.enviados), 1)
        self.assertNotIn(("LC", "2025-07-25"), sc2.consultas)
        # aparece una hora nueva → un correo solo con esa hora
        datos[("PL", "2026-11-18")] = horas("2026-11-18", {12: 0.84, 13: 1.2, 14: 3.0})
        self.ee.actualizar(PortalFalso(datos), self.cfg, datetime(2026, 11, 18, 17, 15), enviar=self.enviar)
        self.assertEqual(len(self.enviados), 2)
        self.assertIn("14:00", self.enviados[1]["texto"])
        self.assertNotIn("13:00", self.enviados[1]["texto"])

    def test_envio_fallido_se_reintenta(self):
        datos = {("LC", "2026-11-18"): horas("2026-11-18", {12: 1.0})}

        def falla(*a, **k):
            raise RuntimeError("SMTP caído")
        info = self.ee.actualizar(PortalFalso(datos), self.cfg, datetime(2026, 11, 18, 15, 15), enviar=falla)
        self.assertEqual(info["aviso"]["enviados"], 0)
        self.ee.actualizar(PortalFalso(datos), self.cfg, datetime(2026, 11, 18, 16, 15), enviar=self.enviar)
        self.assertEqual(len(self.enviados), 1)

    def test_fallo_de_un_dia_no_marca_relleno(self):
        sc = PortalFalso({("VES", "2026-11-01"): horas("2026-11-01", {12: 7.0})}, falla={("LC", "2025-07-25")})
        info = self.ee.actualizar(sc, self.cfg, datetime(2026, 11, 18, 15, 15), enviar=self.enviar)
        self.assertIn("LC 2025-07-25", info["errores"])
        est = self.ee.leer_json(self.ee.ruta_estado(), {})
        self.assertNotIn("LC|2025-07-25", est["relleno_hecho"])
        self.assertIn("VES|2026-11-01", est["relleno_hecho"])

    def test_relleno_sin_filas_se_reintenta(self):
        datos = {("VES", "2026-11-01"): horas("2026-11-01", {12: 7.0})}
        for k in range(3):
            self.ee.actualizar(PortalFalso(datos), self.cfg, datetime(2026, 11, 18, 15 + k, 15), enviar=self.enviar)
            est = self.ee.leer_json(self.ee.ruta_estado(), {})
            self.assertEqual("LC|2025-07-25" in est["relleno_hecho"], k == 2)
        self.assertIn("VES|2026-11-01", est["relleno_hecho"])

    def test_comparar_detecta_faltan_y_difieren(self):
        filas, _ = self.ee.fusionar([], [("PL", "2026-11-01 10:00:00", 16.59), ("PL", "2026-11-08 10:00:00", 1.0)], datetime(2026, 11, 18))
        r = self.ee.comparar({"2026-11-01": 16.59, "2026-11-08": 2.0, "2026-11-15": 3.0}, filas, "PL")
        self.assertEqual(r["faltan"], ["2026-11-15"])
        self.assertEqual(list(r["difieren"]), ["2026-11-08"])

    def test_prueba_no_guarda(self):
        datos = {("LC", "2026-11-18"): horas("2026-11-18", {12: 1.0})}
        self.ee.actualizar(PortalFalso(datos), self.cfg, datetime(2026, 11, 18, 15, 15), prueba=True, enviar=self.enviar)
        self.assertEqual(self.enviados, [])
        self.assertFalse(self.ee.ruta_csv().exists())

    def test_destinatarios_aislamiento_suma_variable(self):
        from fusionsolar import alertas
        os.environ["ALERTA_DESTINATARIOS"] = "gestiondesoluciones@kondu.pe"
        try:
            para = alertas.destinatarios(self.cfg)
        finally:
            os.environ.pop("ALERTA_DESTINATARIOS")
        self.assertEqual(para, ["gestiondesoluciones@kondu.pe", "destinatario@correo.invalido"])


class TestSalidas(Base):
    def test_hoja_excel_y_dashboard(self):
        from openpyxl import load_workbook
        from fusionsolar import excel_indicadores, sitedata
        importlib.reload(excel_indicadores)
        filas, _ = self.ee.fusionar([], [("LC", "2025-06-15 12:00:00", 1.25), ("LC", "2025-07-25 11:00:00", 2.5),
                                         ("PL", "2026-11-15 13:00:00", 0.4)], datetime(2026, 11, 18, 15, 15))
        energia = {"plantas": self.ee.plantas(self.cfg), "filas": self.ee.por_planta(filas)}
        ruta = excel_indicadores.escribir([], self.cfg["inversores"], energia=energia)
        wb = load_workbook(ruta)
        ws = wb["Energía Exportada kWh"]
        self.assertEqual(ws["A1"].value, "BSF Logiscity 244 kWp - 230 kW")
        self.assertEqual((ws["A2"].value, ws["B2"].value), ("Fecha y hora", "Energía exportada (kWh)"))
        self.assertEqual(ws["A3"].value, datetime(2025, 7, 25, 11))      # lo más reciente arriba
        filas2, _ = self.ee.fusionar([], [("LC", "2025-06-15", 0.33)], datetime(2026, 11, 18, 15, 15), sin_aviso=True)
        ws2 = load_workbook(excel_indicadores.escribir([], self.cfg["inversores"], energia={"plantas": self.ee.plantas(self.cfg), "filas": self.ee.por_planta(filas2)}))["Energía Exportada kWh"]
        self.assertEqual((ws2["A3"].value, ws2["B3"].value), (datetime(2025, 6, 15), 0.33))
        self.assertEqual(sitedata._energia(self.cfg, filas2)["filas"]["LC"][0], ["2025-06-15", 0.33])
        self.assertEqual(ws["B4"].value, 1.25)
        self.assertEqual(ws["D1"].value, "BSF Villa el Salvador 244 kWp - 230 kW")
        self.assertIsNone(ws["D3"].value)
        self.assertEqual(ws["G1"].value, "Planta Solar y Bess - BSF PL")
        self.assertEqual(ws["H3"].value, 0.4)
        data = sitedata._energia(self.cfg, filas)
        self.assertEqual(data["filas"]["LC"][0], ["2025-07-25 11:00", 2.5])
        self.assertEqual([p["codigo"] for p in data["plantas"]], ["LC", "VES", "PL"])


if __name__ == "__main__":
    unittest.main()
