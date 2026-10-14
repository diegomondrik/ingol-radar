"""Dos ventanas contiguas y distintas con las ocho fixtures (todas las formas) y la memoria releída del disco.

Feeds: rss, atom, atom sin fechas, youtube. Páginas (SINTÉTICAS): anthropic noticias, anthropic
ingeniería, metr y notas de la plataforma (clave con hash del texto). Lo que cae fuera de la
ventana A y dentro de la B entra en la B; lo que no tiene fecha entra en la primera y no se repite.
"""

import json
import re
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from radar.lectura import leer, por_id, ventana
from radar.memoria import Memoria

FIXTURES = Path(__file__).parent / "fixtures"
AHORA = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
CORRIDA_A = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
CORRIDA_B = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)

POR_FUENTE = {
    "claude-code": "atom_claude_code.xml",  # atom
    "github-changelog": "rss_github_changelog.xml",  # rss
    "google-dev": "atom_google_sin_fechas.xml",  # atom sin fechas
    "ibm-youtube": "atom_youtube.xml",  # atom con media:description
    "anthropic-noticias": "pagina_anthropic_noticias.html",  # página
    "anthropic-ingenieria": "pagina_anthropic_ingenieria.html",  # página
    "metr": "pagina_metr.html",  # página, fecha en el camino
    "claude-plataforma": "pagina_claude_plataforma.html",  # página, clave con hash
}
FUENTES = [por_id(i) for i in POR_FUENTE]
POR_URL = {por_id(i).url: (FIXTURES / nombre).read_text(encoding="utf-8") for i, nombre in POR_FUENTE.items()}

VENTANA_A = ventana(date(2026, 9, 16), date(2026, 9, 30), AHORA)
VENTANA_B = ventana(date(2026, 10, 1), date(2026, 10, 14), AHORA)
VENTANA_COMPLETA = ventana(date(2026, 9, 16), date(2026, 10, 14), AHORA)


def fetcher(url):
    return POR_URL[url]


class DosVentanasTodasTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ruta = Path(self.dir.name) / "generated" / "memoria.json"

    def corrida(self, rango, cuando):
        desde, hasta = rango
        novedades, cobertura = leer(FUENTES, desde, hasta, fetcher, ahora=AHORA)
        with Memoria.abrir(self.ruta) as memoria:  # se reabre del disco en cada corrida
            nuevas = memoria.nuevas(novedades)
            memoria.marcar(nuevas, cuando)
            memoria.guardar()
        return novedades, nuevas, cobertura

    def test_las_ventanas_son_contiguas_y_distintas(self):
        self.assertEqual(VENTANA_A[1].date().isoformat(), "2026-09-30")
        self.assertEqual(VENTANA_B[0].date().isoformat(), "2026-10-01")
        self.assertLess(VENTANA_A[1], VENTANA_B[0])
        self.assertEqual(VENTANA_COMPLETA, (VENTANA_A[0], VENTANA_B[1]))

    def test_cada_item_se_marca_exactamente_una_vez(self):
        todas, _, _ = self.corrida(VENTANA_COMPLETA, CORRIDA_B)
        claves_completas = [n.clave for n in todas]
        self.assertEqual(len(claves_completas), len(set(claves_completas)))  # sin repetidas a la vista
        self.ruta.unlink()

        en_a, nuevas_a, cob_a = self.corrida(VENTANA_A, CORRIDA_A)
        en_b, nuevas_b, cob_b = self.corrida(VENTANA_B, CORRIDA_B)

        claves_a = {n.clave for n in nuevas_a}
        claves_b = {n.clave for n in nuevas_b}
        self.assertEqual(claves_a & claves_b, set(), "un item entró dos veces")
        self.assertEqual(claves_a | claves_b, set(claves_completas), "se perdió algún item")
        self.assertEqual((len(nuevas_a), len(nuevas_b)), (12, 10))

        # el contenido de cada franja
        titulos_a = {n.titulo for n in nuevas_a if n.fecha is not None}
        self.assertEqual(
            titulos_a,
            {"v2.1.290", "Dependabot adds grouped security updates", "Measuring task horizons",
             "Effective harnesses for long-running agents", "Prompt caching now has a longer time to live."},
        )  # fmt: skip
        # lo que cae fuera de A y dentro de B entra en B
        self.assertIn("v2.1.296", {n.titulo for n in nuevas_b})
        self.assertNotIn("v2.1.296", {n.titulo for n in en_a})
        # lo que no tiene fecha entra en A (la primera ventana que lo ve) y en B ya no es nuevo
        sin_fecha_a = {n.clave for n in nuevas_a if n.fecha is None}
        self.assertEqual(len(sin_fecha_a), 7)
        self.assertTrue(sin_fecha_a <= {n.clave for n in en_b})  # entra en la ventana B...
        self.assertTrue(sin_fecha_a.isdisjoint(claves_b))  # ...pero no se repite

        # cada franja y cada fuente en su cobertura
        self.assertEqual(cob_a["google-dev"].sin_fecha, 3)
        self.assertEqual(cob_b["google-dev"].sin_fecha, 3)
        self.assertEqual({c.estado for c in cob_a.values()} | {c.estado for c in cob_b.values()}, {"ok"})

        # la memoria en disco: cada clave una vez, con la primera vez de su corrida
        vistas = json.loads(self.ruta.read_text(encoding="utf-8"))["vistas"]
        self.assertEqual(set(vistas), claves_a | claves_b)
        for clave in claves_a:
            self.assertTrue(vistas[clave]["primera_vez"].startswith("2026-10-01T08:00"), clave)
        for clave in claves_b:
            self.assertTrue(vistas[clave]["primera_vez"].startswith("2026-10-14T08:00"), clave)

    def test_las_claves_de_cada_forma(self):
        self.corrida(VENTANA_COMPLETA, CORRIDA_B)
        vistas = json.loads(self.ruta.read_text(encoding="utf-8"))["vistas"]
        plataforma = [k for k in vistas if k.startswith("claude-plataforma:")]
        self.assertEqual(len(plataforma), 3)  # misma página: una clave por li, con fecha y hash
        for clave in plataforma:
            self.assertRegex(clave, r"^claude-plataforma:https://platform\.claude\.com/docs/en/release-notes/overview#2026-\d\d-\d\d#[0-9a-f]{10}$")
        self.assertIn("claude-code:tag:github.com,2008:Repository/100/v2.1.296", vistas)  # atom: el id
        self.assertIn("github-changelog:https://github.blog/changelog/?p=90001", vistas)  # rss: el guid
        self.assertIn("ibm-youtube:yt:video:AAAAAAAAAAA", vistas)  # youtube: el id
        self.assertIn("metr:https://metr.org/blog/2026-10-06-ai-systems-could-cover-up-misbehavior/", vistas)  # página: el enlace
        self.assertTrue(any(k.startswith("google-dev:tag:blogger.com") for k in vistas))
        self.assertTrue(all(re.match(r"^[a-z-]+:", k) for k in vistas))

    def test_la_tercera_corrida_sobre_la_ventana_completa_no_trae_nada_nuevo(self):
        self.corrida(VENTANA_A, CORRIDA_A)
        self.corrida(VENTANA_B, CORRIDA_B)
        completas, nuevas, _ = self.corrida(VENTANA_COMPLETA, AHORA)
        self.assertEqual(len(completas), 22)
        self.assertEqual(nuevas, [])

    def test_el_orden_al_reves_tambien_marca_cada_item_una_vez(self):
        _, nuevas_b, _ = self.corrida(VENTANA_B, CORRIDA_B)
        _, nuevas_a, _ = self.corrida(VENTANA_A, CORRIDA_A)
        claves_b = {n.clave for n in nuevas_b}
        claves_a = {n.clave for n in nuevas_a}
        self.assertEqual(claves_a & claves_b, set())
        self.assertEqual(len(claves_a | claves_b), 22)
        self.assertEqual((len(nuevas_b), len(nuevas_a)), (17, 5))  # los sin fecha entraron con la primera que corrió
        _, nuevas, _ = self.corrida(VENTANA_COMPLETA, AHORA)
        self.assertEqual(nuevas, [])


if __name__ == "__main__":
    unittest.main()
