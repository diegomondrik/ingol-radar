"""El fetcher real: urllib, sólo https (también en cada redirección), con tope de tamaño y plazo total.

Es el único módulo de la lectura que abre la red. El lector recibe un `fetcher` (url -> texto),
así que las pruebas nunca lo usan: lo reemplazan por uno falso.

PLAZO TOTAL. `descargar` fija una fecha límite de pared al entrar (PLAZO_TOTAL segundos) que cubre
TODO: conexión, TLS, línea de estado, cabeceras, redirecciones y cuerpo. Cómo se hace cumplir: cada
conexión es una `ConexionConPlazo` que envuelve su socket en un `SocketConPlazo`; antes de CADA
operación de red (conectar, enviar, cada recv del TLS, de las cabeceras y del cuerpo) el timeout del
socket pasa a min(TIMEOUT, lo que queda del plazo) y, si ya no queda nada, se levanta `PlazoVencido`.
Se eligió esto y no un hilo vigilante que cierre el socket porque es determinista (no depende de que
cerrar un socket desde otro hilo despierte una lectura bloqueada, que cambia según el sistema) y se
prueba sin red con un socket falso. Cota: el plazo más lo que tarde una operación en ver que venció,
que nunca pasa de lo que quedaba.
Límites que no cubre: la resolución de nombres (getaddrinfo no tiene timeout) y, si un nombre tiene
varias direcciones, cada intento de conexión usa el timeout que quedaba al empezar.
"""

from __future__ import annotations

import codecs
import http.client
import io
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import partial
from urllib.parse import urlsplit

from .limpieza import host_publico
from .modelos import ErrorDescarga

USER_AGENT = "ingol-radar/0.1 (+https://github.com/diegomondrik/ingol-radar)"
TIMEOUT = 20  # segundos por operación de red (como máximo; baja con lo que queda del plazo)
PLAZO_TOTAL = 60  # segundos de pared para TODO el descargar()
TOPE_BYTES = 5 * 1024 * 1024
TROZO = 8 * 1024  # trozos chicos: el reloj se mira seguido aunque el servidor gotee
MAX_REDIRECCIONES = 5


def _ahora() -> float:
    return time.monotonic()


class PlazoVencido(ErrorDescarga):
    """Se agotó el plazo total de la descarga."""


class Plazo:
    """Una fecha límite de pared, fijada al crearla."""

    def __init__(self, segundos: float | None = None, reloj=None) -> None:
        self.segundos = PLAZO_TOTAL if segundos is None else segundos
        self._reloj = reloj or _ahora
        self.limite = self._reloj() + self.segundos

    def restante(self) -> float:
        return self.limite - self._reloj()

    def verificar(self) -> None:
        if self.restante() <= 0:
            raise PlazoVencido(f"plazo total de {self.segundos} segundos agotado")

    def timeout(self) -> float:
        """El timeout para la próxima operación de red: el de siempre, o menos si queda menos."""
        self.verificar()
        return max(0.05, min(TIMEOUT, self.restante()))


def host_sin_www(host: str) -> str:
    """El host en minúscula y sin UN `www.` inicial: `www.anthropic.com` y `anthropic.com` son el mismo sitio."""
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def _puerto_escrito(netloc: str) -> str:
    """El puerto tal como está escrito en la url ("" si no hay): `:0443` no es lo mismo que `:443`."""
    resto = netloc.rpartition("@")[2]
    if resto.startswith("["):  # IPv6: [::1]:443
        resto = resto.partition("]")[2]
        return resto[1:] if resto.startswith(":") else ""
    return resto.partition(":")[2]


def ruta_bajo_prefijo(ruta: str, prefijo: str) -> bool:
    """True si `ruta` (la parte de ruta de una url) está debajo de `prefijo` (por ejemplo `/openai/codex/`).

    El prefijo se compara sobre la ruta CRUDA (sin des-escapar), sin distinguir mayúsculas (GitHub no las
    distingue). Se rechaza una ruta con `%2f` o `%5c` (una barra o una barra invertida escondidas), con doble
    escape (lo que se des-escapa una vez y vuelve a cambiar) y, ya des-escapada una vez, con `.` o `..` como
    segmento, barras invertidas o NUL: `/openai/codex/../evil` y `/openai/codex/%2e%2e/evil` no están debajo.
    """
    if re.search("%(?:2f|5c)", ruta, re.IGNORECASE):
        return False
    actual = urllib.parse.unquote(ruta)
    if urllib.parse.unquote(actual) != actual:
        return False
    if "\\" in actual or "\x00" in actual:
        return False
    segmentos = actual.split("/")
    if any(seg in (".", "..") for seg in segmentos):
        return False
    return ruta.lower().startswith(prefijo.lower())


def _exigir_mismo_host(url: str, host: str, prefijo: str | None = None) -> None:
    """La url tiene que quedarse en `host` (y, si se indica, debajo de `prefijo`), por https en el puerto 443."""
    try:
        partes = urlsplit(url)
        destino = partes.hostname
        puerto = partes.port
    except ValueError as exc:
        raise ErrorDescarga(f"url ilegible: {url[:200]!r}") from exc
    if host_sin_www(destino or "") != host_sin_www(host):
        raise ErrorDescarga(f"el destino sale del sitio {host!r}: {url[:200]!r}")
    if puerto not in (None, 443) or _puerto_escrito(partes.netloc) not in ("", "443"):
        raise ErrorDescarga(f"el destino usa un puerto que no es el 443: {url[:200]!r}")
    if prefijo is not None and not ruta_bajo_prefijo(partes.path or "/", prefijo):
        raise ErrorDescarga(f"el destino sale de {prefijo!r}: {url[:200]!r}")


def _exigir_destino(url: str) -> None:
    """Sólo https, sin usuario ni clave, sin puerto 0 y sólo hacia un host de internet pública
    (nada de localhost, IP privadas, link-local, multicast ni NAT64).

    No cubre un nombre público que resuelva a una IP privada (DNS rebinding): para eso haría
    falta resolver y fijar la IP, y la lista de fuentes es fija y de confianza.
    """
    try:
        partes = urlsplit(url)
        host = partes.hostname
        puerto = partes.port  # un puerto fuera de rango levanta ValueError
        usuario, clave = partes.username, partes.password
    except ValueError as exc:
        raise ErrorDescarga(f"url ilegible: {url[:200]!r}") from exc
    if partes.scheme.lower() != "https":
        raise ErrorDescarga(f"sólo se lee por https: {url[:200]!r}")
    if usuario is not None or clave is not None or "@" in partes.netloc:
        raise ErrorDescarga(f"la url trae usuario o clave: {url[:200]!r}")
    if puerto == 0:
        raise ErrorDescarga(f"puerto 0: {url[:200]!r}")
    if not host_publico(host):
        raise ErrorDescarga(f"el destino no es un host público: {url[:200]!r}")


# ------------------------------------------------------------- sockets con plazo


class _CrudoConPlazo(io.RawIOBase):
    """La lectura cruda de una respuesta: cada readinto (cada recv) respeta el plazo."""

    def __init__(self, crudo, sock) -> None:
        super().__init__()
        self._crudo = crudo
        self._sock = sock

    def readable(self) -> bool:
        return True

    def readinto(self, b):
        self._sock.preparar()
        return self._crudo.readinto(b)

    def fileno(self):
        return self._crudo.fileno()

    def close(self) -> None:
        try:
            self._crudo.close()  # suelta la referencia que el makefile real le tomó al socket
        finally:
            super().close()


class SocketConPlazo:
    """Envuelve un socket: antes de cada operación de red ajusta su timeout al plazo que queda."""

    def __init__(self, sock, plazo: Plazo) -> None:
        self._sock = sock
        self._plazo = plazo

    def preparar(self) -> None:
        self._sock.settimeout(self._plazo.timeout())

    def sendall(self, datos, *args):
        self.preparar()
        return self._sock.sendall(datos, *args)

    def send(self, datos, *args):
        self.preparar()
        return self._sock.send(datos, *args)

    def recv(self, n, *args):
        self.preparar()
        return self._sock.recv(n, *args)

    def recv_into(self, buffer, *args):
        self.preparar()
        return self._sock.recv_into(buffer, *args)

    def makefile(self, mode="r", buffering=None, **kwargs):
        if mode != "rb":
            return self._sock.makefile(mode, buffering, **kwargs)
        crudo = self._sock.makefile("rb", buffering=0)
        return io.BufferedReader(_CrudoConPlazo(crudo, self), io.DEFAULT_BUFFER_SIZE)

    def close(self) -> None:
        self._sock.close()

    def __getattr__(self, nombre):
        return getattr(self._sock, nombre)


class ConexionConPlazo(http.client.HTTPSConnection):
    """Una conexión https cuyo socket respeta el plazo total en cada operación."""

    def __init__(self, *args, plazo: Plazo, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._plazo = plazo

    def _socket_crudo(self):
        """El único punto que toca la red: conectar y negociar TLS."""
        super().connect()
        return self.sock

    def connect(self) -> None:
        self.timeout = self._plazo.timeout()  # conexión y TLS, con lo que quede del plazo
        self.sock = SocketConPlazo(self._socket_crudo(), self._plazo)


class ManejadorHttps(urllib.request.HTTPSHandler):
    """Abre las conexiones https con `ConexionConPlazo`, todas con el mismo plazo (redirecciones incluidas)."""

    _clase = ConexionConPlazo

    def __init__(self, plazo: Plazo) -> None:
        super().__init__()
        self._plazo = plazo

    def https_open(self, req):
        return self.do_open(partial(self._clase, plazo=self._plazo), req, context=self._context)


class SoloHttps(urllib.request.HTTPRedirectHandler):
    """Sigue redirecciones (hasta cinco) sólo si el destino también es https y de un host público."""

    max_redirections = MAX_REDIRECCIONES

    def __init__(self, host: str | None = None, prefijo: str | None = None) -> None:
        super().__init__()
        self.host = host  # si se indica, una redirección sólo puede quedarse en ese sitio (puerto 443)
        self.prefijo = prefijo  # y, si se indica, debajo de esta ruta

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _exigir_destino(newurl)
        if self.host is not None:
            _exigir_mismo_host(newurl, self.host, self.prefijo)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def crear_opener(
    plazo: Plazo | None = None, host: str | None = None, prefijo: str | None = None
) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(SoloHttps(host, prefijo), ManejadorHttps(plazo or Plazo()))


def _abrir_real(url: str, plazo: Plazo | None = None, host: str | None = None, prefijo: str | None = None):
    pedido = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if host is None:
        opener = crear_opener(plazo)
    elif prefijo is None:
        opener = crear_opener(plazo, host)
    else:
        opener = crear_opener(plazo, host, prefijo)
    return opener.open(pedido, timeout=TIMEOUT)


def leer_cuerpo(respuesta, tope: int = TOPE_BYTES, plazo: float | Plazo = PLAZO_TOTAL, reloj=None) -> bytes:
    """Lee la respuesta de a trozos; si pasa el tope o el plazo, error (nunca devuelve algo cortado).

    Usa `read1` (devuelve lo que haya sin esperar a llenar el trozo) y verifica el plazo en cada
    vuelta. `plazo` es un `Plazo` (el de `descargar`, que ya cuenta lo gastado en conectar y en las
    cabeceras) o una cantidad de segundos que empieza a correr acá.
    """
    if not isinstance(plazo, Plazo):
        plazo = Plazo(plazo, reloj)
    leer = getattr(respuesta, "read1", None) or respuesta.read
    largo = respuesta.headers.get("Content-Length")
    if largo is not None:
        largo = largo.strip()
        if largo.isascii() and largo.isdigit() and len(largo) <= 15 and int(largo) > tope:
            raise ErrorDescarga(f"la respuesta declara {largo} bytes, más que el tope de {tope}")
    partes: list[bytes] = []
    total = 0
    while True:
        plazo.verificar()
        trozo = leer(min(TROZO, tope + 1 - total))
        if not trozo:
            break
        total += len(trozo)
        if total > tope:
            raise ErrorDescarga(f"la respuesta pasa el tope de {tope} bytes")
        partes.append(trozo)
    return b"".join(partes)


def decodificar(cuerpo: bytes, charset: str | None) -> str:
    """Con el charset del encabezado si es un código de texto conocido; si no, UTF-8. Nunca lanza."""
    if charset:
        try:
            codecs.lookup(charset)
            return cuerpo.decode(charset, errors="replace")
        except (LookupError, TypeError, ValueError):
            pass
    return cuerpo.decode("utf-8", errors="replace")


def descargar(url: str, abrir=None, host: str | None = None, prefijo: str | None = None) -> str:
    """Baja `url` (https) y devuelve el texto, todo dentro de PLAZO_TOTAL. `abrir` permite inyectar una respuesta falsa.

    Con `host`, la url y TODA redirección tienen que quedarse en ese sitio (ver `host_sin_www`), por
    https en el puerto 443; con `prefijo`, además debajo de esa ruta (ver `ruta_bajo_prefijo`).
    """
    plazo = Plazo()  # la fecha límite se fija al entrar y cubre todo lo que sigue
    _exigir_destino(url)
    if host is not None:
        _exigir_mismo_host(url, host, prefijo)
    abrir = abrir or partial(_abrir_real, plazo=plazo, host=host, prefijo=prefijo)
    try:
        respuesta = abrir(url)
        try:
            cuerpo = leer_cuerpo(respuesta, plazo=plazo)
            charset = respuesta.headers.get_content_charset()
        finally:
            cerrar = getattr(respuesta, "close", None)
            if cerrar is not None:
                cerrar()
    except ErrorDescarga:
        raise
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        if plazo.restante() <= 0:  # el timeout del socket venció justo con el plazo
            raise PlazoVencido(f"plazo total de {plazo.segundos} segundos agotado ({type(exc).__name__})") from exc
        raise ErrorDescarga(f"no se pudo bajar {url[:200]!r}: {type(exc).__name__}: {exc}") from exc
    return decodificar(cuerpo, charset)
