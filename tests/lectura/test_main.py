"""`python -m radar.lectura`: argumentos, salida y que no escribe nada. Con un fetcher falso, sin red."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from radar.lectura import FUENTES, por_id
from radar.lectura.__main__ import main

FIXTURES = Path(__file__).parent / "fixtures"
AHORA = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def fetcher(url):
    if url == por_id("claude-code").url:
        return (FIXTURES / "atom_claude_code.xml").read_text(encoding="utf-8")
    if url == por_id("metr").url:
        return (FIXTURES / "pagina_metr.html").read_text(encoding="utf-8")
    if url == por_id("openai").url:
        return "<html>no es un feed</html>"
    raise OSError("sin red en las pruebas")


def correr(*argv):
    salida = io.StringIO()
    codigo = main(list(argv), fetcher=fetcher, ahora=AHORA, salida=salida)
    return codigo, salida.getvalue()


class MainTest(unittest.TestCase):
    def test_por_defecto_son_los_ultimos_28_dias(self):
        codigo, texto = correr()
        self.assertEqual(codigo, 0)
        self.assertIn("Ventana: 2026-09-16 a 2026-10-14 (UTC)", texto)

    def test_muestra_la_cobertura_de_las_14_fuentes_y_los_titulos(self):
        _, texto = correr("--desde", "2026-10-01", "--hasta", "2026-10-14")
        for fuente in FUENTES:
            self.assertIn(fuente.id, texto)
        self.assertIn("claude-code", texto)
        self.assertRegex(texto, r"claude-code\s+ok\s+en_fuente=3 en_ventana=2 sin_fecha=0")
        self.assertRegex(texto, r"openai\s+error")
        self.assertRegex(texto, r"anthropic-noticias\s+error")
        self.assertIn("v2.1.296", texto)
        self.assertNotIn("v2.1.290", texto)  # fuera de la ventana
        self.assertIn("AI systems could cover up misbehavior", texto)
        self.assertIn("cubre_desde=2026-09-20", texto)

    def test_hasta_incluye_el_dia_entero(self):
        _, texto = correr("--desde", "2026-10-09", "--hasta", "2026-10-09")
        self.assertIn("v2.1.296", texto)  # 2026-10-09T18:00Z

    def test_json(self):
        codigo, texto = correr("--json", "--desde", "2026-10-01")
        datos = json.loads(texto)
        self.assertEqual(codigo, 0)
        self.assertEqual(set(datos), {"ventana", "cobertura", "novedades"})
        self.assertEqual(len(datos["cobertura"]), 14)
        self.assertEqual(datos["cobertura"]["claude-code"]["estado"], "ok")
        self.assertEqual(datos["cobertura"]["openai"]["estado"], "error")
        self.assertEqual({n["fuente"] for n in datos["novedades"]}, {"claude-code", "metr"})

    def test_argumentos_invalidos_salen_con_error_de_uso(self):
        for argv in (
            ["--desde", "ayer"],
            ["--desde", "2026-13-01"],
            ["--desde", "2026-02-30"],
            ["--desde", "2026-10-09\n"],
            ["--desde", "２０２６-10-09"],
            ["--desde", "2026-10-14", "--hasta", "2026-10-01"],
            ["--no-existe"],
        ):
            with self.subTest(argv):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
                    correr(*argv)
                self.assertEqual(ctx.exception.code, 2)

    def test_si_todas_las_fuentes_fallan_el_codigo_de_salida_no_es_cero(self):
        salida = io.StringIO()

        def caido(url):
            raise OSError("sin red")

        self.assertEqual(main([], fetcher=caido, ahora=AHORA, salida=salida), 1)

    def test_no_escribe_archivos_ni_toca_la_memoria(self):
        with tempfile.TemporaryDirectory() as carpeta:
            anterior = os.getcwd()
            os.chdir(carpeta)
            try:
                correr()
                correr("--json")
                self.assertEqual(list(Path(carpeta).iterdir()), [])  # ni generated/ ni memoria.json
            finally:
                os.chdir(anterior)


if __name__ == "__main__":
    unittest.main()
