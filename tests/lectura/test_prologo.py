"""El prólogo del XML: qué se acepta antes del primer elemento y por qué un DOCTYPE sólo se mira ahí.

Primera barrera: recorrer el prólogo. Segunda: el constructor del árbol. Un `<!DOCTYPE html>` dentro
del contenido de un ítem (CDATA o comentario) es texto y no puede tumbar la fuente entera.
"""

import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from radar.lectura import analizar as modulo
from radar.lectura import por_id
from radar.lectura.analizar import analizar_atom, analizar_rss, parsear_xml
from radar.lectura.modelos import DocumentoRechazado

AHORA = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
RSS = por_id("openai")
ATOM = por_id("claude-code")
CUERPO = (
    '<rss version="2.0"><channel><item><title>Hola</title><link>https://ejemplo.test/a</link>'
    "<description>Una frase.</description></item></channel></rss>"
)
DECLARACION = '<?xml version="1.0" encoding="UTF-8"?>'
BOMBA = (
    '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
    '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;"><!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">]>'
    '<rss version="2.0"><channel><item><title>&d;</title><link>https://ejemplo.test/a</link></item></channel></rss>'
)


class ProtegeElLstripTest(unittest.TestCase):
    """Espacios y BOM antes de la declaración XML se aceptan (los feeds reales los traen)."""

    PREFIJOS = {
        "salto": "\n",
        "espacio y tabulador": " \t",
        "CRLF": "\r\n",
        "BOM": "﻿",
        "BOM y salto": "﻿\n",
        "BOM, espacios, tabulador y CRLF": "﻿ \t\r\n",
        "varias lineas en blanco": "\r\n\r\n   \n",
        "BOM repetido": "﻿﻿",
    }

    def test_antes_de_la_declaracion_xml(self):
        for nombre, prefijo in self.PREFIJOS.items():
            with self.subTest(nombre):
                ext = analizar_rss(RSS, prefijo + DECLARACION + CUERPO, AHORA)
                self.assertEqual([n.titulo for n in ext.novedades], ["Hola"])

    def test_antes_del_elemento_raiz_sin_declaracion(self):
        for nombre, prefijo in self.PREFIJOS.items():
            with self.subTest(nombre):
                self.assertEqual(len(analizar_rss(RSS, prefijo + CUERPO, AHORA).novedades), 1)

    def test_con_saltos_crlf_en_todo_el_documento(self):
        texto = (DECLARACION + "\r\n" + CUERPO).replace("><", ">\r\n<")
        self.assertEqual(len(analizar_rss(RSS, "\r\n" + texto, AHORA).novedades), 1)

    def test_atom_tambien(self):
        feed = "<feed xmlns='http://www.w3.org/2005/Atom'><entry><id>1</id><title>T</title><link href='https://ejemplo.test/a'/></entry></feed>"
        for prefijo in ("\n", " \t", "\r\n", "﻿\r\n"):
            with self.subTest(repr(prefijo)):
                self.assertEqual(len(analizar_atom(ATOM, prefijo + DECLARACION + feed, AHORA).novedades), 1)


class DoctypeEnElContenidoTest(unittest.TestCase):
    def test_doctype_dentro_de_un_cdata_de_un_item_se_acepta(self):
        texto = (
            '<rss version="2.0"><channel><item><title>Hola</title><link>https://ejemplo.test/a</link>'
            "<description><![CDATA[<!DOCTYPE html><html><body><p>Un cuerpo en HTML.</p></body></html>]]></description>"
            "</item></channel></rss>"
        )
        ext = analizar_rss(RSS, texto, AHORA)
        self.assertEqual(len(ext.novedades), 1)
        self.assertEqual(ext.novedades[0].frase, "Un cuerpo en HTML.")

    def test_doctype_dentro_de_un_comentario_de_un_item_se_acepta(self):
        texto = (
            '<rss version="2.0"><channel><item><title>Hola</title><!-- <!DOCTYPE html> --><link>https://ejemplo.test/a</link>'
            "</item></channel></rss>"
        )
        self.assertEqual(len(analizar_rss(RSS, texto, AHORA).novedades), 1)

    def test_doctype_en_el_titulo_o_en_texto_se_acepta_como_texto(self):
        texto = (
            '<rss version="2.0"><channel><item><title>Sobre &lt;!DOCTYPE html&gt; y mas</title>'
            "<link>https://ejemplo.test/a</link></item></channel></rss>"
        )
        self.assertEqual(analizar_rss(RSS, texto, AHORA).novedades[0].titulo, "Sobre y mas")

    def test_la_fuente_con_un_cdata_asi_no_cae_y_las_demas_siguen(self):
        from radar.lectura import leer

        texto = (
            '<rss version="2.0"><channel><item><title>Hola</title><link>https://ejemplo.test/a</link>'
            "<description><![CDATA[<!DOCTYPE html>]]></description></item></channel></rss>"
        )
        _, cobertura = leer([RSS], AHORA, AHORA, lambda u: texto, ahora=AHORA)
        self.assertEqual(cobertura["openai"].estado, "ok")

    def test_comentarios_e_instrucciones_bien_cerrados_en_el_prologo_se_aceptan(self):
        casos = [
            '<?xml-stylesheet href="x.xsl" type="text/xsl"?>' + CUERPO,
            "<!-- un comentario -->" + CUERPO,
            DECLARACION + "<!-- uno --><!-- dos -->\n<?pi dato?>\n" + CUERPO,
            "<!-- <!DOCTYPE rss> -->" + CUERPO,  # un DOCTYPE comentado no es un DOCTYPE
            DECLARACION + "<!-- <!DOCTYPE html [<!ENTITY x 'y'>]> -->" + CUERPO,
            "<?uno dato?><?dos otro dato?>" + CUERPO,
        ]
        for i, texto in enumerate(casos):
            with self.subTest(i):
                self.assertEqual(len(analizar_rss(RSS, texto, AHORA).novedades), 1)


DOCTYPE = "<!DOCTYPE rss>"


class DoctypeEnElPrologoTest(unittest.TestCase):
    def rechaza(self, texto, mensaje="DOCTYPE"):
        with self.assertRaises(DocumentoRechazado) as ctx:
            parsear_xml(texto)
        self.assertIn(mensaje, str(ctx.exception))

    def test_todas_las_formas_equivalentes(self):
        formas = {
            "directo": DOCTYPE + CUERPO,
            "minusculas": "<!doctype rss>" + CUERPO,
            "mezcla": "<!DocType rss>" + CUERPO,
            "con espacio tras <!": "<! DOCTYPE rss>" + CUERPO,
            "con salto tras <!": "<!\nDOCTYPE rss>" + CUERPO,
            "BOM": "﻿" + DOCTYPE + CUERPO,
            "espacios y saltos": "  \n\t " + DOCTYPE + CUERPO,
            "tras la declaracion": DECLARACION + DOCTYPE + CUERPO,
            "tras la declaracion con saltos": DECLARACION + "\r\n\r\n" + DOCTYPE + CUERPO,
            "con subconjunto interno": '<!DOCTYPE rss [<!ENTITY x "y">]>' + CUERPO,
            "con identificador externo": '<!DOCTYPE rss SYSTEM "http://ejemplo.test/x.dtd">' + CUERPO,
            "tras un comentario": "<!-- c -->" + DOCTYPE + CUERPO,
            "tras dos comentarios": "<!-- c --><!-- d -->" + DOCTYPE + CUERPO,
            "tras una instruccion": "<?pi dato?>" + DOCTYPE + CUERPO,
            "tras la declaracion y un comentario": DECLARACION + "<!-- c -->\n" + DOCTYPE + CUERPO,
            "tras un comentario con un DOCTYPE adentro": "<!-- <!DOCTYPE x> -->" + DOCTYPE + CUERPO,
        }
        for nombre, texto in formas.items():
            with self.subTest(nombre):
                self.rechaza(texto)
                with self.assertRaises(DocumentoRechazado):
                    analizar_rss(RSS, texto, AHORA)

    def test_un_prologo_ilegible_se_rechaza(self):
        casos = {
            "comentario sin cerrar": "<!-- nunca cierra " + CUERPO,
            "instruccion sin cerrar": "<?pi nunca cierra " + CUERPO,
            "texto antes del elemento": "hola" + CUERPO,
            "CDATA en el prologo": "<![CDATA[x]]>" + CUERPO,
            "declaracion desconocida": "<!ELEMENT rss ANY>" + CUERPO,
            "sin elemento": DECLARACION,
            "solo un comentario": "<!-- c -->",
            "vacio": "",
            "solo BOM y espacios": "﻿ \n",
            "no es xml": "esto no es xml",
        }
        for nombre, texto in casos.items():
            with self.subTest(nombre):
                with self.assertRaises(DocumentoRechazado):
                    parsear_xml(texto)

    def test_un_doctype_despues_de_la_raiz_tambien_cae(self):
        with self.assertRaises(DocumentoRechazado):
            parsear_xml("<rss/>" + DOCTYPE)

    def test_un_doctype_en_medio_del_documento_cae_aunque_la_primera_barrera_no_lo_vea(self):
        with self.assertRaises(DocumentoRechazado):
            parsear_xml('<rss version="2.0"><channel>' + DOCTYPE + "</channel></rss>")


class SegundaBarreraTest(unittest.TestCase):
    """La bomba de expansión y la entidad externa caen aunque se rompa a propósito la primera barrera."""

    def test_la_bomba_se_rechaza_rapido_sin_la_primera_barrera(self):
        with mock.patch.object(modulo, "_revisar_prologo", lambda t: None):
            inicio = time.monotonic()
            with self.assertRaises(DocumentoRechazado) as ctx:
                analizar_rss(RSS, BOMBA, AHORA)
            self.assertLess(time.monotonic() - inicio, 2)
        self.assertIn("DOCTYPE", str(ctx.exception))

    def test_cada_forma_con_doctype_cae_por_la_segunda_barrera(self):
        formas = [
            DOCTYPE + CUERPO,
            '<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]>' + CUERPO,
            DECLARACION + '<!DOCTYPE rss [<!ENTITY x "y">]>' + CUERPO,
        ]
        for i, texto in enumerate(formas):
            with self.subTest(i):
                with mock.patch.object(modulo, "_revisar_prologo", lambda t: None):
                    with self.assertRaises(DocumentoRechazado) as ctx:
                        parsear_xml(texto)
                self.assertIn("DOCTYPE", str(ctx.exception))

    def test_la_bomba_se_rechaza_rapido_con_las_dos_barreras(self):
        inicio = time.monotonic()
        with self.assertRaises(DocumentoRechazado):
            analizar_rss(RSS, BOMBA, AHORA)
        self.assertLess(time.monotonic() - inicio, 2)


class PrologoGrandeTest(unittest.TestCase):
    def test_muchos_comentarios_en_el_prologo_no_son_cuadraticos(self):
        texto = "<!-- c -->" * 20000 + CUERPO
        inicio = time.monotonic()
        self.assertEqual(len(analizar_rss(RSS, texto, AHORA).novedades), 1)
        self.assertLess(time.monotonic() - inicio, 5)

    def test_un_prologo_enorme_sin_cerrar_se_rechaza_rapido(self):
        for texto in ("<!--" * 100000, "<?" * 100000, "<!" * 100000):
            with self.subTest(texto[:4]):
                inicio = time.monotonic()
                with self.assertRaises(DocumentoRechazado):
                    parsear_xml(texto)
                self.assertLess(time.monotonic() - inicio, 5)


if __name__ == "__main__":
    unittest.main()
