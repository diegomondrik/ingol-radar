"""El fetcher real: urllib, sólo https (también en cada redirección), con tope de tamaño y de tiempo.

Es el único módulo de la lectura que abre la red. El lector recibe un `fetcher` (url -> texto),
así que las pruebas nunca lo usan: lo reemplazan por uno falso.
"""

from __future__ import annotations

import codecs
import http.client
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .modelos import ErrorDescarga

USER_AGENT = "ingol-radar/0.1 (+https://github.com/diegomondrik/ingol-radar)"
TIMEOUT = 20  # segundos por operación de red
PLAZO_TOTAL = 60  # segundos para bajar un documento entero (el timeout solo no frena un goteo)
TOPE_BYTES = 5 * 1024 * 1024
TROZO = 64 * 1024
MAX_REDIRECCIONES = 5


def _exigir_https(url: str) -> None:
    if urlsplit(url).scheme.lower() != "https":
        raise ErrorDescarga(f"sólo se lee por https: {url[:200]!r}")


class SoloHttps(urllib.request.HTTPRedirectHandler):
    """Sigue redirecciones (hasta cinco) sólo si el destino también es https."""

    max_redirections = MAX_REDIRECCIONES

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _exigir_https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def crear_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(SoloHttps())


def _abrir_real(url: str):
    pedido = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return crear_opener().open(pedido, timeout=TIMEOUT)


def leer_cuerpo(respuesta, tope: int = TOPE_BYTES, plazo: float = PLAZO_TOTAL, reloj=time.monotonic) -> bytes:
    """Lee la respuesta de a trozos; si pasa el tope o el plazo, error (nunca devuelve algo cortado)."""
    inicio = reloj()
    largo = respuesta.headers.get("Content-Length")
    if largo is not None:
        largo = largo.strip()
        if largo.isascii() and largo.isdigit() and len(largo) <= 15 and int(largo) > tope:
            raise ErrorDescarga(f"la respuesta declara {largo} bytes, más que el tope de {tope}")
    partes: list[bytes] = []
    total = 0
    while True:
        trozo = respuesta.read(min(TROZO, tope + 1 - total))
        if not trozo:
            break
        total += len(trozo)
        if total > tope:
            raise ErrorDescarga(f"la respuesta pasa el tope de {tope} bytes")
        partes.append(trozo)
        if reloj() - inicio > plazo:
            raise ErrorDescarga(f"la descarga tardó más de {plazo} segundos")
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


def descargar(url: str, abrir=None) -> str:
    """Baja `url` (https) y devuelve el texto. `abrir` permite inyectar una respuesta falsa."""
    _exigir_https(url)
    abrir = abrir or _abrir_real
    try:
        respuesta = abrir(url)
        try:
            cuerpo = leer_cuerpo(respuesta)
            charset = respuesta.headers.get_content_charset()
        finally:
            cerrar = getattr(respuesta, "close", None)
            if cerrar is not None:
                cerrar()
    except ErrorDescarga:
        raise
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        raise ErrorDescarga(f"no se pudo bajar {url[:200]!r}: {type(exc).__name__}: {exc}") from exc
    return decodificar(cuerpo, charset)
