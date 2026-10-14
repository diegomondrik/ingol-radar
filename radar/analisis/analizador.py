"""El análisis de las novedades: lotes, reintento de a una, tope por corrida.

Reglas que se cumplen siempre:
- Una novedad nunca se pierde en silencio: o queda en `analizadas` o en `sin_analizar`, con motivo.
- El tope cuenta cada novedad enviada en un lote y cada reintento: ninguna corrida manda más de
  `tope` análisis. Los reintentos de un lote se hacen apenas termina el lote, así que el tope se
  gasta en el orden de entrada.
- Lo que falla en su lote (la llamada entera o su item) se reintenta UNA vez, sola.
- Al modelo no se le manda la clave real de la novedad (viene de internet): se le manda un alias
  neutro (`n1`, `n2`...) por llamada y se mapea de vuelta acá. Un alias que no es de esa llamada se
  ignora y se cuenta.
- Aislamiento: si en un lote una novedad sale con `sospecha_de_instrucciones`, las OTRAS del lote se
  vuelven a analizar de a una (cuenta en el tope) y gana el análisis aislado: el análisis hecho junto
  a un texto que le habla al modelo no es confiable. Si el reanálisis no se puede hacer (falla o
  tope), esa novedad queda sin analizar con el motivo; no se usa el análisis contaminado.
- Segunda pasada: después de la primera (título y frase, en lotes), las novedades con `sirve` "si" o
  "tal_vez" y con artículo legible se analizan DE A UNA (aislamiento), con el texto del artículo en el
  mismo bloque de datos no confiables. El análisis de la segunda pasada reemplaza al de la primera; si el
  artículo no se pudo leer o la segunda pasada falla, queda el de la primera con el motivo anotado
  (`Analisis.articulo` y `Analisis.pasada`). Cuenta en el tope, como todo. No se lee el artículo de una
  novedad sospechosa de instrucciones.
- Cortes: `ClaudeNoDisponible` corta la corrida de inmediato (reintentar no sirve), igual que
  `max_fallas_seguidas` llamadas seguidas sin analizar nada (300 novedades con timeouts de 240 s no
  pueden seguir horas) o un Ctrl+C. Lo que queda pendiente va a `sin_analizar` con el motivo del
  corte y el resultado se devuelve igual: lo ya analizado no se tira.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable

from radar.lectura.articulo import ERROR as ARTICULO_ERROR
from radar.lectura.articulo import Articulo
from radar.lectura.modelos import Novedad

from . import cliente, esquema, prompt
from .errores import ClaudeNoDisponible, ErrorAnalisis
from .modelos import Analisis, InfoArticulo, Resultado

TAMANO_LOTE = 5
TOPE = 300
MAX_FALLAS_SEGUIDAS = 10
MAX_CLAVE = 500

MOTIVO_TOPE = "tope de la corrida"
MOTIVO_CTRL_C = "interrumpida por el usuario (Ctrl+C)"

Runner = Callable[..., dict]
LectorArticulo = Callable[[Novedad], Articulo]

BASES_NOVEDAD_CON_FRASE = ("titulo", "titulo_y_frase")
BASES_NOVEDAD_SIN_FRASE = ("titulo",)
BASES_ARTICULO = ("articulo",)
BASES_IDEA = ("idea_del_owner",)


def _bases_de(payload: dict) -> tuple[str, ...]:
    if payload.get("tipo") == "idea_del_owner":
        return BASES_IDEA
    if "texto_del_articulo" in payload:
        return BASES_ARTICULO
    return BASES_NOVEDAD_CON_FRASE if payload.get("frase") else BASES_NOVEDAD_SIN_FRASE


class _Corrida:
    def __init__(
        self, runner: Runner, sistema: str, tope: int, max_fallas_seguidas: int, sistema_articulo: str | None = None
    ) -> None:
        self.runner = runner
        self.sistema = sistema
        self.sistema_articulo = sistema_articulo or sistema
        self.tope = tope
        self.max_fallas_seguidas = max_fallas_seguidas
        self.unidades = 0  # novedades enviadas, contando reintentos
        self.seguidas = 0  # llamadas seguidas que no analizaron nada
        self.resultado = Resultado()

    def _llamar(self, items: list[dict], sistema: str | None = None) -> tuple[dict[str, Analisis], dict[str, str]]:
        """Corre claude con estos items. Devuelve (analizadas, fallas): clave real -> Analisis o motivo.

        Cada item viaja con un alias (`n1`, `n2`...) en lugar de su clave. No anota nada en el
        resultado salvo contadores. Si hay que cortar la corrida (claude no disponible, demasiadas
        fallas seguidas), deja el motivo en `resultado.abortada`: quien llama no hace más llamadas.
        """
        r = self.resultado
        r.llamadas += 1
        alias = {f"n{i}": p["clave"] for i, p in enumerate(items, 1)}
        enviados = [{**p, "clave": a} for a, p in zip(alias, items)]
        esperadas = {a: _bases_de(p) for a, p in zip(alias, items)}
        reales = list(alias.values())
        try:
            respuesta = self.runner(
                usuario=prompt.construir_usuario(enviados),
                sistema=sistema or self.sistema,
                esquema=esquema.esquema_respuesta(),
            )
            validacion = esquema.validar_respuesta(respuesta, esperadas)
        except ClaudeNoDisponible as exc:
            r.abortada = f"claude no disponible: {exc}"
            return {}, {real: str(exc) for real in reales}
        except ErrorAnalisis as exc:
            ok, fallas = {}, {real: str(exc) for real in reales}
        except Exception as exc:  # noqa: BLE001 - un runner roto no debe perder las novedades del lote
            detalle = f"{type(exc).__name__}: {str(exc)[:200]}"
            ok, fallas = {}, {real: f"error inesperado en la llamada: {detalle}" for real in reales}
        else:
            r.ignoradas += validacion.ignoradas
            ok = {alias[a]: dataclasses.replace(an, clave=alias[a]) for a, an in validacion.analizadas.items()}
            fallas = {alias[a]: motivo for a, motivo in validacion.fallas.items()}
        self.seguidas = 0 if ok else self.seguidas + 1
        if self.seguidas >= self.max_fallas_seguidas:
            ultima = next(iter(fallas.values()), "sin motivo")
            r.abortada = f"{self.seguidas} llamadas seguidas sin analizar nada; última falla: {ultima}"
        return ok, fallas

    def _una_sola(self, payload: dict) -> tuple[Analisis | None, str]:
        """Reintento o reanálisis de una novedad sola: cuenta en el tope. (análisis, motivo si falló)."""
        self.unidades += 1
        ok, fallas = self._llamar([payload])
        clave = payload["clave"]
        return ok.get(clave), fallas.get(clave, "")

    def lote(self, items: list[dict]) -> None:
        r = self.resultado
        self.unidades += len(items)
        ok, fallas = self._llamar(items)
        pendientes = {p["clave"]: p for p in items}

        # Aislamiento: una sospechosa en el lote contamina el análisis de las otras.
        otras: list[str] = []
        if len(items) > 1 and any(a.sospecha_de_instrucciones for a in ok.values()):
            otras = [k for k, a in ok.items() if not a.sospecha_de_instrucciones]
            ok = {k: a for k, a in ok.items() if k not in otras}
        r.analizadas.update(ok)

        for clave, motivo1 in fallas.items():
            if r.abortada:
                r.sin_analizar[clave] = f"{motivo1}; reintento no hecho: corrida cortada ({r.abortada})"
            elif self.unidades >= self.tope:
                r.sin_analizar[clave] = f"{motivo1}; reintento no hecho: {MOTIVO_TOPE}"
                r.por_tope += 1
            else:
                r.reintentos += 1
                analisis, motivo2 = self._una_sola(pendientes[clave])
                if analisis is not None:
                    r.analizadas[clave] = analisis
                else:
                    r.sin_analizar[clave] = f"falló dos veces. Primero: {motivo1}. Después: {motivo2}"
                    r.fallidas += 1

        for clave in otras:
            porque = "analizada en un lote con una novedad sospechosa de instrucciones"
            if r.abortada:
                r.sin_analizar[clave] = f"{porque}; reanálisis aislado no hecho: corrida cortada ({r.abortada})"
            elif self.unidades >= self.tope:
                r.sin_analizar[clave] = f"{porque}; reanálisis aislado no hecho: {MOTIVO_TOPE}"
                r.por_tope += 1
            else:
                r.aisladas += 1
                analisis, motivo2 = self._una_sola(pendientes[clave])
                if analisis is not None:
                    r.analizadas[clave] = analisis
                else:
                    r.sin_analizar[clave] = f"{porque}; el reanálisis aislado falló: {motivo2}"
                    r.fallidas += 1

    def correr(self, items: list[dict], tamano_lote: int, previos: dict[str, str] | None = None) -> Resultado:
        """Analiza `items` (payloads) en lotes. `previos`: novedades que no entran (clave -> motivo)."""
        r = self.resultado
        r.sin_analizar.update(previos or {})
        i = 0
        try:
            while i < len(items) and not r.abortada:
                restante = self.tope - self.unidades
                if restante <= 0:
                    break
                tomar = items[i : i + min(tamano_lote, restante)]
                i += len(tomar)
                self.lote(tomar)
        except KeyboardInterrupt:
            r.abortada = MOTIVO_CTRL_C
        # Lo que no se llegó a enviar: por tope o por corte, pero siempre con motivo.
        for p in items[i:]:
            if r.abortada:
                r.sin_analizar[p["clave"]] = f"sin intentar: corrida cortada ({r.abortada})"
            else:
                r.sin_analizar[p["clave"]] = MOTIVO_TOPE
                r.por_tope += 1
        # Red de seguridad: nada se pierde en silencio, ni siquiera con una interrupción a mitad de un lote.
        for p in items:
            if p["clave"] not in r.analizadas and p["clave"] not in r.sin_analizar:
                r.sin_analizar[p["clave"]] = f"sin resultado: {r.abortada or 'error interno'}"
        return r


def _leer(lector: LectorArticulo, nov: Novedad) -> Articulo:
    """El artículo de `nov` con el lector inyectado. Nunca lanza: un lector roto deja el estado `error`."""
    try:
        articulo = lector(nov)
    except Exception as exc:  # noqa: BLE001 - frontera: leer el artículo no debe frenar el análisis
        return Articulo(estado=ARTICULO_ERROR, motivo=f"error inesperado al leer el artículo: {type(exc).__name__}")
    if not isinstance(articulo, Articulo):
        return Articulo(estado=ARTICULO_ERROR, motivo="el lector de artículos no devolvió un Articulo")
    return articulo


def _segunda_pasada(
    corrida: _Corrida, claves: list[str], novedades: dict[str, Novedad], lector: LectorArticulo | None
) -> None:
    """Segunda pasada de a una, con el texto del artículo. Deja `pasada` y `articulo` anotados en cada análisis."""
    r = corrida.resultado
    info: dict[str, InfoArticulo] = {}
    try:
        for clave in claves:
            actual = r.analizadas.get(clave)
            if actual is None:
                continue
            if actual.sirve == "no":
                info[clave] = InfoArticulo("no_corresponde", "la primera pasada no la consideró útil")
            elif lector is None:
                info[clave] = InfoArticulo("desactivado", "sin segunda pasada")
            elif actual.sospecha_de_instrucciones:
                info[clave] = InfoArticulo("no_leido", "sospecha de instrucciones en el título o la frase: no se lee más de esa fuente")
            elif r.abortada:
                info[clave] = InfoArticulo("no_leido", f"corrida cortada ({r.abortada})")
            elif corrida.unidades >= corrida.tope:
                info[clave] = InfoArticulo("no_leido", MOTIVO_TOPE)
                r.sin_segunda_por_tope += 1
            else:
                _una_segunda(corrida, clave, novedades[clave], lector, info)
    except KeyboardInterrupt:
        r.abortada = r.abortada or MOTIVO_CTRL_C
    finally:
        for clave in claves:
            a = r.analizadas.get(clave)
            if a is not None and a.articulo is None:
                faltante = InfoArticulo("no_leido", f"corrida cortada ({r.abortada or 'interrupción'})")
                r.analizadas[clave] = dataclasses.replace(a, articulo=info.get(clave, faltante))


def _una_segunda(corrida: _Corrida, clave: str, nov: Novedad, lector: LectorArticulo, info: dict) -> None:
    r = corrida.resultado
    articulo = _leer(lector, nov)
    if not articulo.legible:
        info[clave] = InfoArticulo(articulo.estado, articulo.motivo or "sin texto", False)
        return
    payload = prompt.payload_articulo(nov, articulo.texto, articulo.recortado)
    corrida.unidades += 1
    r.segundas += 1
    ok, fallas = corrida._llamar([payload], corrida.sistema_articulo)
    nuevo = ok.get(clave)
    if nuevo is not None:
        r.analizadas[clave] = dataclasses.replace(
            nuevo, pasada=2, articulo=InfoArticulo("leido", None, articulo.recortado)
        )
    else:
        r.segundas_fallidas += 1
        motivo = f"la segunda pasada falló ({fallas.get(clave, 'sin motivo')}); queda el análisis de la primera"
        info[clave] = InfoArticulo("leido", motivo, articulo.recortado)


def _validar_parametros(tamano_lote: int, tope: int, max_fallas_seguidas: int) -> None:
    for nombre, valor in (("tamano_lote", tamano_lote), ("tope", tope), ("max_fallas_seguidas", max_fallas_seguidas)):
        if not isinstance(valor, int) or isinstance(valor, bool) or valor < 1:
            raise ValueError(f"{nombre} tiene que ser un entero positivo")


def _clave_valida(clave) -> str:
    if not isinstance(clave, str) or not clave:
        raise ErrorAnalisis(f"una novedad trae una clave que no sirve: {str(clave)[:60]!r}")
    return clave


def analizar(
    novedades: Iterable[Novedad],
    runner: Runner = cliente.ejecutar,
    contexto: str | None = None,
    tamano_lote: int = TAMANO_LOTE,
    tope: int = TOPE,
    max_fallas_seguidas: int = MAX_FALLAS_SEGUIDAS,
    lector_articulo: LectorArticulo | None = None,
) -> Resultado:
    """Analiza las novedades con Claude. `contexto` es el texto; si es None, se lee del archivo por defecto.

    `lector_articulo(novedad) -> Articulo` activa la segunda pasada (ver arriba); sin él, sólo la primera.

    Lanza `ErrorAnalisis` (antes de gastar nada) si falta el contexto, si una clave no es texto o
    está vacía, o si hay claves repetidas. Una clave de más de MAX_CLAVE caracteres deja a ESA
    novedad en `sin_analizar` con su motivo; las demás siguen.
    """
    _validar_parametros(tamano_lote, tope, max_fallas_seguidas)
    texto_contexto = contexto if contexto is not None else prompt.cargar_contexto()
    sistema = prompt.construir_sistema(texto_contexto)
    sistema_articulo = prompt.construir_sistema(texto_contexto, segunda_pasada=True)
    items: list[dict] = []
    por_clave: dict[str, Novedad] = {}
    previos: dict[str, str] = {}
    vistas: set[str] = set()
    for nov in novedades:
        clave = _clave_valida(nov.clave)
        if clave in vistas:
            raise ErrorAnalisis(f"clave repetida en la entrada: {clave[:60]!r}")
        vistas.add(clave)
        if len(clave) > MAX_CLAVE:
            previos[clave] = f"la clave de la novedad tiene más de {MAX_CLAVE} caracteres: no se analiza"
            continue
        items.append(prompt.payload_novedad(nov))
        por_clave[clave] = nov
    corrida = _Corrida(runner, sistema, tope, max_fallas_seguidas, sistema_articulo)
    resultado = corrida.correr(items, tamano_lote, previos)
    _segunda_pasada(corrida, [p["clave"] for p in items], por_clave, lector_articulo)
    return resultado


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
