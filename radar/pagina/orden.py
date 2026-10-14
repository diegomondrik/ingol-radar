"""Qué novedades van en la página y con qué número: la misma numeración para el HTML y para el JSON."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from radar.analisis.modelos import Analisis
from radar.lectura.modelos import Novedad

# Los grupos, en el orden en que se ven en pantalla y en que se numeran: sirven, tal vez, otras (un veredicto
# fuera de lo esperado no se esconde, pero tampoco va entre lo que sirve) y, al final, el ruido.
GRUPOS = ("sirven", "tal_vez", "otras", "ruido")
_GRUPO_DE_VEREDICTO = {"si": "sirven", "tal_vez": "tal_vez", "no": "ruido"}
_ORIGEN = datetime(1970, 1, 1, tzinfo=timezone.utc)
_RANGO_CONFIANZA = {"alta": 0, "media": 1, "baja": 2}
VEREDICTO_JSON = {"si": "sirve", "tal_vez": "tal_vez", "no": "no"}

SOSPECHA_FUERTE = "fuerte"  # título o frase: se trató como dato, puede ser una manipulación
SOSPECHA_SUAVE = "suave"  # el artículo cita instrucciones: puede ser un ejemplo del propio artículo


@dataclass(frozen=True)
class Entrada:
    numero: int
    novedad: Novedad
    analisis: Analisis


def grupo_de(analisis: Analisis) -> str:
    """En qué grupo de la página va un análisis (uno de GRUPOS)."""
    return _GRUPO_DE_VEREDICTO.get(analisis.sirve, "otras")


def _segundos(fecha: datetime | None) -> float:
    """Segundos desde 1970 (sin `timestamp()`, que falla con fechas extremas); una fecha sin zona se toma como UTC."""
    if fecha is None:
        return 0.0
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=timezone.utc)
    return (fecha - _ORIGEN).total_seconds()


def _clave_de_orden(par: tuple[Novedad, Analisis]):
    novedad, analisis = par
    return (
        GRUPOS.index(grupo_de(analisis)),
        _RANGO_CONFIANZA.get(analisis.confianza, 3),
        novedad.fecha is None,  # sin fecha, al final de su grupo
        -_segundos(novedad.fecha),  # la más nueva primero
        novedad.clave,
    )


def ordenar(novedades: Mapping[str, Novedad], analizadas: Mapping[str, Analisis]) -> list[Entrada]:
    """Las novedades analizadas, numeradas desde 1 en el orden de la pantalla: por grupo (sirven, tal vez, otras, ruido),
    y dentro del grupo, confianza de alta a baja y fecha más nueva primero."""
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

    Una novedad sospechosa en el título o la frase no pasa a la segunda pasada. Entonces, si la marca viene de una
    novedad analizada con el artículo (`pasada` 2) o de la señal fija sobre el artículo
    (`instrucciones_detectadas`), nació del artículo: aviso suave, puede ser un ejemplo del propio artículo. Cualquier
    otra marca (del modelo o de la señal fija) es del título o la frase: aviso fuerte.
    """
    en_articulo = analisis.articulo is not None and analisis.articulo.instrucciones_detectadas
    if not (analisis.sospecha_de_instrucciones or analisis.sospecha_mecanica or en_articulo):
        return None
    return SOSPECHA_SUAVE if en_articulo or analisis.pasada == 2 else SOSPECHA_FUERTE
