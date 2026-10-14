"""La línea de comandos: `--afinado`, `--idea`, la salida en disco y los códigos de salida."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from radar.analisis import __main__ as cli
from radar.analisis import prompt
from radar.analisis.errores import ErrorClaude

from .ayudas import CONTEXTO, RunnerFalso, item_valido

AFINADO = {
    "nota": "prueba",
    "novedades": [
        {
            "clave": "t-01",
            "fuente": "una-fuente",
            "titulo": "Un método de revisión",
            "frase": "",
            "fecha": "2026-10-01",
            "enlace": "https://ejemplo.org/a",
            "esperado": "si",
            "aceptable": ["si", "tal_vez"],
            "por_que_esperado": "SECRETO-DEL-AFINADO",
        },
        {
            "clave": "t-02",
            "fuente": "otra",
            "titulo": "Caso de cliente",
            "frase": "Un banco usa Claude.",
            "fecha": "2026-10-02",
            "enlace": "https://ejemplo.org/b",
            "esperado": "no",
            "aceptable": ["no"],
            "por_que_esperado": "SECRETO-DEL-AFINADO",
        },
    ],
}


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.afinado = self.dir / "afinado.json"
        self.escribir_afinado(AFINADO)

    def escribir_afinado(self, datos):
        self.afinado.write_text(json.dumps(datos), encoding="utf-8")

    def correr(self, argv, runner=None, contexto=CONTEXTO):
        salida = io.StringIO()
        runner = runner or RunnerFalso()
        codigo = cli.main(argv, runner=runner, contexto=contexto, salida=salida)
        return codigo, salida.getvalue(), runner

    def test_afinado_escribe_el_resultado_y_resume(self):
        destino = self.dir / "sub" / "salida.json"
        codigo, texto, runner = self.correr(["--afinado", str(self.afinado), "--salida", str(destino)])
        self.assertEqual(codigo, 0)
        datos = json.loads(destino.read_text(encoding="utf-8"))
        self.assertEqual(set(datos["analizadas"]), {"t-01", "t-02"})
        self.assertEqual(datos["sin_analizar"], {})
        self.assertEqual(datos["contadores"]["llamadas"], 1)
        self.assertIn("Analizadas: 2", texto)
        self.assertIn("t-01", texto)
        self.assertEqual(list(destino.parent.glob("*.tmp")), [])

    def test_esperado_y_lo_demas_no_viajan_al_modelo(self):
        _, _, runner = self.correr(["--afinado", str(self.afinado), "--salida", str(self.dir / "s.json")])
        enviado = runner.llamadas[0]["usuario"] + runner.llamadas[0]["sistema"]
        for secreto in ("SECRETO-DEL-AFINADO", "esperado", "aceptable", "por_que_esperado"):
            self.assertNotIn(secreto, enviado)

    def test_el_resumen_compara_con_lo_esperado(self):
        def funcion(items):
            return {
                "analisis": [
                    item_valido(p["clave"], sirve="si" if p["clave"] == "t-01" else "tal_vez", base="titulo_y_frase" if p["frase"] else "titulo")
                    for p in items
                ]
            }

        _, texto, _ = self.correr(["--afinado", str(self.afinado), "--salida", str(self.dir / "s.json")], RunnerFalso(funcion))
        self.assertRegex(texto, r"t-01 .*esperado=si \(ok\)")
        self.assertRegex(texto, r"t-02 .*esperado=no \(DIFIERE\)")

    def test_sin_salida_escribe_en_generated_afinado_de_la_raiz(self):
        self.assertEqual(cli.SALIDA_POR_DEFECTO, prompt.RAIZ / "generated" / "afinado")

    def test_novedad_sin_analizar_da_codigo_2_y_queda_a_la_vista(self):
        runner = RunnerFalso(lambda items: ErrorClaude("claude no terminó en 240 s"))
        codigo, texto, _ = self.correr(["--afinado", str(self.afinado), "--salida", str(self.dir / "s.json")], runner)
        self.assertEqual(codigo, 2)
        self.assertIn("SIN ANALIZAR", texto)
        datos = json.loads((self.dir / "s.json").read_text(encoding="utf-8"))
        self.assertEqual(set(datos["sin_analizar"]), {"t-01", "t-02"})

    def test_corrida_cortada_da_codigo_1(self):
        from radar.analisis.errores import ClaudeNoDisponible

        runner = RunnerFalso(lambda items: ClaudeNoDisponible("no hay claude"))
        codigo, texto, _ = self.correr(["--afinado", str(self.afinado), "--salida", str(self.dir / "s.json")], runner)
        self.assertEqual(codigo, 1)
        self.assertIn("CORRIDA CORTADA", texto)

    def test_contexto_ausente_da_error_claro_codigo_1(self):
        salida = io.StringIO()
        codigo = cli.main(
            ["--afinado", str(self.afinado), "--contexto", str(self.dir / "no-esta.md")], runner=RunnerFalso(), salida=salida
        )
        self.assertEqual(codigo, 1)
        self.assertIn("no-esta.md", salida.getvalue())

    def test_afinado_que_no_existe(self):
        codigo, texto, _ = self.correr(["--afinado", str(self.dir / "nada.json")])
        self.assertEqual(codigo, 1)
        self.assertIn("nada.json", texto)

    def test_afinado_mal_formado(self):
        casos = {
            "json roto": "{",
            "no es objeto": "[1]",
            "sin lista": '{"novedades": 3}',
            "lista vacia": '{"novedades": []}',
            "sin clave": '{"novedades": [{"titulo": "x"}]}',
            "sin titulo": '{"novedades": [{"clave": "a", "titulo": "<b></b>"}]}',
            "fecha rara": '{"novedades": [{"clave": "a", "titulo": "x", "fecha": "ayer"}]}',
        }
        for nombre, crudo in casos.items():
            with self.subTest(nombre):
                self.afinado.write_text(crudo, encoding="utf-8")
                runner = RunnerFalso()
                codigo, texto, _ = self.correr(["--afinado", str(self.afinado)], runner)
                self.assertEqual(codigo, 1, texto)
                self.assertEqual(runner.llamadas, [])

    def test_los_campos_de_novedad_se_limpian_al_cargar(self):
        self.escribir_afinado(
            {
                "novedades": [
                    {
                        "clave": "a",
                        "fuente": "<i>f</i>",
                        "titulo": "<b>Hola</b>‮ mundo",
                        "frase": "x\x00y",
                        "fecha": "2026-10-01",
                        "enlace": "javascript:alert(1)",
                    }
                ]
            }
        )
        novedades, _ = cli.cargar_afinado(self.afinado)
        n = novedades[0]
        self.assertEqual((n.fuente, n.titulo, n.frase, n.enlace), ("f", "Hola mundo", "xy", ""))
        self.assertEqual(n.fecha.date().isoformat(), "2026-10-01")

    def test_afinado_con_marca_de_orden_de_bytes(self):
        self.afinado.write_bytes(b"\xef\xbb\xbf" + json.dumps(AFINADO).encode("utf-8"))
        novedades, esperados = cli.cargar_afinado(self.afinado)
        self.assertEqual([n.clave for n in novedades], ["t-01", "t-02"])
        self.assertEqual(esperados["t-01"]["esperado"], "si")

    def test_idea(self):
        runner = RunnerFalso(lambda items: {"analisis": [item_valido(items[0]["clave"], base="idea_del_owner")]})
        codigo, texto, _ = self.correr(["--idea", "Usar un revisor de otra familia"], runner)
        self.assertEqual(codigo, 0)
        self.assertIn(prompt.CLAVE_IDEA, texto)
        self.assertEqual(runner.llamadas[0]["claves"], [prompt.CLAVE_IDEA])

    def test_idea_con_salida_escribe_el_archivo(self):
        runner = RunnerFalso(lambda items: {"analisis": [item_valido(items[0]["clave"], base="idea_del_owner")]})
        destino = self.dir / "idea.json"
        self.correr(["--idea", "una idea", "--salida", str(destino)], runner)
        self.assertIn(prompt.CLAVE_IDEA, json.loads(destino.read_text(encoding="utf-8"))["analizadas"])

    def test_hay_que_elegir_entre_afinado_e_idea(self):
        for argv in ([], ["--afinado", "a.json", "--idea", "x"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                cli.main(argv, runner=RunnerFalso(), contexto=CONTEXTO, salida=io.StringIO())


if __name__ == "__main__":
    unittest.main()
