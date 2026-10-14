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
- Señal fija: además de la marca del modelo, `senal.detectar` mira en Python el título, la frase y el
  texto del artículo. Una novedad que la dispara en el título o la frase se trata como sospechosa aunque
  el modelo no la marque (aísla a las vecinas, no pasa a la segunda pasada). Si dispara en el texto del
  artículo, el artículo SE USA igual (puede ser un artículo sobre inyección de instrucciones que cita un
  ejemplo): la segunda pasada es de a una, lleva un aviso en el prompt y el análisis queda con
  `sospecha_mecanica` y `articulo.instrucciones_detectadas`.
- Lectura de artículos: tiene un presupuesto de pared por corrida (15 minutos) y se corta tras 5
  lecturas fallidas seguidas; lo que no se lee queda con la primera pasada y el motivo.
- Cortes: `ClaudeNoDisponible` corta la corrida de inmediato (reintentar no sirve), igual que
  `max_fallas_seguidas` llamadas seguidas sin analizar nada (300 novedades con timeouts de 240 s no
  pueden seguir horas) o un Ctrl+C. Lo que queda pendiente va a `sin_analizar` con el motivo del
  corte y el resultado se devuelve igual: lo ya analizado no se tira.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable, Iterable

from radar.lectura.articulo import ERROR as ARTICULO_ERROR
from radar.lectura.articulo import SIN_TEXTO as ARTICULO_SIN_TEXTO
from radar.lectura.articulo import Articulo
from radar.lectura.modelos import Novedad

from . import cliente, esquema, prompt, senal
from .errores import ClaudeNoDisponible, ErrorAnalisis
from .modelos import Analisis, InfoArticulo, Resultado

TAMANO_LOTE = 5
TOPE = 300
MAX_FALLAS_SEGUIDAS = 10
MAX_CLAVE = 500
PRESUPUESTO_LECTURA_S = 15 * 60  # segundos de pared para leer artículos, por corrida
MAX_LECTURAS_FALLIDAS = 5  # lecturas fallidas seguidas antes de dejar de leer

MOTIVO_TOPE = "tope de la corrida"
MOTIVO_CTRL_C = "interrumpida por el usuario (Ctrl+C)"
MOTIVO_PRESUPUESTO = "no leído: presupuesto de lectura agotado"

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
        self,
        runner: Runner,
        sistema: str,
        tope: int,
        max_fallas_seguidas: int,
        sistema_articulo: str | None = None,
        mecanicas: dict[str, str] | None = None,
        reloj: Callable[[], float] = time.monotonic,
        presupuesto_lectura: float = PRESUPUESTO_LECTURA_S,
        max_lecturas_fallidas: int = MAX_LECTURAS_FALLIDAS,
        contexto: str | None = None,
    ) -> None:
        self.contexto = contexto  # para armar el prompt de la segunda pasada con su aviso
        self.mecanicas = mecanicas or {}  # clave -> señal fija que saltó en su título o frase
        self.reloj = reloj
        self.presupuesto_lectura = presupuesto_lectura
        self.max_lecturas_fallidas = max_lecturas_fallidas
        self.lectura_gastada = 0.0  # segundos de pared gastados leyendo artículos
        self.lecturas_fallidas = 0  # lecturas fallidas seguidas
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
            ok = {
                alias[a]: dataclasses.replace(an, clave=alias[a], sospecha_mecanica=alias[a] in self.mecanicas)
                for a, an in validacion.analizadas.items()
            }
            fallas = {alias[a]: motivo for a, motivo in validacion.fallas.items()}
        self.seguidas = 0 if ok else self.seguidas + 1
        if self.seguidas >= self.max_fallas_seguidas:
            ultima = next(iter(fallas.values()), "sin motivo")
            r.abortada = f"{self.seguidas} llamadas seguidas sin analizar nada; última falla: {ultima}"
        return ok, fallas

    def motivo_sin_lectura(self) -> str | None:
        """Por qué ya no se leen artículos (presupuesto de pared o fallas seguidas), o None si se puede seguir."""
        if self.lectura_gastada >= self.presupuesto_lectura:
            return MOTIVO_PRESUPUESTO
        if self.lecturas_fallidas >= self.max_lecturas_fallidas:
            return f"no leído: se cortó la lectura de artículos tras {self.lecturas_fallidas} lecturas fallidas seguidas"
        return None

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

        # Aislamiento: una sospechosa en el lote contamina el análisis de las otras. Es sospechosa la que
        # marcó el modelo y la que dispara la señal fija, aunque el modelo no la marque (o no la haya analizado).
        otras: list[str] = []
        sospechosas = {p["clave"] for p in items if p["clave"] in self.mecanicas}
        sospechosas |= {k for k, a in ok.items() if a.sospecha_de_instrucciones}
        if len(items) > 1 and sospechosas:
            otras = [k for k in ok if k not in sospechosas]
            for k in otras:
                r.del_lote[k] = ok[k]  # el afinado lo guarda: se ve si la trampa inclinó a las vecinas
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
            elif actual.sospecha_de_instrucciones or actual.sospecha_mecanica:
                info[clave] = InfoArticulo(
                    "no_leido", "sospecha de instrucciones en el título o la frase: no se lee más de esa fuente"
                )
            elif r.abortada:
                info[clave] = InfoArticulo("no_leido", f"corrida cortada ({r.abortada})")
            elif corrida.unidades >= corrida.tope:
                info[clave] = InfoArticulo("no_leido", MOTIVO_TOPE)
                r.sin_segunda_por_tope += 1
            elif corrida.motivo_sin_lectura() is not None:
                info[clave] = InfoArticulo("no_leido", corrida.motivo_sin_lectura())
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
    inicio = corrida.reloj()
    articulo = _leer(lector, nov)
    corrida.lectura_gastada += max(0.0, corrida.reloj() - inicio)
    if not articulo.legible:
        info[clave] = InfoArticulo(articulo.estado, articulo.motivo or "sin texto", False)
        if articulo.estado in (ARTICULO_ERROR, ARTICULO_SIN_TEXTO):  # no_aplica y host_distinto no son lecturas que fallaron
            corrida.lecturas_fallidas += 1
        return
    corrida.lecturas_fallidas = 0
    payload = prompt.payload_articulo(nov, articulo.texto, articulo.recortado)
    sistema = corrida.sistema_articulo
    hallazgo = senal.detectar_frase(payload["texto_del_articulo"], articulo.texto)
    if hallazgo is not None:
        # El artículo trae frases con forma de orden para un modelo: puede citarlas (un artículo sobre inyección de
        # instrucciones) o ser un intento. Se usa igual, como dato, de a una y con el aviso en el prompt; queda marcado.
        r.analizadas[clave] = dataclasses.replace(r.analizadas[clave], sospecha_mecanica=True)
        if corrida.contexto is not None:
            sistema = prompt.construir_sistema(corrida.contexto, True, hallazgo[1])
    corrida.unidades += 1
    r.segundas += 1
    ok, fallas = corrida._llamar([payload], sistema)
    nuevo = ok.get(clave)
    detectadas = hallazgo is not None
    if nuevo is not None:
        r.analizadas[clave] = dataclasses.replace(
            nuevo,
            pasada=2,
            articulo=InfoArticulo("leido", None, articulo.recortado, detectadas),
            sospecha_mecanica=nuevo.sospecha_mecanica or detectadas,
        )
    else:
        r.segundas_fallidas += 1
        motivo = f"la segunda pasada falló ({fallas.get(clave, 'sin motivo')}); queda el análisis de la primera"
        info[clave] = InfoArticulo("leido", motivo, articulo.recortado, detectadas)


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
    reloj: Callable[[], float] = time.monotonic,
    presupuesto_lectura: float = PRESUPUESTO_LECTURA_S,
    max_lecturas_fallidas: int = MAX_LECTURAS_FALLIDAS,
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
    mecanicas: dict[str, str] = {}
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
        payload = prompt.payload_novedad(nov)
        items.append(payload)
        por_clave[clave] = nov
        encontrada = senal.detectar(payload["titulo"], payload["frase"], nov.titulo, nov.frase)
        if encontrada is not None:
            mecanicas[clave] = encontrada
    corrida = _Corrida(
        runner,
        sistema,
        tope,
        max_fallas_seguidas,
        sistema_articulo,
        mecanicas,
        reloj,
        presupuesto_lectura,
        max_lecturas_fallidas,
        texto_contexto,
    )
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
