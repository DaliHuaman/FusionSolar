"""Pruebas de las alarmas históricas (sin portal): lectura del Excel exportado, acumulado sin pisar, relleno/semanal con un portal simulado,
hojas del Excel y archivo del dashboard.   Ejecutar:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import csv
import importlib
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PORTAL = ["Gravedad de la alarma", "Nombre de la planta", "Tipo de dispositivo", "Nombre del dispositivo", "Número SN",
          "Identificador de la causa", "ID de la alarma", "Nombre de la alarma", "Tipo de eliminación",
          "Fecha y hora de eliminación", "Fecha y hora en que ha ocurrido"]          # orden del Excel del portal


def alarma(planta: str, sn: str, id_: int, ocurre: datetime, dur_min: int = 20, grav: str = "Mayor") -> dict:
    return {"Gravedad de la alarma": grav, "Nombre de la planta": f"Planta {planta}", "Tipo de dispositivo": "Inversor",
            "Nombre del dispositivo": f"INV-{sn[-2:]}", "Número SN": sn, "Identificador de la causa": "1", "ID de la alarma": str(id_),
            "Nombre de la alarma": "Baja resistencia de aislamiento", "Tipo de eliminación": "Eliminada después de la recuperación ante el fallo",
            "Fecha y hora de eliminación": (ocurre + timedelta(minutes=dur_min)).strftime("%Y-%m-%d %H:%M:%S"),
            "Fecha y hora en que ha ocurrido": ocurre.strftime("%Y-%m-%d %H:%M:%S")}


def escribir_portal(path: Path, filas: list[dict], columnas=PORTAL) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(columnas)
    for f in filas:
        ws.append([f[c] for c in columnas])
    wb.save(path)


class PortalFalso:
    """Imita al Scraper: guarda alarmas por planta y responde Buscar (Total) / Exportar (Excel con el formato del portal)."""

    def __init__(self, datos: dict[str, list[dict]], total_mal: bool = False, fallar_en: set[str] | None = None, tope_export: int | None = None):
        self.datos, self.planta, self.fila = datos, None, []
        self.total_mal, self.fallar_en, self.tope = total_mal, fallar_en or set(), tope_export
        self.consultas: list[tuple[str, str, str]] = []

    def abrir_alarmas_historicas(self, ne: str) -> None:
        self.planta = ne

    def buscar_alarmas(self, desde: datetime, hasta: datetime):
        d, h = desde.strftime("%Y-%m-%d %H:%M:%S"), hasta.strftime("%Y-%m-%d %H:%M:%S")
        self.consultas.append((self.planta, d, h))
        if d[:10] in self.fallar_en:
            raise RuntimeError("portal caído")
        self.fila = [f for f in self.datos[self.planta] if d <= f["Fecha y hora en que ha ocurrido"] <= h]
        return len(self.fila) + (1 if self.total_mal else 0)

    def exportar_alarmas(self, carpeta: Path) -> Path:
        p = carpeta / f"exp_{len(self.consultas)}.xlsx"
        escribir_portal(p, self.fila[: self.tope] if self.tope else self.fila)
        return p

    def captura(self, nombre: str) -> None:
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["HISTORICO_CSV"] = str(Path(self.tmp.name) / "historico.csv")
        for m in [m for m in sys.modules if m.startswith("fusionsolar")]:
            del sys.modules[m]
        self.ah = importlib.import_module("fusionsolar.alarmas_historicas")
        self.cfg = {"zona_horaria": "America/Lima", "plantas": [{"codigo": "PL"}, {"codigo": "LC"}, {"codigo": "VES"}],
                    "alarmas_historicas": {"inicio_busqueda": "2025-01-01", "ventana_dias": 28, "solape_dias": 45,
                                           "max_filas_ventana": 5000}}

    def tearDown(self):
        self.tmp.cleanup()

    def datos(self, planta="PL", n=40, desde=datetime(2026, 3, 1, 6, 5), cada_h=100):
        return [alarma(planta, f"GR2489{k % 3:06d}", 2062 + k % 2, desde + timedelta(hours=cada_h * k)) for k in range(n)]


class TestLectura(Base):
    def test_columnas_por_nombre_y_orden_de_hoja(self):
        p = Path(self.tmp.name) / "e.xlsx"
        escribir_portal(p, [alarma("PL", "A1", 5, datetime(2026, 11, 17, 6, 5, 38))])
        f = self.ah.leer_exportacion(p, "PL")[0]
        self.assertEqual(f["Fecha y hora en que ha ocurrido"], "2026-11-17 06:05:38")
        self.assertEqual(f["Fecha y hora de eliminación"], "2026-11-17 06:25:38")
        self.assertEqual(self.ah.COLUMNAS_HOJA[-2:], ["Fecha y hora en que ha ocurrido", "Fecha y hora de eliminación"])
        # el portal podría exportar las columnas en otro orden: se leen igual
        q = Path(self.tmp.name) / "e2.xlsx"
        escribir_portal(q, [alarma("PL", "A1", 5, datetime(2026, 11, 17, 6, 5, 38))], columnas=list(reversed(PORTAL)))
        self.assertEqual(self.ah.leer_exportacion(q, "PL")[0], f)

    def test_formato_invalido_se_rechaza(self):
        from openpyxl import Workbook
        wb = Workbook()
        wb.active.append(["Otra cosa", "x"])
        wb.active.append([1, 2])
        p = Path(self.tmp.name) / "mal.xlsx"
        wb.save(p)
        with self.assertRaises(self.ah.ErrorFormato):
            self.ah.leer_exportacion(p, "PL")

    def test_fechas_como_datetime(self):
        from openpyxl import Workbook
        wb = Workbook()
        wb.active.append(PORTAL)
        f = alarma("PL", "A1", 5, datetime(2026, 11, 17, 6, 5, 38))
        wb.active.append([datetime(2026, 11, 17, 6, 25, 38) if c == "Fecha y hora de eliminación" else
                          datetime(2026, 11, 17, 6, 5, 38) if c == "Fecha y hora en que ha ocurrido" else
                          2062.0 if c == "ID de la alarma" else f[c] for c in PORTAL])
        p = Path(self.tmp.name) / "d.xlsx"
        wb.save(p)
        r = self.ah.leer_exportacion(p, "PL")[0]
        self.assertEqual((r["ID de la alarma"], r["Fecha y hora en que ha ocurrido"]), ("2062", "2026-11-17 06:05:38"))


class TestAcumulado(Base):
    def test_no_pisa_no_duplica_y_no_pierde(self):
        ah, datos = self.ah, self.datos(n=30)
        p = Path(self.tmp.name)
        escribir_portal(p / "a.xlsx", datos[:20])
        escribir_portal(p / "b.xlsx", datos[10:])                  # solape de 10 alarmas
        _, n1 = ah.guardar_filas(ah.leer_exportacion(p / "a.xlsx", "PL"))
        todas, n2 = ah.guardar_filas(ah.leer_exportacion(p / "b.xlsx", "PL"))
        self.assertEqual((n1, n2, len(todas)), (20, 10, 30))
        # lo guardado manda: si el portal trae distinto el texto de una fila ya guardada, no se reemplaza
        x = ah.leer_exportacion(p / "a.xlsx", "PL")
        x[0]["Nombre de la alarma"] = "OTRO NOMBRE"
        todas, n3 = ah.guardar_filas(x)
        self.assertEqual(n3, 0)
        self.assertNotIn("OTRO NOMBRE", [f["Nombre de la alarma"] for f in todas])
        self.assertTrue(Path(str(ah.ruta_csv()) + ".bak").exists())
        # el portal "borró" las alarmas viejas: una exportación con solo las nuevas no quita nada
        escribir_portal(p / "c.xlsx", datos[25:])
        todas, _ = ah.guardar_filas(ah.leer_exportacion(p / "c.xlsx", "PL"))
        self.assertEqual(len(todas), 30)
        self.assertEqual(len(ah.leer_filas()), 30)

    def test_misma_alarma_en_otra_planta_no_choca(self):
        ah = self.ah
        a = alarma("PL", "SN1", 1, datetime(2026, 1, 1, 6))
        p = Path(self.tmp.name) / "x.xlsx"
        escribir_portal(p, [a])
        ah.guardar_filas(ah.leer_exportacion(p, "PL"))
        _, n = ah.guardar_filas(ah.leer_exportacion(p, "LC"))
        self.assertEqual(n, 1)


class TestCorrida(Base):
    def correr(self, portal, ahora, **kw):
        nodos = {c: {"ne": c, "nombre": c} for c in self.cfg_codigos}
        return self.ah.actualizar(portal, self.cfg, ahora, plantas_nodos=nodos, **kw)

    cfg_codigos = ["PL", "LC", "VES"]

    def test_relleno_inicial_y_semanal(self):
        datos = {"PL": self.datos("PL", 60, cada_h=80), "LC": self.datos("LC", 25, cada_h=130), "VES": []}
        ahora = datetime(2026, 11, 17, 12)
        portal = PortalFalso(datos)
        res = self.correr(portal, ahora)
        self.assertNotIn("error", res, res)
        filas = self.ah.leer_filas()
        self.assertEqual(sum(f["Planta"] == "PL" for f in filas), 60)
        self.assertEqual(sum(f["Planta"] == "LC" for f in filas), 25)
        est = self.ah.leer_json(self.ah.ruta_estado(), {})["plantas"]
        self.assertEqual(est["PL"]["revisado_hasta"], "2026-11-17")
        self.assertTrue(est["PL"]["relleno_completo"] and est["VES"]["relleno_completo"])
        # VES no tiene alarmas: el relleno igual recorre todas las ventanas hasta el inicio (sin exportar ninguna)
        self.assertGreaterEqual(sum(1 for c in portal.consultas if c[0] == "VES"), 20)
        # semana siguiente: alarmas nuevas + el portal ya borró las viejas
        datos["PL"] = datos["PL"][-5:] + [alarma("PL", "GR2489000009", 2062, datetime(2026, 11, 22, 6, 5))]
        portal2 = PortalFalso(datos)
        self.correr(portal2, datetime(2026, 11, 24, 12))
        filas = self.ah.leer_filas()
        self.assertEqual(sum(f["Planta"] == "PL" for f in filas), 61)           # ninguna perdida, una nueva
        self.assertLessEqual(len([c for c in portal2.consultas if c[0] == "PL"]), 3)    # solo repasa las últimas semanas

    def test_ventanas_nunca_superan_un_mes(self):
        self.cfg["alarmas_historicas"]["ventana_dias"] = 90          # aunque la configuración pida más, se limita a 28 días
        portal = PortalFalso({"PL": [], "LC": [], "VES": []})
        self.correr(portal, datetime(2026, 11, 17, 12))
        for _, d, h in portal.consultas:
            self.assertLessEqual((datetime.strptime(h, "%Y-%m-%d %H:%M:%S") - datetime.strptime(d, "%Y-%m-%d %H:%M:%S")).days, 28)

    def test_total_distinto_no_avanza_y_no_guarda(self):
        datos = {"PL": self.datos("PL", 10), "LC": [], "VES": []}
        res = self.correr(PortalFalso(datos, total_mal=True), datetime(2026, 11, 17, 12))
        self.assertIn("error", res)
        self.assertEqual(self.ah.leer_filas(), [])
        self.assertNotIn("revisado_hasta", self.ah.leer_json(self.ah.ruta_estado(), {}).get("plantas", {}).get("PL", {}))

    def test_fallo_de_una_planta_no_detiene_las_demas(self):
        datos = {"PL": self.datos("PL", 10), "LC": self.datos("LC", 10), "VES": []}
        ahora = datetime(2026, 11, 17, 12)
        hoy = ahora.date().isoformat()
        class P(PortalFalso):
            def buscar_alarmas(s, d, h):
                if s.planta == "PL":
                    raise RuntimeError("PL caída")
                return super().buscar_alarmas(d, h)
        res = self.correr(P(datos), ahora)
        self.assertIn("PL", res["errores"])
        self.assertEqual(sum(f["Planta"] == "LC" for f in self.ah.leer_filas()), 10)

    def test_ventana_grande_se_parte(self):
        datos = {"PL": [alarma("PL", f"S{k}", k, datetime(2026, 11, 1, 0, 0) + timedelta(minutes=10 * k)) for k in range(120)], "LC": [], "VES": []}
        self.cfg["alarmas_historicas"]["max_filas_ventana"] = 50
        self.cfg["alarmas_historicas"]["inicio_busqueda"] = "2026-11-01"
        portal = PortalFalso(datos)
        res = self.correr(portal, datetime(2026, 11, 17, 12))
        self.assertNotIn("error", res, res)
        self.assertEqual(sum(f["Planta"] == "PL" for f in self.ah.leer_filas()), 120)

    def test_filtro_de_fechas_no_aplicado_se_detecta(self):
        datos = {"PL": [alarma("PL", "S1", 1, datetime(2026, 3, 1, 6))], "LC": [], "VES": []}
        class P(PortalFalso):
            def buscar_alarmas(s, d, h):                     # el portal ignora el rango y devuelve todo
                s.fila = list(s.datos[s.planta])
                return len(s.fila)
        res = self.correr(P(datos), datetime(2026, 11, 17, 12))
        self.assertIn("error", res)
        self.assertEqual(self.ah.leer_filas(), [])

    def test_rango_manual(self):
        datos = {"PL": self.datos("PL", 20), "LC": [], "VES": []}
        res = self.correr(PortalFalso(datos), datetime(2026, 11, 17, 12), desde=date(2026, 3, 1), hasta=date(2026, 3, 20))
        self.assertNotIn("error", res, res)
        self.assertTrue(0 < len(self.ah.leer_filas()) < 20)
        self.assertNotIn("PL", self.ah.leer_json(self.ah.ruta_estado(), {}).get("plantas", {}).get("PL", {}).get("revisado_hasta", "") and ["PL"])


class TestSinRepetidas(Base):
    """La misma alarma exportada en dos filas (solo cambia la hora de eliminación) se guarda UNA vez."""

    def test_repetida_en_el_mismo_archivo_conserva_la_eliminacion_mas_tardia(self):
        ah, p = self.ah, Path(self.tmp.name)
        t = datetime(2026, 3, 22, 8, 59, 48)
        a, b = alarma("PL", "SN1", 2032, t, dur_min=700), alarma("PL", "SN1", 2032, t, dur_min=691)     # 2 filas de la misma alarma
        otra = alarma("PL", "SN1", 2032, t + timedelta(hours=30))                                        # otra alarma distinta
        escribir_portal(p / "x.xlsx", [b, otra, a])
        filas = ah.leer_exportacion(p / "x.xlsx", "PL")
        unicas, descartadas = ah.deduplicar(filas)
        self.assertEqual((len(unicas), len(descartadas)), (2, 1))
        self.assertEqual(unicas[0]["Fecha y hora de eliminación"], a["Fecha y hora de eliminación"])
        todas, n = ah.guardar_filas(filas)
        self.assertEqual((n, len(todas)), (2, 2))
        # al repetir el archivo no entra nada
        self.assertEqual(ah.guardar_filas(filas)[1], 0)

    def test_repetida_contra_lo_ya_guardado_no_entra(self):
        ah, p = self.ah, Path(self.tmp.name)
        t = datetime(2026, 3, 22, 8, 59, 48)
        escribir_portal(p / "a.xlsx", [alarma("PL", "SN1", 2032, t, dur_min=10)])
        ah.guardar_filas(ah.leer_exportacion(p / "a.xlsx", "PL"))
        escribir_portal(p / "b.xlsx", [alarma("PL", "SN1", 2032, t, dur_min=500)])       # la misma alarma, otra hora de eliminación
        todas, n = ah.guardar_filas(ah.leer_exportacion(p / "b.xlsx", "PL"))
        self.assertEqual((n, len(todas)), (0, 1))
        self.assertEqual(todas[0]["Fecha y hora de eliminación"], "2026-03-22 09:09:48")          # lo guardado manda

    def test_corrida_semanal_con_filas_repetidas_del_portal(self):
        datos = {"PL": self.datos("PL", 10), "LC": [], "VES": []}
        datos["PL"] = datos["PL"] + [dict(datos["PL"][3], **{"Fecha y hora de eliminación": "2026-12-31 00:00:00"})]     # fila repetida
        nodos = {c: {"ne": c, "nombre": c} for c in ("PL", "LC", "VES")}
        res = self.ah.actualizar(PortalFalso(datos), self.cfg, datetime(2026, 11, 17, 12), plantas_nodos=nodos)
        self.assertNotIn("error", res, res)          # el «Total» del portal cuenta las 11 filas y el Excel trae 11: cuadra
        self.assertEqual(sum(f["Planta"] == "PL" for f in self.ah.leer_filas()), 10)


MANUAL = ["N.º", "Gravedad", "Dispositivo", "Nombre de alarma", "ID de la alarma", "Hora de generación", "Hora de fin", "ID de razón", "Armario", ""]


class TestManuales(Base):
    """Archivos bajados a mano: encabezado con coma final, textos con espacios, fechas ISO o DD/MM/AAAA, Excel con hojas de más."""

    def previas(self):
        base = alarma("PL", "6T0001", 2032, datetime(2026, 11, 1, 6), grav="Mayor")
        base.update({"Nombre del dispositivo": "INVERSOR - A.1", "Nombre de la alarma": "Fallo de la red eléctrica", "Planta": "PL"})
        lc = alarma("LC", "6T0099", 2032, datetime(2026, 11, 1, 6), grav="Mayor")
        lc.update({"Nombre del dispositivo": "INVERSOR N° 1", "Nombre de la alarma": "Fallo de la red eléctrica", "Planta": "LC"})
        lg = alarma("PL", "1025", 1154, datetime(2026, 11, 2, 6), grav="Menores")
        lg.update({"Nombre del dispositivo": "Logger-1025", "Tipo de dispositivo": "SmartLogger", "Planta": "PL"})
        escribir_portal(Path(self.tmp.name) / "p.xlsx", [{k: v for k, v in x.items() if k != "Planta"} for x in (base, lg)])
        self.ah.guardar_filas(self.ah.leer_exportacion(Path(self.tmp.name) / "p.xlsx", "PL"))
        escribir_portal(Path(self.tmp.name) / "q.xlsx", [{k: v for k, v in lc.items() if k != "Planta"}])
        self.ah.guardar_filas(self.ah.leer_exportacion(Path(self.tmp.name) / "q.xlsx", "LC"))

    def csv(self, nombre, filas):
        p = Path(self.tmp.name) / "man" / nombre
        p.parent.mkdir(exist_ok=True)
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(MANUAL)
            w.writerows(filas)                       # una columna menos que el encabezado (coma final), como el portal
        return p.parent

    def test_pl_mayores_y_menores_juntos_y_sin_repetir(self):
        self.previas()
        d = self.csv("PL_mayores.csv", [
            [1, " Grave", " INVERSOR - A.1", " Corte de red eléctrica", 2032, " 2025-09-12 06:49:48", " 2025-09-16 06:41:34", 1, " --"],
            [2, " Grave", " INVERSOR - A.1", " Corte de red eléctrica", 2032, " 2025-09-12 06:49:48", " 2025-09-15 16:38:31", 1, " --"],   # misma alarma
            [3, " Grave", " Logger(Local)", " Excepción de comunicación de dispositivo hacia el sur", 1154, " 2025-09-13 01:00:00", " 2025-09-13 02:00:00", 5, " --"]])
        self.csv("PL_menores.csv", [[1, " Menor", " INVERSOR - A.1", " Error al actualizar", 2065, " 2025-09-23 18:23:24", " 2025-09-29 16:57:39", 4, " --"]])
        res = self.ah.importar_manuales(d)
        self.assertEqual(res["PL"], {"archivos": ["PL_mayores.csv", "PL_menores.csv"], "leidas": 4, "repetidas_en_archivos": 1, "ya_estaban": 0, "nuevas": 3})
        filas = [f for f in self.ah.leer_filas() if f["Fecha y hora en que ha ocurrido"] == "2025-09-12 06:49:48"]
        self.assertEqual(len(filas), 1)
        self.assertEqual((filas[0]["Número SN"], filas[0]["Fecha y hora de eliminación"], filas[0]["Gravedad de la alarma"]),
                         ("6T0001", "2025-09-16 06:41:34", "Mayor"))                  # SN del portal y eliminación más tardía
        self.assertEqual(filas[0]["Nombre de la alarma"], "Fallo de la red eléctrica")      # nombre del portal para ese ID
        lg = [f for f in self.ah.leer_filas() if f["ID de la alarma"] == "1154" and f["Fecha y hora en que ha ocurrido"].startswith("2025")][0]
        self.assertEqual((lg["Número SN"], lg["Tipo de dispositivo"], lg["Gravedad de la alarma"]), ("1025", "SmartLogger", "Mayor"))
        menor = [f for f in self.ah.leer_filas() if f["ID de la alarma"] == "2065"][0]
        self.assertEqual(menor["Gravedad de la alarma"], "Menores")
        # repetir la importación no agrega nada
        self.assertEqual(self.ah.importar_manuales(d)["PL"]["nuevas"], 0)

    def test_manual_contra_lo_ya_guardado(self):
        self.previas()
        d = self.csv("PL_mayores.csv", [[1, " Grave", " INVERSOR - A.1", " Corte de red eléctrica", 2032, " 2026-11-01 06:00:00", " 2026-11-01 07:00:00", 1, " --"]])
        r = self.ah.importar_manuales(d)["PL"]
        self.assertEqual((r["ya_estaban"], r["nuevas"]), (1, 0))

    def test_lc_excel_con_dos_hojas_y_fechas_dia_mes(self):
        from openpyxl import Workbook
        self.previas()
        wb = Workbook()
        o = wb.active
        o.title = "ORIGINAL"
        o.append(["N.º,Gravedad,Dispositivo,Nombre de alarma,ID de la alarma,Hora de generación,Hora de fin,ID de razón,Armario,"])
        o.append(["1, Grave, Inversor 1, Corte de red eléctrica, 2032, 2025-02-18 14:48:25, 2025-02-18 16:36:39, 1, --"])
        h = wb.create_sheet("ORGANIZADA POR COLUMNAS")
        h.append(MANUAL[:9])
        h.append([1, " Grave", " Inversor 1", " Corte de red eléctrica", 2032, "18/02/2025 14:48:25", "18/02/2025 16:36:39", 1, " --"])
        d = Path(self.tmp.name) / "man"
        d.mkdir()
        wb.save(d / "LC.xlsx")
        self.assertEqual(self.ah.importar_manuales(d)["LC"]["nuevas"], 1)
        f = [x for x in self.ah.leer_filas() if x["Planta"] == "LC" and x["Fecha y hora en que ha ocurrido"].startswith("2025")][0]
        self.assertEqual((f["Número SN"], f["Nombre del dispositivo"], f["Fecha y hora de eliminación"]), ("6T0099", "INVERSOR N° 1", "2025-02-18 16:36:39"))

    def test_dispositivo_desconocido_no_guarda_nada(self):
        self.previas()
        d = self.csv("PL_mayores.csv", [
            [1, " Grave", " INVERSOR - A.1", " Corte de red eléctrica", 2032, " 2025-09-12 06:49:48", " 2025-09-16 06:41:34", 1, " --"],
            [2, " Grave", " Medidor X", " Corte de red eléctrica", 2032, " 2025-09-12 06:49:48", " 2025-09-16 06:41:34", 1, " --"]])
        antes = len(self.ah.leer_filas())
        with self.assertRaises(self.ah.ErrorFormato):
            self.ah.importar_manuales(d)
        self.assertEqual(len(self.ah.leer_filas()), antes)

    def test_nombre_de_archivo_sin_planta_se_rechaza(self):
        d = self.csv("otra.csv", [])
        with self.assertRaises(self.ah.ErrorFormato):
            self.ah.importar_manuales(d)


class TestSalidas(Base):
    def test_excel_y_dashboard(self):
        from openpyxl import load_workbook
        ah = self.ah
        p = Path(self.tmp.name)
        datos = self.datos("PL", 30)
        escribir_portal(p / "a.xlsx", datos)
        ah.guardar_filas(ah.leer_exportacion(p / "a.xlsx", "PL"))
        from fusionsolar import excel_indicadores as ex, sitedata
        os.environ["EXCEL_DIR"] = str(p)
        importlib.reload(ex)
        ruta = ex.escribir([], [], filas_bess=None, alarmas=ah.por_planta(ah.leer_filas()))
        wb = load_workbook(ruta)
        self.assertEqual([s for s in wb.sheetnames if s.startswith("Alarma")], ["Alarma Histórica PL", "Alarma Histórica LC", "Alarma Histórica VES"])
        ws = wb["Alarma Histórica PL"]
        self.assertEqual([c.value for c in ws[1]], ah.COLUMNAS_HOJA)
        self.assertEqual(ws["J1"].value, "Fecha y hora en que ha ocurrido")
        self.assertEqual(ws["K1"].value, "Fecha y hora de eliminación")
        self.assertEqual(ws.max_row, 31)
        self.assertGreater(ws["J2"].value, ws["J3"].value)             # lo más reciente arriba
        self.assertEqual(ws["A1"].fill.fgColor.rgb[-6:], "7B1490")
        self.assertEqual(wb["Alarma Histórica LC"].max_row, 1)
        # una segunda corrida no duplica hojas ni filas
        ruta = ex.escribir([], [], alarmas=ah.por_planta(ah.leer_filas()))
        self.assertEqual(sum(s.startswith("Alarma") for s in load_workbook(ruta).sheetnames), 3)
        d = sitedata.construir_alarmas(ah.leer_filas(), datetime(2026, 11, 17, 12))
        self.assertEqual(len(d["filas"]["PL"]), 30)
        self.assertEqual(d["meta"]["n"]["PL"], 30)
        self.assertLess(len(d["dic"]["nombre"]), 3)                    # textos repetidos van al diccionario
        self.assertGreater(d["filas"]["PL"][0][8], d["filas"]["PL"][1][8])


if __name__ == "__main__":
    unittest.main()
