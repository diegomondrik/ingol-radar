"""Piezas comunes de las pruebas: items válidos, un runner falso y novedades de ejemplo."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

from radar.analisis.prompt import FIN_DATOS, INICIO_DATOS
from radar.lectura.modelos import Novedad

CONTEXTO = "INGOL es una plataforma de desarrollo gobernado. (contexto de prueba)"


def item_valido(clave: str, **cambios) -> dict:
    """Un item de respuesta que valida, con `cambios` encima."""
    item = {
        "clave": clave,
        "sirve": "si",
        "ruido": False,
        "mejora_control": {"respuesta": "si", "por_que": "Agrega un control que antes no había."},
        "mejora_software": {"respuesta": "no", "por_que": "No cambia lo que sale."},
        "adaptador_o_core": "adaptador",
        "lo_regala_el_proveedor": {"respuesta": "no_se", "por_que": "Por el título, no se puede saber."},
        "por_que_sirve_o_no": "Sirve porque refuerza la revisión.",
        "en_que_consiste_el_cambio": "Un método nuevo de revisión.",
        "que_implica_para_el_desarrollo": "Habría que sumar un paso al contrato.",
        "accion_propuesta": "Leer la nota y decidir.",
        "base": "titulo",
        "confianza": "media",
        "sospecha_de_instrucciones": False,
    }
    item.update(cambios)
    return item


def novedad(n: int | str, **cambios) -> Novedad:
    datos = {
        "fuente": "fuente-de-prueba",
        "clave": f"f:{n}",
        "titulo": f"Título de la novedad {n}",
        "enlace": f"https://ejemplo.org/nota-{n}",
        "fecha": datetime(2026, 10, 1, tzinfo=timezone.utc),
        "frase": "",
    }
    datos.update(cambios)
    return Novedad(**datos)


def real(p: dict) -> str:
    """La clave REAL de un item que viajó al modelo: ahí sólo va un alias (n1, n2...), así que se
    reconoce por el título de `novedad(n)`. Un item de otro origen devuelve su `clave` tal cual."""
    marca = "Título de la novedad "
    if p.get("titulo", "").startswith(marca):
        return f"f:{p['titulo'][len(marca):]}"
    return p["clave"]


def datos_del_usuario(usuario: str) -> list[dict]:
    """Los items que viajan en el bloque de datos de un mensaje de usuario."""
    assert usuario.count(INICIO_DATOS) == 1 and usuario.count(FIN_DATOS) == 1
    desde = usuario.index(INICIO_DATOS) + len(INICIO_DATOS)
    hasta = usuario.index(FIN_DATOS)
    return json.loads(usuario[desde:hasta].strip())["novedades"]


class RunnerFalso:
    """Un `runner` que se arma con una función `(items) -> respuesta | excepción`.

    Guarda cada llamada (`usuario`, `sistema`, `esquema`), los alias que viajaron (`aliases`) y las
    claves reales de esas novedades (`claves`, reconocidas por el título: ver `real`).
    """

    def __init__(self, funcion=None):
        self.funcion = funcion or self.todo_bien
        self.llamadas: list[dict] = []

    @staticmethod
    def todo_bien(items: list[dict]) -> dict:
        return {"analisis": [item_valido(p["clave"]) for p in items]}

    def __call__(self, *, usuario, sistema, esquema):
        items = datos_del_usuario(usuario)
        self.llamadas.append(
            {
                "usuario": usuario,
                "sistema": sistema,
                "esquema": copy.deepcopy(esquema),
                "aliases": [p["clave"] for p in items],
                "claves": [real(p) for p in items],
            }
        )
        resultado = self.funcion(items)
        if isinstance(resultado, BaseException):
            raise resultado
        return resultado

    @property
    def unidades(self) -> int:
        """Novedades enviadas en total (lo que cuenta el tope)."""
        return sum(len(c["claves"]) for c in self.llamadas)
