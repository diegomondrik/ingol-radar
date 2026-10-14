"""La lista fija: 14 de 14 fuentes, con sus tipos y sus urls exactas."""

import unittest

from radar.lectura import FUENTES, por_id

ESPERADAS = {
    "anthropic-noticias": ("https://www.anthropic.com/news", "pagina"),
    "anthropic-ingenieria": ("https://www.anthropic.com/engineering", "pagina"),
    "claude-code": ("https://github.com/anthropics/claude-code/releases.atom", "atom"),
    "claude-plataforma": ("https://platform.claude.com/docs/en/release-notes/overview", "pagina"),
    "openai": ("https://openai.com/news/rss.xml", "rss"),
    "codex": ("https://github.com/openai/codex/releases.atom", "atom"),
    "google-dev": ("https://developers.googleblog.com/feeds/posts/default", "atom"),
    "github-changelog": ("https://github.blog/changelog/feed/", "rss"),
    "ibm-youtube": ("https://www.youtube.com/feeds/videos.xml?channel_id=UCKWaEZ-_VweaEx1j62do_vQ", "atom"),
    "metr": ("https://metr.org/blog/", "pagina"),
    "owasp-genai": ("https://genai.owasp.org/feed/", "rss"),
    "openssf": ("https://openssf.org/feed/", "rss"),
    "simon-willison": ("https://simonwillison.net/atom/everything/", "atom"),
    "martin-fowler": ("https://martinfowler.com/feed.atom", "atom"),
}


class FuentesTest(unittest.TestCase):
    def test_las_14_con_su_url_y_su_tipo(self):
        self.assertEqual(len(FUENTES), 14)
        self.assertEqual({f.id: (f.url, f.tipo) for f in FUENTES}, ESPERADAS)

    def test_ids_unicos_y_todas_por_https(self):
        self.assertEqual(len({f.id for f in FUENTES}), len(FUENTES))
        self.assertTrue(all(f.url.startswith("https://") for f in FUENTES))

    def test_cada_fuente_lleva_su_analizador(self):
        from radar.lectura.analizar import analizar_atom, analizar_rss

        for f in FUENTES:
            self.assertTrue(callable(f.analizador), f.id)
            self.assertTrue(f.nombre)
            if f.tipo == "atom":
                self.assertIs(f.analizador, analizar_atom, f.id)
            elif f.tipo == "rss":
                self.assertIs(f.analizador, analizar_rss, f.id)

    def test_por_id(self):
        self.assertEqual(por_id("metr").tipo, "pagina")
        with self.assertRaises(KeyError):
            por_id("no-existe")


if __name__ == "__main__":
    unittest.main()
