"""Analizadores de páginas sin feed, con html.parser (nunca expresiones regulares sobre el HTML).

Las formas que buscan (enlaces `/news/<slug>`, `/blog/AAAA-MM-DD-slug/`, títulos h3 con fecha
y una lista debajo) salen de la descripción del diseño y NO se contrastaron con las páginas
reales: si un cambio de diseño las deja mudas, la fuente queda `vacia` en la cobertura.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .limpieza import (
    alimentar,
    clave_de,
    fecha_de_nombre,
    fecha_de_numeros,
    hash_corto,
    limpiar_texto,
    primera_oracion,
    sin_futuro,
    validar_enlace,
)
from .modelos import MAX_ITEMS, DocumentoRechazado, Extraccion, Novedad

_PIEZAS_POR_ENLACE = 20
_FECHA_EN_TEXTO = re.compile(r"\b([A-Za-z]{3,9})\.? (\d{1,2}), (\d{4})\b", re.ASCII)
_FECHA_TITULO = re.compile(r"([A-Za-z]{3,9})\.? (\d{1,2}), (\d{4})", re.ASCII)
_SEPARADORES = " ·|—–-•,"


def _exigir_texto(texto) -> str:
    if not isinstance(texto, str):
        raise DocumentoRechazado("el documento no es texto")
    return texto


# ----------------------------------------------------------------- páginas con enlaces


class _Anclas(HTMLParser):
    """Junta, por cada `<a href>`, los trozos de texto que trae adentro."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anclas: list[tuple[str, list[str]]] = []
        self._actual: tuple[str, list[str]] | None = None
        self._ignorar = 0

    def _cerrar(self) -> None:
        if self._actual is not None:
            self.anclas.append(self._actual)
            self._actual = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._ignorar += 1
        elif tag == "a":
            self._cerrar()
            href = next((v for k, v in attrs if k == "href" and v), None)
            if href:
                self._actual = (href, [])

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._ignorar = max(0, self._ignorar - 1)
        elif tag == "a":
            self._cerrar()

    def handle_data(self, data):
        if self._ignorar or self._actual is None or len(self._actual[1]) >= _PIEZAS_POR_ENLACE:
            return
        pieza = limpiar_texto(data, 300)
        if pieza:
            self._actual[1].append(pieza)

    def terminar(self) -> None:
        self._cerrar()


def _tiene_letras(texto: str) -> bool:
    return any(ch.isalnum() for ch in texto)


def _humanizar(slug: str) -> str:
    texto = re.sub(r"[-_]+", " ", slug).strip()
    return texto[:1].upper() + texto[1:]


def _analizar_enlaces(fuente, texto, ahora: datetime, patron: re.Pattern, fecha_del_camino: bool) -> Extraccion:
    """Las páginas anthropic.com/news, /engineering y metr.org/blog: enlaces con camino reconocible."""
    parser = _Anclas()
    try:
        alimentar(parser, _exigir_texto(texto))
        parser.terminar()
    except Exception as exc:  # noqa: BLE001 - el HTML hostil se rechaza con error propio
        raise DocumentoRechazado(f"HTML no procesable ({type(exc).__name__})") from exc
    host = (urlsplit(fuente.url).hostname or "").lower()
    salida = Extraccion()
    por_enlace: dict[str, tuple[re.Match, list[str]]] = {}
    for href, piezas in parser.anclas:
        try:
            partes = urlsplit(urljoin(fuente.url, html.unescape(href).strip()))
            hostname = partes.hostname
        except ValueError:
            continue
        if (hostname or "").lower() != host:
            continue
        coincide = patron.fullmatch(partes.path)
        if coincide is None:
            continue
        enlace = validar_enlace(href, fuente.url)
        if enlace is None:
            salida.descartadas += 1
            continue
        if enlace in por_enlace:
            por_enlace[enlace][1].extend(piezas)
        else:
            por_enlace[enlace] = (coincide, list(piezas))
    for enlace, (coincide, piezas) in por_enlace.items():
        if len(salida.novedades) >= MAX_ITEMS:
            salida.recortadas += 1
            continue
        fecha = fecha_de_numeros(*coincide.groups()[:3]) if fecha_del_camino else None
        resto = []
        for pieza in piezas:
            m = _FECHA_EN_TEXTO.search(pieza)
            if m:
                if not fecha_del_camino and fecha is None:
                    fecha = fecha_de_nombre(*m.groups())
                pieza = f"{pieza[: m.start()]} {pieza[m.end():]}".strip(_SEPARADORES)
            if _tiene_letras(pieza):
                resto.append(pieza)
        titulo = max(resto, key=len) if resto else _humanizar(coincide.group("slug"))
        salida.novedades.append(
            Novedad(
                fuente=fuente.id,
                clave=clave_de(fuente.id, None, enlace),
                titulo=limpiar_texto(titulo, 300),
                enlace=enlace,
                fecha=sin_futuro(fecha, ahora),
                frase="",  # estas páginas no traen un resumen que se pueda tomar sin adivinar
            )
        )
    return salida


_PATRON_ANTHROPIC_NOTICIAS = re.compile(r"/news/(?P<slug>[A-Za-z0-9][A-Za-z0-9_.\-]*)/?")
_PATRON_ANTHROPIC_INGENIERIA = re.compile(r"/engineering/(?P<slug>[A-Za-z0-9][A-Za-z0-9_.\-]*)/?")
_PATRON_METR = re.compile(r"/blog/(\d{4})-(\d{2})-(\d{2})-(?P<slug>[^/]+)/?", re.ASCII)


def analizar_anthropic_noticias(fuente, texto, ahora: datetime) -> Extraccion:
    return _analizar_enlaces(fuente, texto, ahora, _PATRON_ANTHROPIC_NOTICIAS, fecha_del_camino=False)


def analizar_anthropic_ingenieria(fuente, texto, ahora: datetime) -> Extraccion:
    return _analizar_enlaces(fuente, texto, ahora, _PATRON_ANTHROPIC_INGENIERIA, fecha_del_camino=False)


def analizar_metr(fuente, texto, ahora: datetime) -> Extraccion:
    return _analizar_enlaces(fuente, texto, ahora, _PATRON_METR, fecha_del_camino=True)


# ------------------------------------------------------------ notas de la plataforma


class _Notas(HTMLParser):
    """Títulos h3 con fecha y, debajo, una lista: cada `li` es una novedad con esa fecha."""

    _FUERA = frozenset({"nav", "footer", "aside", "header"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[datetime, str]] = []
        self._fecha: datetime | None = None
        self._h3: list[str] | None = None
        self._pila: list[tuple[int, list[str] | None, datetime | None]] = []
        self._listas = 0
        self._fuera = 0
        self._ignorar = 0

    def _emitir(self, entrada) -> None:
        _, buffer, fecha = entrada
        if buffer is None or fecha is None:
            return
        texto = limpiar_texto(" ".join(buffer), 5000)
        if texto and any(ch.isalnum() for ch in texto):
            self.items.append((fecha, texto))

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._ignorar += 1
        elif tag in self._FUERA:
            self._fuera += 1
        elif tag == "h2":
            self._fecha = None
        elif tag == "h3":
            self._h3 = []
        elif tag in ("ul", "ol"):
            self._listas += 1
        elif tag == "li":
            while self._pila and self._pila[-1][0] >= self._listas:  # `li` hermano sin cerrar
                self._emitir(self._pila.pop())
            activo = self._fecha is not None and self._fuera == 0
            self._pila.append((self._listas, [] if activo else None, self._fecha))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._ignorar = max(0, self._ignorar - 1)
        elif tag in self._FUERA:
            self._fuera = max(0, self._fuera - 1)
        elif tag == "h3" and self._h3 is not None:
            texto = limpiar_texto(" ".join(self._h3), 100)
            self._h3 = None
            m = _FECHA_TITULO.fullmatch(texto)
            fecha = fecha_de_nombre(*m.groups()) if m else None
            if fecha is not None:  # un h3 que no es fecha no cambia la sección
                self._fecha = fecha
        elif tag == "li":
            if self._pila and self._pila[-1][0] == self._listas:
                self._emitir(self._pila.pop())
        elif tag in ("ul", "ol"):
            while self._pila and self._pila[-1][0] >= self._listas:
                self._emitir(self._pila.pop())
            self._listas = max(0, self._listas - 1)

    def handle_data(self, data):
        if self._ignorar:
            return
        if self._h3 is not None:
            self._h3.append(data)
        elif self._pila and self._pila[-1][1] is not None:
            self._pila[-1][1].append(data)

    def terminar(self) -> None:
        while self._pila:
            self._emitir(self._pila.pop())


def analizar_claude_plataforma(fuente, texto, ahora: datetime) -> Extraccion:
    parser = _Notas()
    try:
        alimentar(parser, _exigir_texto(texto))
        parser.terminar()
    except Exception as exc:  # noqa: BLE001 - el HTML hostil se rechaza con error propio
        raise DocumentoRechazado(f"HTML no procesable ({type(exc).__name__})") from exc
    enlace = validar_enlace(fuente.url)
    salida = Extraccion()
    claves: set[str] = set()
    for fecha, contenido in parser.items:
        if len(salida.novedades) >= MAX_ITEMS:
            salida.recortadas += 1
            continue
        if enlace is None:
            salida.descartadas += 1
            continue
        clave = f"{fuente.id}:{enlace}#{fecha.date().isoformat()}#{hash_corto(contenido)}"
        if clave in claves:
            salida.descartadas += 1
            continue
        claves.add(clave)
        salida.novedades.append(
            Novedad(
                fuente=fuente.id,
                clave=clave,
                titulo=limpiar_texto(contenido, 160),
                enlace=enlace,
                fecha=sin_futuro(fecha, ahora),
                frase=primera_oracion(contenido),
            )
        )
    return salida
