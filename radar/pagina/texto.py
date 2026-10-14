"""Todo texto que llega a la página pasa por acá: viene de internet o de un modelo, así que no es confiable.

Dos pasos, siempre juntos y en el momento de armar la página (no antes, no "ya vino limpio"):
1. `plano`: una sola línea, sin controles, sin caracteres de dirección ni de ancho cero, acotada;
2. `e`: `html.escape(..., quote=True)`. Nada se interpreta como HTML ni como Markdown, y acá no se
   arma ningún enlace automático: el único enlace de una novedad es el suyo (ver `enlaces.py`).
"""

from __future__ import annotations

import html
import unicodedata

MAX_CAMPO = 2000  # un campo del análisis llega a 1500; un título, a 300


def _invisible(ch: str) -> bool:
    """Control, formato (dirección, ancho cero), surrogate o selector de variación."""
    o = ord(ch)
    return unicodedata.category(ch) in ("Cc", "Cf", "Cs") or 0xFE00 <= o <= 0xFE0F or 0xE0100 <= o <= 0xE01EF


def plano(valor, limite: int = MAX_CAMPO) -> str:
    """El texto como una sola línea visible: sin invisibles, espacios colapsados, de a lo sumo `limite` caracteres."""
    if valor is None:
        return ""
    texto = valor if isinstance(valor, str) else str(valor)
    visibles = (" " if ch.isspace() else ch for ch in texto[: limite * 4] if ch.isspace() or not _invisible(ch))
    texto = " ".join("".join(visibles).split())
    if len(texto) > limite:
        texto = texto[: limite - 1].rstrip() + "…"
    return texto


def e(valor, limite: int = MAX_CAMPO) -> str:
    """Texto listo para ir dentro de una etiqueta HTML o de un atributo entre comillas."""
    return html.escape(plano(valor, limite), quote=True)
