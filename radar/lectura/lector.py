"""El lector: baja cada fuente, la analiza y arma las novedades de la ventana y la cobertura."""

from __future__ import annotations

import os
import traceback
from collections.abc import Callable, Iterable
from datetime import date, datetime, time, timedelta

from .limpieza import UTC, limpiar_texto
from .modelos import Cobertura, ErrorDescarga, ErrorLectura, Novedad

DIAS_POR_DEFECTO = 28


def ventana(
    desde_dia: date | None = None,
    hasta_dia: date | None = None,
    ahora: datetime | None = None,
    dias: int = DIAS_POR_DEFECTO,
) -> tuple[datetime, datetime]:
    """La ventana de lectura (desde, hasta) en UTC, ambos extremos inclusive.

    `hasta_dia` es un día entero y es inclusivo: llega hasta el último microsegundo de ese día.
    Sin `hasta_dia`, la ventana termina en `ahora`. `desde_dia` empieza a medianoche UTC; sin
    él, empieza `dias` días antes del final.
    """
    ahora = ahora or datetime.now(UTC)
    if ahora.tzinfo is None:
        raise ValueError("`ahora` necesita zona horaria")
    if hasta_dia is not None:
        hasta = datetime.combine(hasta_dia, time.max, tzinfo=UTC)
    else:
        hasta = ahora.astimezone(UTC)
    if desde_dia is not None:
        desde = datetime.combine(desde_dia, time.min, tzinfo=UTC)
    else:
        desde = hasta - timedelta(days=dias)
    if desde > hasta:
        raise ValueError("la ventana empieza después de terminar")
    return desde, hasta


def _motivo(exc: Exception) -> str:
    """Un error esperado (ErrorLectura) se explica solo; uno inesperado lleva su tipo y de dónde salió."""
    if isinstance(exc, ErrorLectura):
        texto = f"{type(exc).__name__}: {exc}"
    else:
        cuadros = traceback.extract_tb(exc.__traceback__)
        donde = f"{os.path.basename(cuadros[-1].filename)}:{cuadros[-1].lineno}" if cuadros else "?"
        texto = f"{type(exc).__name__}: {exc} (inesperado, en {donde})"
    return limpiar_texto(texto, 300)


def leer(
    fuentes: Iterable,
    desde: datetime,
    hasta: datetime,
    fetcher: Callable[[str], str],
    ahora: datetime | None = None,
) -> tuple[list[Novedad], dict[str, Cobertura]]:
    """Lee las fuentes. Devuelve (novedades en la ventana, cobertura por id de fuente).

    La ventana es [desde, hasta], ambos inclusive y con zona horaria. Las novedades sin
    fecha entran (no hay cómo excluirlas) y la cobertura las cuenta en `sin_fecha`.
    Una fuente que falla queda en `error` y no frena a las demás.
    """
    if desde.tzinfo is None or hasta.tzinfo is None:
        raise ValueError("la ventana necesita fechas con zona horaria")
    if desde > hasta:
        raise ValueError("la ventana empieza después de terminar")
    ahora = ahora or datetime.now(UTC)
    novedades: list[Novedad] = []
    cobertura: dict[str, Cobertura] = {}
    for fuente in fuentes:
        try:
            texto = fetcher(fuente.url)
            if not isinstance(texto, str):  # el único chequeo de tipo: los analizadores reciben texto
                raise ErrorDescarga("el fetcher no devolvió texto")
            extraccion = fuente.analizador(fuente, texto, ahora)
        except Exception as exc:  # noqa: BLE001 - frontera por fuente: una que falla no frena a las demás
            cobertura[fuente.id] = Cobertura(estado="error", motivo=_motivo(exc))
            continue
        fechas = [n.fecha for n in extraccion.novedades if n.fecha is not None]
        en_ventana = [n for n in extraccion.novedades if n.fecha is None or desde <= n.fecha <= hasta]
        novedades.extend(en_ventana)
        estado, motivo = "ok", None
        if not extraccion.novedades:
            estado = "vacia"
            motivo = "respondió pero no se sacó ninguna novedad (¿cambió el diseño de la página?)"
            if extraccion.descartadas:
                motivo += f"; {extraccion.descartadas} descartadas"
        cobertura[fuente.id] = Cobertura(
            estado=estado,
            motivo=motivo,
            en_fuente=len(extraccion.novedades),
            en_ventana=len(en_ventana),
            sin_fecha=sum(1 for n in en_ventana if n.fecha is None),
            descartadas=extraccion.descartadas,
            recortadas=extraccion.recortadas,
            cubre_desde=min(fechas) if fechas else None,
        )
    return novedades, cobertura
