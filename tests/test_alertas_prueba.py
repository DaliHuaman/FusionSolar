"""El correo de prueba usa el umbral REAL de cada inversor (portal o respaldo del JSON), no valores fijos.
Ejecutar:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestCorreoPrueba(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["FUSIONSOLAR_STATE"] = self.tmp.name
        os.environ["ALERTA_DESTINATARIOS"] = "prueba@ejemplo.test"
        from fusionsolar import common
        importlib.reload(common)
        from fusionsolar import alertas, umbrales
        self.alertas, self.umbrales = importlib.reload(alertas), importlib.reload(umbrales)
        self.cfg = common.load_config()
        self.enviados = []
        self.alertas.enviar_correo = lambda asunto, cuerpo, para, html=None, cc=None: self.enviados.append(cuerpo)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("FUSIONSOLAR_STATE", None)
        os.environ.pop("ALERTA_DESTINATARIOS", None)

    def test_respaldo_json_tiene_los_umbrales_reales(self):
        u = {i["id"]: i["umbral"] for i in self.cfg["inversores"]}
        self.assertEqual(len(u), 10)
        self.assertTrue(all(v == 1.0 for k, v in u.items() if k.startswith(("LC", "VES", "PL-InvA"))))
        self.assertTrue(all(v == 0.5 for k, v in u.items() if k.startswith("PL-InvB")))

    def test_prueba_usa_umbral_real(self):
        self.alertas.enviar_prueba(self.cfg, datetime(2026, 11, 23, 12, 0))
        self.assertEqual(len(self.enviados), 1)
        cuerpo = self.enviados[0]
        self.assertIn("1,000", cuerpo.replace(".", ","))             # VES Inv2
        self.assertIn("0,500", cuerpo.replace(".", ","))             # PL InvB.2
        self.assertNotIn("0,037", cuerpo.replace(".", ","))

    def test_prueba_sigue_el_umbral_leido_del_portal(self):
        self.umbrales.aplicar(self.cfg, {"umbrales": {"VES-Inv2": {"valor": 2.0, "leido": "2026-11-23 10:00:00"}}})
        self.alertas.enviar_prueba(self.cfg, datetime(2026, 11, 23, 12, 0))
        self.assertIn("2,000", self.enviados[0].replace(".", ","))


if __name__ == "__main__":
    unittest.main()
