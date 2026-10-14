"""La línea de comandos: `--afinado`, `--idea`, la salida en disco y los códigos de salida."""

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

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
        self.assertEqual(runner.llamadas[0]["aliases"], ["n1"])

    def test_idea_con_salida_escribe_el_archivo(self):
        runner = RunnerFalso(lambda items: {"analisis": [item_valido(items[0]["clave"], base="idea_del_owner")]})
        destino = self.dir / "idea.json"
        self.correr(["--idea", "una idea", "--salida", str(destino)], runner)
        self.assertIn(prompt.CLAVE_IDEA, json.loads(destino.read_text(encoding="utf-8"))["analizadas"])

    def test_hay_que_elegir_entre_afinado_e_idea(self):
        for argv in ([], ["--afinado", "a.json", "--idea", "x"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                cli.main(argv, runner=RunnerFalso(), contexto=CONTEXTO, salida=io.StringIO())


class TestCanarios(unittest.TestCase):
    """Nada de lo que trae el archivo de afinado aparte de los campos de la novedad llega al modelo."""

    def test_esperado_aceptable_y_el_resto_no_aparecen_en_ninguna_llamada_ni_en_el_resultado(self):
        datos = {
            "nota": "CANARIO-NOTA-1D4V",
            "novedades": [
                {
                    **n,
                    "esperado": "CANARIO-ESP-3F9Q",
                    "aceptable": ["CANARIO-ACE-7K2X"],
                    "por_que_esperado": "CANARIO-PQE-5M8Z",
                    "otro_campo": "CANARIO-EXTRA-9W6T",
                }
                for n in AFINADO["novedades"]
            ],
        }
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp = Path(tmp)
            (tmp / "a.json").write_text(json.dumps(datos), encoding="utf-8")
            estado = {"n": 0}

            def funcion(items):
                estado["n"] += 1
                if estado["n"] == 1:
                    return ErrorClaude("falla del primer lote: fuerza reintentos")
                return RunnerFalso.todo_bien([{**p, "frase": ""} for p in items])

            runner = RunnerFalso(funcion)
            salida = io.StringIO()
            cli.main(["--afinado", str(tmp / "a.json"), "--salida", str(tmp / "s.json")], runner=runner, contexto=CONTEXTO, salida=salida)
            self.assertGreater(len(runner.llamadas), 1)  # hubo reintentos: también ésos se miran
            for llamada in runner.llamadas:
                enviado = llamada["usuario"] + llamada["sistema"] + json.dumps(llamada["esquema"])
                self.assertNotIn("CANARIO-", enviado)
            self.assertNotIn("CANARIO-", (tmp / "s.json").read_text(encoding="utf-8"))


class TestSalidaPorDefecto(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.afinado = self.dir / "afinado.json"
        self.afinado.write_text(json.dumps(AFINADO), encoding="utf-8")
        self.carpeta = self.dir / "uno" / "dos"  # no existe: se crea
        parche = mock.patch.object(cli, "SALIDA_POR_DEFECTO", self.carpeta)
        parche.start()
        self.addCleanup(parche.stop)

    def correr(self, argv, ahora=None, runner=None):
        salida = io.StringIO()
        kwargs = {"ahora": ahora} if ahora else {}
        codigo = cli.main(argv, runner=runner or RunnerFalso(), contexto=CONTEXTO, salida=salida, **kwargs)
        return codigo, salida.getvalue()

    def test_sin_salida_escribe_un_analisis_valido_en_la_carpeta_por_defecto_aunque_no_exista(self):
        codigo, texto = self.correr(["--afinado", str(self.afinado)])
        self.assertEqual(codigo, 0)
        archivos = list(self.carpeta.glob("analisis-*.json"))
        self.assertEqual(len(archivos), 1)
        datos = json.loads(archivos[0].read_text(encoding="utf-8"))
        self.assertEqual(set(datos["analizadas"]), {"t-01", "t-02"})
        self.assertIn(str(archivos[0]), texto)

    def test_dos_corridas_en_el_mismo_instante_no_se_pisan(self):
        instante = datetime(2026, 10, 14, 12, 0, 0, 123456, tzinfo=timezone.utc)
        self.correr(["--afinado", str(self.afinado)], ahora=lambda: instante)
        primero = sorted(self.carpeta.glob("analisis-*.json"))[0]
        contenido = primero.read_bytes()
        self.correr(["--afinado", str(self.afinado)], ahora=lambda: instante)
        self.correr(["--afinado", str(self.afinado)], ahora=lambda: instante)
        archivos = sorted(self.carpeta.glob("analisis-*.json"))
        self.assertEqual(len(archivos), 3)
        self.assertEqual(primero.read_bytes(), contenido)
        for a in archivos:
            json.loads(a.read_text(encoding="utf-8"))
        self.assertEqual(list(self.carpeta.glob("*.tmp")), [])

    def test_la_marca_de_tiempo_llega_a_los_microsegundos(self):
        instante = datetime(2026, 10, 14, 12, 0, 5, 42, tzinfo=timezone.utc)
        self.correr(["--afinado", str(self.afinado)], ahora=lambda: instante)
        self.assertEqual([a.name for a in self.carpeta.glob("*.json")], ["analisis-20261014T120005000042Z.json"])

    def test_dos_corridas_seguidas_de_verdad_dejan_dos_archivos(self):
        self.correr(["--afinado", str(self.afinado)])
        self.correr(["--afinado", str(self.afinado)])
        self.assertEqual(len(list(self.carpeta.glob("analisis-*.json"))), 2)

    def test_idea_sin_salida_imprime_el_json_a_la_salida_y_no_escribe_nada(self):
        runner = RunnerFalso(
            lambda items: {"analisis": [item_valido(items[0]["clave"], base="idea_del_owner", por_que_sirve_o_no="Ñandú 日本")]}
        )
        codigo, texto = self.correr(["--idea", "Una idea con ñ y 日本"], runner=runner)
        self.assertEqual(codigo, 0)
        datos = json.loads(texto[texto.index("{\n"):])
        self.assertEqual(set(datos["analizadas"]), {prompt.CLAVE_IDEA})
        self.assertIn("Ñandú 日本", texto)  # ensure_ascii=False: se ve tal cual
        self.assertFalse(self.carpeta.exists())


class TestGuardado(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.afinado = self.dir / "afinado.json"
        self.afinado.write_text(json.dumps(AFINADO), encoding="utf-8")
        self.destino = self.dir / "s.json"

    def correr(self, argv, runner=None):
        salida = io.StringIO()
        codigo = cli.main(argv, runner=runner or RunnerFalso(), contexto=CONTEXTO, salida=salida)
        return codigo, salida.getvalue()

    def test_el_archivo_es_utf8_con_lf_y_sin_escapes(self):
        runner = RunnerFalso(
            lambda items: {"analisis": [item_valido(p["clave"], por_que_sirve_o_no="Ñandú 日本 «ok»") for p in items]}
        )
        self.correr(["--afinado", str(self.afinado), "--salida", str(self.destino)], runner)
        crudo = self.destino.read_bytes()
        self.assertNotIn(b"\r", crudo)
        self.assertNotIn(b"\\u", crudo)
        self.assertIn("Ñandú 日本 «ok»".encode("utf-8"), crudo)
        self.assertTrue(crudo.endswith(b"\n"))
        json.loads(crudo.decode("utf-8"))

    def test_si_no_se_puede_guardar_avisa_devuelve_1_y_vuelca_el_json_completo(self):
        # El destino es una carpeta: reemplazarla falla de verdad.
        self.destino.mkdir()
        codigo, texto = self.correr(["--afinado", str(self.afinado), "--salida", str(self.destino)])
        self.assertEqual(codigo, 1)
        self.assertIn("NO SE PUDO GUARDAR", texto)
        datos = json.loads(texto[texto.index("{\n"):])
        self.assertEqual(set(datos["analizadas"]), {"t-01", "t-02"})
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_si_el_guardado_por_defecto_falla_no_queda_un_archivo_vacio(self):
        carpeta = self.dir / "gen"
        with mock.patch.object(cli, "SALIDA_POR_DEFECTO", carpeta), mock.patch.object(
            cli, "_escribir_atomico", side_effect=OSError("disco lleno")
        ):
            codigo, texto = self.correr(["--afinado", str(self.afinado)])
        self.assertEqual(codigo, 1)
        self.assertIn("disco lleno", texto)
        self.assertEqual(list(carpeta.glob("*")), [])
        datos = json.loads(texto[texto.index("{\n"):])
        self.assertIn("t-01", datos["analizadas"])

    def test_una_falla_a_mitad_de_la_escritura_deja_el_destino_previo_intacto_y_sin_temporales(self):
        self.destino.write_text('{"previo": true}\n', encoding="utf-8")
        real = json.dump

        def a_medias(datos, f, **kw):
            f.write('{"analizadas": {"t-0')  # un registro cortado
            f.flush()
            raise OSError("se cortó el disco")

        with mock.patch.object(cli.json, "dump", a_medias):
            with self.assertRaises(OSError):
                cli._escribir_atomico(self.destino, {"x": 1})
        self.assertEqual(self.destino.read_text(encoding="utf-8"), '{"previo": true}\n')
        self.assertEqual(list(self.dir.glob("*.tmp")), [])
        cli._escribir_atomico(self.destino, {"x": 1})  # y después anda con normalidad
        self.assertEqual(json.loads(self.destino.read_text(encoding="utf-8")), {"x": 1})
        self.assertIs(json.dump, real)

    def test_una_falla_en_el_reemplazo_deja_el_destino_previo_intacto_y_sin_temporales(self):
        self.destino.write_text('{"previo": true}\n', encoding="utf-8")
        with mock.patch.object(cli.os, "replace", side_effect=PermissionError("en uso")):
            with self.assertRaises(OSError):
                cli._escribir_atomico(self.destino, {"x": 1})
        self.assertEqual(self.destino.read_text(encoding="utf-8"), '{"previo": true}\n')
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_ctrl_c_durante_el_analisis_igual_guarda_lo_ya_analizado(self):
        estado = {"n": 0}

        def funcion(items):
            estado["n"] += 1
            if estado["n"] == 2:
                return KeyboardInterrupt()
            return RunnerFalso.todo_bien(items)

        afinado = {"novedades": [{**AFINADO["novedades"][0], "clave": f"t-{i:02d}", "frase": ""} for i in range(1, 8)]}
        self.afinado.write_text(json.dumps(afinado), encoding="utf-8")
        codigo, texto = self.correr(["--afinado", str(self.afinado), "--salida", str(self.destino)], RunnerFalso(funcion))
        self.assertEqual(codigo, 1)
        self.assertIn("CORRIDA CORTADA", texto)
        datos = json.loads(self.destino.read_text(encoding="utf-8"))
        self.assertEqual(len(datos["analizadas"]), 5)
        self.assertEqual(len(datos["sin_analizar"]), 2)
        self.assertIn("Ctrl+C", datos["contadores"]["abortada"])


if __name__ == "__main__":
    unittest.main()
