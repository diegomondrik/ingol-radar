"""El plazo total de pared cubre TODO `descargar`: conexión, cabeceras, redirecciones y cuerpo.

Sin red: un socket falso que respeta su timeout como uno real (si la operación tarda más que el
timeout, avanza el reloj hasta el timeout y levanta TimeoutError) y un reloj falso.
"""

import http.client
import io
import socket
import unittest
import urllib.request
from unittest import mock

from radar.lectura import descarga
from radar.lectura.descarga import (
    TIMEOUT,
    ConexionConPlazo,
    ManejadorHttps,
    Plazo,
    PlazoVencido,
    SocketConPlazo,
    SoloHttps,
    descargar,
)
from radar.lectura.modelos import ErrorDescarga


class RelojFalso:
    def __init__(self):
        self.ahora = 1000.0

    def __call__(self):
        return self.ahora

    def avanzar(self, segundos):
        self.ahora += segundos


class SocketFalso:
    """Entrega `segmentos` byte a byte; cada recv tarda lo que diga su segmento y respeta el timeout."""

    def __init__(self, reloj, segmentos):
        self.reloj = reloj
        self.cola = [(bytes([b]), demora) for datos, demora in segmentos for b in datos]
        self.timeout = None
        self.registro = []  # (timeout puesto, lo que quedaba del plazo) en cada operación
        self.plazo = None
        self.enviado = b""
        self.cerrado = False
        self._io_refs = 0

    # lo que un socket real ofrece
    def settimeout(self, valor):
        self.timeout = valor
        if self.plazo is not None:
            self.registro.append((valor, self.plazo.restante()))

    def gettimeout(self):
        return self.timeout

    def _tardar(self, demora):
        if self.timeout is not None and self.timeout < demora:
            self.reloj.avanzar(self.timeout)
            raise TimeoutError("timed out")
        self.reloj.avanzar(demora)

    def recv_into(self, buf, nbytes=0, flags=0):
        if not self.cola:
            return 0
        byte, demora = self.cola[0]
        self._tardar(demora)
        self.cola.pop(0)
        buf[0:1] = byte
        return 1

    def sendall(self, datos, *args):
        self.enviado += datos

    def makefile(self, mode="r", buffering=None, **kwargs):
        self._io_refs += 1
        return socket.SocketIO(self, "rb")

    def _decref_socketios(self):
        self._io_refs -= 1

    def fileno(self):
        return 99

    def close(self):
        self.cerrado = True


def respuesta(cuerpo=b"hi", cabecera=b"HTTP/1.1 200 OK\r\nConnection: close\r\n"):
    return cabecera + b"Content-Length: " + str(len(cuerpo)).encode() + b"\r\n\r\n" + cuerpo


class SocketConPlazoTest(unittest.TestCase):
    def armar(self, segmentos, segundos=60):
        reloj = RelojFalso()
        plazo = Plazo(segundos, reloj)
        sock = SocketFalso(reloj, segmentos)
        sock.plazo = plazo
        return reloj, plazo, sock, SocketConPlazo(sock, plazo)

    def test_cabeceras_que_gotean_un_byte_cada_19_segundos_no_pasan_del_plazo(self):
        reloj, plazo, sock, envuelto = self.armar([(respuesta(), 19)])
        inicio = reloj.ahora
        resp = http.client.HTTPResponse(envuelto)
        with self.assertRaises((TimeoutError, PlazoVencido)):
            resp.begin()
        self.assertLessEqual(reloj.ahora - inicio, 60 + 1e-9)  # ni un segundo de más
        self.assertGreaterEqual(reloj.ahora - inicio, 60 - 1e-9)  # y usó todo el plazo, no se rindió antes

    def test_cada_timeout_es_el_menor_entre_el_de_siempre_y_lo_que_queda(self):
        reloj, plazo, sock, envuelto = self.armar([(respuesta(), 19)])
        with self.assertRaises((TimeoutError, PlazoVencido)):
            http.client.HTTPResponse(envuelto).begin()
        self.assertTrue(sock.registro)
        for puesto, restante in sock.registro:
            self.assertLessEqual(puesto, TIMEOUT)
            self.assertLessEqual(puesto, restante + 1e-9)  # nunca más de lo que quedaba
            self.assertGreater(puesto, 0)
        self.assertEqual(sock.registro[0][0], TIMEOUT)  # al principio, el normal
        self.assertLess(sock.registro[-1][0], TIMEOUT)  # al final, lo que quedaba

    def test_si_ya_no_queda_plazo_ni_se_intenta_la_operacion(self):
        reloj, plazo, sock, envuelto = self.armar([(respuesta(), 15)])
        # 4 recv de 15 s agotan los 60 s justos; el quinto ya no se intenta
        resp = http.client.HTTPResponse(envuelto)
        with self.assertRaises(PlazoVencido):
            resp.begin()
        self.assertEqual(reloj.ahora - 1000.0, 60.0)
        self.assertEqual(len(sock.cola) + 4, len(respuesta()))  # se consumieron exactamente 4 bytes

    def test_con_cabeceras_rapidas_la_respuesta_se_lee_entera(self):
        reloj, plazo, sock, envuelto = self.armar([(respuesta(b"hola mundo"), 0.01)])
        resp = http.client.HTTPResponse(envuelto)
        resp.begin()
        self.assertEqual(resp.read(), b"hola mundo")
        self.assertEqual(resp.status, 200)

    def test_el_cuerpo_que_gotea_tambien_corta(self):
        cuerpo = b"x" * 100
        reloj, plazo, sock, envuelto = self.armar([(respuesta(cuerpo)[: -len(cuerpo)], 0.0), (cuerpo, 19)])
        resp = http.client.HTTPResponse(envuelto)
        resp.begin()
        with self.assertRaises((TimeoutError, PlazoVencido)):
            resp.read()
        self.assertLessEqual(reloj.ahora - 1000.0, 60 + 1e-9)

    def test_enviar_tambien_respeta_el_plazo(self):
        reloj, plazo, sock, envuelto = self.armar([])
        envuelto.sendall(b"GET / HTTP/1.1\r\n\r\n")
        self.assertEqual(sock.enviado, b"GET / HTTP/1.1\r\n\r\n")
        self.assertEqual(sock.registro, [(TIMEOUT, 60.0)])
        reloj.avanzar(61)
        with self.assertRaises(PlazoVencido):
            envuelto.sendall(b"otra vez")

    def test_cerrar_la_respuesta_no_cierra_el_socket_hasta_soltar_las_referencias(self):
        reloj, plazo, sock, envuelto = self.armar([(respuesta(), 0.0)])
        resp = http.client.HTTPResponse(envuelto)
        resp.begin()
        self.assertEqual(sock._io_refs, 1)  # el makefile real tomó su referencia
        resp.read()
        resp.close()
        self.assertEqual(sock._io_refs, 0)  # y la soltó al cerrar

    def test_lo_que_no_es_del_envoltorio_pasa_al_socket(self):
        reloj, plazo, sock, envuelto = self.armar([])
        self.assertEqual(envuelto.fileno(), 99)
        envuelto.close()
        self.assertTrue(sock.cerrado)


class PlazoTest(unittest.TestCase):
    def test_la_fecha_limite_se_fija_al_crear_y_no_se_mueve(self):
        reloj = RelojFalso()
        plazo = Plazo(60, reloj)
        reloj.avanzar(10)
        self.assertEqual(plazo.restante(), 50)
        self.assertEqual(plazo.timeout(), TIMEOUT)
        reloj.avanzar(35)
        self.assertEqual(plazo.timeout(), 15)
        reloj.avanzar(14.99)
        self.assertAlmostEqual(plazo.timeout(), 0.05, places=2)  # nunca cero: un timeout 0 sería "sin esperar"
        reloj.avanzar(0.01)
        with self.assertRaises(PlazoVencido) as ctx:
            plazo.timeout()
        self.assertIn("plazo total", str(ctx.exception))
        self.assertIsInstance(ctx.exception, ErrorDescarga)

    def test_el_plazo_por_defecto_es_el_de_la_constante(self):
        self.assertEqual(descarga.PLAZO_TOTAL, 60)
        self.assertEqual(Plazo().segundos, 60)


class ConexionTest(unittest.TestCase):
    def conexion(self, plazo, segmentos=()):
        reloj = plazo._reloj
        capturado = {}

        class Falsa(ConexionConPlazo):
            def _socket_crudo(self):
                capturado["timeout"] = self.timeout  # lo que la conexión y el TLS recibirían
                capturado["socket"] = SocketFalso(reloj, list(segmentos))
                return capturado["socket"]

        return Falsa("primero.test", plazo=plazo), capturado

    def test_conectar_usa_lo_que_queda_del_plazo(self):
        reloj = RelojFalso()
        plazo = Plazo(60, reloj)
        con, capturado = self.conexion(plazo)
        con.connect()
        self.assertEqual(capturado["timeout"], TIMEOUT)
        self.assertIsInstance(con.sock, SocketConPlazo)
        reloj.avanzar(52)
        con2, capturado2 = self.conexion(plazo)
        con2.connect()
        self.assertEqual(capturado2["timeout"], 8)  # quedaban 8 s: la conexión no puede tardar 20

    def test_con_el_plazo_agotado_ni_se_toca_la_red(self):
        reloj = RelojFalso()
        plazo = Plazo(60, reloj)
        reloj.avanzar(61)
        con, capturado = self.conexion(plazo)
        with self.assertRaises(PlazoVencido):
            con.connect()
        self.assertEqual(capturado, {})


class FlujoCompletoTest(unittest.TestCase):
    """`descargar` con el opener real de urllib y conexiones falsas: redirecciones, cabeceras y cuerpo."""

    def setUp(self):
        self.reloj = RelojFalso()
        self.guiones = {}
        self.conexiones = []
        reloj, guiones, conexiones = self.reloj, self.guiones, self.conexiones

        class ConexionFalsa(ConexionConPlazo):
            def _socket_crudo(self):
                sock = SocketFalso(reloj, guiones[self.host])
                sock.plazo = self._plazo
                conexiones.append((self.host, sock))
                return sock

        class ManejadorFalso(ManejadorHttps):
            _clase = ConexionFalsa

        for parche in (
            mock.patch.object(descarga, "_ahora", self.reloj),
            mock.patch.object(descarga, "ManejadorHttps", ManejadorFalso),
        ):
            parche.start()
            self.addCleanup(parche.stop)

    REDIRIGE = (
        b"HTTP/1.1 302 Found\r\nLocation: https://segundo.test/b\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    )

    def test_una_descarga_normal_pasa_por_el_opener_real(self):
        self.guiones["primero.test"] = [(respuesta("<feed>áé</feed>".encode()), 0.01)]
        self.assertEqual(descargar("https://primero.test/feed"), "<feed>áé</feed>")
        host, sock = self.conexiones[0]
        self.assertIn(b"GET /feed HTTP/1.1", sock.enviado)
        self.assertIn(b"User-Agent: ingol-radar/0.1", sock.enviado)
        self.assertNotIn(b"gzip", sock.enviado)  # no se pide compresión (http.client manda `identity` por su cuenta)
        self.assertTrue(sock.cerrado)

    def test_una_respuesta_con_chunks(self):
        cabecera = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
        self.guiones["primero.test"] = [(cabecera + b"3\r\nhol\r\n2\r\nal\r\n0\r\n\r\n", 0.0)]
        self.assertEqual(descargar("https://primero.test/feed"), "holal")

    def test_la_redireccion_sigue_y_comparte_el_mismo_plazo(self):
        self.guiones["primero.test"] = [(self.REDIRIGE, 0.1)]
        self.guiones["segundo.test"] = [(respuesta(b"destino"), 0.1)]
        self.assertEqual(descargar("https://primero.test/a"), "destino")
        self.assertEqual([h for h, _ in self.conexiones], ["primero.test", "segundo.test"])

    def test_cabeceras_que_gotean_en_la_primera_conexion_cortan_en_el_plazo_total(self):
        self.guiones["primero.test"] = [(respuesta(), 19)]
        with self.assertRaises(ErrorDescarga) as ctx:
            descargar("https://primero.test/a")
        self.assertIn("plazo total", str(ctx.exception))
        self.assertLessEqual(self.reloj.ahora - 1000.0, 60 + 1e-9)

    def test_cabeceras_que_gotean_en_la_redireccion_cortan_en_el_plazo_total_contando_lo_ya_gastado(self):
        self.guiones["primero.test"] = [(self.REDIRIGE, 0.1)]  # unos 9,5 s en el primer salto
        self.guiones["segundo.test"] = [(respuesta(), 19)]
        with self.assertRaises(ErrorDescarga) as ctx:
            descargar("https://primero.test/a")
        self.assertIn("plazo total", str(ctx.exception))
        self.assertEqual([h for h, _ in self.conexiones], ["primero.test", "segundo.test"])  # la redirección sí se siguió
        total = self.reloj.ahora - 1000.0
        self.assertLessEqual(total, 60 + 1e-9)  # el plazo es de TODO, no de cada salto
        self.assertGreaterEqual(total, 60 - 1e-9)
        # y el segundo salto no arrancó con un plazo nuevo de 60 s: sus timeouts nunca pasaron de lo que quedaba
        _, segundo = self.conexiones[1]
        self.assertTrue(all(p <= r + 1e-9 for p, r in segundo.registro))
        self.assertLess(segundo.registro[0][1], 60 - 9)

    def test_el_cuerpo_que_gotea_despues_de_cabeceras_rapidas_corta_en_el_plazo_total(self):
        cuerpo = b"x" * 100
        cabecera = respuesta(cuerpo)[: -len(cuerpo)]
        self.guiones["primero.test"] = [(cabecera, 0.5), (cuerpo, 19)]
        with self.assertRaises(ErrorDescarga) as ctx:
            descargar("https://primero.test/a")
        self.assertIn("plazo total", str(ctx.exception))
        self.assertLessEqual(self.reloj.ahora - 1000.0, 60 + 1e-9)

    def test_el_cuerpo_comparte_el_plazo_con_lo_que_ya_se_gasto_al_abrir(self):
        """Con una respuesta que no es de socket (sin envoltorio) el plazo lo hace cumplir leer_cuerpo."""

        class Goteo:
            headers = http.client.HTTPMessage()

            def __init__(self, reloj):
                self.reloj, self.lecturas = reloj, 0

            def read1(self, n=-1):
                self.lecturas += 1
                self.reloj.avanzar(3)
                return b"x"

            def close(self):
                pass

        goteo = Goteo(self.reloj)

        def abrir(url):
            self.reloj.avanzar(50)  # conectar y recibir cabeceras ya se llevó 50 de los 60 s
            return goteo

        with self.assertRaises(PlazoVencido) as ctx:
            descargar("https://primero.test/a", abrir=abrir)
        self.assertIn("plazo total", str(ctx.exception))
        self.assertEqual(goteo.lecturas, 4)  # 4 lecturas de 3 s agotan los 10 s que quedaban
        self.assertEqual(self.reloj.ahora - 1000.0, 62)

    def test_conectar_a_un_host_que_redirige_a_http_o_a_uno_interno_se_rechaza(self):
        for destino in (b"http://segundo.test/b", b"https://127.0.0.1/b", b"https://localhost/b", b"https://u:p@segundo.test/b"):
            with self.subTest(destino):
                self.conexiones.clear()
                self.guiones["primero.test"] = [
                    (b"HTTP/1.1 302 Found\r\nLocation: " + destino + b"\r\nContent-Length: 0\r\n\r\n", 0.0)
                ]
                with self.assertRaises(ErrorDescarga):
                    descargar("https://primero.test/a")
                self.assertEqual([h for h, _ in self.conexiones], ["primero.test"])  # nunca se conectó al destino

    def test_mas_de_cinco_redirecciones_cortan(self):
        for i in range(8):
            self.guiones[f"h{i}.test"] = [
                (f"HTTP/1.1 302 Found\r\nLocation: https://h{i + 1}.test/\r\nContent-Length: 0\r\n\r\n".encode(), 0.0)
            ]
        with self.assertRaises(ErrorDescarga):
            descargar("https://h0.test/")
        self.assertLessEqual(len(self.conexiones), 6)

    def test_un_error_de_red_antes_del_plazo_sigue_siendo_un_error_normal(self):
        self.guiones["primero.test"] = [(b"HTTP/1.1 503 No\r\nContent-Length: 0\r\n\r\n", 0.0)]
        with self.assertRaises(ErrorDescarga) as ctx:
            descargar("https://primero.test/a")
        self.assertNotIn("plazo total", str(ctx.exception))

    def test_el_manejador_real_se_arma_con_un_plazo(self):
        opener = descarga.crear_opener(Plazo(10, self.reloj))
        tipos = [type(h) for h in opener.handlers]
        self.assertIn(SoloHttps, tipos)
        self.assertIn(descarga.ManejadorHttps, tipos)  # (patcheado en estas pruebas)
        with mock.patch.object(descarga, "ManejadorHttps", ManejadorHttps):
            tipos = [type(h) for h in descarga.crear_opener().handlers]
        self.assertIn(ManejadorHttps, tipos)
        self.assertEqual([t for t in tipos if issubclass(t, urllib.request.HTTPSHandler)], [ManejadorHttps])


class SoloLecturaTest(unittest.TestCase):
    def test_un_archivo_en_memoria_no_pasa_por_el_envoltorio(self):
        """leer_cuerpo sigue andando con respuestas que no son de socket (las de las pruebas)."""

        class Resp:
            headers = http.client.HTTPMessage()

            def __init__(self):
                self._f = io.BytesIO(b"ok")

            def read(self, n=-1):
                return self._f.read(n)

        self.assertEqual(descarga.leer_cuerpo(Resp(), plazo=Plazo(5, RelojFalso())), b"ok")


if __name__ == "__main__":
    unittest.main()
