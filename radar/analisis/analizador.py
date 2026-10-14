"""El análisis de las novedades: lotes, reintento de a una, tope por corrida.

Reglas que se cumplen siempre:
- Una novedad nunca se pierde en silencio: o queda en `analizadas` o en `sin_analizar`, con motivo.
- El tope cuenta cada novedad enviada en un lote y cada reintento: ninguna corrida manda más de
  `tope` análisis. Los reintentos de un lote se hacen apenas termina el lote, así que el tope se
  gasta en el orden de entrada.
- Lo que falla en su lote (la llamada entera o su item) se reintenta UNA vez, sola.
- Cortes: `ClaudeNoDisponible` corta la corrida de inmediato (reintentar no sirve) y
  `max_fallas_seguidas` llamadas seguidas sin analizar nada también (una corrida de 300 novedades
  con timeouts de 240 s no puede seguir horas). Lo que queda pendiente va a `sin_analizar` con el
  motivo del corte.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from . import cliente, esquema, prompt
from .errores import ClaudeNoDisponible, ErrorAnalisis
from .modelos import Resultado
from radar.lectura.modelos import Novedad

TAMANO_LOTE = 5
TOPE = 300
MAX_FALLAS_SEGUIDAS = 10
MAX_CLAVE = 500

MOTIVO_TOPE = "tope de la corrida"

Runner = Callable[..., dict]

BASES_NOVEDAD_CON_FRASE = ("titulo", "titulo_y_frase")
BASES_NOVEDAD_SIN_FRASE = ("titulo",)
BASES_IDEA = ("idea_del_owner",)


def _bases_de(payload: dict) -> tuple[str, ...]:
    if payload.get("tipo") == "idea_del_owner":
        return BASES_IDEA
    return BASES_NOVEDAD_CON_FRASE if payload.get("frase") else BASES_NOVEDAD_SIN_FRASE


class _Corrida:
    def __init__(self, runner: Runner, sistema: str, tope: int, max_fallas_seguidas: int) -> None:
        self.runner = runner
        self.sistema = sistema
        self.tope = tope
        self.max_fallas_seguidas = max_fallas_seguidas
        self.unidades = 0  # novedades enviadas, contando reintentos
        self.seguidas = 0  # llamadas seguidas que no analizaron nada
        self.resultado = Resultado()

    def _llamar(self, items: list[dict]) -> dict[str, str]:
        """Corre claude con estos items. Anota lo analizado; devuelve clave -> motivo de lo que falló.

        Si hay que cortar la corrida (claude no disponible, demasiadas fallas seguidas), deja el
        motivo en `resultado.abortada`; quien llama no hace más llamadas.
        """
        r = self.resultado
        r.llamadas += 1
        esperadas = {p["clave"]: _bases_de(p) for p in items}
        try:
            respuesta = self.runner(
                usuario=prompt.construir_usuario(items),
                sistema=self.sistema,
                esquema=esquema.esquema_respuesta(),
            )
            validacion = esquema.validar_respuesta(respuesta, esperadas)
        except ClaudeNoDisponible as exc:
            r.abortada = f"claude no disponible: {exc}"
            return {clave: str(exc) for clave in esperadas}
        except ErrorAnalisis as exc:
            fallas = {clave: str(exc) for clave in esperadas}
        except Exception as exc:  # noqa: BLE001 - un runner roto no debe perder las novedades del lote
            detalle = f"{type(exc).__name__}: {str(exc)[:200]}"
            fallas = {clave: f"error inesperado en la llamada: {detalle}" for clave in esperadas}
        else:
            r.analizadas.update(validacion.analizadas)
            r.ignoradas += validacion.ignoradas
            fallas = validacion.fallas
        self.seguidas = 0 if len(fallas) < len(esperadas) else self.seguidas + 1
        if self.seguidas >= self.max_fallas_seguidas:
            ultima = next(iter(fallas.values()))
            r.abortada = f"{self.seguidas} llamadas seguidas sin analizar nada; última falla: {ultima}"
        return fallas

    def lote(self, items: list[dict]) -> None:
        r = self.resultado
        self.unidades += len(items)
        fallas = self._llamar(items)
        pendientes = {p["clave"]: p for p in items}
        for clave, motivo1 in fallas.items():
            if r.abortada:
                r.sin_analizar[clave] = f"{motivo1}; reintento no hecho: corrida cortada ({r.abortada})"
                continue
            if self.unidades >= self.tope:
                r.sin_analizar[clave] = f"{motivo1}; reintento no hecho: {MOTIVO_TOPE}"
                r.por_tope += 1
                continue
            self.unidades += 1
            r.reintentos += 1
            de_nuevo = self._llamar([pendientes[clave]])
            if clave in de_nuevo:
                r.sin_analizar[clave] = f"falló dos veces. Primero: {motivo1}. Después: {de_nuevo[clave]}"
                r.fallidas += 1

    def correr(self, items: list[dict], tamano_lote: int) -> Resultado:
        r = self.resultado
        i = 0
        while i < len(items) and not r.abortada:
            restante = self.tope - self.unidades
            if restante <= 0:
                break
            tomar = items[i : i + min(tamano_lote, restante)]
            i += len(tomar)
            self.lote(tomar)
        # Lo que no se llegó a enviar: por tope o por corte, pero siempre con motivo.
        for p in items[i:]:
            if r.abortada:
                r.sin_analizar[p["clave"]] = f"sin intentar: corrida cortada ({r.abortada})"
            else:
                r.sin_analizar[p["clave"]] = MOTIVO_TOPE
                r.por_tope += 1
        return r


def _validar_parametros(tamano_lote: int, tope: int, max_fallas_seguidas: int) -> None:
    for nombre, valor in (("tamano_lote", tamano_lote), ("tope", tope), ("max_fallas_seguidas", max_fallas_seguidas)):
        if not isinstance(valor, int) or isinstance(valor, bool) or valor < 1:
            raise ValueError(f"{nombre} tiene que ser un entero positivo")


def _clave_valida(clave) -> str:
    if not isinstance(clave, str) or not clave or len(clave) > MAX_CLAVE:
        raise ErrorAnalisis(f"una novedad trae una clave que no sirve: {str(clave)[:60]!r}")
    return clave


def analizar(
    novedades: Iterable[Novedad],
    runner: Runner = cliente.ejecutar,
    contexto: str | None = None,
    tamano_lote: int = TAMANO_LOTE,
    tope: int = TOPE,
    max_fallas_seguidas: int = MAX_FALLAS_SEGUIDAS,
) -> Resultado:
    """Analiza las novedades con Claude. `contexto` es el texto; si es None, se lee del archivo por defecto.

    Lanza `ErrorAnalisis` (antes de gastar nada) si falta el contexto o hay claves repetidas o
    inválidas en la entrada.
    """
    _validar_parametros(tamano_lote, tope, max_fallas_seguidas)
    sistema = prompt.construir_sistema(contexto if contexto is not None else prompt.cargar_contexto())
    items: list[dict] = []
    vistas: set[str] = set()
    for nov in novedades:
        clave = _clave_valida(nov.clave)
        if clave in vistas:
            raise ErrorAnalisis(f"clave repetida en la entrada: {clave[:60]!r}")
        vistas.add(clave)
        items.append(prompt.payload_novedad(nov))
    return _Corrida(runner, sistema, tope, max_fallas_seguidas).correr(items, tamano_lote)


def analizar_idea(
    texto: str,
    runner: Runner = cliente.ejecutar,
    contexto: str | None = None,
) -> Resultado:
    """Analiza una idea propia del owner con el mismo esquema (base "idea_del_owner", clave fija)."""
    payload = prompt.payload_idea(texto)
    if not payload["texto"]:
        raise ErrorAnalisis("la idea está vacía")
    sistema = prompt.construir_sistema(contexto if contexto is not None else prompt.cargar_contexto())
    return _Corrida(runner, sistema, tope=2, max_fallas_seguidas=99).correr([payload], 1)
