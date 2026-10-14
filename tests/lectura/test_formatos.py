"""Cada formato con su recorte: atom, rss, atom sin fechas, youtube y las tres páginas.

Los recortes de feeds están armados a mano con la forma del formato (no son capturas). Las
páginas no se pudieron bajar crudas: sus HTML son SINTÉTICOS, con la forma descrita en el
diseño; que los analizadores anden contra las páginas reales no se probó.
"""

import unittest
from datetime import datetime, timezone
from pathlib import Path

from radar.lectura import por_id
from radar.lectura.analizar import analizar_atom, analizar_rss
from radar.lectura.paginas import (
    analizar_anthropic_ingenieria,
    analizar_anthropic_noticias,
    analizar_claude_plataforma,
    analizar_metr,
)

FIXTURES = Path(__file__).parent / "fixtures"
AHORA = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def fixture(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class AtomTest(unittest.TestCase):
    def test_claude_code(self):
        ext = analizar_atom(por_id("claude-code"), fixture("atom_claude_code.xml"), AHORA)
        self.assertEqual(ext.descartadas, 0)
        self.assertEqual(
            [n.titulo for n in ext.novedades], ["v2.1.296", "v2.1.295", "v2.1.290"]
        )
        primera = ext.novedades[0]
        self.assertEqual(primera.fuente, "claude-code")
        self.assertEqual(primera.clave, "claude-code:tag:github.com,2008:Repository/100/v2.1.296")
        self.assertEqual(primera.enlace, "https://github.com/anthropics/claude-code/releases/tag/v2.1.296")
        self.assertEqual(primera.fecha, utc(2026, 10, 9, 18, 0))
        # el contenido venía como HTML escapado: sale texto plano y sólo la primera oración
        self.assertEqual(primera.frase, "Fixed a crash when resuming a session.")

    def test_fecha_con_zona_horaria_se_pasa_a_utc(self):
        ext = analizar_atom(por_id("claude-code"), fixture("atom_claude_code.xml"), AHORA)
        self.assertEqual(ext.novedades[1].fecha, utc(2026, 10, 7, 10, 30))

    def test_google_sin_fechas(self):
        ext = analizar_atom(por_id("google-dev"), fixture("atom_google_sin_fechas.xml"), AHORA)
        self.assertEqual(len(ext.novedades), 3)
        self.assertTrue(all(n.fecha is None for n in ext.novedades))
        # entre dos enlaces elige el alternate, no el de comentarios
        self.assertEqual(ext.novedades[0].enlace, "https://developers.googleblog.com/2026/10/agents.html")
        self.assertEqual(ext.novedades[0].frase, "A walkthrough of the new agent tooling.")

    def test_youtube_usa_media_description(self):
        ext = analizar_atom(por_id("ibm-youtube"), fixture("atom_youtube.xml"), AHORA)
        self.assertEqual(len(ext.novedades), 2)
        primera = ext.novedades[0]
        self.assertEqual(primera.enlace, "https://www.youtube.com/watch?v=AAAAAAAAAAA")
        self.assertEqual(primera.frase, "Why approval clicks do not make agents safe.")
        self.assertEqual(primera.fecha, utc(2026, 10, 10, 15, 0, 7))  # published gana sobre updated

    def test_un_rss_no_pasa_por_atom(self):
        from radar.lectura.modelos import DocumentoRechazado

        with self.assertRaises(DocumentoRechazado):
            analizar_atom(por_id("claude-code"), fixture("rss_github_changelog.xml"), AHORA)


class RssTest(unittest.TestCase):
    def test_github_changelog(self):
        ext = analizar_rss(por_id("github-changelog"), fixture("rss_github_changelog.xml"), AHORA)
        self.assertEqual(len(ext.novedades), 3)
        primera = ext.novedades[0]
        self.assertEqual(primera.titulo, "Copilot code review now supports custom rules")
        self.assertEqual(primera.clave, "github-changelog:https://github.blog/changelog/?p=90001")
        self.assertEqual(primera.fecha, utc(2026, 10, 12, 16, 0))
        self.assertEqual(primera.frase, "Teams can now define review rules in a file.")

    def test_pubdate_con_zona_se_pasa_a_utc(self):
        ext = analizar_rss(por_id("github-changelog"), fixture("rss_github_changelog.xml"), AHORA)
        self.assertEqual(ext.novedades[1].fecha, utc(2026, 10, 8, 12, 30))  # 09:30 -0300

    def test_resumen_desde_content_encoded(self):
        ext = analizar_rss(por_id("github-changelog"), fixture("rss_github_changelog.xml"), AHORA)
        self.assertEqual(ext.novedades[2].frase, "Security updates can be grouped in one pull request.")

    def test_sin_guid_la_clave_es_el_enlace_normalizado(self):
        texto = (
            '<rss version="2.0"><channel><item><title>T</title>'
            "<link> HTTPS://Ejemplo.TEST:443/a#frag </link></item></channel></rss>"
        )
        ext = analizar_rss(por_id("openai"), texto, AHORA)
        self.assertEqual(ext.novedades[0].enlace, "https://ejemplo.test/a")
        self.assertEqual(ext.novedades[0].clave, "openai:https://ejemplo.test/a")


class PaginasTest(unittest.TestCase):
    def test_anthropic_noticias(self):
        ext = analizar_anthropic_noticias(
            por_id("anthropic-noticias"), fixture("pagina_anthropic_noticias.html"), AHORA
        )
        por_enlace = {n.enlace.rsplit("/", 1)[1]: n for n in ext.novedades}
        # el mismo slug en dos anclas es una novedad; el enlace ajeno, el javascript: y el script no cuentan
        self.assertEqual(
            sorted(por_enlace),
            sorted(
                [
                    "how-we-built-claude-code-auto-mode",
                    "harness-design-for-long-running-application-development",
                    "slug-sin-texto",
                ]
            ),
        )
        con_fecha = por_enlace["harness-design-for-long-running-application-development"]
        self.assertEqual(con_fecha.titulo, "Harness design for long-running application development")
        self.assertEqual(con_fecha.fecha, utc(2026, 10, 8))
        sin_fecha = por_enlace["how-we-built-claude-code-auto-mode"]
        self.assertIsNone(sin_fecha.fecha)
        self.assertEqual(sin_fecha.titulo, "How we built Claude Code auto mode")
        self.assertEqual(sin_fecha.enlace, "https://www.anthropic.com/news/how-we-built-claude-code-auto-mode")
        self.assertEqual(ext.descartadas, 0)

    def test_anthropic_ingenieria(self):
        ext = analizar_anthropic_ingenieria(
            por_id("anthropic-ingenieria"), fixture("pagina_anthropic_ingenieria.html"), AHORA
        )
        self.assertEqual(
            [(n.titulo, n.fecha) for n in ext.novedades],
            [
                ("Effective harnesses for long-running agents", utc(2026, 9, 30)),
                ("Writing tools for agents", None),
            ],
        )

    def test_metr_saca_la_fecha_del_camino(self):
        ext = analizar_metr(por_id("metr"), fixture("pagina_metr.html"), AHORA)
        self.assertEqual(
            [(n.titulo, n.fecha) for n in ext.novedades],
            [
                ("AI systems could cover up misbehavior", utc(2026, 10, 6)),
                ("Measuring task horizons", utc(2026, 9, 22)),
                ("Fecha imposible en el camino", None),  # 02-30 no existe: sin fecha, no un error
            ],
        )
        self.assertEqual(ext.novedades[0].enlace, "https://metr.org/blog/2026-10-06-ai-systems-could-cover-up-misbehavior/")

    def test_claude_plataforma_cada_li_es_una_novedad_con_la_fecha_de_su_h3(self):
        fuente = por_id("claude-plataforma")
        ext = analizar_claude_plataforma(fuente, fixture("pagina_claude_plataforma.html"), AHORA)
        self.assertEqual(
            [(n.fecha, n.titulo) for n in ext.novedades],
            [
                (utc(2026, 10, 9), "We launched a new model in the API. It is available to all customers."),
                (utc(2026, 10, 9), "The Files API now supports larger uploads."),
                (utc(2026, 9, 30), "Prompt caching now has a longer time to live."),
            ],
        )  # el menú de navegación y el pie no cuentan
        self.assertEqual({n.enlace for n in ext.novedades}, {fuente.url})
        self.assertEqual(len({n.clave for n in ext.novedades}), 3)  # misma página y misma fecha: claves distintas
        self.assertTrue(all(n.clave.startswith(f"claude-plataforma:{fuente.url}#2026-") for n in ext.novedades))
        self.assertEqual(ext.novedades[0].frase, "We launched a new model in the API.")

    def test_claude_plataforma_titulo_recortado_a_160(self):
        largo = "palabra " * 60
        html = f"<h3>October 9, 2026</h3><ul><li>{largo}</li></ul>"
        ext = analizar_claude_plataforma(por_id("claude-plataforma"), html, AHORA)
        self.assertLessEqual(len(ext.novedades[0].titulo), 160)

    def test_pagina_vacia_no_da_novedades_ni_error(self):
        ext = analizar_metr(por_id("metr"), "<html><body><p>rediseño</p></body></html>", AHORA)
        self.assertEqual(ext.novedades, [])


if __name__ == "__main__":
    unittest.main()
