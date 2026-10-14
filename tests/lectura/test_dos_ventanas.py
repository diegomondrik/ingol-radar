"""Dos ventanas seguidas: la segunda sólo trae lo que la primera no vio, con la memoria releída del disco."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from radar.lectura import leer, por_id
from radar.memoria import Memoria

FIXTURES = Path(__file__).parent / "fixtures"
FUENTE = por_id("openai")  # el recorte de los dos días tiene forma de RSS
DESDE = datetime(2026, 10, 1, tzinfo=timezone.utc)
HASTA = datetime(2026, 10, 14, tzinfo=timezone.utc)
AHORA = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def recorte(nombre: str):
    texto = (FIXTURES / nombre).read_text(encoding="utf-8")
    return lambda url: texto


class DosVentanasTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ruta = Path(self.dir.name) / "generated" / "memoria.json"

    def corrida(self, nombre_recorte: str, cuando: datetime):
        novedades, _ = leer([FUENTE], DESDE, HASTA, recorte(nombre_recorte), ahora=AHORA)
        with Memoria.abrir(self.ruta) as memoria:  # cada corrida relee el archivo del disco
            nuevas = memoria.nuevas(novedades)
            memoria.marcar(nuevas, cuando)
            memoria.guardar()
        return novedades, nuevas

    def test_la_segunda_ventana_trae_solo_lo_que_no_estaba(self):
        vistas_a, nuevas_a = self.corrida("ventana_dia_a.xml", datetime(2026, 10, 8, 8, 0, tzinfo=timezone.utc))
        self.assertEqual([n.titulo for n in nuevas_a], ["Novedad uno", "Novedad dos", "Novedad tres"])
        self.assertEqual(len(nuevas_a), len(vistas_a))  # la primera ventana: todas son nuevas

        en_disco = json.loads(self.ruta.read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(en_disco["vistas"]), ["openai:ej-1", "openai:ej-2", "openai:ej-3"]
        )

        vistas_b, nuevas_b = self.corrida("ventana_dia_b.xml", datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc))
        self.assertEqual(len(vistas_b), 4)  # el día B trajo cuatro, dos repetidas
        self.assertEqual([n.titulo for n in nuevas_b], ["Novedad cinco", "Novedad cuatro"])

    def test_lo_repetido_conserva_su_primera_vez(self):
        self.corrida("ventana_dia_a.xml", datetime(2026, 10, 8, 8, 0, tzinfo=timezone.utc))
        self.corrida("ventana_dia_b.xml", datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc))
        vistas = json.loads(self.ruta.read_text(encoding="utf-8"))["vistas"]
        self.assertEqual(len(vistas), 5)
        self.assertTrue(vistas["openai:ej-2"]["primera_vez"].startswith("2026-10-08"))
        self.assertTrue(vistas["openai:ej-3"]["primera_vez"].startswith("2026-10-08"))
        self.assertEqual(vistas["openai:ej-3"]["titulo"], "Novedad tres")  # el título editado no pisa lo visto
        self.assertTrue(vistas["openai:ej-4"]["primera_vez"].startswith("2026-10-10"))

    def test_repetir_la_misma_ventana_no_trae_nada_nuevo(self):
        self.corrida("ventana_dia_b.xml", datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc))
        _, nuevas = self.corrida("ventana_dia_b.xml", datetime(2026, 10, 11, 8, 0, tzinfo=timezone.utc))
        self.assertEqual(nuevas, [])

    def test_una_corrida_que_no_guarda_no_marca_nada(self):
        novedades, _ = leer([FUENTE], DESDE, HASTA, recorte("ventana_dia_a.xml"), ahora=AHORA)
        with Memoria.abrir(self.ruta) as memoria:
            memoria.marcar(memoria.nuevas(novedades), AHORA)  # sin guardar
        with Memoria.abrir(self.ruta) as memoria:
            self.assertEqual(len(memoria.nuevas(novedades)), 3)


if __name__ == "__main__":
    unittest.main()
