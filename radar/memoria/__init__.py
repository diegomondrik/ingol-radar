"""Memoria de lo ya visto por el radar."""

from .memoria import (
    RUTA_POR_DEFECTO,
    Memoria,
    MemoriaCorrupta,
    MemoriaError,
    MemoriaOcupada,
)

__all__ = ["RUTA_POR_DEFECTO", "Memoria", "MemoriaCorrupta", "MemoriaError", "MemoriaOcupada"]
