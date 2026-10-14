"""Analizadores de feeds: Atom y RSS 2.0, con xml.etree.ElementTree.

Todo documento con DOCTYPE se rechaza: un DOCTYPE es lo que permite declarar entidades
(bombas de expansión, entidades externas). Sin DOCTYPE no hay entidades propias que
expandir, así que no hace falta ningún tope de expansión.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime

from .limpieza import (
    clave_de,
    fecha_rfc2822,
    fecha_rfc3339,
    frase_de,
    sin_futuro,
    titulo_de,
    validar_enlace,
)
from .modelos import MAX_ITEMS, DocumentoRechazado, Extraccion, Novedad

_ATOM = "{http://www.w3.org/2005/Atom}"
_MEDIA = "{http://search.yahoo.com/mrss/}"
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
_DC = "{http://purl.org/dc/elements/1.1/}"

# `<!DOCTYPE` sin importar mayúsculas ni espacios, en cualquier parte del documento ya
# decodificado. Es conservador: también frena un `<!DOCTYPE` dentro de un comentario o un
# CDATA, y eso queda como fuente en error (visible en la cobertura), no como entrada aceptada.
_DOCTYPE = re.compile(r"<!\s*DOCTYPE", re.IGNORECASE)


class _Constructor(ET.TreeBuilder):
    """Segunda barrera: si el analizador de XML llega a ver un DOCTYPE, lo rechaza."""

    def doctype(self, name, pubid, system):
        raise DocumentoRechazado("el documento trae DOCTYPE: se rechaza")


def parsear_xml(texto) -> ET.Element:
    """Parsea un XML ya decodificado; rechaza DOCTYPE y todo lo mal formado con un error propio."""
    if not isinstance(texto, str):
        raise DocumentoRechazado("el documento no es texto")
    texto = texto.lstrip("﻿ \t\r\n")
    if _DOCTYPE.search(texto):
        raise DocumentoRechazado("el documento trae DOCTYPE: se rechaza")
    parser = ET.XMLParser(target=_Constructor())
    try:
        parser.feed(texto)
        return parser.close()
    except DocumentoRechazado:
        raise
    except ET.ParseError as exc:
        raise DocumentoRechazado(f"XML mal formado: {exc}") from exc
    except (ValueError, RecursionError) as exc:
        raise DocumentoRechazado(f"XML no procesable ({type(exc).__name__})") from exc


def _texto(elemento: ET.Element | None) -> str:
    return "".join(elemento.itertext()) if elemento is not None else ""


class _Acumulador:
    """Arma la lista de novedades de una fuente con tope, descartes y sin repetidas."""

    def __init__(self, fuente, ahora: datetime) -> None:
        self.fuente = fuente
        self.ahora = ahora
        self.salida = Extraccion()
        self._claves: set[str] = set()
        self._leidos = 0

    def hay_lugar(self) -> bool:
        if self._leidos >= MAX_ITEMS:
            self.salida.recortadas += 1
            return False
        self._leidos += 1
        return True

    def agregar(self, ident, titulo_crudo, enlace_crudo, fecha, resumen_crudo, base=None) -> None:
        enlace = validar_enlace(enlace_crudo, base)
        if enlace is None:
            self.salida.descartadas += 1
            return
        clave = clave_de(self.fuente.id, ident, enlace)
        if clave in self._claves:
            self.salida.descartadas += 1
            return
        self._claves.add(clave)
        self.salida.novedades.append(
            Novedad(
                fuente=self.fuente.id,
                clave=clave,
                titulo=titulo_de(titulo_crudo) or enlace,
                enlace=enlace,
                fecha=sin_futuro(fecha, self.ahora),
                frase=frase_de(resumen_crudo),
            )
        )


def _enlace_atom(entrada: ET.Element) -> str | None:
    enlaces = [(e.get("rel"), e.get("href")) for e in entrada.findall(_ATOM + "link")]
    for rel, href in enlaces:
        if href and rel in (None, "alternate"):
            return href
    for _, href in enlaces:
        if href:
            return href
    return None


def analizar_atom(fuente, texto, ahora: datetime) -> Extraccion:
    raiz = parsear_xml(texto)
    if raiz.tag != _ATOM + "feed":
        raise DocumentoRechazado(f"no es un feed Atom (raíz: {raiz.tag[:80]!r})")
    acumulador = _Acumulador(fuente, ahora)
    for entrada in raiz.findall(_ATOM + "entry"):
        if not acumulador.hay_lugar():
            continue
        fecha = fecha_rfc3339(entrada.findtext(_ATOM + "published")) or fecha_rfc3339(
            entrada.findtext(_ATOM + "updated")
        )
        resumen = _texto(entrada.find(_ATOM + "summary"))
        if not resumen.strip():
            resumen = _texto(entrada.find(_ATOM + "content"))
        if not resumen.strip():
            resumen = _texto(entrada.find(".//" + _MEDIA + "description"))
        acumulador.agregar(
            entrada.findtext(_ATOM + "id"),
            _texto(entrada.find(_ATOM + "title")),
            _enlace_atom(entrada),
            fecha,
            resumen,
        )
    return acumulador.salida


def analizar_rss(fuente, texto, ahora: datetime) -> Extraccion:
    raiz = parsear_xml(texto)
    if raiz.tag != "rss":
        raise DocumentoRechazado(f"no es un feed RSS (raíz: {raiz.tag[:80]!r})")
    canal = raiz.find("channel")
    items = canal.findall("item") if canal is not None else []
    acumulador = _Acumulador(fuente, ahora)
    for item in items:
        if not acumulador.hay_lugar():
            continue
        guid = (item.findtext("guid") or "").strip()
        enlace = (item.findtext("link") or "").strip()
        if not enlace and guid.lower().startswith(("http://", "https://")):
            enlace = guid
        fecha = fecha_rfc2822(item.findtext("pubDate")) or fecha_rfc3339(item.findtext(_DC + "date"))
        resumen = _texto(item.find("description"))
        if not resumen.strip():
            resumen = _texto(item.find(_CONTENT + "encoded"))
        acumulador.agregar(guid, _texto(item.find("title")), enlace, fecha, resumen)
    return acumulador.salida
