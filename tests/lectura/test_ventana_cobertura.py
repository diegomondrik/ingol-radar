"""Ventana y cobertura: fuera de ventana, sin fecha, cubre_desde, vacía y error sin frenar a las demás."""

import unittest
from datetime import datetime, timezone
from pathlib import Path

from radar.lectura import FUENTES, leer, por_id
from radar.lectura.modelos import MAX_ITEMS

FIXTURES = Path(__file__).parent / "fixtures"
AHORA = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def fixture(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


def fetcher_de(respuestas: dict):
    """Un fetcher falso: url -> texto, o una excepción para simular una fuente caída."""

    def fetcher(url):
        valor = respuestas[url]
        if isinstance(valor, Exception):
            raise valor
        return valor

    return fetcher


class VentanaTest(unittest.TestCase):
    def setUp(self):
        self.fuente = por_id("claude-code")
        self.fetcher = fetcher_de({self.fuente.url: fixture("atom_claude_code.xml")})

    def leer(self, desde, hasta):
        return leer([self.fuente], desde, hasta, self.fetcher, ahora=AHORA)

    def test_deja_fuera_lo_que_cae_fuera_de_la_ventana(self):
        novedades, cobertura = self.leer(utc(2026, 10, 1), utc(2026, 10, 14))
        self.assertEqual([n.titulo for n in novedades], ["v2.1.296", "v2.1.295"])
        c = cobertura["claude-code"]
        self.assertEqual((c.estado, c.en_fuente, c.en_ventana, c.sin_fecha), ("ok", 3, 2, 0))
        # cubre_desde mira lo que trajo la fuente, no la ventana
        self.assertEqual(c.cubre_desde, utc(2026, 9, 20, 9, 15))

    def test_los_bordes_de_la_ventana_son_inclusivos(self):
        novedades, _ = self.leer(utc(2026, 9, 20, 9, 15), utc(2026, 10, 7, 10, 30))
        self.assertEqual([n.titulo for n in novedades], ["v2.1.295", "v2.1.290"])

    def test_ventana_sin_zona_horaria_o_al_reves_es_un_error_de_uso(self):
        with self.assertRaises(ValueError):
            self.leer(datetime(2026, 10, 1), utc(2026, 10, 14))
        with self.assertRaises(ValueError):
            self.leer(utc(2026, 10, 14), utc(2026, 10, 1))

    def test_las_sin_fecha_entran_y_la_cobertura_lo_dice(self):
        google = por_id("google-dev")
        novedades, cobertura = leer(
            [google],
            utc(2026, 10, 1),
            utc(2026, 10, 14),
            fetcher_de({google.url: fixture("atom_google_sin_fechas.xml")}),
            ahora=AHORA,
        )
        self.assertEqual(len(novedades), 3)
        c = cobertura["google-dev"]
        self.assertEqual((c.en_fuente, c.en_ventana, c.sin_fecha), (3, 3, 3))
        self.assertIsNone(c.cubre_desde)

    def test_vacia_es_la_senal_de_una_pagina_que_quedo_muda(self):
        metr = por_id("metr")
        novedades, cobertura = leer(
            [metr], utc(2026, 9, 1), utc(2026, 10, 14),
            fetcher_de({metr.url: "<html><body><p>otro diseño</p></body></html>"}), ahora=AHORA,
        )
        self.assertEqual(novedades, [])
        c = cobertura["metr"]
        self.assertEqual(c.estado, "vacia")
        self.assertIn("no se sacó ninguna novedad", c.motivo)
        self.assertEqual(c.en_fuente, 0)

    def test_una_fuente_que_falla_no_frena_a_las_demas(self):
        a, b, c = por_id("claude-code"), por_id("openai"), por_id("github-changelog")
        fetcher = fetcher_de(
            {
                a.url: fixture("atom_claude_code.xml"),
                b.url: OSError("sin red"),
                c.url: fixture("rss_github_changelog.xml"),
            }
        )
        novedades, cobertura = leer([a, b, c], utc(2026, 9, 1), utc(2026, 10, 14), fetcher, ahora=AHORA)
        self.assertEqual(cobertura["openai"].estado, "error")
        self.assertIn("sin red", cobertura["openai"].motivo)
        self.assertEqual((cobertura["claude-code"].estado, cobertura["github-changelog"].estado), ("ok", "ok"))
        self.assertEqual({n.fuente for n in novedades}, {"claude-code", "github-changelog"})

    def test_documento_hostil_o_roto_deja_la_fuente_en_error(self):
        a, b = por_id("claude-code"), por_id("openai")
        fetcher = fetcher_de({a.url: "<feed><sin cerrar", b.url: fixture("rss_github_changelog.xml")})
        _, cobertura = leer([a, b], utc(2026, 9, 1), utc(2026, 10, 14), fetcher, ahora=AHORA)
        self.assertEqual(cobertura["claude-code"].estado, "error")
        self.assertEqual(cobertura["openai"].estado, "ok")

    def test_un_fetcher_que_no_devuelve_texto_es_error_de_esa_fuente(self):
        a = por_id("claude-code")
        _, cobertura = leer([a], utc(2026, 9, 1), utc(2026, 10, 14), lambda url: b"bytes", ahora=AHORA)
        self.assertEqual(cobertura["claude-code"].estado, "error")

    def test_descartadas_se_cuentan(self):
        a = por_id("openai")
        texto = (
            '<rss version="2.0"><channel>'
            "<item><title>buena</title><link>https://ejemplo.test/a</link></item>"
            "<item><title>mala</title><link>javascript:alert(1)</link></item>"
            "<item><title>sin enlace</title></item>"
            "</channel></rss>"
        )
        novedades, cobertura = leer([a], utc(2026, 9, 1), utc(2026, 10, 14), fetcher_de({a.url: texto}), ahora=AHORA)
        self.assertEqual([n.titulo for n in novedades], ["buena"])
        self.assertEqual(cobertura["openai"].descartadas, 2)

    def test_fuente_con_todo_descartado_es_vacia_y_lo_dice(self):
        a = por_id("openai")
        texto = '<rss version="2.0"><channel><item><title>x</title><link>data:text/html,hola</link></item></channel></rss>'
        _, cobertura = leer([a], utc(2026, 9, 1), utc(2026, 10, 14), fetcher_de({a.url: texto}), ahora=AHORA)
        self.assertEqual(cobertura["openai"].estado, "vacia")
        self.assertIn("1 descartadas", cobertura["openai"].motivo)

    def test_las_14_fuentes_con_un_fetcher_falso_no_abren_la_red(self):
        pedidas = []

        def fetcher(url):
            pedidas.append(url)
            raise OSError("sin red en las pruebas")

        _, cobertura = leer(FUENTES, utc(2026, 9, 1), utc(2026, 10, 14), fetcher, ahora=AHORA)
        self.assertEqual(pedidas, [f.url for f in FUENTES])
        self.assertEqual({c.estado for c in cobertura.values()}, {"error"})
        self.assertEqual(len(cobertura), 14)

    def test_mas_de_500_items_se_recortan_y_se_cuentan(self):
        items = "".join(
            f"<item><title>t{i}</title><link>https://ejemplo.test/{i}</link></item>" for i in range(MAX_ITEMS + 25)
        )
        a = por_id("openai")
        texto = f'<rss version="2.0"><channel>{items}</channel></rss>'
        novedades, cobertura = leer([a], utc(2026, 9, 1), utc(2026, 10, 14), fetcher_de({a.url: texto}), ahora=AHORA)
        self.assertEqual(len(novedades), MAX_ITEMS)
        self.assertEqual(cobertura["openai"].en_fuente, MAX_ITEMS)
        self.assertEqual(cobertura["openai"].recortadas, 25)

    def test_repetidas_dentro_de_la_fuente_se_descartan(self):
        a = por_id("openai")
        item = "<item><title>t</title><link>https://ejemplo.test/x</link><guid>g</guid></item>"
        texto = f'<rss version="2.0"><channel>{item}{item}</channel></rss>'
        novedades, cobertura = leer([a], utc(2026, 9, 1), utc(2026, 10, 14), fetcher_de({a.url: texto}), ahora=AHORA)
        self.assertEqual(len(novedades), 1)
        self.assertEqual(cobertura["openai"].descartadas, 1)


if __name__ == "__main__":
    unittest.main()
