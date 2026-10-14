"""El lector: baja cada fuente, la analiza y arma las novedades de la ventana y la cobertura."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime

from . import analizar
from .limpieza import UTC, limpiar_texto
from .modelos import Cobertura, ErrorDescarga, Extraccion, Novedad


def _analizar(fuente, texto: str, ahora: datetime) -> Extraccion:
    if fuente.tipo == "atom":
        return analizar.analizar_atom(fuente, texto, ahora)
    if fuente.tipo == "rss":
        return analizar.analizar_rss(fuente, texto, ahora)
    return fuente.analizador(fuente, texto, ahora)


def _motivo(exc: Exception) -> str:
    return limpiar_texto(f"{type(exc).__name__}: {exc}", 300)


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
            if not isinstance(texto, str):
                raise ErrorDescarga("el fetcher no devolvió texto")
            extraccion = _analizar(fuente, texto, ahora)
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
