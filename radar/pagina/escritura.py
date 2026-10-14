"""Escribir la página y su JSON sin pisar nada y sin dejar nada a medias.

- El nombre lleva la fecha y la hora y se reserva con creación exclusiva (`O_EXCL`): dos corridas en el
  mismo segundo no eligen el mismo nombre (la segunda recibe `-2`, `-3`...). Nunca se pisa un archivo.
- Cada archivo se escribe a un temporal de nombre único en la misma carpeta, con flush y fsync, y se
  reemplaza de una vez: el destino queda entero o como estaba.
- Primero el JSON y al final la página: la página es el punto de confirmación (el owner abre ésa). Si
  cualquier paso falla, se borra lo reservado y lo escrito de esta corrida y se vuelve a lanzar el error:
  no queda una página sin su JSON ni un archivo vacío. Sólo si esta función vuelve sin error, quien la
  llama puede marcar la memoria.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_BINARIO = getattr(os, "O_BINARY", 0)
MAX_INTENTOS = 1000


def _crear_exclusivo(ruta: Path) -> None:
    os.close(os.open(ruta, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _BINARIO, 0o644))


def _reservar(carpeta: Path, base: str) -> tuple[Path, Path]:
    """Reserva `<base>.html` y `<base>.json` (o con `-2`, `-3`...) creándolos vacíos y de forma exclusiva."""
    for n in range(1, MAX_INTENTOS + 1):
        sufijo = "" if n == 1 else f"-{n}"
        ruta_html = carpeta / f"{base}{sufijo}.html"
        ruta_json = carpeta / f"{base}{sufijo}.json"
        try:
            _crear_exclusivo(ruta_html)
        except FileExistsError:
            continue
        try:
            _crear_exclusivo(ruta_json)
        except FileExistsError:
            _borrar(ruta_html)
            continue
        except BaseException:
            _borrar(ruta_html)
            raise
        return ruta_html, ruta_json
    raise OSError(f"no encontré un nombre libre para el resumen en {carpeta}")


def _borrar(ruta: Path) -> None:
    try:
        os.unlink(ruta)
    except OSError:
        pass


def _reemplazar(destino: Path, contenido: str) -> None:
    fd, temporal = tempfile.mkstemp(dir=destino.parent, prefix=destino.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as archivo:
            archivo.write(contenido.encode("utf-8"))
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(temporal, destino)
    except BaseException:
        _borrar(Path(temporal))
        raise


def escribir_resumen(
    carpeta: Path, ahora: datetime, pagina: str, datos_json: str, prefijo: str = "resumen"
) -> tuple[Path, Path]:
    """Escribe la página y su JSON en `carpeta`. Devuelve (ruta de la página, ruta del JSON)."""
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    marca = ahora.astimezone(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    ruta_html, ruta_json = _reservar(carpeta, f"{prefijo}-{marca}")
    try:
        _reemplazar(ruta_json, datos_json)
        _reemplazar(ruta_html, pagina)
    except BaseException:
        _borrar(ruta_json)
        _borrar(ruta_html)
        raise
    return ruta_html, ruta_json
