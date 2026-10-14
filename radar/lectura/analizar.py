"""Analizadores de feeds: Atom y RSS 2.0, con xml.etree.ElementTree.

Todo documento con DOCTYPE se rechaza: un DOCTYPE es lo que permite declarar entidades
(bombas de expansión, entidades externas). Sin DOCTYPE no hay entidades propias que
expandir, así que no hace falta ningún tope de expansión.

Dos barreras independientes:
1. `_revisar_prologo` recorre el prólogo (lo que está antes del primer elemento) y rechaza un
   DOCTYPE en cualquiera de sus formas o un prólogo ilegible. Un `<!DOCTYPE html>` dentro de un
   CDATA o un comentario de un ítem es contenido y no tumba la fuente.
2. El constructor del árbol rechaza el DOCTYPE si el analizador de XML llega a verlo.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from functools import partial

from .limpieza import (
    clave_de,
    fecha_rfc2822,
    fecha_rfc3339,
    frase_de,
    sin_futuro,
    titulo_de,
    validar_enlace,
)
from .modelos import Acumulador, DocumentoRechazado, Extraccion, Novedad

_ATOM = "{http://www.w3.org/2005/Atom}"
_MEDIA = "{http://search.yahoo.com/mrss/}"
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
_DC = "{http://purl.org/dc/elements/1.1/}"

_ESPACIO_XML = " \t\r\n"
_DOCTYPE = re.compile(r"<!\s*DOCTYPE", re.IGNORECASE)
# Enlaces que no son una página para leer: el propio documento Atom, su edición, sus comentarios.
_REL_NO_LEGIBLES = frozenset({"self", "edit", "replies"})


class _Constructor(ET.TreeBuilder):
    """Segunda barrera: si el analizador de XML llega a ver un DOCTYPE, lo rechaza."""

    def doctype(self, name, pubid, system):
        raise DocumentoRechazado("el documento trae DOCTYPE: se rechaza")


def _revisar_prologo(texto: str) -> None:
    """Recorre el prólogo y rechaza un DOCTYPE o un prólogo ilegible.

    El prólogo es lo que está antes del primer elemento: espacios, la declaración XML y las
    instrucciones y comentarios bien cerrados. `texto` ya viene sin BOM ni espacios al principio.
    """
    i, n = 0, len(texto)
    while True:
        while i < n and texto[i] in _ESPACIO_XML:
            i += 1
        if i >= n:
            raise DocumentoRechazado("el documento no tiene elemento raíz")
        if texto.startswith("<?", i):
            fin = texto.find("?>", i + 2)
            if fin < 0:
                raise DocumentoRechazado("prólogo ilegible: instrucción sin cerrar")
            i = fin + 2
        elif texto.startswith("<!--", i):
            fin = texto.find("-->", i + 4)
            if fin < 0:
                raise DocumentoRechazado("prólogo ilegible: comentario sin cerrar")
            i = fin + 3
        elif texto.startswith("<!", i):
            if _DOCTYPE.match(texto, i):
                raise DocumentoRechazado("el documento trae DOCTYPE: se rechaza")
            raise DocumentoRechazado("prólogo ilegible: declaración desconocida")
        elif texto[i] == "<":
            return  # el primer elemento: se acabó el prólogo
        else:
            raise DocumentoRechazado("prólogo ilegible: texto antes del elemento raíz")


def parsear_xml(texto: str) -> ET.Element:
    """Parsea un XML ya decodificado; rechaza DOCTYPE y todo lo mal formado con un error propio."""
    texto = texto.lstrip("﻿" + _ESPACIO_XML)
    _revisar_prologo(texto)
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


def _novedad(fuente, clave, ahora: datetime, titulo_crudo: str, enlace: str, fecha, resumen_crudo: str) -> Novedad | None:
    """Arma la novedad. Un título que existe pero queda vacío al limpiarlo (sólo etiquetas, o
    escapado sin fin) es hostil: no hay novedad. Un título que falta usa el enlace."""
    if titulo_crudo.strip():
        titulo = titulo_de(titulo_crudo)
        if not titulo:
            return None
    else:
        titulo = enlace
    return Novedad(
        fuente=fuente.id,
        clave=clave,
        titulo=titulo,
        enlace=enlace,
        fecha=sin_futuro(fecha, ahora),
        frase=frase_de(resumen_crudo),
    )


def _enlace_atom(entrada: ET.Element) -> str | None:
    """El enlace de la entrada: uno sin `rel` o `alternate` (la página para leer); si no hay,
    el primero de cualquier otro `rel` salvo `self`, `edit` y `replies`."""
    enlaces = [(e.get("rel"), e.get("href")) for e in entrada.findall(_ATOM + "link")]
    for rel, href in enlaces:
        if href and rel in (None, "alternate"):
            return href
    for rel, href in enlaces:
        if href and rel not in _REL_NO_LEGIBLES:
            return href
    return None


def analizar_atom(fuente, texto: str, ahora: datetime) -> Extraccion:
    raiz = parsear_xml(texto)
    if raiz.tag != _ATOM + "feed":
        raise DocumentoRechazado(f"no es un feed Atom (raíz: {raiz.tag[:80]!r})")
    acumulador = Acumulador()
    for entrada in raiz.findall(_ATOM + "entry"):
        enlace = validar_enlace(_enlace_atom(entrada))
        if enlace is None:
            acumulador.descartar()
            continue
        fecha = fecha_rfc3339(entrada.findtext(_ATOM + "published")) or fecha_rfc3339(
            entrada.findtext(_ATOM + "updated")
        )
        resumen = _texto(entrada.find(_ATOM + "summary"))
        if not resumen.strip():
            resumen = _texto(entrada.find(_ATOM + "content"))
        if not resumen.strip():
            resumen = _texto(entrada.find(".//" + _MEDIA + "description"))
        clave = clave_de(fuente.id, entrada.findtext(_ATOM + "id"), enlace)
        acumulador.registrar(
            clave, partial(_novedad, fuente, clave, ahora, _texto(entrada.find(_ATOM + "title")), enlace, fecha, resumen)
        )
    return acumulador.resultado()


def analizar_rss(fuente, texto: str, ahora: datetime) -> Extraccion:
    raiz = parsear_xml(texto)
    if raiz.tag != "rss":
        raise DocumentoRechazado(f"no es un feed RSS (raíz: {raiz.tag[:80]!r})")
    canal = raiz.find("channel")
    items = canal.findall("item") if canal is not None else []
    acumulador = Acumulador()
    for item in items:
        guid = (item.findtext("guid") or "").strip()
        enlace_crudo = (item.findtext("link") or "").strip()
        if not enlace_crudo and guid.lower().startswith(("http://", "https://")):
            enlace_crudo = guid
        enlace = validar_enlace(enlace_crudo)
        if enlace is None:
            acumulador.descartar()
            continue
        fecha = fecha_rfc2822(item.findtext("pubDate")) or fecha_rfc3339(item.findtext(_DC + "date"))
        resumen = _texto(item.find("description"))
        if not resumen.strip():
            resumen = _texto(item.find(_CONTENT + "encoded"))
        clave = clave_de(fuente.id, guid, enlace)
        acumulador.registrar(
            clave, partial(_novedad, fuente, clave, ahora, _texto(item.find("title")), enlace, fecha, resumen)
        )
    return acumulador.resultado()
