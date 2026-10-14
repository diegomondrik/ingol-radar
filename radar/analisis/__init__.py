"""Análisis de las novedades con Claude: sin herramientas, respuesta de forma fija, validada en Python."""

from .analizador import analizar, analizar_idea
from .errores import (
    ClaudeNoDisponible,
    ErrorAnalisis,
    ErrorClaude,
    ErrorContexto,
    ErrorRespuesta,
)
from .modelos import Analisis, Opinion, Resultado

__all__ = [
    "Analisis",
    "ClaudeNoDisponible",
    "ErrorAnalisis",
    "ErrorClaude",
    "ErrorContexto",
    "ErrorRespuesta",
    "Opinion",
    "Resultado",
    "analizar",
    "analizar_idea",
]
