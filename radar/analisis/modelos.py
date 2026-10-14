"""Tipos del análisis: la opinión, el análisis de una novedad y el resultado de una corrida."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Opinion:
    """Una pregunta de INGOL respondida por separado: sí, no o no sé, y por qué."""

    respuesta: str
    por_que: str

    def a_dict(self) -> dict:
        return {"respuesta": self.respuesta, "por_que": self.por_que}


@dataclass(frozen=True, slots=True)
class InfoArticulo:
    """Qué pasó con el artículo de una novedad (no lo escribe el modelo: lo anota el analizador).

    `estado`: "leido" (se usó en la segunda pasada), "no_corresponde" (la primera pasada dijo que no),
    "desactivado" (sin segunda pasada), "no_leido" (corrida cortada o tope), o el estado de la lectura
    cuando no se pudo: "no_aplica", "host_distinto", "sin_texto", "error".
    """

    estado: str
    motivo: str | None = None
    recortado: bool = False

    def a_dict(self) -> dict:
        return {"estado": self.estado, "motivo": self.motivo, "recortado": self.recortado}


@dataclass(frozen=True, slots=True)
class Analisis:
    """El análisis de una novedad, ya validado y con todo el texto limpio. Inmutable."""

    clave: str
    sirve: str  # "si" | "no" | "tal_vez"
    ruido: bool
    mejora_control: Opinion
    mejora_software: Opinion
    adaptador_o_core: str  # "adaptador" | "core" | "ninguno" | "no_se"
    lo_regala_el_proveedor: Opinion
    por_que_sirve_o_no: str
    en_que_consiste_el_cambio: str
    que_implica_para_el_desarrollo: str
    accion_propuesta: str
    base: str  # "titulo" | "titulo_y_frase" | "idea_del_owner"
    confianza: str  # "alta" | "media" | "baja"
    sospecha_de_instrucciones: bool
    # Lo que anota el analizador, fuera del esquema del modelo:
    pasada: int = 1  # 1 título y frase; 2 con el artículo
    articulo: InfoArticulo | None = None
    sospecha_mecanica: bool = False  # la señal fija (senal.py) saltó en el título, la frase o el artículo

    def a_dict(self) -> dict:
        return {
            "clave": self.clave,
            "sirve": self.sirve,
            "ruido": self.ruido,
            "mejora_control": self.mejora_control.a_dict(),
            "mejora_software": self.mejora_software.a_dict(),
            "adaptador_o_core": self.adaptador_o_core,
            "lo_regala_el_proveedor": self.lo_regala_el_proveedor.a_dict(),
            "por_que_sirve_o_no": self.por_que_sirve_o_no,
            "en_que_consiste_el_cambio": self.en_que_consiste_el_cambio,
            "que_implica_para_el_desarrollo": self.que_implica_para_el_desarrollo,
            "accion_propuesta": self.accion_propuesta,
            "base": self.base,
            "confianza": self.confianza,
            "sospecha_de_instrucciones": self.sospecha_de_instrucciones,
            "pasada": self.pasada,
            "articulo": self.articulo.a_dict() if self.articulo else None,
            "sospecha_mecanica": self.sospecha_mecanica,
        }


@dataclass
class Resultado:
    """Lo que dejó una corrida. Toda novedad que entró está en `analizadas` o en `sin_analizar`.

    Contadores:
    - `llamadas`: veces que se corrió claude (lotes y reintentos).
    - `reintentos`: novedades que se reintentaron de a una.
    - `fallidas`: novedades que quedaron sin analizar porque fallaron también en el reintento.
    - `ignoradas`: entradas de las respuestas del modelo que se descartaron (clave repetida,
      desconocida o sin clave).
    - `por_tope`: novedades que quedaron sin analizar porque se acabó el tope de la corrida
      (sin intentar, o sin reintento).
    - `aisladas`: novedades de un lote con una sospechosa de instrucciones que se volvieron a
      analizar solas.
    - `segundas`: llamadas de la segunda pasada (con el artículo); `segundas_fallidas`: las que fallaron
      (queda el análisis de la primera pasada); `sin_segunda_por_tope`: las que no se hicieron por el tope.
    - `abortada`: si la corrida se cortó antes de tiempo, el motivo; si no, None.
    """

    analizadas: dict[str, Analisis] = field(default_factory=dict)
    sin_analizar: dict[str, str] = field(default_factory=dict)
    # Para el afinado: el análisis que una novedad traía del lote antes de reanalizarse aislada (no va en a_dict).
    del_lote: dict[str, Analisis] = field(default_factory=dict)
    llamadas: int = 0
    reintentos: int = 0
    fallidas: int = 0
    ignoradas: int = 0
    por_tope: int = 0
    aisladas: int = 0
    segundas: int = 0
    segundas_fallidas: int = 0
    sin_segunda_por_tope: int = 0
    abortada: str | None = None

    def contadores(self) -> dict:
        return {
            "llamadas": self.llamadas,
            "reintentos": self.reintentos,
            "fallidas": self.fallidas,
            "ignoradas": self.ignoradas,
            "por_tope": self.por_tope,
            "aisladas": self.aisladas,
            "segundas": self.segundas,
            "segundas_fallidas": self.segundas_fallidas,
            "sin_segunda_por_tope": self.sin_segunda_por_tope,
            "abortada": self.abortada,
        }

    def a_dict(self) -> dict:
        return {
            "analizadas": {k: a.a_dict() for k, a in self.analizadas.items()},
            "sin_analizar": dict(self.sin_analizar),
            "contadores": self.contadores(),
        }
