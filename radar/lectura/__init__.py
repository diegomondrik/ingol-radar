"""Lectura de las fuentes públicas del radar: baja, analiza y entrega novedades limpias."""

from .fuentes import FUENTES, Fuente, por_id
from .lector import leer, ventana
from .modelos import (
    MAX_ITEMS,
    Cobertura,
    DocumentoRechazado,
    ErrorDescarga,
    ErrorLectura,
    Novedad,
)

__all__ = [
    "FUENTES",
    "MAX_ITEMS",
    "Cobertura",
    "DocumentoRechazado",
    "ErrorDescarga",
    "ErrorLectura",
    "Fuente",
    "Novedad",
    "leer",
    "por_id",
    "ventana",
]
