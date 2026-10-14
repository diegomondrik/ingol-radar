"""Errores propios del análisis: todo lo que sale mal con claude, su respuesta o el contexto."""

from __future__ import annotations


class ErrorAnalisis(Exception):
    """Base de los errores propios del análisis."""


class ErrorContexto(ErrorAnalisis):
    """El contexto de INGOL no está, no se puede leer o no sirve."""


class ErrorClaude(ErrorAnalisis):
    """Una llamada a `claude -p` falló (no salió, tardó de más, o su salida no pasó la verificación)."""


class ClaudeNoDisponible(ErrorClaude):
    """No hay un `claude` usable en esta máquina. Reintentar no sirve: se corta la corrida."""


class ErrorRespuesta(ErrorAnalisis):
    """La respuesta del modelo no tiene la forma fija: no se usa nada de ella."""
