"""La forma fija de la respuesta y su validación propia.

El esquema se le pasa a `claude --json-schema`, pero no se confía sólo en eso: la respuesta del
modelo es una entrada no confiable y se valida acá, campo por campo, antes de usar nada.
Un item que no valida deja SU novedad sin analizar; los demás del lote siguen.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from radar.lectura.limpieza import limpiar_texto

from .errores import ErrorRespuesta
from .modelos import Analisis, Opinion

MAX_TEXTO = 1500

SIRVE = ("si", "no", "tal_vez")
TRES = ("si", "no", "no_se")
ADAPTADOR_O_CORE = ("adaptador", "core", "ninguno", "no_se")
BASES = ("titulo", "titulo_y_frase", "articulo", "idea_del_owner")
CONFIANZAS = ("alta", "media", "baja")

CAMPOS_ITEM = frozenset(
    {
        "clave",
        "sirve",
        "ruido",
        "mejora_control",
        "mejora_software",
        "adaptador_o_core",
        "lo_regala_el_proveedor",
        "por_que_sirve_o_no",
        "en_que_consiste_el_cambio",
        "que_implica_para_el_desarrollo",
        "accion_propuesta",
        "base",
        "confianza",
        "sospecha_de_instrucciones",
    }
)
CAMPOS_OPINION = frozenset({"respuesta", "por_que"})
CAMPOS_TEXTO = (
    "por_que_sirve_o_no",
    "en_que_consiste_el_cambio",
    "que_implica_para_el_desarrollo",
    "accion_propuesta",
)


# ---------------------------------------------------------------------------- esquema


def _texto() -> dict:
    return {"type": "string", "minLength": 1, "maxLength": MAX_TEXTO}


def _enum(valores) -> dict:
    return {"type": "string", "enum": list(valores)}


def _opinion(valores) -> dict:
    return {
        "type": "object",
        "properties": {"respuesta": _enum(valores), "por_que": _texto()},
        "required": ["respuesta", "por_que"],
        "additionalProperties": False,
    }


def esquema_respuesta() -> dict:
    """El JSON Schema de la respuesta. Un objeto nuevo en cada llamada (nadie comparte el mismo)."""
    item = {
        "type": "object",
        "properties": {
            "clave": {"type": "string", "minLength": 1},
            "sirve": _enum(SIRVE),
            "ruido": {"type": "boolean"},
            "mejora_control": _opinion(TRES),
            "mejora_software": _opinion(TRES),
            "adaptador_o_core": _enum(ADAPTADOR_O_CORE),
            "lo_regala_el_proveedor": _opinion(TRES),
            "por_que_sirve_o_no": _texto(),
            "en_que_consiste_el_cambio": _texto(),
            "que_implica_para_el_desarrollo": _texto(),
            "accion_propuesta": _texto(),
            "base": _enum(BASES),
            "confianza": _enum(CONFIANZAS),
            "sospecha_de_instrucciones": {"type": "boolean"},
        },
        "required": sorted(CAMPOS_ITEM),
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"analisis": {"type": "array", "items": item}},
        "required": ["analisis"],
        "additionalProperties": False,
    }


# ------------------------------------------------------------------------ validación


class _Invalido(Exception):
    """Un item no valida; el mensaje dice por qué."""


def _enumerado(valor, valores, nombre: str) -> str:
    if not isinstance(valor, str) or valor not in valores:
        raise _Invalido(f"{nombre}: valor no permitido")
    return valor


def _booleano(valor, nombre: str) -> bool:
    if not isinstance(valor, bool):  # bool exacto: 1 y "true" no valen
        raise _Invalido(f"{nombre}: no es verdadero/falso")
    return valor


def _texto_limpio(valor, nombre: str) -> str:
    """Texto no vacío, de a lo sumo MAX_TEXTO caracteres, limpio (sin etiquetas, controles ni bidi).

    Un texto más largo que el tope se rechaza (no se recorta en silencio). Uno que la limpieza deja
    vacío (puro relleno hostil o puras etiquetas) también.
    """
    if not isinstance(valor, str):
        raise _Invalido(f"{nombre}: no es texto")
    if len(valor) > MAX_TEXTO:
        raise _Invalido(f"{nombre}: más de {MAX_TEXTO} caracteres")
    limpio = limpiar_texto(valor, MAX_TEXTO)
    if not limpio:
        raise _Invalido(f"{nombre}: vacío o sin texto utilizable")
    return limpio


def _objeto_exacto(valor, campos: frozenset, nombre: str) -> dict:
    if not isinstance(valor, dict):
        raise _Invalido(f"{nombre}: no es un objeto")
    faltan = sorted(campos - set(valor))
    sobran = sorted(set(valor) - campos)
    if faltan:
        raise _Invalido(f"{nombre}: faltan campos {faltan}")
    if sobran:
        raise _Invalido(f"{nombre}: campos de más {[str(s)[:40] for s in sobran]}")
    return valor


def _opinion_validada(valor, nombre: str) -> Opinion:
    obj = _objeto_exacto(valor, CAMPOS_OPINION, nombre)
    return Opinion(
        _enumerado(obj["respuesta"], TRES, f"{nombre}.respuesta"),
        _texto_limpio(obj["por_que"], f"{nombre}.por_que"),
    )


def validar_item(item, bases_permitidas: tuple[str, ...]) -> Analisis:
    """Valida un item y lo devuelve como `Analisis`; lanza `_Invalido` (con motivo) si no."""
    obj = _objeto_exacto(item, CAMPOS_ITEM, "item")
    clave = obj["clave"]
    if not isinstance(clave, str) or not clave:
        raise _Invalido("clave: no es texto")
    sirve = _enumerado(obj["sirve"], SIRVE, "sirve")
    ruido = _booleano(obj["ruido"], "ruido")
    if ruido and sirve != "no":
        raise _Invalido("ruido=true con sirve distinto de 'no': contradictorio")
    base = _enumerado(obj["base"], BASES, "base")
    if base not in bases_permitidas:
        raise _Invalido(f"base: '{base}' no corresponde a esta novedad (permitidas: {list(bases_permitidas)})")
    return Analisis(
        clave=clave,
        sirve=sirve,
        ruido=ruido,
        mejora_control=_opinion_validada(obj["mejora_control"], "mejora_control"),
        mejora_software=_opinion_validada(obj["mejora_software"], "mejora_software"),
        adaptador_o_core=_enumerado(obj["adaptador_o_core"], ADAPTADOR_O_CORE, "adaptador_o_core"),
        lo_regala_el_proveedor=_opinion_validada(obj["lo_regala_el_proveedor"], "lo_regala_el_proveedor"),
        por_que_sirve_o_no=_texto_limpio(obj["por_que_sirve_o_no"], "por_que_sirve_o_no"),
        en_que_consiste_el_cambio=_texto_limpio(obj["en_que_consiste_el_cambio"], "en_que_consiste_el_cambio"),
        que_implica_para_el_desarrollo=_texto_limpio(
            obj["que_implica_para_el_desarrollo"], "que_implica_para_el_desarrollo"
        ),
        accion_propuesta=_texto_limpio(obj["accion_propuesta"], "accion_propuesta"),
        base=base,
        confianza=_enumerado(obj["confianza"], CONFIANZAS, "confianza"),
        sospecha_de_instrucciones=_booleano(obj["sospecha_de_instrucciones"], "sospecha_de_instrucciones"),
    )


@dataclass
class Validacion:
    analizadas: dict[str, Analisis] = field(default_factory=dict)
    fallas: dict[str, str] = field(default_factory=dict)  # clave -> motivo (de las enviadas)
    ignoradas: int = 0  # items repetidos, desconocidos o sin clave


def validar_respuesta(respuesta, esperadas: Mapping[str, tuple[str, ...]]) -> Validacion:
    """Valida la respuesta entera contra lo que se envió.

    `esperadas`: clave enviada -> bases que le corresponden. Toda clave enviada termina en
    `analizadas` o en `fallas` (falta, o su item no valida). Items con clave repetida (se queda el
    primero), desconocida o sin clave se ignoran y se cuentan. Si la respuesta no tiene la forma
    `{"analisis": [...]}` lanza `ErrorRespuesta`: no se usa nada de ella.
    """
    if not isinstance(respuesta, dict) or set(respuesta) != {"analisis"}:
        raise ErrorRespuesta("la respuesta no es un objeto con la única propiedad 'analisis'")
    lista = respuesta["analisis"]
    if not isinstance(lista, list):
        raise ErrorRespuesta("'analisis' no es una lista")
    resultado = Validacion()
    vistas: set[str] = set()
    for item in lista:
        clave = item.get("clave") if isinstance(item, dict) else None
        if not isinstance(clave, str) or clave not in esperadas or clave in vistas:
            resultado.ignoradas += 1
            continue
        vistas.add(clave)
        try:
            resultado.analizadas[clave] = validar_item(item, esperadas[clave])
        except _Invalido as exc:
            resultado.fallas[clave] = f"la respuesta no valida: {exc}"
    for clave in esperadas:
        if clave not in vistas:
            resultado.fallas[clave] = "la respuesta no trajo esta novedad"
    return resultado
