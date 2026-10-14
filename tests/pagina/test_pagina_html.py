"""Lo que dice la página generada, leído de su propio HTML: tarjetas de resumen, encabezados, cobertura, avisos,
detalles técnicos y cabeceras. Nada se compara contra las constantes del código: se cruza con lo que se armó."""

from __future__ import annotations

import html as modulo_html
import json
import re
import unittest
from datetime import datetime, timedelta, timezone

from radar.analisis.modelos import InfoArticulo, Resultado
from radar.lectura.modelos import Cobertura
from radar.analisis.prompt import CLAVE_IDEA
from radar.pagina import Corrida, construir
from radar.pagina.vista import PIE

from .ayudas import AHORA, analisis, corrida_de, novedad, revisar
from .test_orden import _pares

FILA = re.compile(
    r'<tr class="(.*?)"><td class="nombre">(.*?)</td><td data-label="Estado"><span class="badge (.*?)">(.*?)</span>'
    r'(?:<small>(.*?)</small>)?</td><td data-label="Cubre desde">(.*?)</td><td data-label="En la ventana">(.*?)</td>'
    r'<td data-label="Sin fecha">(.*?)</td><td data-label="Descartadas">(.*?)</td><td data-label="Recortadas">(.*?)</td></tr>'
)


def _kpis(pagina_html: str) -> dict[str, int]:
    return {r: int(n) for n, r in re.findall(r'<div class="kpi[^"]*"><b>(\d+)</b><span>([^<]*)</span></div>', pagina_html)}


class TestTarjetasYEncabezados(unittest.TestCase):
    def setUp(self):
        pares = _pares() + [(novedad(11), None), (novedad(12), None), (novedad(13), None)]
        self.pagina = construir(corrida_de(pares, leidas=57))

    def test_cada_tarjeta_de_resumen_con_su_rotulo_coincide_con_los_conteos(self):
        c = self.pagina.conteos
        self.assertEqual(
            _kpis(self.pagina.html),
            {
                "novedades leídas": 57,
                "nuevas": 13,
                "analizadas": 10,
                "sirven": 5,
                "tal vez": 2,
                "ruido": 2,
                "sin analizar": 3,
            },
        )
        self.assertEqual(_kpis(self.pagina.html)["sirven"], c["sirven"])
        self.assertEqual(_kpis(self.pagina.html)["sin analizar"], c["sin_analizar"])
        self.assertEqual(json.loads(self.pagina.json)["conteos"], c)

    def test_los_encabezados_traen_la_cantidad_de_cada_grupo(self):
        html = self.pagina.html
        self.assertEqual(re.findall(r"<h2>Sirven \((\d+)\)</h2>", html), ["5"])
        self.assertEqual(re.findall(r"<h2>Tal vez \((\d+)\)</h2>", html), ["2"])
        self.assertEqual(re.findall(r"<h2>Otras \((\d+)\)</h2>", html), ["1"])
        self.assertEqual(re.findall(r"<summary>Ruido \((\d+)\)", html), ["2"])

    def test_cada_grupo_vacio_no_tiene_encabezado(self):
        solo_ruido = construir(corrida_de([(novedad(1), analisis("f:1", sirve="no"))]))
        for rotulo in ("Sirven (", "Tal vez (", "Otras ("):
            self.assertNotIn(rotulo, solo_ruido.html)
        self.assertIn("Ruido (1)", solo_ruido.html)


class TestTablaDeCobertura(unittest.TestCase):
    def setUp(self):
        cobertura = {
            "fuente-a": Cobertura(
                estado="ok", en_fuente=20, en_ventana=7, sin_fecha=2, descartadas=3, recortadas=1,
                cubre_desde=datetime(2026, 8, 20, 9, 30, tzinfo=timezone.utc),
            ),
            "fuente-b": Cobertura(estado="vacia", motivo="respondió pero no se sacó ninguna novedad"),
            "fuente-c": Cobertura(estado="error", motivo="ErrorDescarga: sin conexión", descartadas=4, recortadas=5),
        }
        fuentes = {"fuente-a": "Fuente A", "fuente-b": "Fuente B & C", "fuente-c": "Fuente C"}
        self.pagina = construir(corrida_de([(novedad(1), analisis("f:1"))], cobertura=cobertura, fuentes=fuentes))
        self.filas = [tuple(modulo_html.unescape(x) for x in fila) for fila in FILA.findall(self.pagina.html)]

    def test_una_fila_por_fuente_con_cada_celda(self):
        self.assertEqual(
            self.filas,
            [
                ("", "Fuente A", "b-ok", "Bien", "", "2026-08-20", "7", "2", "3", "1"),
                ("fila-vacia", "Fuente B & C", "b-warn", "No trajo nada", "respondió pero no se sacó ninguna novedad", "—", "0", "0", "0", "0"),
                ("fila-error", "Fuente C", "b-crit", "Error", "ErrorDescarga: sin conexión", "—", "0", "0", "4", "5"),
            ],
        )

    def test_el_encabezado_de_la_tabla_tiene_las_siete_columnas(self):
        cabeza = re.search(r"<thead><tr>(.*?)</tr></thead>", self.pagina.html).group(1)
        self.assertEqual(
            re.findall(r"<th>(.*?)</th>", cabeza),
            ["Fuente", "Estado", "Cubre desde", "En la ventana", "Sin fecha", "Descartadas", "Recortadas"],
        )

    def test_una_fuente_con_estado_raro_se_muestra_como_texto(self):
        cobertura = {"x": Cobertura(estado="<b>raro</b>")}
        pagina = construir(corrida_de([], cobertura=cobertura, fuentes={"x": "X"}))
        self.assertEqual(revisar(pagina.html).problemas, [])
        self.assertIn("&lt;b&gt;raro&lt;/b&gt;", pagina.html)


class TestSinAnalizar(unittest.TestCase):
    def _pagina(self, cuantas: int, **cambios):
        pares = [(novedad(i), None) for i in range(cuantas)]
        corrida = corrida_de(pares + [(novedad(900), analisis("f:900"))], **cambios)
        return corrida, construir(corrida).html

    def test_abierta_con_quince_o_menos_y_cerrada_con_dieciseis(self):
        _c, quince = self._pagina(15)
        _c, dieciseis = self._pagina(16)
        self.assertIn("<details open><summary>15 sin analizar</summary>", quince)
        self.assertIn("<details><summary>16 sin analizar</summary>", dieciseis)
        self.assertNotIn("<details open><summary>16", dieciseis)

    def test_dice_que_vuelven_a_aparecer(self):
        _c, html = self._pagina(2)
        self.assertIn("Vuelven a aparecer como nuevas en la próxima corrida.", html)
        self.assertEqual(html.count("falló dos veces"), 2)

    def test_el_tope_en_singular_y_en_plural_con_su_cifra(self):
        for cuantas, verbo in ((1, "quedó"), (2, "quedaron")):
            with self.subTest(cuantas=cuantas):
                corrida = corrida_de([(novedad(i), None) for i in range(cuantas)], tope=50)
                corrida.resultado.por_tope = cuantas
                html = construir(corrida).html
                self.assertIn(f"Se alcanzó el tope de la corrida (50 análisis): {cuantas} {verbo} sin analizar por eso.", html)
        sin_tope = construir(corrida_de([(novedad(1), None)])).html
        self.assertNotIn("Se alcanzó el tope", sin_tope)


class TestAvisos(unittest.TestCase):
    ESTADOS = {
        "no_leido": "No se leyó el artículo",
        "desactivado": "Se analizó sin leer el artículo",
        "no_aplica": "No se leyó el artículo",
        "host_distinto": "No se leyó el artículo",
        "sin_texto": "No se pudo leer el artículo",
        "error": "No se pudo leer el artículo",
        "leido": "Con el artículo",
        "estado-desconocido": "No se pudo leer el artículo",
    }

    def _tarjeta(self, sirve: str, info: InfoArticulo | None, cortada: str | None = None, **cambios) -> str:
        an = analisis("f:1", sirve=sirve, articulo=info, **cambios)
        corrida = corrida_de([(novedad(1), an)])
        corrida.resultado.abortada = cortada
        return construir(corrida).html

    def test_el_aviso_del_articulo_de_una_novedad_que_importa_en_cada_estado(self):
        for estado, prefijo in self.ESTADOS.items():
            with self.subTest(estado=estado):
                html = self._tarjeta("tal_vez", InfoArticulo(estado, "el motivo & su detalle"))
                self.assertIn(f'<p class="nota gris">{prefijo}: el motivo &amp; su detalle', html)
                self.assertEqual(html.count('class="nota gris"'), 1)

    def test_sin_motivo_dice_sin_detalle(self):
        self.assertIn("No se leyó el artículo: sin detalle", self._tarjeta("si", InfoArticulo("no_aplica", None)))

    def test_en_el_ruido_no_hay_aviso(self):
        # El ruido no tiene tarjeta en el resumen; la página de una idea sí muestra tarjeta para un «no».
        for estado in self.ESTADOS:
            with self.subTest(estado=estado):
                resultado = Resultado(analizadas={CLAVE_IDEA: analisis(CLAVE_IDEA, "no", articulo=InfoArticulo(estado, "motivo"))})
                pagina = construir(Corrida(generado=AHORA, resultado=resultado, idea="una idea"))
                self.assertIn('<article class="card"', pagina.html)  # hay tarjeta...
                self.assertNotIn('class="nota gris"', pagina.html)  # ...y no hay aviso del artículo
        self.assertNotIn('class="nota gris"', self._tarjeta("quizás", InfoArticulo("error", "motivo")))

    def test_si_el_articulo_se_leyo_bien_no_hay_aviso(self):
        self.assertNotIn('class="nota gris"', self._tarjeta("si", InfoArticulo("leido", None)))
        self.assertNotIn('class="nota gris"', self._tarjeta("si", None))

    def test_el_articulo_pendiente_dice_que_vuelve_a_salir(self):
        corte = "interrumpida por el usuario (Ctrl+C)"
        pendiente = self._tarjeta("si", InfoArticulo("no_leido", "corrida cortada (Ctrl+C)"), cortada=corte)
        self.assertIn("corrida cortada (Ctrl+C) — vuelve a salir en la próxima corrida para leer el artículo.", pendiente)
        for info in (InfoArticulo("desactivado", "sin segunda pasada"), InfoArticulo("error", "x"), InfoArticulo("sin_texto", "x")):
            self.assertNotIn("vuelve a salir", self._tarjeta("si", info, cortada=corte))
        sospechosa = self._tarjeta(
            "si", InfoArticulo("no_leido", "sospecha de instrucciones"), cortada=corte, sospecha_de_instrucciones=True
        )
        self.assertNotIn("vuelve a salir", sospechosa)  # su artículo no se lee nunca: repetirla no sirve
        # Sin corte (el tope o el presupuesto de lectura) no vuelve a salir: repetir no avanzaría.
        self.assertNotIn("vuelve a salir", self._tarjeta("si", InfoArticulo("no_leido", "tope de la corrida")))

    def test_dos_avisos_a_la_vez_se_muestran_los_dos(self):
        corrida = corrida_de(
            [(novedad(1), analisis("f:1"))],
            cobertura={"fuente-a": Cobertura(estado="vacia", motivo="nada")},
        )
        corrida.resultado.abortada = "interrumpida por el usuario (Ctrl+C)"
        html = construir(corrida).html
        self.assertEqual(html.count('class="aviso crit"'), 2)
        self.assertIn("La corrida se cortó antes de terminar", html)
        self.assertIn("Atención con las fuentes", html)

    def test_sospecha_mecanica_sola_es_aviso_fuerte(self):
        an = analisis("f:1", sospecha_mecanica=True)  # sin artículo, primera pasada
        pagina = construir(corrida_de([(novedad(1), an)]))
        self.assertIn('<p class="nota fuerte">Esta novedad trae instrucciones para un modelo', pagina.html)
        self.assertEqual(json.loads(pagina.json)["novedades"][0]["sospecha"], "titulo_o_frase")

    def test_en_el_ruido_la_marca_fuerte_se_ve_en_la_linea(self):
        an = analisis("f:1", sirve="no", sospecha_de_instrucciones=True)
        self.assertIn("(trae instrucciones para un modelo)", construir(corrida_de([(novedad(1), an)])).html)


class TestDetallesTecnicosYJson(unittest.TestCase):
    def test_lectura_desactivada_y_corrida_cortada_en_los_detalles(self):
        corrida = corrida_de([(novedad(1), analisis("f:1"))], sin_articulos=True)
        corrida.resultado.abortada = "claude no disponible: x"
        html = construir(corrida).html
        self.assertIn("<dt>Lectura de artículos</dt><dd>desactivada (--sin-articulos)</dd>", html)
        self.assertIn("<dt>Corrida cortada</dt><dd>claude no disponible: x</dd>", html)
        normal = construir(corrida_de([(novedad(1), analisis("f:1"))])).html
        self.assertNotIn("Lectura de artículos", normal)
        self.assertNotIn("Corrida cortada", normal)

    def test_los_contadores_del_analizador_estan_en_los_detalles_y_en_el_json(self):
        resultado = Resultado(analizadas={"f:1": analisis("f:1")}, llamadas=14, reintentos=2, segundas=4, segundas_fallidas=1)
        pagina = construir(corrida_de([(novedad(1), analisis("f:1"))], resultado=resultado))
        self.assertIn("<dt>Llamadas a Claude</dt><dd>14</dd>", pagina.html)
        self.assertIn("<dt>Segundas pasadas fallidas</dt><dd>1</dd>", pagina.html)
        self.assertEqual(json.loads(pagina.json)["contadores"]["llamadas"], 14)

    def test_sin_articulos_es_false_si_no_se_pidio(self):
        datos = json.loads(construir(corrida_de([(novedad(1), analisis("f:1"))])).json)
        self.assertIs(datos["sin_articulos"], False)
        datos = json.loads(construir(corrida_de([(novedad(1), analisis("f:1"))], sin_articulos=True)).json)
        self.assertIs(datos["sin_articulos"], True)

    def test_sin_analizar_completo_en_el_json(self):
        pares = [(novedad(1, fuente="fuente-a", enlace="https://ejemplo.org/uno"), None), (novedad(2, fuente="fuente-b"), analisis("f:2"))]
        datos = json.loads(construir(corrida_de(pares)).json)
        self.assertEqual(
            datos["sin_analizar"],
            [{"clave": "f:1", "titulo": "TITULO_1", "enlace": "https://ejemplo.org/uno", "fuente": "fuente-a", "motivo": "falló dos veces"}],
        )

    def test_generado_en_otra_zona_sale_en_utc(self):
        argentina = AHORA.astimezone(timezone(timedelta(hours=-3)))
        pagina = construir(corrida_de([(novedad(1), analisis("f:1"))], generado=argentina))
        self.assertEqual(json.loads(pagina.json)["generado"], "2026-10-14T12:00:00+00:00")
        self.assertIn("Corrida del 2026-10-14 12:00 UTC", pagina.html)

    def test_tipo_y_pagina_del_json(self):
        pagina = construir(corrida_de([(novedad(1), analisis("f:1"))]))
        datos = json.loads(pagina.json)
        self.assertEqual((datos["tipo"], datos["pagina"]), ("resumen", None))
        con_nombre = json.loads(pagina.json_para("resumen-2026-10-14_120000.html"))
        self.assertEqual(con_nombre["pagina"], "resumen-2026-10-14_120000.html")
        self.assertEqual({k: v for k, v in con_nombre.items() if k != "pagina"}, {k: v for k, v in datos.items() if k != "pagina"})


class TestCabeceras(unittest.TestCase):
    def setUp(self):
        self.pagina = construir(corrida_de(_pares()))
        self.revisor = revisar(self.pagina.html)

    def test_las_metas(self):
        metas = self.revisor.metas
        self.assertIn({"charset": "utf-8"}, metas)
        self.assertIn({"name": "referrer", "content": "no-referrer"}, metas)
        self.assertIn({"name": "viewport", "content": "width=device-width, initial-scale=1"}, metas)
        self.assertIn({"name": "color-scheme", "content": "light dark"}, metas)

    def test_idioma_doctype_y_titulo(self):
        self.assertTrue(self.pagina.html.startswith('<!doctype html>\n<html lang="es">'))
        self.assertIn("<title>Radar de novedades para INGOL</title>", self.pagina.html)
        self.assertIn("<h1>Radar de novedades para INGOL</h1>", self.pagina.html)

    def test_el_pie_esta(self):
        self.assertIn(f'<footer><div class="wrap">{PIE}</div></footer>', self.pagina.html)

    def test_la_frase_de_cada_novedad_esta(self):
        for k in (1, 2, 4, 6):
            self.assertIn(f'<p class="frase">Frase de la novedad {k}.</p>', self.pagina.html)

    def test_el_borde_de_la_tarjeta_sigue_al_veredicto(self):
        clases = re.findall(r'<article class="([^"]*)" id="n(\d+)"', self.pagina.html)
        self.assertEqual([c for c, _n in clases], ["card v-si"] * 5 + ["card v-tal_vez"] * 2 + ["card"])


if __name__ == "__main__":
    unittest.main()
