"""Limpieza de lo que viene de internet: texto, enlaces y fechas.

Nada de lo que sale de acá confía en la fuente. Los filtros se aplican sobre el
contenido ya interpretado (entidades resueltas, etiquetas fuera), igual a como
lo va a leer quien lo abra después, nunca sobre los bytes.
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

LARGO_TITULO = 300
LARGO_FRASE = 400
MAX_ENLACE = 2048
# Antes de sacar etiquetas se corta el crudo: acota el trabajo del analizador de HTML.
TOPE_CRUDO = 20000
# Una fecha más de un día adelante de "ahora" no puede quedar fija en la ventana.
MARGEN_FUTURO = timedelta(days=1)

UTC = timezone.utc

_BLOQUES = frozenset(
    {"p", "div", "br", "li", "ul", "ol", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "section", "article"}
)


# --------------------------------------------------------------------------- texto


class _SacaEtiquetas(HTMLParser):
    """Se queda con el texto: sin etiquetas, sin scripts ni estilos, entidades resueltas."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self._ignorar = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._ignorar += 1
        elif tag in _BLOQUES:
            self.partes.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._ignorar = max(0, self._ignorar - 1)
        elif tag in _BLOQUES:
            self.partes.append(" ")

    def handle_data(self, data):
        if not self._ignorar:
            self.partes.append(data)


def alimentar(parser: HTMLParser, texto: str) -> None:
    """Le da el texto entero al analizador de HTML SIN llamar a `close()`.

    En Python 3.12.10, `HTMLParser.close()` es cuadrático con construcciones sin cerrar
    (`<!--<!--<!--...`, `<?<?<?...`: 200 KB tardan más de 20 segundos). Sin `close()`, lo
    incompleto del final queda en `rawdata`: si es texto (por ejemplo `Q&A`, que el analizador
    retiene esperando más) se entrega; si es el principio de una etiqueta o un comentario sin
    cerrar, se descarta, que es lo seguro.
    """
    parser.feed(texto)
    resto = parser.rawdata
    if resto and "<" not in resto:
        parser.handle_data(html.unescape(resto))


def _depurar(texto: str) -> str:
    """Espacios colapsados; sin controles, sin caracteres de formato (dirección, ancho cero)."""
    salida = []
    for ch in texto:
        if ch.isspace():
            salida.append(" ")
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs"):
            continue
        else:
            salida.append(ch)
    return " ".join("".join(salida).split())


def _recortar(texto: str, limite: int) -> str:
    if len(texto) <= limite:
        return texto
    return texto[: limite - 1].rstrip() + "…"


def limpiar_texto(valor, limite: int) -> str:
    """Texto plano, limpio y de a lo sumo `limite` caracteres. Nunca lanza."""
    if valor is None:
        return ""
    if not isinstance(valor, str):
        valor = str(valor)
    texto = valor[:TOPE_CRUDO]
    # Hasta tres pasadas: un título escapado dos veces (`&amp;lt;b&amp;gt;`) vuelve a ser una
    # etiqueta tras la primera y tiene que salir en la segunda. Con texto sin etiquetas la
    # segunda pasada no cambia nada y se corta ahí.
    for _ in range(3):
        extractor = _SacaEtiquetas()
        try:
            alimentar(extractor, texto)
        except Exception:  # noqa: BLE001 - el analizador de HTML no debe tumbar la lectura
            return ""
        siguiente = "".join(extractor.partes)
        if siguiente == texto:
            break
        texto = siguiente
    return _recortar(_depurar(texto), limite)


def primera_oracion(texto: str, limite: int = LARGO_FRASE) -> str:
    """La primera oración de un texto ya limpio, de a lo sumo `limite` caracteres."""
    m = re.search(r"[.!?](?=\s|$)", texto)
    oracion = texto[: m.end()] if m else texto
    return _recortar(oracion, limite)


def frase_de(valor) -> str:
    return primera_oracion(limpiar_texto(valor, 5000))


def titulo_de(valor) -> str:
    return limpiar_texto(valor, LARGO_TITULO)


# ------------------------------------------------------------------------- enlaces

_RECORTE = "".join(chr(c) for c in range(0x21)) + "                 　"
_PROHIBIDOS = set('\\<>"')
_ESQUEMAS_OK = ("http", "https")
_PUERTO = {"http": 80, "https": 443}
_ESQUEMA = re.compile(r"([a-z][a-z0-9+.\-]*):", re.ASCII)


def _tiene_invisibles(texto: str) -> bool:
    """Controles, formato, espacios o caracteres que un enlace no debe traer sin codificar."""
    return any(
        ch.isspace() or ch in _PROHIBIDOS or unicodedata.category(ch) in ("Cc", "Cf", "Cs")
        for ch in texto
    )


def _esquema_ilegal(texto: str) -> bool:
    """True si el texto, interpretado como lo haría un navegador, abre algo que no es http(s).

    Un navegador ignora controles y espacios al principio y en medio del esquema, y
    no distingue mayúsculas: `JaVa\\tScRiPt:` es `javascript:`.
    """
    limpio = "".join(
        ch
        for ch in texto
        if not ch.isspace() and ch != "\x00" and unicodedata.category(ch) not in ("Cc", "Cf")
    ).lower()
    m = _ESQUEMA.match(limpio)
    return m is not None and m.group(1) not in _ESQUEMAS_OK


def validar_enlace(valor, base: str | None = None) -> str | None:
    """Devuelve el enlace absoluto http(s) normalizado, o None si no es válido.

    Rechaza otros esquemas escritos de cualquier forma equivalente (mayúsculas,
    controles o espacios al principio o en medio, entidades numéricas o con nombre,
    `data:`, `vbscript:`), enlaces con usuario o clave, y todo lo que traiga
    controles, espacios o caracteres de formato adentro.
    """
    if not isinstance(valor, str):
        return None
    texto = valor.strip(_RECORTE)
    if not texto or len(texto) > MAX_ENLACE or _tiene_invisibles(texto):
        return None
    # Cada etapa de des-escapado de entidades se mira como esquema: `&#106;avascript:`,
    # `javascript&colon;` y las entidades dobles caen acá.
    etapa = texto
    for _ in range(4):
        if _esquema_ilegal(etapa):
            return None
        siguiente = html.unescape(etapa)
        if siguiente == etapa:
            break
        etapa = siguiente
    else:
        return None
    try:
        absoluto = urljoin(base, texto) if base else texto
        partes = urlsplit(absoluto)
        host = partes.hostname
        puerto = partes.port
        usuario, clave = partes.username, partes.password
    except ValueError:
        return None
    esquema = partes.scheme.lower()
    if esquema not in _ESQUEMAS_OK or not host:
        return None
    if usuario is not None or clave is not None or "@" in partes.netloc:
        return None
    host = host.lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if puerto in (None, _PUERTO[esquema]) else f"{host}:{puerto}"
    return urlunsplit((esquema, netloc, partes.path or "/", partes.query, ""))


# -------------------------------------------------------------------------- fechas

_RFC3339 = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?([Zz]|[+-]\d{2}:\d{2})",
    re.ASCII,
)

_MESES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip


def fecha_rfc3339(valor) -> datetime | None:
    """Fecha de Atom (`published`, `updated`) en UTC; None si no se entiende entera."""
    if not isinstance(valor, str) or len(valor) > 64:
        return None
    m = _RFC3339.fullmatch(valor.strip())
    if m is None:
        return None
    try:
        anio, mes, dia, hora, minuto, segundo = (int(g) for g in m.groups()[:6])
        micro = int((m.group(7) or "")[:6].ljust(6, "0"))
        zona = m.group(8)
        if zona in ("Z", "z"):
            tz = UTC
        else:
            hh, mm = int(zona[1:3]), int(zona[4:6])
            if hh > 23 or mm > 59:
                return None
            delta = timedelta(hours=hh, minutes=mm)
            tz = timezone(-delta if zona[0] == "-" else delta)
        return datetime(anio, mes, dia, hora, minuto, segundo, micro, tzinfo=tz).astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def fecha_rfc2822(valor) -> datetime | None:
    """Fecha de RSS (`pubDate`) en UTC; None si no se entiende."""
    if not isinstance(valor, str) or len(valor) > 100:
        return None
    try:
        dt = parsedate_to_datetime(valor.strip())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:  # "-0000": sin zona; se toma como UTC
        dt = dt.replace(tzinfo=UTC)
    try:
        return dt.astimezone(UTC)
    except OverflowError:
        return None


def fecha_de_nombre(mes, dia, anio) -> datetime | None:
    """`Oct`, `8`, `2026` o `October`, `9`, `2026` a un datetime UTC a medianoche."""
    numero = _MESES.get(str(mes).lower())
    if numero is None:
        return None
    try:
        return datetime(int(anio), numero, int(dia), tzinfo=UTC)
    except ValueError:
        return None


def fecha_de_numeros(anio, mes, dia) -> datetime | None:
    try:
        return datetime(int(anio), int(mes), int(dia), tzinfo=UTC)
    except ValueError:
        return None


def sin_futuro(fecha: datetime | None, ahora: datetime) -> datetime | None:
    """Una fecha más de un día adelante de `ahora` no es creíble: se trata como sin fecha."""
    if fecha is not None and fecha > ahora + MARGEN_FUTURO:
        return None
    return fecha


# --------------------------------------------------------------------------- claves


def hash_corto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8", "replace")).hexdigest()[:10]


def clave_de(fuente_id: str, identificador, enlace: str) -> str:
    """fuente + ":" + id/guid si viene y es razonable; si no, el enlace normalizado."""
    ident = identificador.strip() if isinstance(identificador, str) else ""
    if ident:
        if len(ident) > 300 or _tiene_invisibles(ident):
            ident = "sha256:" + hashlib.sha256(ident.encode("utf-8", "replace")).hexdigest()
        return f"{fuente_id}:{ident}"
    return f"{fuente_id}:{enlace}"
