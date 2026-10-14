"""La página con datos hostiles: lo que viene de internet o del modelo nunca se vuelve HTML activo.

Regla de las formas equivalentes (habilidad `entradas-adversariales`): un ataque escrito de otra forma
(mayúsculas, tabuladores, entidades, ya escapado una vez) tiene que fallar igual. Las páginas se leen con
`html.parser`, como las lee un navegador, y no con búsquedas de texto.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import unicodedata
import unittest
from pathlib import Path

from radar.pagina import construir
from radar.pagina.enlaces import enlace_seguro
from radar.pagina.estilo import CSP, ESTILO, LOGO
from radar.pagina.texto import e, plano

from .ayudas import ENLACE_BUENO, HOSTILES, corrida_de, corrida_hostil, analisis, novedad, revisar

RAIZ = Path(__file__).resolve().parents[2]
PELIGROS = ("<script", "<img", "</style><", "</title><", "<!--", "]]>", "<svg onload", "<a href=javascript")


def _cadenas(valor):
    """Todas las cadenas de un JSON ya leído (valores y claves)."""
    if isinstance(valor, str):
        yield valor
    elif isinstance(valor, dict):
        for k, v in valor.items():
            yield from _cadenas(k)
            yield from _cadenas(v)
    elif isinstance(valor, list):
        for v in valor:
            yield from _cadenas(v)


class TestEntradasHostiles(unittest.TestCase):
    def _revisar(self, corrida, etiqueta):
        pagina = construir(corrida)
        revisor = revisar(pagina.html)
        self.assertEqual(revisor.problemas, [], etiqueta)
        self.assertEqual(revisor.etiquetas["script"], 0, etiqueta)
        for peligro in PELIGROS:
            self.assertNotIn(peligro, pagina.html, f"{etiqueta}: apareció {peligro!r} sin escapar")
        # El único enlace posible de esta página es el bueno (las novedades hostiles traen ese o uno inválido).
        self.assertTrue(set(revisor.hrefs) <= {ENLACE_BUENO}, f"{etiqueta}: {set(revisor.hrefs)}")
        # Lo que se escribe a disco tiene que poder codificarse: un sustituto suelto no puede romper la escritura.
        pagina.html.encode("utf-8")
        datos = pagina.json.encode("utf-8")
        leido = json.loads(datos.decode("utf-8"))
        for cadena in _cadenas(leido):
            for ch in cadena:
                self.assertNotIn(unicodedata.category(ch), ("Cc", "Cf", "Cs"), f"{etiqueta}: carácter invisible en el JSON")
        return pagina, leido

    def test_cada_texto_hostil_en_todos_los_campos(self):
        for i, p in enumerate(HOSTILES):
            for enums in (False, True):
                self._revisar(corrida_hostil(p, enums_hostiles=enums), f"hostil {i} enums={enums}")

    def test_cada_texto_hostil_en_la_pagina_de_una_idea(self):
        for i, p in enumerate(HOSTILES):
            for enums in (False, True):
                _pagina, leido = self._revisar(corrida_hostil(p, enums_hostiles=enums, idea=True), f"idea {i} enums={enums}")
                self.assertEqual(len(leido["novedades"]), 1)

    def test_la_pagina_no_crece_con_un_texto_enorme(self):
        pagina = construir(corrida_hostil("x" * 100_000))
        self.assertLess(len(pagina.html), 400_000)
        self.assertLess(len(pagina.json), 400_000)

    def test_el_texto_ya_escapado_se_escapa_otra_vez(self):
        # `&lt;marquee&gt;` ya escapado tiene que verse como esas letras, no volverse `<marquee>` al leerlo.
        nov, an = novedad(1, titulo="&lt;marquee&gt;rotulo&lt;/marquee&gt;"), analisis("f:1")
        pagina = construir(corrida_de([(nov, an)]))
        self.assertIn("&amp;lt;marquee&amp;gt;rotulo", pagina.html)
        revisor = revisar(pagina.html)
        self.assertEqual(revisor.etiquetas["marquee"], 0)
        self.assertIn("&lt;marquee&gt;rotulo&lt;/marquee&gt;", revisor.texto)  # las letras tal cual, sin interpretar

    def test_los_controles_y_la_direccion_invertida_no_llegan(self):
        nov = novedad(1, titulo="uno\u202edos\u200btres\x00cuatro\ud800cinco")
        pagina = construir(corrida_de([(nov, analisis("f:1"))]))
        self.assertIn("unodostrescuatrocinco", pagina.html)
        self.assertNotIn("\u202e", pagina.html + pagina.json)
        self.assertNotIn("\x00", pagina.html + pagina.json)

    def test_la_lista_blanca_de_las_pruebas_puede_fallar(self):
        # Control del control: el revisor tiene que ver una etiqueta, un atributo y un comentario de más.
        casos = [
            '<p onclick="x()">a</p>',
            "<script>x</script>",
            '<p style="color:red">a</p>',
            "<!-- hola -->",
            '<img src="x">',
            '<span class="no-existe">a</span>',
            '<div href="http://a.b/">a</div>',
        ]
        for caso in casos:
            self.assertTrue(revisar(f"<!doctype html><html><body>{caso}</body></html>").problemas, caso)


class TestEnlaces(unittest.TestCase):
    MALOS = [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        " \tJaVa\tScRiPt\t:alert(1)",
        "java\nscript:alert(1)",
        "&#106;avascript:alert(1)",
        "javascript&colon;alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "DATA:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
        "vbscript:msgbox(1)",
        "file:///C:/Windows/win.ini",
        "/relativo/sin/host",
        "nota-1.html",
        "//ejemplo.com/protocolo-relativo",
        "http://127.0.0.1/",
        "http://localhost/",
        "http://10.0.0.1/",
        "http://[::1]/",
        "http://192.168.1.1/",
        "https://user:pass@ejemplo.com/",
        'https://ejemplo.com/"onmouseover=x',
        "https://ejemplo.com/a b",
        "https://ejemplo.com/<script>",
        "ftp://ejemplo.com/archivo",
        "",
        None,
        "https://",
    ]

    def test_cada_enlace_malo_queda_sin_ancla_y_el_titulo_como_texto(self):
        for i, malo in enumerate(self.MALOS):
            with self.subTest(enlace=malo):
                nov = novedad(i, enlace=malo)
                pagina = construir(corrida_de([(nov, analisis(nov.clave))]))
                revisor = revisar(pagina.html)
                self.assertEqual(revisor.problemas, [])
                self.assertEqual(revisor.hrefs, [])
                self.assertEqual(revisor.etiquetas["a"], 0)
                self.assertIn(f"TITULO_{i}", revisor.texto)
                self.assertIsNone(json.loads(pagina.json)["novedades"][0]["enlace"])

    def test_el_enlace_bueno_es_exactamente_un_ancla(self):
        nov = novedad(1, enlace=ENLACE_BUENO)
        pagina = construir(corrida_de([(nov, analisis("f:1"))]))
        revisor = revisar(pagina.html)
        self.assertEqual(revisor.problemas, [])
        self.assertEqual(revisor.hrefs, [ENLACE_BUENO])
        self.assertEqual(revisor.etiquetas["a"], 1)
        self.assertEqual(json.loads(pagina.json)["novedades"][0]["enlace"], ENLACE_BUENO)

    def test_el_enlace_con_ampersand_se_escapa_y_se_lee_igual(self):
        destino = "https://ejemplo.com/buscar?x=1&y=2"
        nov = novedad(1, enlace=destino)
        pagina = construir(corrida_de([(nov, analisis("f:1"))]))
        self.assertIn('href="https://ejemplo.com/buscar?x=1&amp;y=2"', pagina.html)
        self.assertEqual(revisar(pagina.html).hrefs, [destino])

    def test_cada_href_de_la_pagina_es_el_enlace_de_alguna_novedad(self):
        pares = [(novedad(i, enlace=m), analisis(f"f:{i}")) for i, m in enumerate(self.MALOS)]
        buenas = [novedad(100 + i, enlace=f"https://www.anthropic.com/news/{i}") for i in range(3)]
        pares += [(n, analisis(n.clave, sirve=("si", "tal_vez", "no")[i])) for i, n in enumerate(buenas)]
        pagina = construir(corrida_de(pares))
        revisor = revisar(pagina.html)
        self.assertEqual(revisor.problemas, [])
        self.assertEqual(sorted(revisor.hrefs), sorted(n.enlace for n in buenas))

    def test_los_enlaces_malos_tampoco_salen_en_lo_sin_analizar_del_json(self):
        pares = [(novedad(i, enlace=m), None) for i, m in enumerate(self.MALOS)]
        pares.append((novedad(500, enlace=ENLACE_BUENO), None))
        pagina = construir(corrida_de(pares))
        enlaces = {x["clave"]: x["enlace"] for x in json.loads(pagina.json)["sin_analizar"]}
        self.assertEqual(len(enlaces), len(self.MALOS) + 1)
        self.assertEqual(enlaces.pop("f:500"), ENLACE_BUENO)
        self.assertEqual(set(enlaces.values()), {None})
        self.assertEqual(revisar(pagina.html).hrefs, [])  # y la lista de la página es texto, sin enlaces

    def test_la_pagina_no_confia_en_que_la_lectura_ya_validara(self):
        # Una novedad armada a mano con un enlace que la lectura habría rechazado: no sale como enlace.
        self.assertIsNone(enlace_seguro("javascript:alert(1)"))
        self.assertIsNone(enlace_seguro("ftp://ejemplo.com/"))
        self.assertEqual(enlace_seguro("HTTPS://EJEMPLO.COM:443/a"), "https://ejemplo.com/a")


class TestPoliticaDeContenido(unittest.TestCase):
    def test_el_hash_de_la_meta_es_el_del_style_de_la_pagina_generada(self):
        pagina = construir(corrida_hostil("<script>alert(1)</script>"))
        revisor = revisar(pagina.html)
        metas = [m for m in revisor.metas if m.get("http-equiv") == "Content-Security-Policy"]
        self.assertEqual(len(metas), 1)
        politica = metas[0]["content"]
        # El hash se calcula acá, sobre lo que el navegador va a leer entre <style> y </style>.
        calculado = "'sha256-" + base64.b64encode(hashlib.sha256(revisor.estilo.encode("utf-8")).digest()).decode() + "'"
        directivas = {}
        for parte in politica.split(";"):
            nombre, *fuentes = parte.split()
            directivas[nombre] = fuentes
        self.assertEqual(directivas["style-src"], [calculado])
        self.assertEqual(revisor.estilo, ESTILO)
        self.assertEqual(revisar(pagina.html).etiquetas["style"], 1)

    def test_la_politica_no_permite_nada_de_mas(self):
        directivas = {}
        for parte in CSP.split(";"):
            nombre, *fuentes = parte.split()
            directivas[nombre] = fuentes
        self.assertEqual(directivas["default-src"], ["'none'"])
        self.assertEqual(directivas["base-uri"], ["'none'"])
        self.assertEqual(directivas["form-action"], ["'none'"])
        self.assertEqual(set(directivas), {"default-src", "style-src", "base-uri", "form-action"})
        for prohibido in ("unsafe-inline", "unsafe-eval", "data:", "http:", "https:", "*"):
            self.assertNotIn(prohibido, CSP)

    def test_sin_javascript_ni_recursos_de_afuera(self):
        pagina = construir(corrida_hostil("texto"))
        revisor = revisar(pagina.html)
        for etiqueta in ("script", "link", "img", "iframe", "object", "embed", "form", "base"):
            self.assertEqual(revisor.etiquetas[etiqueta], 0, etiqueta)
        self.assertNotRegex(pagina.html, r"(?i)\bon[a-z]+\s*=")
        self.assertNotRegex(pagina.html, r"(?i)\sstyle\s*=")
        self.assertNotRegex(pagina.html, r"(?i)src\s*=")

    def test_el_hash_sale_del_texto_del_estilo(self):
        # El hash sale del texto del estilo: un estilo distinto no puede heredar el hash viejo.
        from radar.pagina.estilo import hash_de_estilo

        self.assertNotEqual(hash_de_estilo(ESTILO), hash_de_estilo(ESTILO + " "))
        self.assertIn(hash_de_estilo(ESTILO), CSP)


class TestLogo(unittest.TestCase):
    def test_el_logo_es_el_de_la_propuesta(self):
        propuesta = (RAIZ / "docs" / "propuesta.html").read_text(encoding="utf-8")
        encontrado = re.search(r'<svg class="logo".*?</svg>', propuesta, re.DOTALL)
        self.assertIsNotNone(encontrado)
        normalizado = re.sub(r">\s+<", "><", encontrado.group(0)).strip()
        self.assertEqual(LOGO, normalizado)
        self.assertIn("currentColor", LOGO)

    def test_el_logo_va_en_la_pagina(self):
        pagina = construir(corrida_de([]))
        self.assertIn(LOGO, pagina.html)


class TestTexto(unittest.TestCase):
    def test_plano_una_sola_linea_sin_invisibles(self):
        self.assertEqual(plano("a\n\n b\t\tc\u2028d"), "a b c d")
        self.assertEqual(plano("a\u202eb\u200bc\x00d\ud800e"), "abcde")
        self.assertEqual(plano(None), "")
        self.assertEqual(plano(123), "123")

    def test_plano_acota(self):
        self.assertEqual(len(plano("x" * 100_000, 50)), 50)
        self.assertTrue(plano("x" * 100_000, 50).endswith("…"))

    def test_plano_quita_el_selector_de_variacion(self):
        self.assertEqual(plano("a️b󠄀c︎"), "abc")

    def test_plano_en_el_limite(self):
        justo, de_mas = "x" * 50, "x" * 51
        self.assertEqual(plano(justo, 50), justo)  # justo en el límite: intacto, sin puntos suspensivos
        self.assertEqual(plano(de_mas, 50), "x" * 49 + "…")  # un carácter de más: se recorta con el signo
        self.assertEqual(len(plano(de_mas, 50)), 50)

    def test_plano_con_un_texto_enorme_respeta_el_corte_previo(self):
        # Sólo se miran los primeros `limite * 4` caracteres: un texto de millones de caracteres no se recorre entero.
        self.assertEqual(plano("x" * 10_000_000, 50), "x" * 49 + "…")
        self.assertEqual(plano("xxx" + "​" * 100 + "yyyy", 5), "xxx")
        self.assertEqual(plano("xxx" + "​" * 10 + "yyyy", 5), "xxxy…")

    def test_e_escapa_comillas_y_menores(self):
        self.assertEqual(e('<a href="x">&\'</a>'), "&lt;a href=&quot;x&quot;&gt;&amp;&#x27;&lt;/a&gt;")


if __name__ == "__main__":
    unittest.main()
