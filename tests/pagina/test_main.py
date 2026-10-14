"""El comando `python -m radar`: el orden de los pasos, lo que marca la memoria y los códigos de salida.

Todo se inyecta: fuentes y fetcher falsos, un runner que hace de claude, la hora, la salida, la memoria y la carpeta.
Nada sale a internet ni gasta cuota.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import radar.__main__ as modulo
from radar.__main__ import main
from radar.analisis import cliente
from radar.analisis.errores import ClaudeNoDisponible
from radar.analisis.modelos import InfoArticulo, Resultado
from radar.lectura.fuentes import Fuente
from radar.lectura.modelos import ErrorDescarga, Extraccion
from radar.memoria import Memoria

from tests.analisis.ayudas import CONTEXTO, RunnerFalso, item_valido, novedad, real

from .ayudas import analisis as un_analisis

AHORA = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)


def fuente(id_: str, nombre: str, novedades=()) -> Fuente:
    return Fuente(id_, nombre, f"https://{id_}.ejemplo.org/feed", "atom", lambda _f, _texto, _ahora: Extraccion(list(novedades)))


def runner_de(veredictos: dict[str, str] | None = None, solo: set[str] | None = None) -> RunnerFalso:
    """Un claude falso: `veredictos` dice qué sirve cada clave (por defecto «si»); con `solo`, sólo analiza esas."""
    veredictos = veredictos or {}

    def responder(items):
        analisis = []
        for p in items:
            clave = real(p)
            if solo is not None and clave not in solo:
                continue
            sirve = veredictos.get(clave, "si")
            base = "idea_del_owner" if p.get("tipo") == "idea_del_owner" else "titulo"
            analisis.append(item_valido(p["clave"], sirve=sirve, ruido=sirve == "no", base=base))
        return {"analisis": analisis}

    return RunnerFalso(responder)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="radar-main-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.temporal = self.dir / "temporal"  # la «carpeta temporal del sistema» de estas pruebas: nunca la de verdad
        patcher = mock.patch.object(modulo.tempfile, "gettempdir", return_value=str(self.temporal))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.memoria = self.dir / "memoria.json"
        self.carpeta = self.dir / "resumenes"
        self.salida = io.StringIO()
        self.pedidos: list[str] = []
        self.novedades_a = [novedad(n, fuente="src-a") for n in (1, 2, 3)]
        self.novedades_b = [novedad(n, fuente="src-b") for n in (4, 5)]
        self.fuentes = (
            fuente("src-a", "Fuente A", self.novedades_a),
            fuente("src-b", "Fuente B", self.novedades_b),
        )

    def fetcher(self, url, **_extra):
        self.pedidos.append(url)
        return "<feed/>"

    def correr(self, *argv, runner=None, fuentes=None, **cambios) -> int:
        parametros = dict(
            runner=runner or runner_de(),
            contexto=CONTEXTO,
            salida=self.salida,
            ahora=lambda: AHORA,
            fetcher=self.fetcher,
            fuentes=fuentes if fuentes is not None else self.fuentes,
            ruta_memoria=self.memoria,
            carpeta=self.carpeta,
        )
        parametros.update(cambios)
        return main(list(argv), **parametros)

    def texto(self) -> str:
        return self.salida.getvalue()

    def paginas(self) -> list[Path]:
        return sorted(self.carpeta.glob("*.html")) if self.carpeta.exists() else []

    def jsons(self) -> list[Path]:
        return sorted(p for p in self.carpeta.glob("*.json") if not p.name.startswith("volcado-")) if self.carpeta.exists() else []

    def volcados(self) -> list[Path]:
        return sorted(self.carpeta.glob("volcado-*.json")) if self.carpeta.exists() else []

    def claves_de_la_memoria(self) -> set[str]:
        if not self.memoria.exists():
            return set()
        return set(json.loads(self.memoria.read_text(encoding="utf-8"))["vistas"])

    def datos_del_resumen(self) -> dict:
        (ruta,) = self.jsons()
        return json.loads(ruta.read_text(encoding="utf-8"))


class TestFlujoNormal(Base):
    def test_una_corrida_completa(self):
        codigo = self.correr("--sin-articulos")
        self.assertEqual(codigo, 0, self.texto())
        (pagina,) = self.paginas()
        self.assertEqual(pagina.name, "resumen-2026-10-14_120000.html")
        self.assertEqual(self.claves_de_la_memoria(), {"f:1", "f:2", "f:3", "f:4", "f:5"})
        datos = self.datos_del_resumen()
        self.assertEqual(len(datos["novedades"]), 5)
        self.assertEqual(datos["conteos"]["leidas"], 5)
        self.assertEqual(datos["conteos"]["nuevas"], 5)
        self.assertTrue(datos["sin_articulos"])
        self.assertEqual(sorted(datos["cobertura"]), ["src-a", "src-b"])
        self.assertEqual(datos["ventana"]["hasta"], AHORA.isoformat())

    def test_imprime_la_ruta_la_uri_y_los_conteos(self):
        self.correr("--sin-articulos", runner=runner_de({"f:1": "tal_vez", "f:2": "no", "f:3": "no"}))
        texto = self.texto()
        (pagina,) = self.paginas()
        (datos,) = self.jsons()
        self.assertIn(str(pagina), texto)
        self.assertIn(pagina.resolve().as_uri(), texto)
        self.assertIn("file:///", texto)
        self.assertIn(str(datos), texto)
        self.assertIn("Leídas: 5  nuevas: 5  sirven: 2  tal vez: 1  ruido: 2  sin analizar: 0", texto)

    def test_se_lee_cada_fuente_una_vez_con_la_ventana_pedida(self):
        self.correr("--sin-articulos", "--desde", "2026-09-01", "--hasta", "2026-09-30")
        self.assertEqual(sorted(self.pedidos), ["https://src-a.ejemplo.org/feed", "https://src-b.ejemplo.org/feed"])
        ventana = self.datos_del_resumen()["ventana"]
        self.assertEqual(ventana["desde"], "2026-09-01T00:00:00+00:00")
        self.assertTrue(ventana["hasta"].startswith("2026-09-30T23:59:59"))

    def _lector_pasado_a_analizar(self, *argv):
        with mock.patch.object(modulo, "analizar", wraps=modulo.analizar) as analizar:
            self.correr(*argv)
        return analizar.call_args.kwargs["lector_articulo"]

    def test_sin_articulos_no_hay_segunda_pasada(self):
        self.assertIsNone(self._lector_pasado_a_analizar("--sin-articulos"))

    def test_por_defecto_se_pasa_el_lector_de_articulos_con_el_fetcher(self):
        lector = self._lector_pasado_a_analizar()
        self.assertTrue(callable(lector))
        with mock.patch.object(modulo, "leer_articulo") as leer_articulo:
            lector(self.novedades_a[0])
        leer_articulo.assert_called_once_with(self.novedades_a[0], self.fetcher)

    def test_dos_corridas_seguidas_no_repiten_nada(self):
        self.assertEqual(self.correr("--sin-articulos"), 0)
        segundo = runner_de()
        self.assertEqual(self.correr("--sin-articulos", runner=segundo), 0)
        self.assertEqual(segundo.llamadas, [])  # todo lo leído ya estaba visto: claude no se llama
        self.assertEqual(  # y la página sale igual, con nombre nuevo (-2)
            {p.name for p in self.paginas()}, {"resumen-2026-10-14_120000.html", "resumen-2026-10-14_120000-2.html"}
        )


class TestOrdenDeLosPasos(Base):
    def test_la_memoria_se_marca_solo_cuando_la_pagina_y_el_json_ya_existen_en_disco(self):
        visto = {}
        marcar = Memoria.marcar
        guardar = Memoria.guardar

        def marcar_viendo(memoria, novedades, cuando):
            visto["marcar"] = (len(self.paginas()), len(self.jsons()), self.memoria.exists())
            return marcar(memoria, novedades, cuando)

        def guardar_viendo(memoria, ahora=None):
            visto["guardar"] = (len(self.paginas()), len(self.jsons()))
            return guardar(memoria, ahora)

        with mock.patch.object(Memoria, "marcar", marcar_viendo), mock.patch.object(Memoria, "guardar", guardar_viendo):
            self.assertEqual(self.correr("--sin-articulos"), 0)
        self.assertEqual(visto["marcar"], (1, 1, False))  # página y JSON escritos; la memoria todavía no existe en disco
        self.assertEqual(visto["guardar"], (1, 1))

    def test_si_escribir_la_pagina_falla_no_se_marca_nada_y_se_vuelca_el_resultado(self):
        self.memoria.write_text(json.dumps({"version": 1, "vistas": {}}), encoding="utf-8")
        antes = self.memoria.read_bytes()
        self.carpeta.parent.mkdir(parents=True, exist_ok=True)
        self.carpeta.write_text("soy un archivo, no una carpeta", encoding="utf-8")
        codigo = self.correr("--sin-articulos")
        self.assertEqual(codigo, 1)
        self.assertEqual(self.memoria.read_bytes(), antes)  # la memoria quedó igual, byte por byte
        self.assertIn("NO SE PUDO ESCRIBIR", self.texto())
        # La carpeta de destino no sirve: el volcado va a la carpeta temporal, a un archivo, y se dice dónde.
        (archivo,) = self.temporal.glob("volcado-*.json")
        self.assertIn(str(archivo), self.texto())
        self.assertNotIn('"analizadas"', self.texto())  # no hace falta ensuciar la pantalla
        volcado = json.loads(archivo.read_text(encoding="utf-8"))
        self.assertEqual(sorted(volcado["analizadas"]), ["f:1", "f:2", "f:3", "f:4", "f:5"])
        self.assertEqual(len(volcado["novedades"]), 5)  # con título y enlace: sin eso el volcado no sirve para nada
        self.assertIn("motivo", volcado)

    def test_si_falla_el_reemplazo_de_la_pagina_no_queda_ningun_archivo_ni_se_marca(self):
        real_replace = os.replace
        llamadas = []

        def reemplazar(origen, destino):
            if str(destino).endswith(".html"):
                raise OSError("disco lleno")
            llamadas.append(destino)
            return real_replace(origen, destino)

        with mock.patch("radar.pagina.escritura.os.replace", reemplazar):
            codigo = self.correr("--sin-articulos")
        self.assertEqual(codigo, 1)
        self.assertEqual(self.paginas(), [])
        self.assertEqual(self.jsons(), [])  # ni el JSON que ya estaba escrito
        self.assertEqual([p.name for p in self.carpeta.iterdir()], [self.volcados()[0].name])  # sólo el volcado
        self.assertTrue(llamadas)  # el JSON sí se escribió antes de fallar la página
        self.assertEqual(self.claves_de_la_memoria(), set())
        self.assertIn("NO SE PUDO ESCRIBIR", self.texto())

    def test_si_la_memoria_no_se_puede_guardar_se_dice_y_sale_1(self):
        with mock.patch.object(Memoria, "guardar", side_effect=OSError("sin permiso")):
            codigo = self.correr("--sin-articulos")
        self.assertEqual(codigo, 1)
        self.assertIn("NO SE PUDO GUARDAR la memoria", self.texto())
        self.assertEqual(len(self.paginas()), 1)  # la página sí quedó
        self.assertEqual(self.claves_de_la_memoria(), set())


class TestQueSeMarca(Base):
    def test_solo_lo_analizado_se_marca(self):
        runner = runner_de(solo={"f:1", "f:2", "f:4"})
        codigo = self.correr("--sin-articulos", runner=runner)
        self.assertEqual(codigo, 2)
        self.assertEqual(self.claves_de_la_memoria(), {"f:1", "f:2", "f:4"})
        datos = self.datos_del_resumen()
        self.assertEqual({x["clave"] for x in datos["sin_analizar"]}, {"f:3", "f:5"})
        self.assertEqual(datos["conteos"]["sin_analizar"], 2)
        self.assertIn("sin analizar: 2", self.texto())
        # La próxima corrida vuelve a intentar sólo las que faltaron.
        segundo = runner_de()
        self.assertEqual(self.correr("--sin-articulos", runner=segundo), 0)
        self.assertEqual(sorted(c for llamada in segundo.llamadas for c in llamada["claves"]), ["f:3", "f:5"])
        self.assertEqual(self.claves_de_la_memoria(), {"f:1", "f:2", "f:3", "f:4", "f:5"})

    def test_el_ruido_analizado_tambien_se_marca(self):
        self.correr("--sin-articulos", runner=runner_de({"f:1": "no", "f:2": "no", "f:3": "no", "f:4": "no", "f:5": "no"}))
        self.assertEqual(len(self.claves_de_la_memoria()), 5)

    def test_corrida_cortada_escribe_la_pagina_con_su_aviso_y_marca_solo_lo_analizado(self):
        for n in range(6, 8):  # 7 novedades: dos lotes (5 + 2); el segundo se corta
            self.novedades_b.append(novedad(n, fuente="src-b"))
        self.fuentes = (fuente("src-a", "Fuente A", self.novedades_a), fuente("src-b", "Fuente B", self.novedades_b))
        base = runner_de()
        llamadas = []

        def cortar_en_el_segundo(items):
            llamadas.append(items)
            if len(llamadas) >= 2:
                raise KeyboardInterrupt
            return base.funcion(items)

        codigo = self.correr("--sin-articulos", runner=RunnerFalso(cortar_en_el_segundo))
        self.assertEqual(codigo, 1)
        (pagina,) = self.paginas()
        self.assertIn("La corrida se cortó antes de terminar", pagina.read_text(encoding="utf-8"))
        self.assertEqual(len(self.claves_de_la_memoria()), 5)
        self.assertEqual(len(self.datos_del_resumen()["sin_analizar"]), 2)
        self.assertIn("LA CORRIDA SE CORTÓ", self.texto())

    def test_claude_que_no_esta_corta_la_corrida_sin_marcar_nada(self):
        def sin_claude(_items):
            raise ClaudeNoDisponible("no encuentro claude")

        codigo = self.correr("--sin-articulos", runner=RunnerFalso(sin_claude))
        self.assertEqual(codigo, 1)
        self.assertEqual(self.claves_de_la_memoria(), set())
        self.assertEqual(len(self.paginas()), 1)
        self.assertEqual(len(self.datos_del_resumen()["sin_analizar"]), 5)


class TestCorteAntesDeGastar(Base):
    def test_memoria_ocupada_no_baja_nada(self):
        with Memoria.abrir(self.memoria):
            codigo = self.correr("--sin-articulos")
        self.assertEqual(codigo, 1)
        self.assertEqual(self.pedidos, [])  # el fetcher no se llamó
        self.assertIn("otra corrida", self.texto())
        self.assertEqual(self.paginas(), [])

    def test_memoria_corrupta_no_baja_nada(self):
        self.memoria.write_text("{esto no es json", encoding="utf-8")
        codigo = self.correr("--sin-articulos")
        self.assertEqual(codigo, 1)
        self.assertEqual(self.pedidos, [])
        self.assertIn("corrupta", self.texto())
        self.assertEqual(self.memoria.read_text(encoding="utf-8"), "{esto no es json")  # no se pisa

    def test_sin_contexto_no_baja_nada(self):
        codigo = self.correr("--sin-articulos", "--contexto", str(self.dir / "no-existe.md"), contexto=None)
        self.assertEqual(codigo, 1)
        self.assertEqual(self.pedidos, [])
        self.assertIn("falta el contexto", self.texto())
        self.assertEqual(self.claves_de_la_memoria(), set())
        self.assertEqual(self.paginas(), [])

    def test_clave_de_api_en_el_entorno_corta_antes_de_leer_nada(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-de-prueba"}):
            codigo = self.correr("--sin-articulos", runner=cliente.ejecutar)
        self.assertEqual(codigo, 1)
        self.assertEqual(self.pedidos, [])
        self.assertIn("ANTHROPIC_API_KEY", self.texto())
        self.assertEqual(self.claves_de_la_memoria(), set())
        self.assertEqual(self.paginas(), [])

    def test_claude_que_no_esta_instalado_corta_antes_de_leer_nada(self):
        with mock.patch.object(cliente, "exigir_suscripcion"), mock.patch.object(
            cliente, "localizar_claude", side_effect=ClaudeNoDisponible("no encuentro claude")
        ):
            codigo = self.correr("--sin-articulos", runner=cliente.ejecutar)
        self.assertEqual(codigo, 1)
        self.assertEqual(self.pedidos, [])
        self.assertIn("no encuentro claude", self.texto())

    def test_ctrl_c_durante_la_lectura_no_marca_nada(self):
        def fetcher_que_se_corta(url, **_extra):
            raise KeyboardInterrupt

        codigo = self.correr("--sin-articulos", fetcher=fetcher_que_se_corta)
        self.assertEqual(codigo, 1)
        self.assertIn("Interrumpido", self.texto())
        self.assertEqual(self.claves_de_la_memoria(), set())
        self.assertEqual(self.paginas(), [])
        # y la memoria quedó libre: otra corrida puede abrirla
        with Memoria.abrir(self.memoria):
            pass

    def test_una_ventana_al_reves_es_un_error_de_argumentos(self):
        with contextlib.redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit) as salida:
            self.correr("--desde", "2026-10-10", "--hasta", "2026-10-01")
        self.assertEqual(salida.exception.code, 2)
        self.assertIn("--desde es posterior a --hasta", error.getvalue())
        self.assertEqual(self.pedidos, [])

    def test_una_fecha_que_no_es_fecha_es_un_error_de_argumentos(self):
        for mala in ("2026-02-30", "ayer", "2026-1-1", "2026-10-01x", "2026-10-01\n"):
            with self.subTest(fecha=mala), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as salida:
                self.correr("--desde", mala)
            self.assertEqual(salida.exception.code, 2)


class TestCeroNuevas(Base):
    def test_sin_novedades_nuevas_la_pagina_sale_y_claude_no_se_llama(self):
        self.memoria.write_text(
            json.dumps(
                {
                    "version": 1,
                    "vistas": {
                        f"f:{n}": {"fuente": "x", "titulo": "t", "enlace": "https://x.org/", "primera_vez": AHORA.isoformat()}
                        for n in range(1, 6)
                    },
                }
            ),
            encoding="utf-8",
        )
        runner = runner_de()
        with mock.patch.object(modulo, "analizar", wraps=modulo.analizar) as analizar:
            codigo = self.correr(runner=runner)
        self.assertEqual(codigo, 0)
        self.assertEqual(runner.llamadas, [])
        analizar.assert_not_called()  # ni siquiera se entra al análisis
        (pagina,) = self.paginas()
        self.assertIn("ya se había visto", pagina.read_text(encoding="utf-8"))
        self.assertEqual(self.datos_del_resumen()["conteos"]["nuevas"], 0)

    def test_si_ninguna_fuente_trae_nada_la_pagina_lo_dice_y_sale_0(self):
        runner = runner_de()
        codigo = self.correr("--sin-articulos", runner=runner, fuentes=(fuente("src-a", "Fuente A"), fuente("src-b", "Fuente B")))
        self.assertEqual(codigo, 0)
        self.assertEqual(runner.llamadas, [])
        (pagina,) = self.paginas()
        html = pagina.read_text(encoding="utf-8")
        self.assertIn("No trajo nada", html)  # las dos fuentes vacías están marcadas
        self.assertIn("Fuentes que no trajeron nada o fallaron: Fuente A, Fuente B", self.texto())

    def test_si_todas_las_fuentes_fallan_sale_1_y_no_marca_nada(self):
        def fetcher_roto(url, **_extra):
            raise ErrorDescarga("sin conexión")

        runner = runner_de()
        codigo = self.correr("--sin-articulos", runner=runner, fetcher=fetcher_roto)
        self.assertEqual(codigo, 1)
        self.assertEqual(runner.llamadas, [])
        self.assertIn("No se pudo leer ninguna fuente", self.paginas()[0].read_text(encoding="utf-8"))
        self.assertEqual(self.claves_de_la_memoria(), set())

    def test_si_una_fuente_falla_las_otras_siguen(self):
        def fetcher_a_medias(url, **_extra):
            if "src-a" in url:
                raise ErrorDescarga("sin conexión")
            return "<feed/>"

        codigo = self.correr("--sin-articulos", fetcher=fetcher_a_medias)
        self.assertEqual(codigo, 0)
        self.assertEqual(self.claves_de_la_memoria(), {"f:4", "f:5"})
        self.assertEqual(self.datos_del_resumen()["cobertura"]["src-a"]["estado"], "error")


class TestIdea(Base):
    def test_la_idea_escribe_su_pagina_y_no_crea_la_memoria(self):
        runner = runner_de()
        codigo = self.correr("--idea", "Que INGOL lea los cambios de los proveedores.", runner=runner)
        self.assertEqual(codigo, 0, self.texto())
        self.assertFalse(self.memoria.exists())
        self.assertFalse(self.memoria.with_name(self.memoria.name + ".lock").exists())  # ni siquiera su candado
        self.assertEqual(self.pedidos, [])  # y no lee ninguna fuente
        (pagina,) = self.paginas()
        self.assertTrue(pagina.name.startswith("idea-2026-10-14_120000"))
        datos = self.datos_del_resumen()
        self.assertEqual(datos["idea"], "Que INGOL lea los cambios de los proveedores.")
        self.assertEqual(len(datos["novedades"]), 1)
        self.assertEqual(len(runner.llamadas), 1)
        self.assertIn(str(pagina), self.texto())

    def test_la_idea_no_toca_una_memoria_que_ya_existe(self):
        self.memoria.write_text(json.dumps({"version": 1, "vistas": {}}), encoding="utf-8")
        antes, momento = self.memoria.read_bytes(), self.memoria.stat().st_mtime_ns
        self.assertEqual(self.correr("--idea", "una idea"), 0)
        self.assertEqual(self.memoria.read_bytes(), antes)
        self.assertEqual(self.memoria.stat().st_mtime_ns, momento)

    def test_la_idea_funciona_aunque_otra_corrida_tenga_la_memoria(self):
        with Memoria.abrir(self.memoria):
            self.assertEqual(self.correr("--idea", "una idea"), 0, self.texto())

    def test_la_idea_con_desde_hasta_o_sin_articulos_es_un_error(self):
        for extra in (("--desde", "2026-10-01"), ("--hasta", "2026-10-01"), ("--sin-articulos",)):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit) as salida:
                self.correr("--idea", "una idea", *extra)
            self.assertEqual(salida.exception.code, 2)
            self.assertIn("--idea", error.getvalue())
        self.assertEqual(self.paginas(), [])

    def test_una_idea_vacia_es_un_error_claro(self):
        runner = runner_de()
        self.assertEqual(self.correr("--idea", "   ", runner=runner), 1)
        self.assertIn("la idea está vacía", self.texto())
        self.assertEqual(runner.llamadas, [])

    def test_una_idea_que_no_se_pudo_analizar_sale_2(self):
        runner = runner_de(solo=set())  # el claude falso no devuelve nada
        self.assertEqual(self.correr("--idea", "una idea", runner=runner), 2)
        self.assertEqual(self.datos_del_resumen()["conteos"]["sin_analizar"], 1)

    def test_si_escribir_la_pagina_de_la_idea_falla_se_vuelca(self):
        self.carpeta.parent.mkdir(parents=True, exist_ok=True)
        self.carpeta.write_text("archivo", encoding="utf-8")
        self.assertEqual(self.correr("--idea", "una idea"), 1)
        self.assertIn("NO SE PUDO ESCRIBIR", self.texto())
        self.assertFalse(self.memoria.exists())
        self.assertEqual(len(list(self.temporal.glob("volcado-*.json"))), 1)


class TestCarpeta(Base):
    def test_carpeta_por_argumento(self):
        otra = self.dir / "otra" / "carpeta"
        self.assertEqual(self.correr("--sin-articulos", "--carpeta", str(otra)), 0)
        self.assertEqual(len(list(otra.glob("*.html"))), 1)
        self.assertEqual(self.paginas(), [])

    def test_la_carpeta_por_defecto_es_generated_resumenes_del_proyecto(self):
        CARPETA_POR_DEFECTO = modulo.CARPETA_POR_DEFECTO
        self.assertEqual(CARPETA_POR_DEFECTO.parts[-2:], ("generated", "resumenes"))
        self.assertTrue((CARPETA_POR_DEFECTO.parents[1] / "radar" / "__main__.py").is_file())  # anclada a la raíz del proyecto



class SalidaRota:
    """Una salida que falla al escribir: tubería cerrada, consola que no codifica."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.intentos = 0

    def write(self, _texto):
        self.intentos += 1
        raise self.error


class TestSalidaRotaYOrdenDeInformar(Base):
    def test_una_salida_rota_no_impide_marcar_ni_guardar_la_memoria(self):
        errores = (OSError("tubería cerrada"), UnicodeEncodeError("charmap", "á", 0, 1, "no se puede codificar"), ValueError("I/O on closed file"))
        for i, error in enumerate(errores):
            with self.subTest(error=type(error).__name__):
                memoria = self.dir / f"memoria-{i}.json"
                rota = SalidaRota(error)
                codigo = self.correr("--sin-articulos", salida=rota, ruta_memoria=memoria, carpeta=self.dir / f"carpeta-{i}")
                self.assertEqual(codigo, 0)
                self.assertGreater(rota.intentos, 0)  # sí se intentó informar
                vistas = set(json.loads(memoria.read_text(encoding="utf-8"))["vistas"])
                self.assertEqual(vistas, {"f:1", "f:2", "f:3", "f:4", "f:5"})
                self.assertEqual(len(list((self.dir / f"carpeta-{i}").glob("*.html"))), 1)

    def test_la_memoria_queda_marcada_y_guardada_antes_de_informar(self):
        visto = {}

        def informar_viendo(*_args, **_kwargs):
            visto["claves"] = self.claves_de_la_memoria()  # lo que hay EN DISCO cuando se informa

        with mock.patch.object(modulo, "_informar", informar_viendo):
            self.assertEqual(self.correr("--sin-articulos"), 0)
        self.assertEqual(visto["claves"], {"f:1", "f:2", "f:3", "f:4", "f:5"})

    def test_con_la_salida_rota_el_mensaje_de_memoria_sin_guardar_tampoco_tira_la_corrida(self):
        with mock.patch.object(Memoria, "guardar", side_effect=OSError("sin permiso")):
            codigo = self.correr("--sin-articulos", salida=SalidaRota(OSError("tubería cerrada")))
        self.assertEqual(codigo, 1)

    def test_marca_con_la_hora_de_la_corrida_y_guarda_con_ella(self):
        visto = {}
        marcar, guardar = Memoria.marcar, Memoria.guardar

        def marcar_viendo(memoria, novedades, cuando):
            visto["marcar"] = cuando
            return marcar(memoria, novedades, cuando)

        def guardar_viendo(memoria, ahora=None):
            visto["guardar"] = ahora
            return guardar(memoria, ahora)

        with mock.patch.object(Memoria, "marcar", marcar_viendo), mock.patch.object(Memoria, "guardar", guardar_viendo):
            self.correr("--sin-articulos")
        self.assertEqual(visto, {"marcar": AHORA, "guardar": AHORA})
        vistas = json.loads(self.memoria.read_text(encoding="utf-8"))["vistas"]
        self.assertEqual({v["primera_vez"] for v in vistas.values()}, {AHORA.isoformat()})

    def test_memoria_sin_guardar_dice_y_sale_1_con_cada_error_propio(self):
        from radar.memoria import MemoriaDemasiadoGrande

        for i, error in enumerate((MemoriaDemasiadoGrande("no cabe"), OSError("sin permiso"))):
            with self.subTest(error=type(error).__name__):
                self.salida = io.StringIO()
                carpeta = self.dir / f"carpeta-{i}"
                with mock.patch.object(Memoria, "guardar", side_effect=error):
                    codigo = self.correr("--sin-articulos", carpeta=carpeta, ruta_memoria=self.dir / f"memoria-{i}.json")
                self.assertEqual(codigo, 1)
                self.assertIn("NO SE PUDO GUARDAR la memoria", self.texto())
                self.assertIn("volver a salir como nuevas", self.texto())
                self.assertEqual(len(list(carpeta.glob("*.html"))), 1)

    def test_una_corrida_sin_nada_analizado_no_crea_el_archivo_de_memoria(self):
        codigo = self.correr("--sin-articulos", runner=runner_de(solo=set()))
        self.assertEqual(codigo, 2)
        self.assertFalse(self.memoria.exists())
        self.assertEqual(len(self.paginas()), 1)


class TestVolcadoDeEmergencia(Base):
    def _falla_el_reemplazo_de_la_pagina(self, error: BaseException):
        real_replace = os.replace

        def reemplazar(origen, destino):
            if str(destino).endswith(".html"):
                raise error
            return real_replace(origen, destino)

        return mock.patch("radar.pagina.escritura.os.replace", reemplazar)

    def test_ctrl_c_al_reemplazar_la_pagina_vuelca_sale_1_y_no_marca(self):
        with self._falla_el_reemplazo_de_la_pagina(KeyboardInterrupt()):
            codigo = self.correr("--sin-articulos")
        self.assertEqual(codigo, 1)
        self.assertIn("NO SE PUDO ESCRIBIR la página (KeyboardInterrupt", self.texto())
        self.assertEqual(self.paginas(), [])
        self.assertEqual(self.jsons(), [])
        (volcado,) = self.volcados()
        self.assertEqual(sorted(json.loads(volcado.read_text(encoding="utf-8"))["analizadas"]), ["f:1", "f:2", "f:3", "f:4", "f:5"])
        self.assertEqual(self.claves_de_la_memoria(), set())

    def test_si_falla_construir_tambien_se_vuelca(self):
        with mock.patch.object(modulo, "construir", side_effect=ValueError("un error de la página")):
            codigo = self.correr("--sin-articulos")
        self.assertEqual(codigo, 1)
        self.assertIn("NO SE PUDO ESCRIBIR la página (ValueError: un error de la página)", self.texto())
        self.assertEqual(len(self.volcados()), 1)
        self.assertEqual(self.claves_de_la_memoria(), set())

    def test_el_volcado_va_a_la_carpeta_de_destino_y_se_dice_donde(self):
        with self._falla_el_reemplazo_de_la_pagina(OSError("disco lleno")):
            self.correr("--sin-articulos")
        (volcado,) = self.volcados()
        self.assertIn(f"El resultado completo quedó guardado en {volcado}", self.texto())
        self.assertEqual(list(self.temporal.glob("*")) if self.temporal.exists() else [], [])

    def test_si_la_carpeta_no_sirve_va_a_la_temporal_del_sistema(self):
        self.carpeta.parent.mkdir(parents=True, exist_ok=True)
        self.carpeta.write_text("no soy una carpeta", encoding="utf-8")
        self.correr("--sin-articulos")
        (volcado,) = self.temporal.glob("volcado-*.json")
        self.assertIn(str(volcado), self.texto())

    def test_si_no_se_puede_guardar_en_ningun_lado_va_a_la_pantalla(self):
        with self._falla_el_reemplazo_de_la_pagina(OSError("disco lleno")), mock.patch.object(
            modulo, "escribir_volcado", side_effect=OSError("tampoco acá")
        ):
            codigo = self.correr("--sin-articulos")
        self.assertEqual(codigo, 1)
        texto = self.texto()
        self.assertIn("No se pudo guardar el volcado en un archivo (OSError: tampoco acá); va completo acá abajo.", texto)
        volcado = json.loads(texto[texto.index("{"):])
        self.assertEqual(len(volcado["novedades"]), 5)
        self.assertEqual(self.volcados(), [])

    def test_un_volcado_que_falla_nunca_tira_la_corrida(self):
        with mock.patch.object(modulo, "construir", side_effect=RuntimeError("x")), mock.patch.object(
            modulo, "escribir_volcado", side_effect=KeyboardInterrupt
        ):
            codigo = self.correr("--sin-articulos", salida=SalidaRota(OSError("tubería cerrada")))
        self.assertEqual(codigo, 1)
        self.assertEqual(self.claves_de_la_memoria(), set())

    def test_un_volcado_que_no_se_puede_armar_se_dice_y_no_tira_la_corrida(self):
        with mock.patch.object(modulo, "construir", side_effect=RuntimeError("x")), mock.patch.object(
            modulo.json, "dumps", side_effect=TypeError("no serializable")
        ):
            codigo = self.correr("--sin-articulos")
        self.assertEqual(codigo, 1)
        self.assertIn("No se pudo volcar el resultado (TypeError)", self.texto())

    def test_dos_volcados_en_el_mismo_segundo_no_se_pisan(self):
        with self._falla_el_reemplazo_de_la_pagina(OSError("disco lleno")):
            self.correr("--sin-articulos")
            primero = self.volcados()[0].read_bytes()
            self.correr("--sin-articulos")
        self.assertEqual([p.name for p in self.volcados()], ["volcado-2026-10-14_120000-2.json", "volcado-2026-10-14_120000.json"])
        self.assertEqual((self.carpeta / "volcado-2026-10-14_120000.json").read_bytes(), primero)

    def test_un_volcado_con_texto_hostil_es_json_valido_y_ascii(self):
        hostil = un_analisis("f:1", "si", texto="sustituto \ud800 suelto, nulo \x00, bidi \u202e y </script>")
        with self._falla_el_reemplazo_de_la_pagina(OSError("disco lleno")), mock.patch.object(
            modulo, "analizar", return_value=Resultado(analizadas={"f:1": hostil})
        ):
            self.correr("--sin-articulos")
        (volcado,) = self.volcados()
        texto = volcado.read_text(encoding="utf-8")
        self.assertTrue(texto.isascii())  # ASCII puro: se puede escribir en cualquier consola
        self.assertEqual(json.loads(texto)["analizadas"]["f:1"]["accion_propuesta"], hostil.accion_propuesta)


class TestArticuloPendiente(Base):
    """Lo analizado con el artículo sin leer por un corte o por el tope sale en la página pero no se marca."""

    def _correr_con(self, casos: dict[str, object], *argv):
        resultado = Resultado(analizadas={k: a for k, a in casos.items()})
        with mock.patch.object(modulo, "analizar", return_value=resultado):
            return self.correr(*argv)

    def _casos(self):
        return {
            "f:1": un_analisis("f:1", "si", articulo=InfoArticulo("no_leido", "corrida cortada (Ctrl+C)")),
            "f:2": un_analisis("f:2", "tal_vez", articulo=InfoArticulo("no_leido", "tope de la corrida")),
            "f:3": un_analisis("f:3", "no", articulo=InfoArticulo("no_corresponde", "la primera pasada no la consideró útil")),
            "f:4": un_analisis("f:4", "si", articulo=InfoArticulo("desactivado", "sin segunda pasada")),
            "f:5": un_analisis("f:5", "si", articulo=InfoArticulo("error", "ErrorDescarga: tiempo agotado")),
        }

    def test_si_y_tal_vez_con_el_articulo_no_leido_no_se_marcan_y_el_resto_si(self):
        codigo = self._correr_con(self._casos(), "--sin-articulos")
        self.assertEqual(self.claves_de_la_memoria(), {"f:3", "f:4", "f:5"})
        self.assertEqual(codigo, 2)  # algo queda pendiente
        datos = self.datos_del_resumen()
        self.assertEqual(datos["conteos"]["articulo_pendiente"], 2)
        self.assertEqual(len(datos["novedades"]), 5)  # las cinco están en la página y en el JSON
        self.assertIn("2 sin leer su artículo", self.texto())

    def test_la_pagina_lo_dice_en_las_tarjetas_pendientes_y_en_ninguna_otra(self):
        self._correr_con(self._casos(), "--sin-articulos")
        (pagina,) = self.paginas()
        html = pagina.read_text(encoding="utf-8")
        self.assertEqual(html.count("vuelve a salir en la próxima corrida para leer el artículo"), 2)
        tarjetas = {}
        for tarjeta in re.findall(r"<article .*?</article>", html, re.DOTALL):
            k = int(re.search(r"Título de la novedad (\d+)", tarjeta).group(1))
            tarjetas[k] = tarjeta
        self.assertEqual(sorted(tarjetas), [1, 2, 4, 5])  # f:3 es ruido: no tiene tarjeta
        for k in (1, 2):
            self.assertIn("vuelve a salir", tarjetas[k])
        for k in (4, 5):
            self.assertNotIn("vuelve a salir", tarjetas[k])

    def test_la_proxima_corrida_vuelve_a_analizar_solo_las_pendientes_y_las_sin_analizar(self):
        self._correr_con(self._casos(), "--sin-articulos")
        segundo = runner_de()
        self.assertEqual(self.correr("--sin-articulos", runner=segundo), 0)
        self.assertEqual(sorted(c for llamada in segundo.llamadas for c in llamada["claves"]), ["f:1", "f:2"])
        self.assertEqual(self.claves_de_la_memoria(), {"f:1", "f:2", "f:3", "f:4", "f:5"})

    def test_una_novedad_sospechosa_no_se_repite_para_siempre(self):
        casos = {
            "f:1": un_analisis("f:1", "si", sospecha_de_instrucciones=True, articulo=InfoArticulo("no_leido", "sospecha de instrucciones")),
            "f:2": un_analisis("f:2", "tal_vez", sospecha_mecanica=True, articulo=InfoArticulo("no_leido", "sospecha de instrucciones")),
        }
        codigo = self._correr_con(casos, "--sin-articulos")
        self.assertEqual(self.claves_de_la_memoria(), {"f:1", "f:2"})
        self.assertEqual(codigo, 2)  # f:3, f:4 y f:5 no tienen resultado en este caso armado: quedaron sin analizar

    def test_cada_estado_del_articulo_distinto_de_no_leido_se_marca(self):
        estados = ("desactivado", "error", "sin_texto", "no_aplica", "host_distinto", "leido", "no_corresponde")
        casos = {f"f:{i}": un_analisis(f"f:{i}", "si", articulo=InfoArticulo(e, "m")) for i, e in enumerate(estados[:5], 1)}
        self._correr_con(casos, "--sin-articulos")
        self.assertEqual(self.claves_de_la_memoria(), set(casos))

    def test_con_la_corrida_cortada_de_verdad_las_pendientes_vuelven(self):
        # De punta a punta: la primera pasada termina, la segunda (con el artículo) se corta en la tercera novedad.
        from radar.lectura.articulo import OK, Articulo

        base = runner_de()
        llamadas = []

        def cortar(items):
            llamadas.append(items)
            if len(llamadas) >= 4:  # 1 = primera pasada; 2 y 3 = segundas pasadas hechas; 4 = Ctrl+C
                raise KeyboardInterrupt
            return base.funcion(items)

        # Las fuentes de la prueba no están en la lista real: el lector real diría «no aplica». Se usa uno que lee.
        lector = lambda nov, _fetcher: Articulo(estado=OK, texto="Un artículo con bastante texto. " * 20)  # noqa: E731
        with mock.patch.object(modulo, "leer_articulo", lector):
            codigo = self.correr(runner=RunnerFalso(cortar))
        self.assertEqual(codigo, 1)
        datos = self.datos_del_resumen()
        marcadas = self.claves_de_la_memoria()
        self.assertEqual(len(marcadas), 2)  # las dos que alcanzaron a leerse
        self.assertEqual(datos["conteos"]["articulo_pendiente"], 3)  # las otras tres vuelven
        for x in datos["novedades"]:
            estado = x["analisis"]["articulo"]["estado"]
            self.assertEqual(x["clave"] in marcadas, estado == "leido", x["clave"])
            self.assertEqual(estado, "leido" if x["clave"] in marcadas else "no_leido")


class TestIdeaYClaveDeApi(Base):
    def test_la_idea_con_clave_de_api_en_el_entorno_corta_sin_llamar_a_nadie(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-de-prueba"}), mock.patch.object(
            modulo, "analizar_idea", wraps=modulo.analizar_idea
        ) as analizar_idea:
            codigo = self.correr("--idea", "una idea", runner=cliente.ejecutar)
        self.assertEqual(codigo, 1)
        analizar_idea.assert_not_called()
        self.assertIn("ANTHROPIC_API_KEY", self.texto())
        self.assertEqual(self.paginas(), [])
        self.assertFalse(self.memoria.exists())


class TestJsonTipoYPagina(Base):
    def test_el_resumen_dice_su_tipo_y_su_pagina(self):
        self.correr("--sin-articulos")
        (pagina,) = self.paginas()
        datos = self.datos_del_resumen()
        self.assertEqual((datos["tipo"], datos["pagina"]), ("resumen", pagina.name))
        self.assertEqual(self.jsons()[0].stem, Path(datos["pagina"]).stem)

    def test_la_idea_dice_su_tipo_y_su_pagina(self):
        self.correr("--idea", "una idea")
        (pagina,) = self.paginas()
        datos = self.datos_del_resumen()
        self.assertEqual((datos["tipo"], datos["pagina"]), ("idea", pagina.name))
        self.assertTrue(datos["pagina"].startswith("idea-"))

    def test_con_dos_corridas_en_el_mismo_segundo_cada_json_apunta_a_su_pagina(self):
        self.correr("--sin-articulos")
        self.correr("--sin-articulos", runner=runner_de())  # no hay nuevas, pero la página sale igual, con -2
        for ruta_json in self.jsons():
            datos = json.loads(ruta_json.read_text(encoding="utf-8"))
            self.assertEqual(datos["pagina"], ruta_json.with_suffix(".html").name)
        self.assertEqual({json.loads(p.read_text(encoding="utf-8"))["pagina"] for p in self.jsons()}, {p.name for p in self.paginas()})


class TestCableadoPorDefecto(Base):
    def test_los_valores_por_defecto_de_main_son_los_reales(self):
        from radar.lectura.descarga import descargar
        from radar.lectura.fuentes import FUENTES
        from radar.memoria import RUTA_POR_DEFECTO

        parametros = inspect.signature(main).parameters
        self.assertIs(parametros["fuentes"].default, FUENTES)
        self.assertEqual(len(FUENTES), 14)
        self.assertIs(parametros["runner"].default, cliente.ejecutar)
        self.assertIs(parametros["fetcher"].default, descargar)
        self.assertEqual(parametros["ruta_memoria"].default, RUTA_POR_DEFECTO)
        self.assertEqual(RUTA_POR_DEFECTO.parts[-2:], ("generated", "memoria.json"))
        self.assertIs(parametros["ahora"].default, modulo._ahora)
        self.assertIsNone(parametros["carpeta"].default)  # la carpeta por defecto es CARPETA_POR_DEFECTO
        self.assertIsNone(parametros["contexto"].default)  # y el contexto, el archivo de material/

    def test_el_contexto_de_un_archivo_llega_al_analizador(self):
        ruta = self.dir / "mi-contexto.md"
        contenido = "\ufeffINGOL es una plataforma de desarrollo gobernado.\r\nSegunda línea.\r\n"
        ruta.write_bytes(contenido.encode("utf-8"))  # en bytes: write_text cambiaría los saltos de línea en Windows
        with mock.patch.object(modulo, "analizar", wraps=modulo.analizar) as analizar:
            self.assertEqual(self.correr("--sin-articulos", "--contexto", str(ruta), contexto=None), 0)
        self.assertEqual(analizar.call_args.kwargs["contexto"], "INGOL es una plataforma de desarrollo gobernado.\nSegunda línea.")

    def test_el_contexto_de_un_archivo_llega_tambien_a_la_idea(self):
        ruta = self.dir / "mi-contexto.md"
        ruta.write_text("Contexto de la idea.", encoding="utf-8")
        with mock.patch.object(modulo, "analizar_idea", wraps=modulo.analizar_idea) as analizar_idea:
            self.assertEqual(self.correr("--idea", "una idea", "--contexto", str(ruta), contexto=None), 0)
        self.assertEqual(analizar_idea.call_args.kwargs["contexto"], "Contexto de la idea.")


RAIZ = Path(__file__).resolve().parents[2]


class TestComoProceso(unittest.TestCase):
    """El comando tal como lo corre el owner: un proceso aparte, desde la raíz del proyecto."""

    def _correr(self, *argv, entorno=None):
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", **(entorno or {})}
        return subprocess.run(
            [sys.executable, "-m", "radar", *argv], cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60
        )

    def test_help_sale_con_0_y_muestra_las_opciones(self):
        proceso = self._correr("--help")
        self.assertEqual(proceso.returncode, 0, proceso.stderr)
        for opcion in ("--desde", "--hasta", "--sin-articulos", "--carpeta", "--contexto", "--idea"):
            self.assertIn(opcion, proceso.stdout)

    def test_argumentos_invalidos_salen_con_2(self):
        proceso = self._correr("--idea", "x", "--desde", "2026-01-01")
        self.assertEqual(proceso.returncode, 2)
        self.assertIn("--idea", proceso.stderr)

    def test_el_codigo_de_salida_del_proceso_es_el_que_devuelve_main(self):
        # Con una clave de API en el entorno main devuelve 1 antes de tocar la red, la memoria o la carpeta.
        with tempfile.TemporaryDirectory() as d:
            contexto = Path(d) / "contexto.md"
            contexto.write_text("Contexto de prueba.", encoding="utf-8")
            proceso = self._correr("--idea", "una idea", "--contexto", str(contexto), entorno={"ANTHROPIC_API_KEY": "sk-de-prueba"})
        self.assertEqual(proceso.returncode, 1, proceso.stderr)
        self.assertIn("Error:", proceso.stdout)
        self.assertIn("ANTHROPIC_API_KEY", proceso.stdout)


if __name__ == "__main__":
    unittest.main()
