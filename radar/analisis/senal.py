"""Señal fija de instrucciones para un modelo: una detección mecánica, en Python, sin IA.

Además de la marca `sospecha_de_instrucciones` que pone el modelo, el texto que viene de internet
(título, frase, texto del artículo) se mira acá con frases fijas. El modelo puede dejarse convencer; esta
comprobación no. Se aplica sobre el texto ya interpretado: sin invisibles (ancho cero, dirección), con
formas compatibles unificadas (NFKC: letras de ancho completo), sin distinguir mayúsculas ni cantidad de
espacios. No detecta todo (un texto en otro idioma o con sinónimos pasa): es un cinturón, no la defensa.

Lo que dispara una señal es sólo una frase de orden dirigida a un modelo; una frase suelta como
"system:" en medio de una oración ("operating system: Linux") no, y "you are now able to" tampoco.
"""

from __future__ import annotations

import re
import unicodedata

from radar.lectura.limpieza import _invisible

# Cada patrón se aplica al texto normalizado (minúsculas, espacios simples, sin invisibles).
# Sin límite de palabra al principio: en el pipeline real `limpiar_texto` ya sacó el ESC de una secuencia de
# terminal y queda su resto (`[0mIgnore...`), y `xignore` / `_ignore` son otra forma de lo mismo. El residuo
# `[0m` también se tolera antes de `system:` y `assistant:`.
_INICIO_DE_FRASE = r"(?:^|[.!?:;\n>\]\)\"'«])\s*(?:\[[0-9;?]*[a-z])?\s*"

PATRONES: dict[str, re.Pattern] = {
    nombre: re.compile(patron)
    for nombre, patron in {
        "ignore previous instructions": r"ignore\s+(?:(?:all|any|the|your|my|of)\s+)*(?:previous|prior|above|earlier|preceding)\s+(?:instructions?|prompts?|directions?|rules?)",
        "ignora las instrucciones": r"ignora(?:r|d)?\s+(?:(?:todas|tus|las|mis|y)\s+)*(?:instrucciones|indicaciones|reglas)",
        "disregard": r"disregard\s+(?:(?:all|any|the|your|my|of)\s+)*(?:previous|prior|above|earlier|preceding|instructions?|prompts?|rules?|directions?)",
        "system:": _INICIO_DE_FRASE + r"system\s*:",
        "assistant:": _INICIO_DE_FRASE + r"assistant\s*:",
        "you are now": r"(?<![a-z])you\s+are\s+now\s+(?:a|an|the|my|in|dan|acting|playing|no\s+longer|free|going\s+to|to\s+(?:act|ignore|obey|respond|answer|follow))\b",
        "nuevas instrucciones": r"(?<![a-z])nuevas\s+instrucciones\b",
        "new instructions": r"(?<![a-z])new\s+instructions\b",
        "delimitador del bloque de datos": r"datos_no_confiables",
    }.items()
}


# Secuencias de escape de terminal (color, cursor): un modelo las ve como texto raro, no como espacio.
_SECUENCIA_DE_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# Lo que queda de un color (`ESC[0m`) cuando `limpiar_texto` ya sacó el ESC: `[0m`, `[1;31m`, `[m`.
_RESIDUO_DE_COLOR = re.compile(r"\[(?:[0-9]{1,3}(?:;[0-9]{1,3})*)?m")


def normalizar(texto: str) -> str:
    """Minúsculas, NFKC, sin invisibles y con los espacios colapsados: la forma sobre la que se busca."""
    if not isinstance(texto, str):
        return ""
    texto = _RESIDUO_DE_COLOR.sub("", _SECUENCIA_DE_ESCAPE.sub("", unicodedata.normalize("NFKC", texto)))
    limpio = "".join(" " if ch.isspace() else ch for ch in texto if ch.isspace() or not _invisible(ch))
    return " ".join(limpio.casefold().split())


def detectar_frase(*textos: str) -> tuple[str, str] | None:
    """(nombre de la señal, fragmento del texto normalizado donde saltó) de la primera que encuentra, o None.

    El fragmento empieza en la frase de orden y dura hasta 200 caracteres: quien lo muestra lo limpia y lo acota.
    """
    for texto in textos:
        forma = normalizar(texto)
        if not forma:
            continue
        for nombre, patron in PATRONES.items():
            m = patron.search(forma)
            if m:
                inicio = m.start() + len(forma[m.start() : m.end()]) - len(forma[m.start() : m.end()].lstrip(" .!?:;>])\"'«"))
                return nombre, forma[inicio : inicio + 200]
    return None


def detectar(*textos: str) -> str | None:
    """El nombre de la primera señal que encuentra en alguno de los `textos`, o None."""
    hallazgo = detectar_frase(*textos)
    return hallazgo[0] if hallazgo else None

