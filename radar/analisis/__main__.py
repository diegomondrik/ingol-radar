"""`python -m radar.analisis --afinado <json> [--salida <ruta>]` o `--idea "<texto>"`

Corre el análisis REAL (claude, con la cuota del owner) sobre las novedades de un archivo de afinado
o sobre una idea, y muestra un resumen. Del archivo usa sólo los campos de la novedad: `esperado`
y lo demás no viaja al modelo. No toca la memoria ni arma la página (eso es del trabajo 4).

Código de salida: 0 todo analizado; 2 alguna novedad quedó sin analizar; 1 error o corrida cortada.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from radar.lectura.limpieza import LARGO_FRASE, LARGO_TITULO, fecha_de_numeros, limpiar_texto, validar_enlace
from radar.lectura.modelos import Novedad

from . import analizador, cliente
from .errores import ErrorAnalisis
from .modelos import Resultado
from .prompt import RAIZ, cargar_contexto

SALIDA_POR_DEFECTO = RAIZ / "generated" / "afinado"
MAX_ARCHIVO = 2_000_000


class ErrorAfinado(ErrorAnalisis):
    """El archivo de afinado no tiene la forma esperada."""


def _fecha(valor) -> datetime | None:
    if valor in (None, ""):
        return None
    if isinstance(valor, str) and len(valor) >= 10 and valor[4] == "-" and valor[7] == "-":
        fecha = fecha_de_numeros(valor[0:4], valor[5:7], valor[8:10])  # None si no es una fecha
        if fecha is not None:
            return fecha
    raise ErrorAfinado(f"fecha que no entiendo: {str(valor)[:40]!r}")


def cargar_afinado(ruta: Path) -> tuple[list[Novedad], dict[str, dict]]:
    """Las novedades del archivo (sólo sus campos de novedad, limpios) y, aparte, lo esperado."""
    try:
        if ruta.stat().st_size > MAX_ARCHIVO:
            raise ErrorAfinado(f"{ruta} pesa más de {MAX_ARCHIVO} bytes")
        datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise ErrorAfinado(f"no existe el archivo de afinado {ruta}") from None
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ErrorAfinado(f"no se pudo leer {ruta}: {type(exc).__name__}") from None
    lista = datos.get("novedades") if isinstance(datos, dict) else None
    if not isinstance(lista, list) or not lista:
        raise ErrorAfinado("el archivo no trae una lista 'novedades'")
    novedades: list[Novedad] = []
    esperados: dict[str, dict] = {}
    for crudo in lista:
        if not isinstance(crudo, dict) or not isinstance(crudo.get("clave"), str) or not crudo["clave"]:
            raise ErrorAfinado("una novedad del archivo no trae 'clave'")
        titulo = limpiar_texto(crudo.get("titulo"), LARGO_TITULO)
        if not titulo:
            raise ErrorAfinado(f"la novedad {crudo['clave'][:40]!r} no tiene título utilizable")
        novedades.append(
            Novedad(
                fuente=limpiar_texto(crudo.get("fuente"), 80),
                clave=crudo["clave"],
                titulo=titulo,
                enlace=validar_enlace(crudo.get("enlace")) or "",
                fecha=_fecha(crudo.get("fecha")),
                frase=limpiar_texto(crudo.get("frase"), LARGO_FRASE),
            )
        )
        esperados[crudo["clave"]] = {"esperado": crudo.get("esperado"), "aceptable": crudo.get("aceptable")}
    return novedades, esperados


def _destino_nuevo(carpeta: Path, ahora: datetime) -> Path:
    """Un `analisis-<marca>.json` que no existe: la marca llega a los microsegundos y, si aun así existe
    (dos corridas en el mismo instante), se agrega `-2`, `-3`... Se reserva con creación exclusiva para que
    dos procesos no elijan el mismo nombre."""
    carpeta.mkdir(parents=True, exist_ok=True)
    marca = ahora.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S") + f"{ahora.microsecond:06d}Z"
    for n in range(1, 1000):
        ruta = carpeta / (f"analisis-{marca}.json" if n == 1 else f"analisis-{marca}-{n}.json")
        try:
            os.close(os.open(ruta, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            continue
        return ruta
    raise OSError(f"no encontré un nombre libre en {carpeta}")


def _escribir_atomico(ruta: Path, datos: dict) -> None:
    """Escribe a un temporal de nombre único y lo reemplaza de una vez: el destino queda entero o como estaba."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=ruta.name + ".", suffix=".tmp", dir=ruta.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, ruta)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _resumen(resultado: Resultado, segundos: float, esperados: dict[str, dict], salida) -> None:
    c = resultado.contadores()
    salida.write(
        f"Analizadas: {len(resultado.analizadas)}  Sin analizar: {len(resultado.sin_analizar)}  "
        f"Tiempo: {segundos:.0f} s\n"
        f"Llamadas: {c['llamadas']}  reintentos: {c['reintentos']}  aisladas: {c['aisladas']}  "
        f"fallidas: {c['fallidas']}  ignoradas: {c['ignoradas']}  por tope: {c['por_tope']}\n"
    )
    if resultado.abortada:
        salida.write(f"CORRIDA CORTADA: {resultado.abortada}\n")
    for clave, a in resultado.analizadas.items():
        linea = f"  {clave:<10} sirve={a.sirve:<7} ruido={'si' if a.ruido else 'no':<2} confianza={a.confianza:<5} base={a.base}"
        if a.sospecha_de_instrucciones:
            linea += "  SOSPECHA_DE_INSTRUCCIONES"
        esp = esperados.get(clave)
        if esp and esp.get("esperado") is not None:
            acept = esp.get("aceptable") or [esp["esperado"]]
            linea += f"  esperado={esp['esperado']} ({'ok' if a.sirve in acept else 'DIFIERE'})"
        salida.write(linea + "\n")
    for clave, motivo in resultado.sin_analizar.items():
        salida.write(f"  {clave:<10} SIN ANALIZAR: {motivo[:200]}\n")


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def main(argv=None, runner=cliente.ejecutar, contexto: str | None = None, salida=None, reloj=time.monotonic, ahora=_ahora) -> int:
    salida = salida or sys.stdout
    parser = argparse.ArgumentParser(
        prog="python -m radar.analisis",
        description="Analiza novedades (o una idea) con Claude, sin herramientas y con respuesta de forma fija.",
    )
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--afinado", type=Path, help="archivo JSON con una lista 'novedades' (como afinado-trabajo-3.json)")
    grupo.add_argument("--idea", help="una idea propia del owner para analizar")
    parser.add_argument("--salida", type=Path, help="dónde escribir el resultado en JSON (por defecto, generated/afinado/)")
    parser.add_argument("--contexto", type=Path, help="ruta del contexto de INGOL (por defecto, material/contexto-ingol.md)")
    args = parser.parse_args(argv)

    try:
        texto_contexto = contexto if contexto is not None else cargar_contexto(args.contexto)
        inicio = reloj()
        if args.afinado is not None:
            novedades, esperados = cargar_afinado(args.afinado)
            resultado = analizador.analizar(novedades, runner=runner, contexto=texto_contexto)
        else:
            esperados = {}
            resultado = analizador.analizar_idea(args.idea, runner=runner, contexto=texto_contexto)
        segundos = reloj() - inicio
    except ErrorAnalisis as exc:
        salida.write(f"Error: {exc}\n")
        return 1

    _resumen(resultado, segundos, esperados, salida)
    datos = resultado.a_dict()
    guardado = True
    destino = args.salida
    reservado = None  # el nombre que reservó `_destino_nuevo`: si no se llega a escribir, no queda un archivo vacío
    try:
        if destino is None and args.afinado is not None:
            destino = reservado = _destino_nuevo(SALIDA_POR_DEFECTO, ahora())
        if destino is not None:
            _escribir_atomico(destino, datos)
            salida.write(f"Resultado escrito en {destino}\n")
    except OSError as exc:
        # Lo analizado costó cuota: si no se puede guardar, se vuelca a la pantalla para no perderlo.
        guardado = False
        if reservado is not None:
            try:
                if reservado.stat().st_size == 0:
                    reservado.unlink()
            except OSError:
                pass
        salida.write(f"NO SE PUDO GUARDAR el resultado ({type(exc).__name__}: {exc}); va completo acá abajo.\n")
        salida.write(json.dumps(datos, ensure_ascii=False, indent=2) + "\n")
    else:
        if destino is None:  # --idea sin --salida
            salida.write(json.dumps(datos, ensure_ascii=False, indent=2) + "\n")
    if resultado.abortada or not guardado:
        return 1
    return 2 if resultado.sin_analizar else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    sys.exit(main())
