"""Tipos de la lectura: la novedad, la cobertura por fuente y los errores propios."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

# Como mucho esta cantidad de novedades por fuente; el resto se cuenta, no se lee.
MAX_ITEMS = 500


class ErrorLectura(Exception):
    """Base de los errores propios de la lectura (entrada inválida o fuente inalcanzable)."""


class DocumentoRechazado(ErrorLectura):
    """El documento no se acepta: DOCTYPE, XML roto, forma que no es la esperada."""


class ErrorDescarga(ErrorLectura):
    """No se pudo bajar la fuente: red, esquema no permitido, tope de tamaño, tiempo."""


@dataclass(frozen=True, slots=True)
class Novedad:
    """Una novedad leída de una fuente. Todo el texto ya está limpio (texto plano).

    La identidad es `clave`; `enlace` puede repetirse (en claude-plataforma todas las novedades
    de una página comparten el enlace de la página).
    """

    fuente: str
    clave: str
    titulo: str
    enlace: str
    fecha: datetime | None
    frase: str

    def a_dict(self) -> dict:
        return {
            "fuente": self.fuente,
            "clave": self.clave,
            "titulo": self.titulo,
            "enlace": self.enlace,
            "fecha": self.fecha.isoformat() if self.fecha else None,
            "frase": self.frase,
        }


@dataclass
class Extraccion:
    """Lo que saca un analizador de un documento."""

    novedades: list[Novedad] = field(default_factory=list)
    descartadas: int = 0  # enlace inválido, repetidas dentro de la fuente
    recortadas: int = 0  # válidas y únicas que no entraron por el tope de MAX_ITEMS


@dataclass
class Cobertura:
    """Qué pasó con una fuente en una lectura."""

    estado: str  # "ok" | "vacia" | "error"
    motivo: str | None = None
    en_fuente: int = 0  # novedades válidas que trajo la fuente
    en_ventana: int = 0  # las que entran en la ventana (incluye las sin fecha)
    sin_fecha: int = 0  # las que no traen fecha y entran igual
    descartadas: int = 0
    recortadas: int = 0
    cubre_desde: datetime | None = None  # la fecha más vieja que trajo la fuente

    def a_dict(self) -> dict:
        return {
            "estado": self.estado,
            "motivo": self.motivo,
            "en_fuente": self.en_fuente,
            "en_ventana": self.en_ventana,
            "sin_fecha": self.sin_fecha,
            "descartadas": self.descartadas,
            "recortadas": self.recortadas,
            "cubre_desde": self.cubre_desde.isoformat() if self.cubre_desde else None,
        }


class Acumulador:
    """El único lugar donde se cuenta una fuente: tope, repetidas y descartes, igual para feeds y páginas.

    - `descartar()`: la novedad no vale (enlace inválido, sin título): suma a `descartadas`.
    - `registrar(clave, fabrica)`: una novedad con enlace válido. Si su clave ya entró, es una
      repetida (`descartadas`). Si ya hay MAX_ITEMS, no entra y suma a `recortadas`: así
      `recortadas` son las novedades válidas y únicas que quedaron fuera por el tope. La novedad
      se arma con `fabrica()` sólo si entra, para no limpiar texto de lo que se va a recortar;
      si `fabrica()` devuelve None, se descarta.
    """

    def __init__(self) -> None:
        self.novedades: list[Novedad] = []
        self.descartadas = 0
        self.recortadas = 0
        self._claves: set[str] = set()

    def descartar(self) -> None:
        self.descartadas += 1

    def registrar(self, clave: str, fabrica: Callable[[], Novedad | None]) -> None:
        if clave in self._claves:
            self.descartadas += 1
            return
        self._claves.add(clave)
        if len(self.novedades) >= MAX_ITEMS:
            self.recortadas += 1
            return
        novedad = fabrica()
        if novedad is None:
            self.descartadas += 1
            return
        self.novedades.append(novedad)

    def resultado(self) -> Extraccion:
        return Extraccion(self.novedades, self.descartadas, self.recortadas)
