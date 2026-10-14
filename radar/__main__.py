"""`python -m radar [--desde AAAA-MM-DD] [--hasta AAAA-MM-DD] [--sin-articulos] [--carpeta <dir>] [--contexto <ruta>]`
o `python -m radar --idea "<texto>"`

El radar entero: lee las fuentes, descarta lo ya visto, analiza lo nuevo con Claude (con la cuota del owner) y
escribe la página del resumen y su JSON compañero. Con `--idea` analiza una idea propia y escribe su página, sin
tocar la memoria.

Orden (no se cambia): abrir la memoria (si está ocupada, se corta antes de bajar nada) -> leer las fuentes ->
descartar lo ya visto -> analizar -> armar y escribir la página y el JSON -> RECIÉN ENTONCES marcar como visto lo
analizado -> guardar la memoria. Así una página que no se pudo escribir no esconde novedades: todo lo que no
quedó en una página vuelve a salir como nuevo.

Código de salida: 0 todo analizado; 2 alguna novedad quedó sin analizar; 1 error, corrida cortada, fuentes
ilegibles o memoria sin guardar. (Un argumento mal puesto lo corta `argparse` con 2, como en todo programa.)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from radar.analisis import ErrorAnalisis, Resultado, analizar, analizar_idea, cliente
from radar.analisis.prompt import RAIZ, cargar_contexto
from radar.lectura.articulo import leer_articulo
from radar.lectura.descarga import descargar
from radar.lectura.fuentes import FUENTES
from radar.lectura.lector import DIAS_POR_DEFECTO, leer, ventana
from radar.memoria import RUTA_POR_DEFECTO, Memoria, MemoriaError
from radar.pagina import Corrida, escribir_resumen
from radar.pagina.vista import construir

CARPETA_POR_DEFECTO = RAIZ / "generated" / "resumenes"
_FECHA = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


def _fecha(valor: str) -> date:
    if not _FECHA.fullmatch(valor):
        raise argparse.ArgumentTypeError(f"{valor!r} no es una fecha AAAA-MM-DD")
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{valor!r} no es una fecha válida") from exc


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _exigir_claude() -> None:
    """Con el claude real, lo que haría fallar a la primera llamada (clave de API en el entorno, claude que no está)
    se comprueba antes de bajar nada: `ClaudeNoDisponible`, que es un `ErrorAnalisis`."""
    cliente.exigir_suscripcion(os.environ)
    cliente.localizar_claude(os.environ)


def _publicar(corrida: Corrida, carpeta: Path, prefijo: str, nuevas: list, salida):
    """Arma y escribe la página y el JSON. Devuelve (página, ruta de la página, ruta del JSON) o None si falló.

    Si falla (o se corta con Ctrl+C) no queda nada a medias (lo garantiza `escribir_resumen`) y lo analizado, que
    costó cuota, se vuelca completo a la salida para no perderlo. Quien llama no marca nada como visto.
    """
    try:
        pagina = construir(corrida)
        ruta_html, ruta_json = escribir_resumen(carpeta, corrida.generado, pagina.html, pagina.json, prefijo)
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - frontera: lo analizado no se tira por un fallo al escribir
        salida.write(
            f"NO SE PUDO ESCRIBIR la página ({type(exc).__name__}: {exc}). No se marcó nada como visto; "
            "el resultado completo va acá abajo.\n"
        )
        volcado = {"novedades": [n.a_dict() for n in nuevas], **corrida.resultado.a_dict()}
        salida.write(json.dumps(volcado, ensure_ascii=False, indent=2) + "\n")
        return None
    return pagina, ruta_html, ruta_json


def _informar(pagina, ruta_html: Path, ruta_json: Path, resultado: Resultado, corrida: Corrida, salida) -> None:
    c = pagina.conteos
    salida.write(f"Resumen: {ruta_html}\n")
    salida.write(f"Para abrirlo en el navegador: {ruta_html.resolve().as_uri()}\n")
    salida.write(f"Datos para la sesión de INGOL: {ruta_json}\n")
    if corrida.idea is None:
        salida.write(f"Leídas: {c['leidas']}  nuevas: {c['nuevas']}  ")
    salida.write(
        f"sirven: {c['sirven']}  tal vez: {c['tal_vez']}  ruido: {c['ruido']}  sin analizar: {c['sin_analizar']}\n"
    )
    if resultado.abortada:
        salida.write(f"LA CORRIDA SE CORTÓ: {resultado.abortada}. Lo que no se analizó vuelve a salir como nuevo.\n")
    mudas = [corrida.fuentes.get(fid, fid) for fid, cob in corrida.cobertura.items() if cob.estado != "ok"]
    if mudas:
        salida.write(f"Fuentes que no trajeron nada o fallaron: {', '.join(mudas)}. Mirá la tabla de cobertura de la página.\n")


def _codigo(resultado: Resultado, pagina, corrida: Corrida) -> int:
    if resultado.abortada:
        return 1
    if corrida.cobertura and all(c.estado == "error" for c in corrida.cobertura.values()):
        return 1  # ninguna fuente se pudo leer: no es que no haya novedades
    return 2 if pagina.conteos["sin_analizar"] else 0


def _correr_idea(args, texto_contexto: str, runner, carpeta: Path, ahora, salida) -> int:
    resultado = analizar_idea(args.idea, runner=runner, contexto=texto_contexto)
    corrida = Corrida(generado=ahora(), resultado=resultado, idea=args.idea)
    publicada = _publicar(corrida, carpeta, "idea", [], salida)
    if publicada is None:
        return 1
    pagina, ruta_html, ruta_json = publicada
    _informar(pagina, ruta_html, ruta_json, resultado, corrida, salida)
    return _codigo(resultado, pagina, corrida)


def _correr_resumen(args, desde, hasta, inicio, texto_contexto, runner, fetcher, fuentes, ruta_memoria, carpeta, ahora, salida) -> int:
    with Memoria.abrir(ruta_memoria) as memoria:  # ocupada o rota: se corta acá, antes de bajar nada
        novedades, cobertura = leer(fuentes, desde, hasta, fetcher, ahora=inicio)
        nuevas = memoria.nuevas(novedades)
        if nuevas:
            lector = None if args.sin_articulos else (lambda nov: leer_articulo(nov, fetcher))
            resultado = analizar(nuevas, runner=runner, contexto=texto_contexto, lector_articulo=lector)
        else:  # nada que analizar: no se llama a claude
            resultado = Resultado()
        corrida = Corrida(
            generado=ahora(),
            resultado=resultado,
            nuevas=nuevas,
            desde=desde,
            hasta=hasta,
            leidas=len(novedades),
            cobertura=cobertura,
            fuentes={f.id: f.nombre for f in fuentes},
            sin_articulos=args.sin_articulos,
        )
        publicada = _publicar(corrida, carpeta, "resumen", nuevas, salida)
        if publicada is None:
            return 1
        pagina, ruta_html, ruta_json = publicada
        _informar(pagina, ruta_html, ruta_json, resultado, corrida, salida)
        codigo = _codigo(resultado, pagina, corrida)
        # Con la página ya en disco: lo analizado (y sólo eso) se marca como visto.
        analizadas = [n for n in nuevas if n.clave in resultado.analizadas]
        if analizadas:
            try:
                memoria.marcar(analizadas, corrida.generado)
                memoria.guardar(corrida.generado)
            except (MemoriaError, OSError) as exc:
                salida.write(
                    f"NO SE PUDO GUARDAR la memoria ({type(exc).__name__}: {exc}). La página está escrita, pero "
                    "estas novedades van a volver a salir como nuevas en la próxima corrida.\n"
                )
                return 1
        return codigo


def main(
    argv=None,
    runner=cliente.ejecutar,
    contexto: str | None = None,
    salida=None,
    ahora=_ahora,
    fetcher=descargar,
    fuentes=FUENTES,
    ruta_memoria: Path = RUTA_POR_DEFECTO,
    carpeta: Path | None = None,
) -> int:
    salida = salida or sys.stdout
    fuentes = tuple(fuentes)
    parser = argparse.ArgumentParser(
        prog="python -m radar",
        description="Lee las fuentes, analiza las novedades con Claude y escribe la página del resumen.",
    )
    parser.add_argument("--desde", type=_fecha, help=f"primer día de la ventana (por defecto, hace {DIAS_POR_DEFECTO} días)")
    parser.add_argument("--hasta", type=_fecha, help="último día de la ventana, inclusive (por defecto, ahora)")
    parser.add_argument("--sin-articulos", action="store_true", help="no lee los artículos: sólo la primera pasada (más rápido)")
    parser.add_argument("--carpeta", type=Path, help="dónde dejar la página y su JSON (por defecto, generated/resumenes/)")
    parser.add_argument("--contexto", type=Path, help="ruta del contexto de INGOL (por defecto, material/contexto-ingol.md)")
    parser.add_argument("--idea", help="analiza una idea propia y escribe su página; no usa ni cambia la memoria")
    args = parser.parse_args(argv)
    if args.idea is not None and (args.desde is not None or args.hasta is not None or args.sin_articulos):
        parser.error("--desde, --hasta y --sin-articulos son para el resumen, no para --idea")
    inicio = ahora()
    desde = hasta = None
    if args.idea is None:
        try:
            desde, hasta = ventana(args.desde, args.hasta, inicio)
        except ValueError:
            parser.error("--desde es posterior a --hasta")
    destino = args.carpeta or carpeta or CARPETA_POR_DEFECTO

    try:
        texto_contexto = contexto if contexto is not None else cargar_contexto(args.contexto)  # falla antes de gastar
        if runner is cliente.ejecutar:
            _exigir_claude()
        if args.idea is not None:
            return _correr_idea(args, texto_contexto, runner, destino, ahora, salida)
        return _correr_resumen(
            args, desde, hasta, inicio, texto_contexto, runner, fetcher, fuentes, ruta_memoria, destino, ahora, salida
        )
    except (ErrorAnalisis, MemoriaError) as exc:
        salida.write(f"Error: {exc}\n")
        return 1
    except KeyboardInterrupt:
        salida.write(
            "Interrumpido con Ctrl+C antes de terminar. Lo que no llegó a guardarse vuelve a salir como nuevo "
            "en la próxima corrida.\n"
        )
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    sys.exit(main())
