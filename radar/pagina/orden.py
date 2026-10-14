"""Qué novedades van en la página y con qué número: la misma numeración para el HTML y para el JSON."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from radar.analisis.modelos import Analisis
from radar.lectura.modelos import Novedad

_RANGO_SIRVE = {"si": 0, "tal_vez": 1, "no": 2}
_RANGO_CONFIANZA = {"alta": 0, "media": 1, "baja": 2}
VEREDICTO_JSON = {"si": "sirve", "tal_vez": "tal_vez", "no": "no"}

SOSPECHA_FUERTE = "fuerte"  # título o frase: se trató como dato, puede ser una manipulación
SOSPECHA_SUAVE = "suave"  # el artículo cita instrucciones: puede ser un ejemplo del propio artículo


@dataclass(frozen=True)
class Entrada:
    numero: int
    novedad: Novedad
    analisis: Analisis


def _clave_de_orden(par: tuple[Novedad, Analisis]):
    novedad, analisis = par
    fecha = novedad.fecha.timestamp() if novedad.fecha is not None else float("-inf")
    return (
        _RANGO_SIRVE.get(analisis.sirve, 3),
        _RANGO_CONFIANZA.get(analisis.confianza, 3),
        -fecha,  # la más nueva primero; sin fecha, al final de su grupo
        novedad.clave,
    )


def ordenar(novedades: Mapping[str, Novedad], analizadas: Mapping[str, Analisis]) -> list[Entrada]:
    """Las novedades analizadas, numeradas desde 1: sirve, tal vez, no; confianza alta a baja; fecha más nueva primero."""
    pares = []
    for clave, analisis in analizadas.items():
        novedad = novedades.get(clave)
        if novedad is None:  # no debería pasar; no se pierde: va con la clave como título
            novedad = Novedad(fuente="", clave=clave, titulo=clave[:80], enlace="", fecha=None, frase="")
        pares.append((novedad, analisis))
    pares.sort(key=_clave_de_orden)
    return [Entrada(i, nov, an) for i, (nov, an) in enumerate(pares, 1)]


def sospecha_de(analisis: Analisis) -> str | None:
    """Qué aviso lleva una novedad: fuerte (título o frase), suave (sólo el artículo) o ninguno.

    Si el artículo trajo la señal fija (`instrucciones_detectadas`), la novedad ya había pasado limpia por
    el título y la frase (una sospechosa no llega a la segunda pasada), así que la marca del modelo en esa
    pasada viene del artículo: aviso suave. Sin eso, cualquier marca (el modelo o la señal fija) es fuerte.
    """
    en_articulo = analisis.articulo is not None and analisis.articulo.instrucciones_detectadas
    if en_articulo:
        return SOSPECHA_SUAVE
    if analisis.sospecha_de_instrucciones or analisis.sospecha_mecanica:
        return SOSPECHA_FUERTE
    return None
