"""Entradas armadas a propósito (habilidad `entradas-adversariales`).

Puntos de entrada de la lectura: el cuerpo de cada fuente (feed XML o página HTML), cada campo de
texto que sale de él (título, resumen, id), los enlaces, las fechas y los argumentos de la línea
de comandos (en test_main). Las respuestas de la red y el tope del fetcher están en test_descarga.
"""

import time
import unittest
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from radar.lectura import FUENTES, leer, por_id
from radar.lectura.analizar import _Constructor, analizar_atom, analizar_rss, parsear_xml
from radar.lectura.limpieza import (
    fecha_rfc2822,
    fecha_rfc3339,
    limpiar_texto,
    sin_futuro,
    validar_enlace,
)
from radar.lectura.modelos import MAX_ITEMS, DocumentoRechazado
from radar.lectura.paginas import analizar_anthropic_noticias, analizar_claude_plataforma, analizar_metr

AHORA = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
RSS = por_id("openai")
ATOM = por_id("claude-code")


def rss(*items: str) -> str:
    return f'<rss version="2.0"><channel>{"".join(items)}</channel></rss>'


def item(titulo="T", link="https://ejemplo.test/a", extra="") -> str:
    return f"<item><title>{titulo}</title><link>{link}</link>{extra}</item>"


def sin_invisibles(texto: str) -> bool:
    return not any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in texto) and "  " not in texto


# ------------------------------------------------------------------------- DOCTYPE


BOMBA = (
    '<?xml version="1.0"?>\n<!DOCTYPE lolz [\n'
    '<!ENTITY a "aaaaaaaaaa">\n<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">\n'
    '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">\n<!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">\n]>\n'
    '<rss version="2.0"><channel><item><title>&d;</title><link>https://ejemplo.test/a</link></item></channel></rss>'
)
EXTERNA = (
    '<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    '<rss version="2.0"><channel><item><title>&x;</title><link>https://ejemplo.test/a</link></item></channel></rss>'
)
SOLO_DOCTYPE = '<!DOCTYPE rss><rss version="2.0"><channel>' + item() + "</channel></rss>"


class DoctypeTest(unittest.TestCase):
    def test_bomba_de_expansion_se_rechaza_rapido(self):
        inicio = time.monotonic()
        with self.assertRaises(DocumentoRechazado) as ctx:
            analizar_rss(RSS, BOMBA, AHORA)
        self.assertIn("DOCTYPE", str(ctx.exception))
        self.assertLess(time.monotonic() - inicio, 2)

    def test_entidad_externa_se_rechaza_por_el_doctype_y_no_por_otra_cosa(self):
        with self.assertRaises(DocumentoRechazado) as ctx:
            analizar_rss(RSS, EXTERNA, AHORA)
        self.assertIn("DOCTYPE", str(ctx.exception))

    def test_formas_equivalentes_de_doctype_se_rechazan_todas(self):
        formas = {
            "mayusculas": SOLO_DOCTYPE,
            "minusculas": SOLO_DOCTYPE.replace("DOCTYPE", "doctype"),
            "mezcla": SOLO_DOCTYPE.replace("DOCTYPE", "DocType"),
            "BOM antes": "﻿" + SOLO_DOCTYPE,
            "espacios y saltos antes": "  \n\t " + SOLO_DOCTYPE,
            "BOM y espacios": "﻿ \n" + SOLO_DOCTYPE,
            "declaracion xml antes": '<?xml version="1.0"?>' + SOLO_DOCTYPE,
            "espacio tras <!": SOLO_DOCTYPE.replace("<!DOCTYPE", "<! DOCTYPE"),
            "salto tras <!": SOLO_DOCTYPE.replace("<!DOCTYPE", "<!\nDOCTYPE"),
        }
        for nombre, texto in formas.items():
            with self.subTest(nombre):
                with self.assertRaises(DocumentoRechazado) as ctx:
                    analizar_rss(RSS, texto, AHORA)
                self.assertIn("DOCTYPE", str(ctx.exception))
                with self.assertRaises(DocumentoRechazado) as ctx:
                    parsear_xml(texto)
                self.assertIn("DOCTYPE", str(ctx.exception))

    def test_tambien_en_atom(self):
        texto = '<!DOCTYPE feed [<!ENTITY x "y">]><feed xmlns="http://www.w3.org/2005/Atom"/>'
        with self.assertRaises(DocumentoRechazado) as ctx:
            analizar_atom(ATOM, texto, AHORA)
        self.assertIn("DOCTYPE", str(ctx.exception))

    def test_segunda_barrera_el_analizador_de_xml_rechaza_el_doctype_por_si_solo(self):
        parser = ET.XMLParser(target=_Constructor())
        with self.assertRaises(DocumentoRechazado):
            parser.feed(BOMBA)
            parser.close()

    def test_segunda_barrera_esta_conectada_aunque_la_primera_no_vea_nada(self):
        from unittest import mock

        from radar.lectura import analizar as modulo

        for texto in (BOMBA, EXTERNA, SOLO_DOCTYPE):
            with mock.patch.object(modulo, "_revisar_prologo", lambda t: None):  # la primera barrera, rota
                with self.assertRaises(DocumentoRechazado) as ctx:
                    parsear_xml(texto)
            self.assertIn("DOCTYPE", str(ctx.exception))

    def test_la_fuente_con_doctype_queda_en_error_y_las_demas_siguen(self):
        textos = {RSS.url: BOMBA, ATOM.url: "<feed xmlns='http://www.w3.org/2005/Atom'/>"}
        _, cobertura = leer([RSS, ATOM], AHORA - timedelta(days=28), AHORA, lambda u: textos[u], ahora=AHORA)
        self.assertEqual(cobertura["openai"].estado, "error")
        self.assertIn("DOCTYPE", cobertura["openai"].motivo)
        self.assertEqual(cobertura["claude-code"].estado, "vacia")


# --------------------------------------------------------------------- XML y tamaño


class XmlHostilTest(unittest.TestCase):
    def test_xml_roto_se_rechaza_con_error_propio(self):
        casos = {
            "cortado": '<rss version="2.0"><channel><item><title>T',
            "etiquetas cruzadas": "<rss><a></b></rss>",
            "vacio": "",
            "solo BOM": "﻿",
            "solo espacios": "   \n ",
            "no es xml": "esto no es xml",
            "byte nulo": '<rss version="2.0">\x00</rss>',
            "caracter de control": '<rss version="2.0">\x01</rss>',
            "surrogate suelto": '<rss version="2.0">\ud800</rss>',
            "dos raices": "<rss/><rss/>",
        }
        for nombre, texto in casos.items():
            with self.subTest(nombre):
                with self.assertRaises(DocumentoRechazado):
                    analizar_rss(RSS, texto, AHORA)

    def test_una_raiz_que_no_es_la_esperada_se_rechaza(self):
        with self.assertRaises(DocumentoRechazado):
            analizar_rss(RSS, "<html><body>Error 200 con una página de bloqueo</body></html>", AHORA)
        with self.assertRaises(DocumentoRechazado):
            analizar_atom(ATOM, rss(item()), AHORA)

    def test_bom_y_declaracion_de_otra_codificacion_se_leen_igual(self):
        base = rss(item("Árbol"))
        formas = [
            base,
            "﻿" + base,
            '<?xml version="1.0" encoding="UTF-8"?>' + base,
            '<?xml version="1.0" encoding="ISO-8859-1"?>' + base,  # el texto ya viene decodificado
            "\n\n" + base,
            base.replace("\n", "\r\n"),
        ]
        for i, texto in enumerate(formas):
            with self.subTest(i):
                self.assertEqual(analizar_rss(RSS, texto, AHORA).novedades[0].titulo, "Árbol")

    def test_acentos_compuestos_de_otra_forma_dan_el_mismo_titulo_visible(self):
        nfc = unicodedata.normalize("NFC", "Evaluación")
        nfd = unicodedata.normalize("NFD", "Evaluación")
        a = analizar_rss(RSS, rss(item(nfc)), AHORA).novedades[0].titulo
        b = analizar_rss(RSS, rss(item(nfd)), AHORA).novedades[0].titulo
        self.assertEqual(unicodedata.normalize("NFC", a), unicodedata.normalize("NFC", b))

    def test_mas_de_500_items_se_leen_500(self):
        items = [item(f"t{i}", f"https://ejemplo.test/{i}") for i in range(MAX_ITEMS + 100)]
        ext = analizar_rss(RSS, rss(*items), AHORA)
        self.assertEqual(len(ext.novedades), MAX_ITEMS)
        self.assertEqual(ext.recortadas, 100)
        atom = "<feed xmlns='http://www.w3.org/2005/Atom'>" + "".join(
            f"<entry><id>{i}</id><title>t</title><link href='https://ejemplo.test/{i}'/></entry>"
            for i in range(MAX_ITEMS + 1)
        ) + "</feed>"
        ext = analizar_atom(ATOM, atom, AHORA)
        self.assertEqual((len(ext.novedades), ext.recortadas), (MAX_ITEMS, 1))

    def test_el_anidamiento_pasado_el_tope_se_rechaza_y_el_del_tope_se_acepta(self):
        from radar.lectura.analizar import MAX_PROFUNDIDAD

        def con_niveles(n):  # rss > channel > item > title son 4 niveles; el resto, <a> anidados
            profundo = "<a>" * n + "x" + "</a>" * n
            return rss(f"<item><title>{profundo}</title><link>https://ejemplo.test/a</link></item>")

        self.assertEqual(MAX_PROFUNDIDAD, 256)
        ext = analizar_rss(RSS, con_niveles(MAX_PROFUNDIDAD - 4), AHORA)
        self.assertEqual(ext.novedades[0].titulo, "x")  # justo en el tope: pasa
        with self.assertRaises(DocumentoRechazado) as ctx:
            analizar_rss(RSS, con_niveles(MAX_PROFUNDIDAD - 3), AHORA)  # un nivel más: no
        self.assertIn("anidado", str(ctx.exception))

    def test_anidamiento_enorme_se_rechaza_rapido_y_sin_memoria(self):
        import tracemalloc

        profundo = '<rss version="2.0">' + "<a>" * 1_700_000  # unos 5 MB
        tracemalloc.start()
        try:
            inicio = time.monotonic()
            with self.assertRaises(DocumentoRechazado):
                parsear_xml(profundo)
            demora = time.monotonic() - inicio
            _, pico = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(demora, 5)
        self.assertLess(pico, 100 * 1024 * 1024)  # el tope pedido; en la práctica son unos pocos MB

    def test_un_rss_de_5_mb_con_relleno_hostil_en_cdata_tarda_poco(self):
        relleno = "<" * 19900
        items = "".join(
            f"<item><title><![CDATA[{relleno}]]></title><link>https://ejemplo.test/{i}</link>"
            f"<description><![CDATA[{relleno}]]></description></item>"
            for i in range(131)
        )
        texto = rss(items)
        self.assertGreater(len(texto), 5_000_000)
        inicio = time.monotonic()
        ext = analizar_rss(RSS, texto, AHORA)
        self.assertLess(time.monotonic() - inicio, 10)
        self.assertEqual((len(ext.novedades), ext.descartadas), (0, 131))  # el relleno no es un título: se descarta

    def test_texto_enorme_se_recorta(self):
        enorme = "palabra " * 200000  # 1,6 MB en un solo título y un solo resumen
        ext = analizar_rss(RSS, rss(item(enorme, extra=f"<description>{enorme}</description>")), AHORA)
        n = ext.novedades[0]
        self.assertLessEqual(len(n.titulo), 300)
        self.assertLessEqual(len(n.frase), 400)


# ------------------------------------------------------------------ texto y controles


class TextoHostilTest(unittest.TestCase):
    def test_controles_bidi_y_ancho_cero_salen_del_texto(self):
        sucio = (
            "Hola\x00\x01\x1b[31m mundo‮‪‫‬‭ "  # controles y dirección
            "⁦a⁧b⁨c⁩ ​cero‌‍⁠﻿"  # dirección aislada y ancho cero
            "\x85\x9f fin  "  # controles C1 y separadores de línea
        )
        limpio = limpiar_texto(sucio, 300)
        self.assertTrue(sin_invisibles(limpio), repr(limpio))
        self.assertEqual(limpio, "Hola[31m mundo abc cero fin")

    def test_los_espacios_se_colapsan(self):
        self.assertEqual(limpiar_texto("  a \t\n\r  b  c  ", 300), "a b c")

    def test_en_un_feed_bidi_y_ancho_cero_se_quitan_del_titulo_y_de_la_frase(self):
        texto = rss(
            item(
                "Gana‮ un​ premio⁦!⁩",
                extra="<description>Resumen‮ raro.​ Segunda.</description>",
            )
        )
        n = analizar_rss(RSS, texto, AHORA).novedades[0]
        self.assertEqual(n.titulo, "Gana un premio!")
        self.assertEqual(n.frase, "Resumen raro.")

    def test_etiquetas_y_scripts_salen_y_las_entidades_se_resuelven(self):
        casos = {
            "<script>alert(1)</script>Titulo<b>!</b>": "Titulo!",
            "<style>p{}</style>Uno<br>Dos": "Uno Dos",
            "Tom &amp; Jerry &lt;3": "Tom & Jerry <3",
            "<a href='javascript:alert(1)'>click</a>": "click",
            "<img src=x onerror=alert(1)>Sano": "Sano",
        }
        for crudo, esperado in casos.items():
            with self.subTest(crudo):
                self.assertEqual(limpiar_texto(crudo, 300), esperado)

    def test_titulo_escapado_dos_veces_tambien_sale_sin_etiquetas(self):
        # &amp;lt;script&amp;gt; llega al texto como &lt;script&gt;, que se vuelve <script>
        doble = "&amp;lt;b&amp;gt;negrita&amp;lt;/b&amp;gt;&amp;lt;script&amp;gt;alert(1)&amp;lt;/script&amp;gt;fin"
        n = analizar_rss(RSS, rss(item(doble)), AHORA).novedades[0]
        self.assertEqual(n.titulo, "negritafin")
        self.assertNotIn("<", n.titulo)

    def test_texto_que_termina_en_ampersand_no_se_pierde(self):
        self.assertEqual(limpiar_texto("Preguntas&Q", 300), "Preguntas&Q")
        self.assertEqual(limpiar_texto("Q&A", 300), "Q&A")

    def test_construcciones_sin_cerrar_no_cuelgan_el_analizador_de_html(self):
        # En Python 3.12.10 HTMLParser.close() tarda más de 20 s con 200 KB de esto: no se llama.
        for crudo in ("<!--" * 5000, "<?" * 10000, "<a href='" * 3000, "<" * 20000, "<![CDATA[" * 3000):
            with self.subTest(crudo[:12]):
                inicio = time.monotonic()
                limpiar_texto(crudo, 300)
                self.assertLess(time.monotonic() - inicio, 2)

    def test_titulo_y_frase_tienen_tope(self):
        n = analizar_rss(RSS, rss(item("x" * 1000, extra="<description>" + "y" * 1000 + "</description>")), AHORA).novedades[0]
        self.assertEqual(len(n.titulo), 300)
        self.assertEqual(len(n.frase), 400)
        self.assertTrue(n.titulo.endswith("…"))

    def test_frase_es_la_primera_oracion(self):
        n = analizar_rss(RSS, rss(item(extra="<description>Una. Dos! Tres?</description>")), AHORA).novedades[0]
        self.assertEqual(n.frase, "Una.")
        n = analizar_rss(RSS, rss(item(extra="<description>v2.1.296 salió hoy sin punto final</description>")), AHORA).novedades[0]
        self.assertEqual(n.frase, "v2.1.296 salió hoy sin punto final")

    def test_sin_titulo_se_usa_el_enlace(self):
        n = analizar_rss(RSS, rss(item("")), AHORA).novedades[0]
        self.assertEqual(n.titulo, "https://ejemplo.test/a")

    def test_guid_hostil_o_enorme_no_rompe_la_clave(self):
        enorme = "g" * 5000
        a = analizar_rss(RSS, rss(item(extra=f"<guid>{enorme}</guid>")), AHORA).novedades[0]
        self.assertLess(len(a.clave), 100)
        b = analizar_rss(RSS, rss(item(extra=f"<guid>{enorme}</guid>")), AHORA).novedades[0]
        self.assertEqual(a.clave, b.clave)  # estable
        c = analizar_rss(RSS, rss(item(extra=f"<guid>{enorme[:-1]}h</guid>")), AHORA).novedades[0]
        self.assertNotEqual(a.clave, c.clave)


# ----------------------------------------------------------------------------- enlaces

JAVASCRIPT_Y_OTROS = [
    "javascript:alert(1)",
    "JAVASCRIPT:alert(1)",
    "JaVaScRiPt:alert(1)",
    " javascript:alert(1)",
    "\tjavascript:alert(1)",
    "\n\r javascript:alert(1)",
    "\x01javascript:alert(1)",
    "\x00javascript:alert(1)",
    "​javascript:alert(1)",
    "‮javascript:alert(1)",
    " javascript:alert(1)",
    "java\tscript:alert(1)",
    "java\nscript:alert(1)",
    "java\rscript:alert(1)",
    "java\x00script:alert(1)",
    "java script:alert(1)",
    "java​script:alert(1)",
    "javascript\t:alert(1)",
    "&#106;avascript:alert(1)",
    "&#x6A;avascript:alert(1)",
    "&#x6a;avascript:alert(1)",
    "&#0000106;avascript:alert(1)",
    "&#106avascript:alert(1)",
    "j&#97;vascript:alert(1)",
    "javascript&colon;alert(1)",
    "javascript&#58;alert(1)",
    "javascript&#x3A;alert(1)",
    "java&Tab;script:alert(1)",
    "java&#09;script:alert(1)",
    "java&NewLine;script:alert(1)",
    "&amp;#106;avascript:alert(1)",  # escapado dos veces
    "&amp;amp;#106;avascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "DATA:text/html;base64,PHNjcmlwdD4=",
    " data:text/html,hola",
    "da\tta:text/html,hola",
    "&#100;ata:text/html,hola",
    "vbscript:msgbox(1)",
    "VBScript:msgbox(1)",
    "vb&#115;cript:msgbox(1)",
    "file:///etc/passwd",
    "FILE://host/share",
    "ftp://ejemplo.test/x",
    "mailto:a@ejemplo.test",
    "tel:+5491100000000",
    "blob:https://ejemplo.test/uuid",
    "about:blank",
    "x:y",
]


class EnlacesTest(unittest.TestCase):
    def test_otros_esquemas_en_todas_sus_formas_equivalentes_se_rechazan(self):
        for forma in JAVASCRIPT_Y_OTROS:
            with self.subTest(forma):
                self.assertIsNone(validar_enlace(forma))
                self.assertIsNone(validar_enlace(forma, "https://www.anthropic.com/news"))

    def test_la_forma_de_cada_esquema_tambien_se_rechaza_dentro_de_un_feed(self):
        for forma in JAVASCRIPT_Y_OTROS:
            with self.subTest(forma):
                # sólo lo que el XML admite en texto; los controles van en el prueba directa de arriba
                if any(ord(c) < 0x20 and c not in "\t\n\r" for c in forma):
                    continue
                escapado = forma.replace("&", "&amp;").replace("<", "&lt;")
                ext = analizar_rss(RSS, rss(f"<item><title>t</title><link>{escapado}</link></item>"), AHORA)
                self.assertEqual(ext.novedades, [])
                self.assertEqual(ext.descartadas, 1)

    def test_atom_con_enlace_peligroso_se_descarta(self):
        texto = (
            "<feed xmlns='http://www.w3.org/2005/Atom'><entry><id>1</id><title>t</title>"
            "<link rel='alternate' href='javascript:alert(1)'/></entry></feed>"
        )
        ext = analizar_atom(ATOM, texto, AHORA)
        self.assertEqual((ext.novedades, ext.descartadas), ([], 1))

    def test_usuario_y_clave_en_el_enlace_se_rechazan(self):
        for malo in (
            "https://a:b@ejemplo.test/",
            "https://user@ejemplo.test/",
            "https://:clave@ejemplo.test/",
            "https://www.anthropic.com@evil.test/",
            "http://a:b@ejemplo.test:8080/x",
        ):
            with self.subTest(malo):
                self.assertIsNone(validar_enlace(malo))

    def test_enlaces_que_el_navegador_leeria_distinto_se_rechazan(self):
        for malo in (
            "https://ejemplo.test\\@evil.test/",
            "https://evil.test\\.ejemplo.test/",
            "https:\\\\evil.test/",
            "https:evil.test",
            "https://",
            "https:///x",
            "http://[::1/x",
            "https://ejemplo.test:99999/",
            "https://ejemplo.test:abc/",
            'https://ejemplo.test/"onmouseover="x',
            "https://ejemplo.test/<script>",
            "https://ejemplo.test/a b",
            "https://ejemplo.test/a\nb",
            "https://ejemplo.test/" + "a" * 3000,
            "",
            "   ",
            None,
            123,
        ):
            with self.subTest(malo):
                self.assertIsNone(validar_enlace(malo))

    def test_validos_se_normalizan_igual_en_todas_sus_formas(self):
        base = "https://ejemplo.test/a?x=1"
        for forma in (
            "https://ejemplo.test/a?x=1",
            "HTTPS://EJEMPLO.TEST/a?x=1",
            "  https://ejemplo.test/a?x=1  ",
            "\nhttps://ejemplo.test/a?x=1\n",
            "https://ejemplo.test:443/a?x=1",
            "https://ejemplo.test/a?x=1#parte",
        ):
            with self.subTest(forma):
                self.assertEqual(validar_enlace(forma), base)
        self.assertEqual(validar_enlace("http://ejemplo.test"), "http://ejemplo.test/")
        self.assertEqual(validar_enlace("http://ejemplo.test:80/"), "http://ejemplo.test/")
        self.assertEqual(validar_enlace("https://ejemplo.test:8443/"), "https://ejemplo.test:8443/")

    def test_un_parametro_que_parece_entidad_no_se_toca(self):
        self.assertEqual(validar_enlace("https://ejemplo.test/?a=1&copy=2&para=3"), "https://ejemplo.test/?a=1&copy=2&para=3")

    def test_relativos_se_resuelven_contra_la_fuente_y_sin_fuente_se_rechazan(self):
        base = "https://www.anthropic.com/news"
        self.assertEqual(validar_enlace("/news/x", base), "https://www.anthropic.com/news/x")
        self.assertEqual(validar_enlace("x", base), "https://www.anthropic.com/x")
        self.assertEqual(validar_enlace("../a", "https://ejemplo.test/b/c/"), "https://ejemplo.test/b/a")
        self.assertEqual(validar_enlace("//otro.test/z", base), "https://otro.test/z")
        self.assertIsNone(validar_enlace("/news/x"))
        self.assertIsNone(validar_enlace("ejemplo.test/a"))


class PaginasHostilesTest(unittest.TestCase):
    def test_enlace_relativo_en_pagina_se_resuelve_contra_la_url_de_la_fuente(self):
        fuente = por_id("anthropic-noticias")
        html = '<a href="/news/uno">Uno</a><a href="news/dos">Dos</a><a href="./tres-sin-prefijo">x</a>'
        ext = analizar_anthropic_noticias(fuente, html, AHORA)
        self.assertEqual(
            [n.enlace for n in ext.novedades],
            ["https://www.anthropic.com/news/uno", "https://www.anthropic.com/news/dos"],
        )

    def test_enlaces_de_pagina_con_esquema_peligroso_o_clave_no_dan_novedades(self):
        fuente = por_id("anthropic-noticias")
        html = (
            '<a href="javascript:alert(1)">/news/x</a>'
            '<a href="&#106;avascript:alert(1)">/news/y</a>'
            '<a href="data:text/html,/news/z">z</a>'
            '<a href="https://u:p@www.anthropic.com/news/con-clave">con clave</a>'
            '<a href="https://evil.test/news/ajeno">ajeno</a>'
            '<a href="/news/bueno">Bueno</a>'
        )
        ext = analizar_anthropic_noticias(fuente, html, AHORA)
        self.assertEqual([n.titulo for n in ext.novedades], ["Bueno"])
        self.assertEqual(ext.descartadas, 1)  # el de la clave: cumple el camino y el servidor, pero no el filtro

    def test_pagina_con_controles_y_bidi_en_el_texto_del_enlace(self):
        fuente = por_id("anthropic-noticias")
        html = '<a href="/news/a"><span>Oct 8, 2026</span><span>Titulo\x1b[0m‮ raro​</span></a>'
        n = analizar_anthropic_noticias(fuente, html, AHORA).novedades[0]
        self.assertEqual(n.titulo, "Titulo[0m raro")
        self.assertTrue(sin_invisibles(n.titulo))

    def test_fecha_basura_en_una_pagina_es_sin_fecha(self):
        fuente = por_id("metr")
        html = '<a href="/blog/2026-13-45-x/">Mes trece</a><a href="/blog/0000-01-01-y/">Año cero</a>'
        ext = analizar_metr(fuente, html, AHORA)
        self.assertEqual([n.fecha for n in ext.novedades], [None, None])
        self.assertEqual(len(ext.novedades), 2)

    def test_pagina_gigante_o_sin_cerrar_se_procesa_en_tiempo_acotado(self):
        for nombre, crudo in {
            "comentarios": "<!--" * 120000,
            "pi": "<?" * 120000,
            "enlaces sin cerrar": "<a href='/blog/x' " * 25000,
            "anidada": "<h3>October 9, 2026</h3>" + "<ul><li>x" * 20000,
        }.items():
            with self.subTest(nombre):
                inicio = time.monotonic()
                analizar_metr(por_id("metr"), crudo, AHORA)
                analizar_claude_plataforma(por_id("claude-plataforma"), crudo, AHORA)
                self.assertLess(time.monotonic() - inicio, 10)

    def test_plataforma_ignora_li_de_menu_y_de_antes_de_la_primera_fecha(self):
        html = "<ul><li>Menu</li></ul><h3>October 9, 2026</h3><ul><li>Real</li></ul><h2>Otra seccion</h2><ul><li>Suelto</li></ul>"
        ext = analizar_claude_plataforma(por_id("claude-plataforma"), html, AHORA)
        self.assertEqual([n.titulo for n in ext.novedades], ["Real"])

    def test_plataforma_li_sin_cerrar_y_anidados(self):
        html = "<h3>October 9, 2026</h3><ul><li>Uno<li>Dos<ul><li>Dos A</li></ul></ul>"
        ext = analizar_claude_plataforma(por_id("claude-plataforma"), html, AHORA)
        self.assertEqual([n.titulo for n in ext.novedades], ["Uno", "Dos A", "Dos"])

    def test_plataforma_dos_li_iguales_el_mismo_dia_son_una_sola(self):
        html = "<h3>October 9, 2026</h3><ul><li>Igual</li><li>Igual</li></ul>"
        ext = analizar_claude_plataforma(por_id("claude-plataforma"), html, AHORA)
        self.assertEqual((len(ext.novedades), ext.descartadas), (1, 1))


# ----------------------------------------------------------------------------- fechas


class FechasTest(unittest.TestCase):
    def test_fecha_en_el_futuro_no_queda_fija_en_la_ventana(self):
        futuro = rss(item("t", extra="<pubDate>Wed, 01 Jan 2099 10:00:00 GMT</pubDate>"))
        n = analizar_rss(RSS, futuro, AHORA).novedades[0]
        self.assertIsNone(n.fecha)
        atom = (
            "<feed xmlns='http://www.w3.org/2005/Atom'><entry><id>1</id><title>t</title>"
            "<link href='https://ejemplo.test/a'/><published>2099-01-01T00:00:00Z</published></entry></feed>"
        )
        self.assertIsNone(analizar_atom(ATOM, atom, AHORA).novedades[0].fecha)

    def test_en_la_ventana_la_novedad_con_fecha_futura_entra_como_sin_fecha(self):
        futuro = rss(item("t", extra="<pubDate>Wed, 01 Jan 2099 10:00:00 GMT</pubDate>"))
        novedades, cobertura = leer([RSS], AHORA - timedelta(days=28), AHORA, lambda u: futuro, ahora=AHORA)
        self.assertEqual(len(novedades), 1)
        self.assertEqual(cobertura["openai"].sin_fecha, 1)

    def test_el_margen_es_un_dia(self):
        self.assertEqual(sin_futuro(AHORA + timedelta(hours=23), AHORA), AHORA + timedelta(hours=23))
        self.assertIsNone(sin_futuro(AHORA + timedelta(days=1, seconds=1), AHORA))
        self.assertEqual(sin_futuro(AHORA - timedelta(days=400), AHORA), AHORA - timedelta(days=400))
        self.assertIsNone(sin_futuro(None, AHORA))

    def test_fecha_basura_es_none_y_la_novedad_se_lee_igual(self):
        basura = rss(item("t", extra="<pubDate>ayer a la tarde</pubDate>"))
        n = analizar_rss(RSS, basura, AHORA).novedades[0]
        self.assertIsNone(n.fecha)

    def test_rfc3339_se_valida_entera(self):
        buenos = {
            "2026-10-09T18:00:00Z": datetime(2026, 10, 9, 18, tzinfo=timezone.utc),
            "2026-10-09t18:00:00z": datetime(2026, 10, 9, 18, tzinfo=timezone.utc),
            "2026-10-09T18:00:00+00:00": datetime(2026, 10, 9, 18, tzinfo=timezone.utc),
            "2026-10-09T18:00:00-03:00": datetime(2026, 10, 9, 21, tzinfo=timezone.utc),
            "2026-10-09T18:00:00.123456789Z": datetime(2026, 10, 9, 18, 0, 0, 123456, tzinfo=timezone.utc),
            "  2026-10-09T18:00:00Z\n": datetime(2026, 10, 9, 18, tzinfo=timezone.utc),  # espacios alrededor del texto del elemento
        }
        for valor, esperado in buenos.items():
            with self.subTest(valor):
                self.assertEqual(fecha_rfc3339(valor), esperado)
        for malo in (
            "2026-10-09T18:00:00Zabc",  # basura al final
            "2026-10-09T18:00:00Z\nZ",
            "2026-10-09T18:\n00:00Z",  # salto en el medio
            "2026-10-09",  # sin hora
            "2026-10-09T18:00:00",  # sin zona
            "2026-13-09T18:00:00Z",
            "2026-02-30T18:00:00Z",
            "2026-10-09T25:00:00Z",
            "2026-10-09T18:60:00Z",
            "2026-10-09T18:00:00+24:00",
            "2026-10-09T18:00:00+00:60",
            "0000-10-09T18:00:00Z",
            "２０２６-10-09T18:00:00Z",  # cifras de ancho completo
            "٢٠٢٦-10-09T18:00:00Z",  # cifras arábigo-índicas
            "-2026-10-09T18:00:00Z",
            "",
            "   ",
            "Z",
            None,
            20261009,
            "2026-10-09T18:00:00Z" * 10,
        ):
            with self.subTest(malo):
                self.assertIsNone(fecha_rfc3339(malo))

    def test_rfc2822(self):
        self.assertEqual(
            fecha_rfc2822("Mon, 12 Oct 2026 16:00:00 +0000"), datetime(2026, 10, 12, 16, tzinfo=timezone.utc)
        )
        self.assertEqual(
            fecha_rfc2822("Thu, 08 Oct 2026 09:30:00 -0300"), datetime(2026, 10, 8, 12, 30, tzinfo=timezone.utc)
        )
        self.assertEqual(
            fecha_rfc2822("Thu, 08 Oct 2026 09:30:00 -0000"), datetime(2026, 10, 8, 9, 30, tzinfo=timezone.utc)
        )
        for malo in ("", "no es fecha", "Mon, 99 Foo 2026 10:00:00 GMT", None, 5, "x" * 500, "Mon, 12 Oct 2026 99:99:99 GMT"):
            with self.subTest(malo):
                self.assertIsNone(fecha_rfc2822(malo))


class ConjuntoDeFuentesTest(unittest.TestCase):
    def test_ningun_documento_hostil_rompe_el_lector_con_otro_error(self):
        hostiles = [BOMBA, EXTERNA, "", "﻿", "<", "\x00" * 100, "<a>" * 100000, "&" * 1000, "\ud800"]
        for texto in hostiles:
            with self.subTest(texto[:20]):
                novedades, cobertura = leer(FUENTES, AHORA - timedelta(days=28), AHORA, lambda u: texto, ahora=AHORA)
                self.assertEqual(len(cobertura), 14)
                self.assertTrue(all(c.estado in ("ok", "vacia", "error") for c in cobertura.values()))


if __name__ == "__main__":
    unittest.main()
