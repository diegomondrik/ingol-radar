"""Texto escapado en varios niveles, enlaces a hosts no públicos, invisibles y los bordes de la limpieza.

Complementa test_entradas_armadas.py (habilidad `entradas-adversariales`): mismas reglas, más formas.
"""

import html
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from radar.lectura import por_id
from radar.lectura import limpieza
from radar.lectura.analizar import analizar_atom, analizar_rss
from radar.lectura.limpieza import (
    MAX_DENSIDAD,
    MAX_PASADAS,
    TOPE_CRUDO,
    fecha_rfc3339,
    host_publico,
    limpiar_texto,
    sin_futuro,
    validar_enlace,
)
from radar.lectura.modelos import MAX_ITEMS
from radar.lectura.paginas import (
    analizar_anthropic_noticias,
    analizar_claude_plataforma,
    analizar_metr,
)

AHORA = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
RSS = por_id("openai")
ATOM = por_id("claude-code")
PAYLOAD = "<img src=x onerror=alert(1)>"


def escapar(texto: str, niveles: int) -> str:
    for _ in range(niveles):
        texto = html.escape(texto, quote=False)
    return texto


def rss(*items: str, extra_ns: str = "") -> str:
    return f'<rss version="2.0" {extra_ns}><channel>{"".join(items)}</channel></rss>'


def item(titulo="T", link="https://ejemplo.test/a", extra="") -> str:
    return f"<item><title>{titulo}</title><link>{link}</link>{extra}</item>"


def atom(*entradas: str) -> str:
    return "<feed xmlns='http://www.w3.org/2005/Atom'>" + "".join(entradas) + "</feed>"


def sin_etiquetas(testigo: unittest.TestCase, texto: str):
    testigo.assertNotIn("<", texto)


# ------------------------------------------------------------- punto fijo del texto


class PuntoFijoTest(unittest.TestCase):
    def test_de_1_a_8_niveles_de_escape_salen_sin_etiqueta(self):
        for niveles in range(1, 9):
            with self.subTest(niveles):
                self.assertEqual(limpiar_texto("Hola " + escapar(PAYLOAD, niveles), 300), "Hola")

    def test_un_escape_que_no_termina_da_texto_vacio(self):
        profundo = escapar(PAYLOAD, MAX_PASADAS + 5)
        self.assertEqual(limpiar_texto("Hola " + profundo, 300), "")

    def test_una_vuelta_mas_no_cambia_el_resultado(self):
        for niveles in (1, 3, 5):
            limpio = limpiar_texto(escapar(PAYLOAD, niveles) + " texto & mas", 300)
            self.assertEqual(limpiar_texto(limpio, 300), limpio)

    def test_3_4_y_5_niveles_en_un_feed_en_el_titulo_y_en_la_frase(self):
        for niveles in (3, 4, 5):
            with self.subTest(niveles):
                en_xml = escapar(PAYLOAD, niveles)  # el XML resuelve uno; quedan niveles-1 para la limpieza
                texto = rss(
                    item(
                        f"Hola {en_xml}",
                        extra=f"<description>{en_xml} Resumen real. Otra frase.</description>",
                    )
                )
                n = analizar_rss(RSS, texto, AHORA).novedades[0]
                self.assertEqual(n.titulo, "Hola")
                self.assertEqual(n.frase, "Resumen real.")
                sin_etiquetas(self, n.titulo + n.frase)

    def test_el_mismo_ataque_por_cdata(self):
        for niveles in (3, 4, 5):
            with self.subTest(niveles):
                cdata = escapar(PAYLOAD, niveles)  # en un CDATA el XML no resuelve nada
                texto = rss(
                    f"<item><title><![CDATA[Hola {cdata}]]></title><link>https://ejemplo.test/a</link>"
                    f"<description><![CDATA[{cdata} Resumen real.]]></description></item>"
                )
                n = analizar_rss(RSS, texto, AHORA).novedades[0]
                self.assertEqual((n.titulo, n.frase), ("Hola", "Resumen real."))

    def test_en_atom_con_contenido_html_escapado(self):
        for niveles in (3, 4, 5):
            with self.subTest(niveles):
                entrada = (
                    f"<entry><id>1</id><title type='html'>Hola {escapar(PAYLOAD, niveles)}</title>"
                    f"<link href='https://ejemplo.test/a'/>"
                    f"<content type='html'>{escapar(PAYLOAD, niveles)} Cuerpo real.</content></entry>"
                )
                n = analizar_atom(ATOM, atom(entrada), AHORA).novedades[0]
                self.assertEqual((n.titulo, n.frase), ("Hola", "Cuerpo real."))

    def test_un_titulo_que_no_converge_descarta_la_novedad_y_la_cuenta(self):
        sin_fin = escapar(PAYLOAD, MAX_PASADAS + 5)
        ext = analizar_rss(RSS, rss(item(f"Hola {sin_fin}"), item("Normal", "https://ejemplo.test/b")), AHORA)
        self.assertEqual([n.titulo for n in ext.novedades], ["Normal"])
        self.assertEqual(ext.descartadas, 1)

    def test_un_titulo_que_es_solo_etiquetas_se_descarta_pero_uno_en_blanco_usa_el_enlace(self):
        ext = analizar_rss(RSS, rss(item("&lt;b&gt;&lt;/b&gt;", "https://ejemplo.test/a")), AHORA)
        self.assertEqual((ext.novedades, ext.descartadas), ([], 1))
        ext = analizar_rss(RSS, rss(item("   ", "https://ejemplo.test/b")), AHORA)
        self.assertEqual(ext.novedades[0].titulo, "https://ejemplo.test/b")

    def test_en_las_anclas_de_una_pagina(self):
        fuente = por_id("anthropic-noticias")
        for niveles in (3, 4, 5):
            with self.subTest(niveles):
                ataque = escapar(PAYLOAD, niveles)
                pagina = (
                    f'<a href="/news/uno"><span>Titulo real</span><span>{ataque}</span></a>'
                    f'<a href="/news/dos">{ataque}</a>'
                )
                ext = analizar_anthropic_noticias(fuente, pagina, AHORA)
                self.assertEqual([n.titulo for n in ext.novedades], ["Titulo real", "Dos"])
                sin_etiquetas(self, "".join(n.titulo for n in ext.novedades))

    def test_en_los_li_de_la_plataforma(self):
        fuente = por_id("claude-plataforma")
        for niveles in (3, 4, 5):
            with self.subTest(niveles):
                pagina = f"<h3>October 9, 2026</h3><ul><li>Cambio real {escapar(PAYLOAD, niveles)} y mas.</li></ul>"
                n = analizar_claude_plataforma(fuente, pagina, AHORA).novedades[0]
                self.assertEqual(n.titulo, "Cambio real y mas.")
                sin_etiquetas(self, n.titulo + n.frase)

    def test_un_invisible_en_medio_de_la_etiqueta_no_la_esconde(self):
        # un script cuya etiqueta de cierre también viene partida se traga el resto: sale vacío (seguro)
        peligroso = limpiar_texto("<​script>alert(1)</​script>Hola", 300)
        self.assertNotRegex(peligroso, r"<[A-Za-z/!?]")
        self.assertNotIn("alert(1)", peligroso)
        casos = {
            "<‮img src=x onerror=1>Hola": "Hola",
            "<️img src=x>Hola": "Hola",
            "<\U000e0100img src=x>Hola": "Hola",
            "<\x00img src=x>Hola": "Hola",
        }
        for crudo, esperado in casos.items():
            with self.subTest(repr(crudo)):
                self.assertEqual(limpiar_texto(crudo, 300), esperado)

    def test_el_cinturon_quita_una_apertura_de_etiqueta_aunque_el_analizador_no_la_vea(self):
        class Ciego:  # un analizador que no saca nada: lo que queda lo tiene que frenar el cinturón
            def __init__(self):
                self.partes = []
                self.rawdata = ""

            def feed(self, texto):
                self.partes = [texto]

        with mock.patch.object(limpieza, "_SacaEtiquetas", Ciego):
            limpio = limpiar_texto("a <img src=x> b </p> c <!-- d --> e <?php ?> f < g", 300)
        self.assertNotRegex(limpio, r"<[A-Za-z/!?]")
        self.assertIn("img src=x", limpio)  # queda como texto inerte
        self.assertIn("< g", limpio)  # un `<` suelto, que no abre nada, no se toca

    def test_selectores_de_variacion_se_quitan(self):
        self.assertEqual(limpiar_texto("a︀b️c\U000e0100d\U000e01efe", 300), "abcde")
        self.assertEqual(limpiar_texto("❤️", 300), "❤")

    def test_tope_crudo_el_texto_sale_de_los_primeros_4000_caracteres(self):
        self.assertEqual(TOPE_CRUDO, 4000)
        self.assertEqual(limpiar_texto("x" * TOPE_CRUDO + "COLA", 50000), "x" * TOPE_CRUDO)
        self.assertEqual(limpiar_texto("x" * (TOPE_CRUDO - 4) + "COLA" + "MAS", 50000), "x" * (TOPE_CRUDO - 4) + "COLA")

    def test_un_relleno_de_aperturas_se_descarta_sin_parsear(self):
        for relleno in ("<" * 3000, "&" * 1500, "<&" * 600, "<!--" * 1100):
            with self.subTest(relleno[:6]):
                self.assertEqual(limpiar_texto(relleno, 300), "")
        self.assertEqual(MAX_DENSIDAD, 1000)
        # justo en el límite se procesa como cualquier texto
        self.assertEqual(limpiar_texto("a" + "<b>" * 300, 300), "a")

    def test_el_costo_de_los_niveles_es_acotado(self):
        inicio = time.monotonic()
        limpiar_texto("&amp;" * 4000, 300)
        limpiar_texto(escapar(PAYLOAD * 200, 6), 300)
        self.assertLess(time.monotonic() - inicio, 5)


# ----------------------------------------------------------------------------- hosts


class HostsTest(unittest.TestCase):
    def test_hosts_no_publicos_se_rechazan(self):
        malos = [
            "https://localhost/",
            "http://LOCALHOST:8080/",
            "https://foo.localhost/",
            "https://intranet/",
            "https://servidor/ruta",
            "https://a.local/",
            "https://a.internal/",
            "https://a.localdomain/",
            "http://127.0.0.1/",
            "http://127.0.0.1:8000/x",
            "http://127.1/",
            "http://10.0.0.5/",
            "http://192.168.1.1/",
            "http://172.16.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://100.64.0.1/",
            "http://0.0.0.0/",
            "http://255.255.255.255/",
            "https://[::1]/",
            "https://[::]/",
            "https://[fe80::1]/",
            "https://[fc00::1]/",
            "https://[::ffff:127.0.0.1]/",
            "https://[::ffff:10.0.0.1]/",
            "http://2130706433/",
            "http://0x7f.0.0.1/",
            "http://0x7f000001/",
            "http://017700000001/",
            "http://127.0.0.1./",
            "https://ejemplo.test./",
            "https://-ejemplo.test/",
            "https://ejemplo-.test/",
            "https://ejemplo..test/",
            "https://" + "a" * 64 + ".test/",
            "https://ejemplo.123/",
            "https://ejemplo.test_/",
        ]
        for url in malos:
            with self.subTest(url):
                self.assertIsNone(validar_enlace(url))

    def test_hosts_no_ascii_se_rechazan(self):
        malos = [
            "https://ｅjemplo.test/",  # e de ancho completo
            "https://пример.рф/",  # cirílico
            "https://ejemplo。test/",  # punto ideográfico
            "https://ejemplo．test/",  # punto de ancho completo
            "https://ejemplo.tést/",
            "https://①.test/",
        ]
        for url in malos:
            with self.subTest(url):
                self.assertIsNone(validar_enlace(url))

    def test_hosts_publicos_se_aceptan_y_se_normalizan(self):
        self.assertEqual(validar_enlace("http://8.8.8.8/x"), "http://8.8.8.8/x")
        self.assertEqual(validar_enlace("https://Sub.Ejemplo.TEST/a"), "https://sub.ejemplo.test/a")
        self.assertEqual(validar_enlace("http://ejemplo.test/"), "http://ejemplo.test/")  # http sí, para enlaces
        self.assertEqual(validar_enlace("https://xn--bcher-kva.example/"), "https://xn--bcher-kva.example/")
        self.assertEqual(validar_enlace("https://a-b.c-d.ejemplo.test:8443/"), "https://a-b.c-d.ejemplo.test:8443/")

    def test_ipv6_publica_entre_corchetes_es_valida(self):
        self.assertEqual(
            validar_enlace("https://[2606:4700:4700::1111]/x?q=1"), "https://[2606:4700:4700::1111]/x?q=1"
        )
        self.assertEqual(
            validar_enlace("https://[2606:4700:4700::1111]:8443/"), "https://[2606:4700:4700::1111]:8443/"
        )
        self.assertEqual(
            validar_enlace("HTTPS://[2606:4700:4700:0:0:0:0:1111]/"), "https://[2606:4700:4700::1111]/"
        )

    def test_host_publico_directo(self):
        for bueno in ("ejemplo.test", "a.b.c.example", "8.8.8.8", "2606:4700:4700::1111", "xn--p1ai.xn--p1ai"):
            with self.subTest(bueno):
                self.assertTrue(host_publico(bueno))
        for malo in (None, "", "localhost", "a", "10.0.0.1", "::1", "fe80::1%eth0", "ejemplo.test%", "ejemplo test", 5):
            with self.subTest(repr(malo)):
                self.assertFalse(host_publico(malo))

    def test_multicast_nat64_y_puerto_cero_se_rechazan(self):
        for malo in (
            "http://224.0.0.1/",
            "http://239.255.255.250/",
            "https://[ff02::1]/",
            "https://[ff0e::1]/",
            "https://[64:ff9b::808:808]/",  # NAT64: traduce hacia una IPv4 cualquiera
            "https://[64:ff9b::7f00:1]/",
            "https://[64:ff9b:1::1]/",  # NAT64 de uso local
            "https://ejemplo.test:0/",
            "http://8.8.8.8:0/",
            "https://[2606:4700:4700::1111]:0/",
        ):
            with self.subTest(malo):
                self.assertIsNone(validar_enlace(malo))
        for bueno in ("https://ejemplo.test:1/", "https://ejemplo.test:65535/", "http://8.8.8.8:8080/"):
            self.assertIsNotNone(validar_enlace(bueno), bueno)
        for ip in ("224.0.0.1", "ff02::1", "64:ff9b::808:808", "64:ff9b:1::1"):
            self.assertFalse(host_publico(ip), ip)

    def test_las_comillas_se_rechazan(self):
        for malo in (
            "https://ejemplo.test/a'b",
            "https://ejemplo.test/a`b",
            'https://ejemplo.test/a"b',
            "https://ejemplo.test/?q='x'",
            "https://ejemplo.test/?q=`x`",
        ):
            with self.subTest(malo):
                self.assertIsNone(validar_enlace(malo))
        # codificadas, entran: es la forma segura de escribirlas
        self.assertEqual(validar_enlace("https://ejemplo.test/a%27b%60c"), "https://ejemplo.test/a%27b%60c")

    def test_un_enlace_de_feed_a_un_host_interno_se_descarta_y_se_cuenta(self):
        texto = rss(
            item("a", "http://169.254.169.254/latest"),
            item("b", "http://localhost:8080/admin"),
            item("c", "https://ejemplo.test/ok"),
        )
        ext = analizar_rss(RSS, texto, AHORA)
        self.assertEqual([n.titulo for n in ext.novedades], ["c"])
        self.assertEqual(ext.descartadas, 2)

    def test_en_una_pagina_un_enlace_a_la_ip_interna_no_cuenta(self):
        fuente = por_id("anthropic-noticias")
        ext = analizar_anthropic_noticias(
            fuente, '<a href="http://127.0.0.1/news/x">x</a><a href="/news/ok">Ok</a>', AHORA
        )
        self.assertEqual([n.titulo for n in ext.novedades], ["Ok"])


# ------------------------------------------------------------- bordes de la limpieza


class BordesTest(unittest.TestCase):
    def test_atom_rel_self_no_es_un_enlace(self):
        e_self_y_alterno = (
            "<entry><id>1</id><title>A</title><link rel='self' href='https://ejemplo.test/self.atom'/>"
            "<link rel='alternate' href='https://ejemplo.test/a'/></entry>"
        )
        e_solo_self = "<entry><id>2</id><title>B</title><link rel='self' href='https://ejemplo.test/b.atom'/></entry>"
        e_otro_rel = (
            "<entry><id>3</id><title>C</title><link rel='related' href='https://ejemplo.test/c'/>"
            "<link rel='self' href='https://ejemplo.test/c.atom'/></entry>"
        )
        e_sin_rel_gana = (
            "<entry><id>4</id><title>D</title><link rel='related' href='https://ejemplo.test/d-related'/>"
            "<link href='https://ejemplo.test/d'/></entry>"
        )
        e_solo_edicion = (
            "<entry><id>5</id><title>E</title><link rel='edit' href='https://ejemplo.test/e-edit'/>"
            "<link rel='replies' href='https://ejemplo.test/e-replies'/></entry>"
        )
        ext = analizar_atom(ATOM, atom(e_self_y_alterno, e_solo_self, e_otro_rel, e_sin_rel_gana, e_solo_edicion), AHORA)
        self.assertEqual(
            [(n.titulo, n.enlace) for n in ext.novedades],
            [
                ("A", "https://ejemplo.test/a"),
                ("C", "https://ejemplo.test/c"),
                ("D", "https://ejemplo.test/d"),
            ],
        )
        self.assertEqual(ext.descartadas, 2)  # B y E: ningún enlace para leer

    def test_rss_sin_link_usa_el_guid_si_es_una_url(self):
        texto = rss(
            "<item><title>Con guid url</title><guid>https://ejemplo.test/g</guid></item>",
            "<item><title>Con guid http</title><guid isPermaLink='true'>HTTP://ejemplo.test/h</guid></item>",
            "<item><title>Guid que no es url</title><guid>abc-123</guid></item>",
            "<item><title>Sin nada</title></item>",
        )
        ext = analizar_rss(RSS, texto, AHORA)
        self.assertEqual(
            [(n.titulo, n.enlace) for n in ext.novedades],
            [("Con guid url", "https://ejemplo.test/g"), ("Con guid http", "http://ejemplo.test/h")],
        )
        self.assertEqual(ext.descartadas, 2)

    def test_rss_dc_date_cuando_no_hay_pubdate(self):
        ns = 'xmlns:dc="http://purl.org/dc/elements/1.1/"'
        texto = rss(
            item("a", "https://ejemplo.test/a", "<dc:date>2026-10-09T10:00:00Z</dc:date>"),
            item("b", "https://ejemplo.test/b", "<dc:date>no es fecha</dc:date>"),
            item(
                "c",
                "https://ejemplo.test/c",
                "<pubDate>Mon, 12 Oct 2026 16:00:00 +0000</pubDate><dc:date>2026-10-09T10:00:00Z</dc:date>",
            ),
            extra_ns=ns,
        )
        fechas = [n.fecha for n in analizar_rss(RSS, texto, AHORA).novedades]
        self.assertEqual(
            fechas,
            [
                datetime(2026, 10, 9, 10, tzinfo=timezone.utc),
                None,
                datetime(2026, 10, 12, 16, tzinfo=timezone.utc),  # pubDate gana sobre dc:date
            ],
        )

    def test_fraccion_de_segundo(self):
        casos = {
            "2026-10-09T10:00:00.5Z": 500000,
            "2026-10-09T10:00:00.05Z": 50000,
            "2026-10-09T10:00:00.123Z": 123000,
            "2026-10-09T10:00:00.000001Z": 1,
            "2026-10-09T10:00:00.1234567Z": 123456,
            "2026-10-09T10:00:00Z": 0,
        }
        for texto, micro in casos.items():
            with self.subTest(texto):
                self.assertEqual(fecha_rfc3339(texto).microsecond, micro)

    def test_el_borde_del_futuro_es_exacto(self):
        limite = AHORA + timedelta(days=1)
        self.assertEqual(sin_futuro(limite, AHORA), limite)
        self.assertIsNone(sin_futuro(limite + timedelta(microseconds=1), AHORA))

    def test_en_plataforma_una_fecha_futura_queda_sin_fecha(self):
        pagina = "<h3>October 9, 2026</h3><ul><li>Hoy</li></ul><h3>January 1, 2099</h3><ul><li>Futuro</li></ul>"
        ext = analizar_claude_plataforma(por_id("claude-plataforma"), pagina, AHORA)
        self.assertEqual([(n.titulo, n.fecha is None) for n in ext.novedades], [("Hoy", False), ("Futuro", True)])

    def test_en_las_paginas_con_enlaces_una_fecha_futura_queda_sin_fecha(self):
        ext = analizar_metr(por_id("metr"), '<a href="/blog/2099-01-01-del-futuro/">Futuro</a>', AHORA)
        self.assertIsNone(ext.novedades[0].fecha)
        ext = analizar_anthropic_noticias(
            por_id("anthropic-noticias"), '<a href="/news/x"><span>Jan 1, 2099</span><span>Futuro</span></a>', AHORA
        )
        self.assertIsNone(ext.novedades[0].fecha)

    def test_un_h3_que_no_es_fecha_no_reinicia_la_seccion(self):
        pagina = (
            "<h3>October 9, 2026</h3><ul><li>Uno</li></ul>"
            "<h3>Models</h3><ul><li>Dos</li></ul>"
            "<h3>Pricing and limits</h3><ul><li>Tres</li></ul>"
        )
        ext = analizar_claude_plataforma(por_id("claude-plataforma"), pagina, AHORA)
        self.assertEqual(
            [(n.titulo, n.fecha) for n in ext.novedades],
            [(t, datetime(2026, 10, 9, tzinfo=timezone.utc)) for t in ("Uno", "Dos", "Tres")],
        )

    def test_las_anclas_ignoran_lo_que_esta_dentro_de_un_script(self):
        pagina = (
            '<a href="/news/x"><span>Titulo</span><script>CARGA_UTIL_MUCHO_MAS_LARGA_QUE_EL_TITULO</script></a>'
            '<a href="/news/y"><style>.x{color:red} MAS_TEXTO_LARGO_EN_UN_ESTILO</style><span>Otro</span></a>'
        )
        ext = analizar_anthropic_noticias(por_id("anthropic-noticias"), pagina, AHORA)
        self.assertEqual([n.titulo for n in ext.novedades], ["Titulo", "Otro"])

    def test_el_camino_se_compara_entero(self):
        fuente = por_id("anthropic-noticias")
        pagina = (
            '<a href="/news/x/otra/cosa">No entra</a>'
            '<a href="/news/x/">Con barra final</a>'
            '<a href="/news/">El indice</a>'
            '<a href="/news">Tambien</a>'
            '<a href="/news/y/../z">Con puntos</a>'
            '<a href="/otra/news/w">Otro camino</a>'
        )
        ext = analizar_anthropic_noticias(fuente, pagina, AHORA)
        self.assertEqual([n.enlace for n in ext.novedades], ["https://www.anthropic.com/news/x/", "https://www.anthropic.com/news/z"])
        ext = analizar_metr(por_id("metr"), '<a href="/blog/2026-10-06-x/otra">No</a><a href="/blog/2026-10-06-x/">Si</a>', AHORA)
        self.assertEqual([n.titulo for n in ext.novedades], ["Si"])

    def test_el_tope_de_500_vale_tambien_para_las_paginas(self):
        total = MAX_ITEMS + 5
        casos = {
            "metr": (
                analizar_metr,
                "".join(f'<a href="/blog/2026-10-06-slug{i}/">Titulo {i}</a>' for i in range(total)),
            ),
            "anthropic-noticias": (
                analizar_anthropic_noticias,
                "".join(f'<a href="/news/slug{i}">Titulo {i}</a>' for i in range(total)),
            ),
            "claude-plataforma": (
                analizar_claude_plataforma,
                "<h3>October 9, 2026</h3><ul>" + "".join(f"<li>Cambio {i}</li>" for i in range(total)) + "</ul>",
            ),
        }
        for id_fuente, (analizador, pagina) in casos.items():
            with self.subTest(id_fuente):
                ext = analizador(por_id(id_fuente), pagina, AHORA)
                self.assertEqual(len(ext.novedades), MAX_ITEMS)
                self.assertEqual(ext.recortadas, 5)
                self.assertEqual(ext.descartadas, 0)
                self.assertEqual(ext.novedades[-1].titulo.split()[-1], str(MAX_ITEMS - 1))  # entran los primeros


if __name__ == "__main__":
    unittest.main()
