"""Escribir la página y su JSON: nunca pisa, nunca deja nada a medias, y el JSON es UTF-8 con LF."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from radar.pagina import construir, escribir_resumen
from radar.pagina import escritura

from .ayudas import AHORA, analisis, corrida_de, corrida_hostil, novedad


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="radar-escritura-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.carpeta = self.dir / "generated" / "resumenes"  # no existe: se crea
        self.pagina = construir(corrida_de([(novedad(1), analisis("f:1"))]))

    def archivos(self) -> list[str]:
        return sorted(p.name for p in self.carpeta.iterdir()) if self.carpeta.exists() else []


class TestNombres(Base):
    def test_el_nombre_lleva_la_fecha_y_la_hora_y_crea_la_carpeta(self):
        ruta_html, ruta_json = escribir_resumen(self.carpeta, AHORA, self.pagina.html, self.pagina.json)
        self.assertEqual(ruta_html.name, "resumen-2026-10-14_120000.html")
        self.assertEqual(ruta_json.name, "resumen-2026-10-14_120000.json")
        self.assertEqual(self.archivos(), [ruta_html.name, ruta_json.name])

    def test_dos_corridas_en_el_mismo_segundo_no_se_pisan(self):
        primera = escribir_resumen(self.carpeta, AHORA, "<p>primera</p>", '{"n": 1}\n')
        segunda = escribir_resumen(self.carpeta, AHORA, "<p>segunda</p>", '{"n": 2}\n')
        tercera = escribir_resumen(self.carpeta, AHORA, "<p>tercera</p>", '{"n": 3}\n')
        self.assertEqual([p[0].name for p in (primera, segunda, tercera)], [
            "resumen-2026-10-14_120000.html", "resumen-2026-10-14_120000-2.html", "resumen-2026-10-14_120000-3.html",
        ])
        self.assertEqual(segunda[1].name, "resumen-2026-10-14_120000-2.json")
        self.assertEqual(primera[0].read_text(encoding="utf-8"), "<p>primera</p>")  # la primera sigue entera
        self.assertEqual(segunda[1].read_text(encoding="utf-8"), '{"n": 2}\n')
        self.assertEqual(len(self.archivos()), 6)

    def test_un_archivo_que_ya_existe_no_se_pisa_ni_siquiera_su_json(self):
        self.carpeta.mkdir(parents=True)
        (self.carpeta / "resumen-2026-10-14_120000.json").write_text("del owner", encoding="utf-8")
        ruta_html, ruta_json = escribir_resumen(self.carpeta, AHORA, "<p>nueva</p>", "{}\n")
        self.assertEqual((ruta_html.name, ruta_json.name), ("resumen-2026-10-14_120000-2.html", "resumen-2026-10-14_120000-2.json"))
        self.assertEqual((self.carpeta / "resumen-2026-10-14_120000.json").read_text(encoding="utf-8"), "del owner")
        self.assertNotIn("resumen-2026-10-14_120000.html", self.archivos())  # no quedó un html suelto de la prueba

    def test_la_hora_es_utc_aunque_la_corrida_tenga_otra_zona(self):
        from datetime import timezone

        argentina = AHORA.astimezone(timezone(timedelta(hours=-3)))
        ruta_html, _ = escribir_resumen(self.carpeta, argentina, "<p/>", "{}\n")
        self.assertEqual(ruta_html.name, "resumen-2026-10-14_120000.html")

    def test_el_prefijo_de_una_idea(self):
        ruta_html, _ = escribir_resumen(self.carpeta, AHORA, "<p/>", "{}\n", prefijo="idea")
        self.assertEqual(ruta_html.name, "idea-2026-10-14_120000.html")

    def test_se_agotan_los_nombres_con_un_error_claro(self):
        with mock.patch.object(escritura, "MAX_INTENTOS", 2):
            escribir_resumen(self.carpeta, AHORA, "<p/>", "{}\n")
            escribir_resumen(self.carpeta, AHORA, "<p/>", "{}\n")
            antes = self.archivos()
            with self.assertRaises(OSError):
                escribir_resumen(self.carpeta, AHORA, "<p/>", "{}\n")
            self.assertEqual(self.archivos(), antes)


class TestFallas(Base):
    """Si cualquier paso falla no queda nada de la corrida: ni una página sin su JSON ni un archivo vacío."""

    def _falla_el_reemplazo_numero(self, n: int, error: BaseException):
        real = escritura.os.replace
        llamadas = []

        def reemplazar(origen, destino):
            llamadas.append(destino)
            if len(llamadas) == n:
                raise error
            return real(origen, destino)

        return mock.patch.object(escritura.os, "replace", reemplazar)

    def test_falla_al_escribir_el_json(self):
        with self._falla_el_reemplazo_numero(1, OSError("disco lleno")):
            with self.assertRaises(OSError):
                escribir_resumen(self.carpeta, AHORA, self.pagina.html, self.pagina.json)
        self.assertEqual(self.archivos(), [])

    def test_falla_al_escribir_la_pagina_y_el_json_ya_estaba_escrito(self):
        with self._falla_el_reemplazo_numero(2, OSError("disco lleno")):
            with self.assertRaises(OSError):
                escribir_resumen(self.carpeta, AHORA, self.pagina.html, self.pagina.json)
        self.assertEqual(self.archivos(), [])  # tampoco queda el JSON suelto ni un temporal

    def test_ctrl_c_a_mitad_de_la_escritura_tampoco_deja_nada(self):
        with self._falla_el_reemplazo_numero(2, KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                escribir_resumen(self.carpeta, AHORA, self.pagina.html, self.pagina.json)
        self.assertEqual(self.archivos(), [])

    def test_un_texto_que_no_se_puede_codificar_no_deja_nada(self):
        with self.assertRaises(UnicodeEncodeError):
            escribir_resumen(self.carpeta, AHORA, "sustituto suelto \ud800", "{}\n")
        self.assertEqual(self.archivos(), [])

    def test_una_falla_no_toca_lo_que_habia(self):
        previa = escribir_resumen(self.carpeta, AHORA, "<p>previa</p>", '{"previa": true}\n')
        antes = self.archivos()
        with self._falla_el_reemplazo_numero(2, OSError("disco lleno")):
            with self.assertRaises(OSError):
                escribir_resumen(self.carpeta, AHORA, "<p>nueva</p>", "{}\n")
        self.assertEqual(self.archivos(), antes)
        self.assertEqual(previa[0].read_text(encoding="utf-8"), "<p>previa</p>")

    def test_la_carpeta_es_un_archivo(self):
        self.carpeta.parent.mkdir(parents=True)
        self.carpeta.write_text("no soy una carpeta", encoding="utf-8")
        with self.assertRaises(OSError):
            escribir_resumen(self.carpeta, AHORA, "<p/>", "{}\n")
        self.assertEqual(self.carpeta.read_text(encoding="utf-8"), "no soy una carpeta")


class TestFormato(Base):
    def test_el_json_es_utf8_con_lf_y_se_lee_de_vuelta(self):
        pagina = construir(corrida_hostil("línea\r\nacentuada ñ € 😀"))
        _html, ruta_json = escribir_resumen(self.carpeta, AHORA, pagina.html, pagina.json)
        crudo = ruta_json.read_bytes()
        self.assertNotIn(b"\r", crudo)
        self.assertTrue(crudo.endswith(b"}\n"))
        self.assertFalse(crudo.startswith(b"\xef\xbb\xbf"))  # sin marca de orden de bytes
        datos = json.loads(crudo.decode("utf-8"))
        self.assertEqual(datos["version"], 1)
        self.assertIn("€ 😀", crudo.decode("utf-8"))  # UTF-8 de verdad, no escapes

    def test_la_pagina_es_utf8_con_lf(self):
        ruta_html, _ = escribir_resumen(self.carpeta, AHORA, self.pagina.html, self.pagina.json)
        crudo = ruta_html.read_bytes()
        self.assertNotIn(b"\r", crudo)
        self.assertEqual(crudo.decode("utf-8"), self.pagina.html)
        self.assertTrue(crudo.startswith(b"<!doctype html>\n"))

    def test_no_quedan_temporales(self):
        escribir_resumen(self.carpeta, AHORA, self.pagina.html, self.pagina.json)
        self.assertFalse([n for n in self.archivos() if n.endswith(".tmp")])


if __name__ == "__main__":
    unittest.main()
