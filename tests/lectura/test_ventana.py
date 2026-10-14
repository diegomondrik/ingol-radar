"""La función `ventana`, el motivo de los errores de `leer` y el acumulador común de feeds y páginas."""

import io
import re
import unittest
from datetime import date, datetime, timedelta, timezone

from radar.lectura import leer, por_id, ventana
from radar.lectura.__main__ import main
from radar.lectura.analizar import analizar_rss
from radar.lectura.modelos import MAX_ITEMS, Acumulador, Novedad
from radar.lectura.paginas import analizar_anthropic_noticias, analizar_claude_plataforma, analizar_metr

AHORA = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class VentanaTest(unittest.TestCase):
    def test_por_defecto_son_28_dias_hasta_ahora(self):
        desde, hasta = ventana(ahora=AHORA)
        self.assertEqual(hasta, AHORA)
        self.assertEqual(desde, AHORA - timedelta(days=28))

    def test_la_cantidad_de_dias_se_puede_cambiar(self):
        desde, hasta = ventana(ahora=AHORA, dias=7)
        self.assertEqual((hasta - desde, hasta), (timedelta(days=7), AHORA))

    def test_hasta_inclusivo_es_el_ultimo_microsegundo_del_dia(self):
        _, hasta = ventana(None, date(2026, 10, 9), AHORA)
        self.assertEqual(hasta, datetime(2026, 10, 9, 23, 59, 59, 999999, tzinfo=timezone.utc))

    def test_desde_empieza_a_medianoche_utc(self):
        desde, _ = ventana(date(2026, 10, 1), None, AHORA)
        self.assertEqual(desde, utc(2026, 10, 1))

    def test_sin_desde_se_cuentan_los_dias_hacia_atras_desde_el_final(self):
        desde, hasta = ventana(None, date(2026, 10, 9), AHORA, dias=3)
        self.assertEqual(desde, hasta - timedelta(days=3))

    def test_los_bordes_con_novedades_reales_en_el_instante_exacto(self):
        def con_fecha(i, pub):
            return f"<item><title>t{i}</title><link>https://ejemplo.test/{i}</link><guid>g{i}</guid><pubDate>{pub}</pubDate></item>"

        texto = '<rss version="2.0"><channel>' + "".join(
            [
                con_fecha(1, "Wed, 30 Sep 2026 23:59:59 GMT"),  # un segundo antes de desde: afuera
                con_fecha(2, "Thu, 01 Oct 2026 00:00:00 GMT"),  # justo en desde: adentro
                con_fecha(3, "Fri, 09 Oct 2026 23:59:59 GMT"),  # último segundo de hasta: adentro
                con_fecha(4, "Sat, 10 Oct 2026 00:00:00 GMT"),  # justo después: afuera
            ]
        ) + "</channel></rss>"
        desde, hasta = ventana(date(2026, 10, 1), date(2026, 10, 9), AHORA)
        novedades, _ = leer([por_id("openai")], desde, hasta, lambda u: texto, ahora=AHORA)
        self.assertEqual([n.titulo for n in novedades], ["t2", "t3"])

    def test_un_dia_solo(self):
        desde, hasta = ventana(date(2026, 10, 9), date(2026, 10, 9), AHORA)
        self.assertEqual(hasta - desde, timedelta(days=1) - timedelta(microseconds=1))

    def test_desde_despues_de_hasta_o_ahora_sin_zona_es_un_error_de_uso(self):
        with self.assertRaises(ValueError):
            ventana(date(2026, 10, 14), date(2026, 10, 1), AHORA)
        with self.assertRaises(ValueError):
            ventana(None, None, datetime(2026, 10, 14, 12, 0))
        with self.assertRaises(ValueError):
            ventana(date(2026, 11, 1), None, AHORA)  # desde en el futuro respecto del final por defecto

    def test_ahora_en_otra_zona_se_pasa_a_utc(self):
        ahora = datetime(2026, 10, 14, 9, 0, tzinfo=timezone(timedelta(hours=-3)))
        _, hasta = ventana(ahora=ahora)
        self.assertEqual(hasta, AHORA)

    def test_el_comando_usa_la_misma_ventana(self):
        salida = io.StringIO()
        main(["--desde", "2026-10-01", "--hasta", "2026-10-09"], fetcher=lambda u: (_ for _ in ()).throw(OSError("x")), ahora=AHORA, salida=salida)
        self.assertIn("Ventana: 2026-10-01 a 2026-10-09 (UTC)", salida.getvalue())


class MotivoTest(unittest.TestCase):
    def leer_con(self, fetcher):
        _, cobertura = leer([por_id("openai")], utc(2026, 9, 1), AHORA, fetcher, ahora=AHORA)
        return cobertura["openai"]

    def test_un_error_esperado_se_explica_solo(self):
        c = self.leer_con(lambda u: "<html>no es un feed</html>")
        self.assertEqual(c.estado, "error")
        self.assertTrue(c.motivo.startswith("DocumentoRechazado:"))
        self.assertNotIn("inesperado", c.motivo)
        self.assertNotRegex(c.motivo, r"\.py:\d+")

    def test_un_error_de_descarga_propio_tampoco_trae_ubicacion(self):
        from radar.lectura.modelos import ErrorDescarga

        def fetcher(url):
            raise ErrorDescarga("la respuesta pasa el tope")

        c = self.leer_con(fetcher)
        self.assertEqual(c.motivo, "ErrorDescarga: la respuesta pasa el tope")

    def test_un_error_inesperado_trae_el_tipo_y_el_archivo_y_la_linea_del_ultimo_cuadro(self):
        def fetcher(url):
            raise ValueError("algo que no se esperaba")  # esta línea es la que debe aparecer

        c = self.leer_con(fetcher)
        self.assertEqual(c.estado, "error")
        m = re.search(r"ValueError: algo que no se esperaba \(inesperado, en (\w+\.py):(\d+)\)", c.motivo)
        self.assertIsNotNone(m, c.motivo)
        self.assertEqual(m.group(1), "test_ventana.py")
        with open(__file__, encoding="utf-8") as archivo:
            linea = archivo.read().splitlines()[int(m.group(2)) - 1]
        self.assertIn("raise ValueError", linea)

    def test_un_bug_en_un_analizador_se_ubica_en_el_analizador(self):
        from dataclasses import replace

        def roto(fuente, texto, ahora):
            return {}["no existe"]

        fuente = replace(por_id("openai"), analizador=roto)
        _, cobertura = leer([fuente], utc(2026, 9, 1), AHORA, lambda u: "<x/>", ahora=AHORA)
        self.assertRegex(cobertura["openai"].motivo, r"KeyError.*inesperado, en test_ventana\.py:\d+")

    def test_el_motivo_no_arrastra_texto_hostil(self):
        def fetcher(url):
            raise OSError("fallo\x1b[31m‮ con controles " + "x" * 1000)

        motivo = self.leer_con(fetcher).motivo
        self.assertLessEqual(len(motivo), 300)
        self.assertNotIn("\x1b", motivo)
        self.assertNotIn("‮", motivo)


class AcumuladorTest(unittest.TestCase):
    def novedad(self, clave):
        return Novedad("f", clave, "t", f"https://ejemplo.test/{clave}", None, "")

    def test_tope_repetidas_y_descartes_con_una_sola_definicion(self):
        acum = Acumulador()
        creadas = []

        def fabrica(clave):
            def hacer():
                creadas.append(clave)
                return self.novedad(clave)

            return hacer

        for i in range(MAX_ITEMS + 100):
            acum.registrar(f"k{i}", fabrica(f"k{i}"))
        for i in range(10):
            acum.registrar(f"k{i}", fabrica(f"k{i}"))  # repetidas de las que entraron
        acum.registrar(f"k{MAX_ITEMS + 50}", fabrica("repetida de una recortada"))  # ya vista, aunque no entró
        for _ in range(5):
            acum.descartar()  # enlace inválido
        acum.registrar("sin-titulo", lambda: None)  # la fábrica dice que no hay novedad... pero ya no hay lugar
        r = acum.resultado()
        self.assertEqual(len(r.novedades), MAX_ITEMS)
        self.assertEqual(r.recortadas, 100 + 1)  # las 100 válidas y únicas, más 'sin-titulo' (válida, única y sin lugar)
        self.assertEqual(r.descartadas, 10 + 1 + 5)
        self.assertEqual(len(creadas), MAX_ITEMS)  # lo recortado ni se arma

    def test_una_fabrica_que_devuelve_none_descarta_si_hay_lugar(self):
        acum = Acumulador()
        acum.registrar("a", lambda: None)
        acum.registrar("b", lambda: self.novedad("b"))
        r = acum.resultado()
        self.assertEqual((len(r.novedades), r.descartadas, r.recortadas), (1, 1, 0))

    def test_los_feeds_y_las_paginas_cuentan_igual(self):
        total = MAX_ITEMS + 100
        rss = '<rss version="2.0"><channel>' + "".join(
            f"<item><title>t{i}</title><link>https://ejemplo.test/{i}</link></item>" for i in range(total)
        ) + "</channel></rss>"
        metr = "".join(f'<a href="/blog/2026-10-06-slug{i}/">Titulo {i}</a>' for i in range(total))
        noticias = "".join(f'<a href="/news/slug{i}">Titulo {i}</a>' for i in range(total))
        plataforma = "<h3>October 9, 2026</h3><ul>" + "".join(f"<li>Cambio {i}</li>" for i in range(total)) + "</ul>"
        resultados = {
            "rss": analizar_rss(por_id("openai"), rss, AHORA),
            "metr": analizar_metr(por_id("metr"), metr, AHORA),
            "noticias": analizar_anthropic_noticias(por_id("anthropic-noticias"), noticias, AHORA),
            "plataforma": analizar_claude_plataforma(por_id("claude-plataforma"), plataforma, AHORA),
        }
        for nombre, r in resultados.items():
            with self.subTest(nombre):
                self.assertEqual((len(r.novedades), r.recortadas, r.descartadas), (MAX_ITEMS, 100, 0))

    def test_repetidas_y_recortadas_se_distinguen_en_un_feed(self):
        items = [f"<item><title>t{i}</title><link>https://ejemplo.test/{i}</link></item>" for i in range(MAX_ITEMS + 3)]
        items += [items[0], items[MAX_ITEMS + 1]]  # una repetida entre las que entraron y una entre las recortadas
        r = analizar_rss(por_id("openai"), '<rss version="2.0"><channel>' + "".join(items) + "</channel></rss>", AHORA)
        self.assertEqual((len(r.novedades), r.recortadas, r.descartadas), (MAX_ITEMS, 3, 2))


if __name__ == "__main__":
    unittest.main()
