"""El único enlace de una novedad: el suyo, y sólo si pasa la validación de la lectura."""

from __future__ import annotations

import html
from urllib.parse import urlsplit

from radar.lectura.limpieza import validar_enlace

_ESQUEMAS = ("http", "https")


def enlace_seguro(enlace) -> str | None:
    """El enlace normalizado si es http(s) público y válido; si no, None (el título va sin enlace).

    Se vuelve a mirar el esquema del resultado: la página no confía en que quien armó la novedad
    ya haya validado, ni en que la validación de la lectura no cambie.
    """
    valido = validar_enlace(enlace)
    if valido is None or urlsplit(valido).scheme not in _ESQUEMAS:
        return None
    return valido


def ancla(enlace, texto_html: str) -> str:
    """`<a>` con el texto ya escapado, o el texto solo si el enlace no sirve."""
    destino = enlace_seguro(enlace)
    if destino is None:
        return texto_html
    return (
        f'<a href="{html.escape(destino, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer nofollow" referrerpolicy="no-referrer">{texto_html}</a>'
    )
