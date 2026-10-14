"""Descarga: el plazo total con un servidor que gotea, y destinos que no son de internet pública.

Sin red: respuestas y sockets falsos.
"""

import io
import unittest
import urllib.request
from email.message import Message

from radar.lectura.descarga import TROZO, SoloHttps, descargar, leer_cuerpo
from radar.lectura.modelos import ErrorDescarga


def cabeceras(**campos) -> Message:
    m = Message()
    for nombre, valor in campos.items():
        m[nombre.replace("_", "-")] = valor
    return m


class Reloj:
    def __init__(self):
        self.ahora = 0.0

    def __call__(self):
        return self.ahora

    def avanzar(self, segundos):
        self.ahora += segundos


class SocketFalso:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, valor):
        self.timeouts.append(valor)


class GoteoConRead1:
    """Un servidor que gotea: cada read1 devuelve un byte y 'tarda' lo que diga el reloj."""

    def __init__(self, reloj, segundos_por_byte):
        self.headers = cabeceras()
        self.reloj, self.segundos = reloj, segundos_por_byte
        self.pedidos = []
        self.sock = SocketFalso()
        self.fp = type("Fp", (), {"raw": type("Raw", (), {"_sock": self.sock})()})()

    def read1(self, n=-1):
        self.pedidos.append(n)
        self.reloj.avanzar(self.segundos)
        return b"x"

    def read(self, n=-1):  # una lectura bloqueante que espera llenar el trozo: no debe usarse
        raise AssertionError("se usó read() en vez de read1()")

    def close(self):
        pass


class GoteoTest(unittest.TestCase):
    def test_un_servidor_que_gotea_corta_en_el_plazo_aunque_cada_lectura_sea_corta(self):
        reloj = Reloj()
        r = GoteoConRead1(reloj, segundos_por_byte=7)
        with self.assertRaises(ErrorDescarga) as ctx:
            leer_cuerpo(r, plazo=60, reloj=reloj)
        self.assertIn("plazo total", str(ctx.exception))
        self.assertLessEqual(reloj.ahora, 60 + 7)  # no estiró la descarga más allá del plazo (más una lectura)
        self.assertEqual(len(r.pedidos), 9)  # 9 lecturas de 7 s: 63 s

    def test_se_pide_en_trozos_chicos_y_nunca_mas_que_el_tope(self):
        reloj = Reloj()
        r = GoteoConRead1(reloj, segundos_por_byte=1)
        with self.assertRaises(ErrorDescarga):
            leer_cuerpo(r, plazo=5, reloj=reloj)
        self.assertEqual(TROZO, 8 * 1024)
        self.assertTrue(all(0 < n <= TROZO for n in r.pedidos))
        r2 = GoteoConRead1(Reloj(), 0)
        with self.assertRaises(ErrorDescarga):
            leer_cuerpo(r2, tope=3, plazo=1000)
        self.assertEqual(r2.pedidos[-1], 1)  # al final sólo pide lo que falta para pasarse por un byte

    def test_una_respuesta_que_no_es_de_socket_no_rompe_el_ajuste(self):
        class SoloRead:
            headers = cabeceras()

            def __init__(self):
                self._flujo = io.BytesIO(b"hola")

            def read(self, n=-1):
                return self._flujo.read(n)

        self.assertEqual(leer_cuerpo(SoloRead()), b"hola")



class DestinoTest(unittest.TestCase):
    NO_PUBLICOS = [
        "https://localhost/feed",
        "https://LOCALHOST:8443/",
        "https://127.0.0.1/",
        "https://127.1/",
        "https://10.1.2.3/feed",
        "https://192.168.0.10/feed",
        "https://172.31.0.1/",
        "https://169.254.169.254/latest/meta-data/",
        "https://100.64.0.1/",
        "https://0.0.0.0/",
        "https://[::1]/",
        "https://[ff02::1]/",
        "https://224.0.0.1/",
        "https://[64:ff9b::808:808]/",
        "https://[64:ff9b:1::1]/",
        "https://ejemplo.test:0/",
        "https://usuario@ejemplo.test/",
        "https://usuario:clave@ejemplo.test/",
        "https://ejemplo.test@127.0.0.1/",
        "https://[fe80::1]/",
        "https://[fd00::1]/",
        "https://[::ffff:10.0.0.1]/",
        "https://servidor-interno/feed",
        "https://nas.local/feed",
        "https://x.internal/",
        "https://2130706433/",
        "https://0x7f.0.0.1/",
        "https://ｅjemplo.test/",
    ]

    def test_la_descarga_no_abre_la_conexion_a_un_host_no_publico(self):
        for url in self.NO_PUBLICOS:
            with self.subTest(url):
                llamadas = []
                with self.assertRaises(ErrorDescarga) as ctx:
                    descargar(url, abrir=lambda u: llamadas.append(u))
                self.assertEqual(llamadas, [])
                self.assertTrue(str(ctx.exception))

    def test_la_redireccion_a_un_host_no_publico_se_rechaza(self):
        manejador = SoloHttps()
        pedido = urllib.request.Request("https://ejemplo.test/a")
        for destino in self.NO_PUBLICOS:
            with self.subTest(destino):
                with self.assertRaises(ErrorDescarga):
                    manejador.redirect_request(pedido, io.BytesIO(), 302, "Found", cabeceras(), destino)

    def test_la_redireccion_a_un_host_publico_sigue_y_a_http_no(self):
        manejador = SoloHttps()
        pedido = urllib.request.Request("https://ejemplo.test/a")
        nuevo = manejador.redirect_request(pedido, io.BytesIO(), 302, "Found", cabeceras(), "https://otro.ejemplo.test/b")
        self.assertEqual(nuevo.full_url, "https://otro.ejemplo.test/b")
        nuevo = manejador.redirect_request(pedido, io.BytesIO(), 302, "Found", cabeceras(), "https://8.8.8.8/b")
        self.assertEqual(nuevo.full_url, "https://8.8.8.8/b")
        with self.assertRaises(ErrorDescarga):
            manejador.redirect_request(pedido, io.BytesIO(), 302, "Found", cabeceras(), "http://otro.ejemplo.test/b")

    def test_una_url_ilegible_es_error_propio(self):
        for url in ("https://[::1/", "https://ejemplo.test:99999999999/"):
            with self.subTest(url):
                with self.assertRaises(ErrorDescarga):
                    descargar(url, abrir=lambda u: None)


if __name__ == "__main__":
    unittest.main()
