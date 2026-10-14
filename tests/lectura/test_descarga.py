"""El fetcher real, probado sin red: respuestas falsas, un manejador de redirecciones con objetos falsos."""

import io
import socket
import unittest
import urllib.error
import urllib.request
from email.message import Message
from unittest import mock

from radar.lectura import descarga
from radar.lectura.descarga import (
    MAX_REDIRECCIONES,
    TOPE_BYTES,
    USER_AGENT,
    SoloHttps,
    crear_opener,
    decodificar,
    descargar,
    leer_cuerpo,
)
from radar.lectura.modelos import ErrorDescarga, ErrorLectura


def cabeceras(**campos) -> Message:
    m = Message()
    for nombre, valor in campos.items():
        m[nombre.replace("_", "-")] = valor
    return m


class RespuestaFalsa:
    """Una respuesta con cuerpo y cabeceras; cuenta cuánto se le leyó y si se cerró."""

    def __init__(self, cuerpo=b"", **campos):
        self._flujo = io.BytesIO(cuerpo)
        self.headers = cabeceras(**campos)
        self.leidos = 0
        self.cerrada = False

    def read(self, n=-1):
        datos = self._flujo.read(n)
        self.leidos += len(datos)
        return datos

    def close(self):
        self.cerrada = True


class RespuestaInfinita(RespuestaFalsa):
    """Sirve bytes sin parar: un servidor que no termina nunca."""

    def read(self, n=-1):
        n = 65536 if n is None or n < 0 else n
        self.leidos += n
        return b"x" * n


class AbrirTest(unittest.TestCase):
    def test_solo_https_ni_siquiera_se_abre_la_conexion(self):
        for url in (
            "http://ejemplo.test/feed",
            "HTTP://ejemplo.test/feed",
            "ftp://ejemplo.test/feed",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/xml,<a/>",
            "//ejemplo.test/feed",
            "ejemplo.test/feed",
            "",
        ):
            with self.subTest(url):
                llamadas = []
                with self.assertRaises(ErrorDescarga):
                    descargar(url, abrir=lambda u: llamadas.append(u))
                self.assertEqual(llamadas, [])

    def test_https_lee_y_decodifica(self):
        r = RespuestaFalsa("<a>áé</a>".encode("utf-8"), Content_Type="text/xml; charset=utf-8")
        self.assertEqual(descargar("https://ejemplo.test/f", abrir=lambda u: r), "<a>áé</a>")
        self.assertTrue(r.cerrada)

    def test_los_errores_de_red_salen_como_error_propio(self):
        errores = [
            urllib.error.URLError("sin ruta"),
            urllib.error.HTTPError("https://ejemplo.test/f", 503, "Servicio caído", cabeceras(), None),
            socket.timeout("lento"),
            TimeoutError("lento"),
            ConnectionResetError("cortada"),
            OSError("disco"),
            ValueError("url rara"),
        ]
        for exc in errores:
            with self.subTest(type(exc).__name__):

                def abrir(url, exc=exc):
                    raise exc

                with self.assertRaises(ErrorDescarga):
                    descargar("https://ejemplo.test/f", abrir=abrir)

    def test_agente_timeout_y_sin_accept_encoding(self):
        visto = {}

        class OpenerFalso:
            def open(self, pedido, timeout=None):
                visto["pedido"], visto["timeout"] = pedido, timeout
                return RespuestaFalsa(b"<a/>")

        with mock.patch.object(descarga, "crear_opener", return_value=OpenerFalso()):
            descargar("https://ejemplo.test/f")
        pedido = visto["pedido"]
        self.assertEqual(USER_AGENT, "ingol-radar/0.1 (+https://github.com/diegomondrik/ingol-radar)")
        self.assertEqual(pedido.get_header("User-agent"), USER_AGENT)
        self.assertIsNone(pedido.get_header("Accept-encoding"))
        self.assertEqual(visto["timeout"], 20)
        self.assertEqual(pedido.full_url, "https://ejemplo.test/f")

    def test_el_opener_real_usa_el_manejador_de_https_y_ningun_otro_de_redirecciones(self):
        manejadores = crear_opener().handlers
        redirectores = [h for h in manejadores if isinstance(h, urllib.request.HTTPRedirectHandler)]
        self.assertEqual([type(h) for h in redirectores], [SoloHttps])


class TopeTest(unittest.TestCase):
    def test_un_servidor_infinito_corta_en_el_tope_sin_leer_de_mas(self):
        r = RespuestaInfinita()
        with self.assertRaises(ErrorDescarga) as ctx:
            descargar("https://ejemplo.test/f", abrir=lambda u: r)
        self.assertIn("tope", str(ctx.exception))
        self.assertLessEqual(r.leidos, TOPE_BYTES + 1)  # nunca pidió más que el tope más un byte
        self.assertTrue(r.cerrada)

    def test_el_tope_exacto_pasa_y_un_byte_mas_no(self):
        justo = RespuestaFalsa(b"a" * 1000)
        self.assertEqual(len(leer_cuerpo(justo, tope=1000)), 1000)
        with self.assertRaises(ErrorDescarga):
            leer_cuerpo(RespuestaFalsa(b"a" * 1001), tope=1000)

    def test_el_tope_es_de_5_mb(self):
        self.assertEqual(TOPE_BYTES, 5 * 1024 * 1024)

    def test_content_length_mayor_que_el_tope_corta_sin_leer(self):
        r = RespuestaFalsa(b"a" * 10, Content_Length=str(TOPE_BYTES + 1))
        with self.assertRaises(ErrorDescarga):
            leer_cuerpo(r)
        self.assertEqual(r.leidos, 0)

    def test_content_length_mentiroso_no_salva_del_tope(self):
        r = RespuestaInfinita(Content_Length="10")
        with self.assertRaises(ErrorDescarga):
            leer_cuerpo(r, tope=5000)

    def test_content_length_basura_se_ignora(self):
        for valor in ("abc", "-5", "1e9", "９９９９９９９９９９", "9" * 5000, ""):
            with self.subTest(valor):
                self.assertEqual(leer_cuerpo(RespuestaFalsa(b"ok", Content_Length=valor)), b"ok")

    def test_un_goteo_lento_corta_por_plazo_total(self):
        r = RespuestaInfinita()
        tiempos = iter(range(0, 1000, 30))  # cada lectura "tarda" 30 s
        with self.assertRaises(ErrorDescarga) as ctx:
            leer_cuerpo(r, plazo=60, reloj=lambda: next(tiempos))
        self.assertIn("plazo total", str(ctx.exception))

    def test_el_error_de_tope_es_un_error_propio_de_la_lectura(self):
        self.assertTrue(issubclass(ErrorDescarga, ErrorLectura))


class DecodificarTest(unittest.TestCase):
    def test_con_el_charset_del_encabezado(self):
        self.assertEqual(decodificar("canción".encode("latin-1"), "ISO-8859-1"), "canción")
        self.assertEqual(decodificar("canción".encode("utf-16"), "utf-16"), "canción")

    def test_sin_charset_es_utf8_y_los_bytes_invalidos_se_reemplazan(self):
        self.assertEqual(decodificar("canción".encode("utf-8"), None), "canción")
        self.assertEqual(decodificar(b"a\xffb", None), "a�b")
        self.assertEqual(decodificar(b"a\xffb", "utf-8"), "a�b")

    def test_charset_desconocido_o_que_no_es_de_texto_cae_a_utf8_sin_romper(self):
        for charset in ("x-no-existe", "rot13", "base64", "hex", "zlib", "a" * 5000, "utf-8\x00", ""):
            with self.subTest(charset[:12]):
                self.assertEqual(decodificar("ñ".encode("utf-8"), charset), "ñ")

    def test_charset_en_el_encabezado_de_una_respuesta_falsa(self):
        r = RespuestaFalsa("canción".encode("latin-1"), Content_Type="application/atom+xml; charset=ISO-8859-1")
        self.assertEqual(descargar("https://ejemplo.test/f", abrir=lambda u: r), "canción")


class RedireccionTest(unittest.TestCase):
    def setUp(self):
        self.manejador = SoloHttps()
        self.pedido = urllib.request.Request("https://ejemplo.test/a")

    def redirigir(self, destino):
        return self.manejador.redirect_request(self.pedido, io.BytesIO(), 302, "Found", cabeceras(), destino)

    def test_redireccion_a_http_se_rechaza(self):
        for destino in (
            "http://ejemplo.test/b",
            "HTTP://ejemplo.test/b",
            "ftp://ejemplo.test/b",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/html,hola",
            "//ejemplo.test/b",
        ):
            with self.subTest(destino):
                with self.assertRaises(ErrorDescarga):
                    self.redirigir(destino)

    def test_redireccion_a_https_sigue(self):
        nuevo = self.redirigir("https://otro.test/b")
        self.assertEqual(nuevo.full_url, "https://otro.test/b")

    def test_la_cadena_de_redirecciones_corta_a_las_cinco(self):
        self.assertEqual(MAX_REDIRECCIONES, 5)
        self.assertEqual(SoloHttps.max_redirections, 5)

        class Padre:
            def __init__(self, manejador):
                self.manejador, self.abiertos = manejador, 0

            def open(self, pedido, data=None, timeout=20):
                pedido.timeout = timeout  # lo que hace OpenerDirector.open
                self.abiertos += 1
                destino = cabeceras(Location=f"https://ejemplo.test/{self.abiertos}")
                return self.manejador.http_error_302(pedido, io.BytesIO(), 302, "Found", destino)

        padre = Padre(self.manejador)
        self.manejador.parent = padre
        with self.assertRaises(urllib.error.HTTPError):
            padre.open(self.pedido)
        self.assertEqual(padre.abiertos, 1 + MAX_REDIRECCIONES)  # el pedido inicial y cinco redirecciones

    def test_la_cadena_a_http_se_corta_en_la_primera_redireccion(self):
        class Padre:
            def open(self, pedido, data=None, timeout=None):
                raise AssertionError("no debía seguir una redirección a http")

        self.manejador.parent = Padre()
        with self.assertRaises(ErrorDescarga):
            self.manejador.http_error_302(
                self.pedido, io.BytesIO(), 302, "Found", cabeceras(Location="http://ejemplo.test/b")
            )


if __name__ == "__main__":
    unittest.main()
