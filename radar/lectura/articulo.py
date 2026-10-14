"""La lectura del artículo de una novedad, para la segunda pasada del análisis.

Qué se baja y qué no:
- Sólo https, sólo un host público (la misma validación que el resto de la lectura) y sólo el
  MISMO sitio que la fuente de la novedad (`www.` inicial normalizado: `anthropic.com/news` y
  `www.anthropic.com` son el mismo). Una redirección a otro sitio se rechaza (lo hace `descargar`).
- No se baja: `ibm-youtube` (un video no tiene texto) y `claude-plataforma` (la nota es el contenido
  y su enlace es la página misma). Las versiones de GitHub (`claude-code`, `codex`) SÍ se leen:
  la frase del feed es sólo la primera oración de las notas, y la página de la versión trae el
  resto.
- Lo que baja es de internet y no es confiable: se extrae el texto como dato (nada de lo que diga
  se interpreta), se limpia como el resto de la lectura y se recorta a MAX_ARTICULO caracteres.

Extracción con `html.parser`: se prefiere `<article>`, si no `<main>`, si no todo el cuerpo; se
descartan script, style, noscript, nav, header, footer, aside, form, svg y template; los
párrafos quedan separados por un salto de línea.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .descarga import descargar, host_sin_www
from .fuentes import por_id
from .limpieza import alimentar, limpiar_texto, validar_enlace
from .modelos import DocumentoRechazado, ErrorLectura, Novedad

MAX_ARTICULO = 12_000
MIN_TEXTO = 40  # menos que esto no es un artículo (página vacía, "cargando...")
TOPE_PAGINA = 1_000_000  # caracteres de HTML que se analizan; el resto se descarta
TOPE_ACUMULADO = 400_000  # caracteres de texto que se juntan por contenedor antes de cortar
MAX_ANIDAMIENTO = 2_000
NL = chr(10)
TROZO_LINEA = 3_000  # `limpiar_texto` corta el crudo a 4000: una línea más larga se limpia por trozos

# Estados de `Articulo.estado`
OK = "ok"
NO_APLICA = "no_aplica"
HOST_DISTINTO = "host_distinto"
SIN_TEXTO = "sin_texto"
ERROR = "error"

NO_SE_LEE = {
    "ibm-youtube": "video sin texto",
    "claude-plataforma": "la nota es el contenido: su enlace es la página misma",
}

_DESCARTADAS = frozenset(
    {"head", "script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "template"}
)  # head: el título de la pestaña no es parte del artículo
_BLOQUES = frozenset(
    {
        "p", "div", "br", "li", "ul", "ol", "tr", "td", "th", "table", "h1", "h2", "h3", "h4", "h5", "h6",
        "blockquote", "pre", "section", "article", "main", "figure", "figcaption", "dd", "dt", "dl", "hr",
    }
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Articulo:
    """El resultado de intentar leer el artículo: con `texto` (estado "ok") o con el `motivo` por el que no."""

    estado: str
    texto: str = ""
    motivo: str | None = None
    recortado: bool = False
    enlace: str | None = None

    @property
    def legible(self) -> bool:
        return self.estado == OK and bool(self.texto)


def _sin_texto(estado: str, motivo: str, enlace: str | None = None) -> Articulo:
    return Articulo(estado=estado, motivo=limpiar_texto(motivo, 300), enlace=enlace)


# ------------------------------------------------------------------------- extracción


class _Principal(HTMLParser):
    """Junta el texto de `<article>`, de `<main>` y de todo lo demás, sin lo descartado."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.articulo: list[str] = []
        self.principal: list[str] = []
        self.todo: list[str] = []
        self._n = {"articulo": 0, "principal": 0, "todo": 0}
        self._abiertas: list[str] = []  # pila de etiquetas descartadas abiertas
        self._en_articulo = 0
        self._en_principal = 0

    def _agregar(self, texto: str) -> None:
        for destino, activo, nombre in (
            (self.articulo, self._en_articulo > 0, "articulo"),
            (self.principal, self._en_principal > 0, "principal"),
            (self.todo, True, "todo"),
        ):
            if activo and self._n[nombre] < TOPE_ACUMULADO:
                destino.append(texto)
                self._n[nombre] += len(texto)

    def handle_starttag(self, tag, attrs):
        if tag == "body" and "head" in self._abiertas:  # un <head> que nunca se cerró no se come el cuerpo
            del self._abiertas[len(self._abiertas) - 1 - self._abiertas[::-1].index("head") :]
        if tag in _DESCARTADAS:
            if len(self._abiertas) >= MAX_ANIDAMIENTO:
                raise DocumentoRechazado("demasiadas etiquetas descartadas sin cerrar")
            self._abiertas.append(tag)
            return
        if tag == "article":
            self._en_articulo += 1
        elif tag == "main":
            self._en_principal += 1
        if tag in _BLOQUES:
            self._agregar("\n")

    def handle_startendtag(self, tag, attrs):
        # `<nav/>` no abre nada; un `<br/>` sí separa.
        if tag in _BLOQUES and tag not in _DESCARTADAS:
            self._agregar("\n")

    def handle_endtag(self, tag):
        if tag in _DESCARTADAS:
            if tag in self._abiertas:
                while self._abiertas and self._abiertas.pop() != tag:
                    pass
            return
        if tag == "article":
            self._en_articulo = max(0, self._en_articulo - 1)
        elif tag == "main":
            self._en_principal = max(0, self._en_principal - 1)
        if tag in _BLOQUES:
            self._agregar("\n")

    def handle_data(self, data):
        if not self._abiertas:
            # Los saltos de línea del HTML son espacios; los párrafos los marcan las etiquetas.
            self._agregar((" " if data[:1].isspace() else "") + " ".join(data.split()) + (" " if data[-1:].isspace() and data.strip() else ""))


def _limpiar_lineas(crudo: str) -> str:
    """Una línea por párrafo, cada una limpia (sin etiquetas, controles ni invisibles); sin líneas vacías."""
    lineas = []
    for linea in crudo.split(NL):
        trozos: list[str] = []
        actual: list[str] = []
        largo = 0
        for palabra in linea.split():
            if actual and largo + len(palabra) + 1 > TROZO_LINEA:
                trozos.append(" ".join(actual))
                actual, largo = [], 0
            actual.append(palabra)
            largo += len(palabra) + 1
        if actual:
            trozos.append(" ".join(actual))
        limpios = [t for t in (limpiar_texto(t, TROZO_LINEA * 2) for t in trozos) if t]
        if limpios:
            lineas.append(" ".join(limpios))
    return NL.join(lineas)


def recortar(texto: str, limite: int = MAX_ARTICULO) -> tuple[str, bool]:
    """Recorta a `limite` caracteres a límite de palabra. Devuelve (texto, si se recortó)."""
    if len(texto) <= limite:
        return texto, False
    if texto[limite].isspace():
        corte = texto[:limite]
    else:
        corte = texto[:limite]
        i = max(corte.rfind(" "), corte.rfind("\n"))
        if i > limite // 2:
            corte = corte[:i]
    return corte.rstrip(), True


def texto_plano(texto: str) -> tuple[str, bool]:
    """Limpia un texto que ya es texto (no HTML): por línea, sin controles ni etiquetas, recortado."""
    return recortar(_limpiar_lineas(texto if isinstance(texto, str) else ""))


def texto_principal(html: str) -> tuple[str, bool]:
    """Del HTML de una página al texto principal, limpio y recortado: (texto, recortado). Puede ser ("", False)."""
    if not isinstance(html, str):
        return "", False
    parser = _Principal()
    try:
        alimentar(parser, html[:TOPE_PAGINA])
    except DocumentoRechazado:
        raise
    except Exception as exc:  # noqa: BLE001 - un HTML que rompe el analizador no debe tumbar la corrida
        raise DocumentoRechazado(f"el HTML no se pudo analizar: {type(exc).__name__}") from exc
    for pedazos in (parser.articulo, parser.principal, parser.todo):
        texto = _limpiar_lineas("".join(pedazos))
        if texto:
            return recortar(texto)
    return "", False


# ----------------------------------------------------------------------------- lectura


def _enlace_https_del_sitio(novedad: Novedad, fuente) -> tuple[str | None, str | None]:
    """(enlace validado, motivo de rechazo). Sólo https, host público, mismo sitio que la fuente."""
    enlace = validar_enlace(novedad.enlace)
    if enlace is None:
        return None, "el enlace de la novedad no es válido"
    partes = urlsplit(enlace)
    if partes.scheme != "https":
        return None, "el enlace no es https"
    if partes.port not in (None, 443):
        return None, "el enlace usa un puerto que no es el de https"
    sitio = host_sin_www(urlsplit(fuente.url).hostname or "")
    if host_sin_www(partes.hostname or "") != sitio:
        return None, f"el artículo está en otro sitio ({partes.hostname}) que la fuente ({sitio})"
    return enlace, None


def leer_articulo(novedad: Novedad, fetcher: Callable[..., str] = descargar) -> Articulo:
    """Baja y extrae el artículo de `novedad`. Nunca lanza: devuelve un `Articulo` con su estado.

    `fetcher(url, host=<sitio de la fuente>)` devuelve el HTML como texto (por defecto `descargar`,
    que además rechaza una redirección fuera del sitio).
    """
    try:
        fuente = por_id(novedad.fuente)
    except KeyError:
        return _sin_texto(NO_APLICA, "la fuente de la novedad no está en la lista")
    if fuente.id in NO_SE_LEE:
        return _sin_texto(NO_APLICA, NO_SE_LEE[fuente.id])
    enlace, rechazo = _enlace_https_del_sitio(novedad, fuente)
    if enlace is None:
        estado = HOST_DISTINTO if rechazo and "otro sitio" in rechazo else ERROR
        return _sin_texto(estado, rechazo or "enlace rechazado")
    sitio = host_sin_www(urlsplit(fuente.url).hostname or "")
    try:
        html = fetcher(enlace, host=sitio)
        if not isinstance(html, str):
            return _sin_texto(ERROR, "el fetcher no devolvió texto", enlace)
        texto, recortado = texto_principal(html)
    except ErrorLectura as exc:
        return _sin_texto(ERROR, f"{type(exc).__name__}: {exc}", enlace)
    except Exception as exc:  # noqa: BLE001 - frontera: una página que falla no frena el análisis
        return _sin_texto(ERROR, f"error inesperado: {type(exc).__name__}: {exc}", enlace)
    if len(texto) < MIN_TEXTO:
        return _sin_texto(SIN_TEXTO, "la página no trae texto utilizable", enlace)
    return Articulo(estado=OK, texto=texto, recortado=recortado, enlace=enlace)


__all__ = ["Articulo", "leer_articulo", "recortar", "texto_plano", "texto_principal"]
