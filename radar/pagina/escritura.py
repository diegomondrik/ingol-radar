"""Escribir la página y su JSON sin pisar nada y sin dejar nada a medias.

- El nombre lleva la fecha y la hora y se reserva con creación exclusiva (`O_EXCL`): dos corridas en el
  mismo segundo no eligen el mismo nombre (la segunda recibe `-2`, `-3`...). Nunca se pisa un archivo.
- Cada archivo se escribe a un temporal de nombre único en la misma carpeta, con flush y fsync, y se
  reemplaza de una vez: el destino queda entero o como estaba.
- Primero el JSON y al final la página: la página es el punto de confirmación (el owner abre ésa). Si
  cualquier paso falla, se borra lo reservado y lo escrito de esta corrida y se vuelve a lanzar el error:
  no queda una página sin su JSON ni un archivo vacío. Sólo si esta función vuelve sin error, quien la
  llama puede marcar la memoria.
- Limitación conocida (a propósito): los nombres se reservan con archivos vacíos. Si se corta la luz entre la
  reserva y el reemplazo puede quedar un `.html` o un `.json` vacío; quien lea el JSON tiene que ignorar uno vacío.
- `escribir_volcado` guarda aparte, con el mismo cuidado, lo analizado cuando no se pudo escribir la página.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path

_BINARIO = getattr(os, "O_BINARY", 0)
MAX_INTENTOS = 1000


def _crear_exclusivo(ruta: Path) -> None:
    os.close(os.open(ruta, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _BINARIO, 0o644))


def _reservar(carpeta: Path, base: str, extensiones: tuple[str, ...]) -> list[Path]:
    """Reserva `<base><ext>` para cada extensión (o con `-2`, `-3`...) creándolos vacíos y de forma exclusiva.

    Todo o nada: si una extensión ya existe con ese sufijo, se borran las que se habían reservado y se prueba el
    sufijo siguiente.
    """
    for n in range(1, MAX_INTENTOS + 1):
        sufijo = "" if n == 1 else f"-{n}"
        reservadas: list[Path] = []
        try:
            for extension in extensiones:
                ruta = carpeta / f"{base}{sufijo}{extension}"
                _crear_exclusivo(ruta)
                reservadas.append(ruta)
        except FileExistsError:
            for ruta in reservadas:
                _borrar(ruta)
            continue
        except BaseException:
            for ruta in reservadas:
                _borrar(ruta)
            raise
        return reservadas
    raise OSError(f"no encontré un nombre libre para {base} en {carpeta}")


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
    carpeta: Path,
    ahora: datetime,
    pagina: str,
    datos_json: str | Callable[[str], str],
    prefijo: str = "resumen",
) -> tuple[Path, Path]:
    """Escribe la página y su JSON en `carpeta`. Devuelve (ruta de la página, ruta del JSON).

    `datos_json` es el texto del JSON o una función que lo arma a partir del nombre del `.html` reservado (el JSON
    dice qué página lo acompaña, y ese nombre se conoce recién al reservar).
    """
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    marca = ahora.astimezone(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    ruta_html, ruta_json = _reservar(carpeta, f"{prefijo}-{marca}", (".html", ".json"))
    try:
        _reemplazar(ruta_json, datos_json(ruta_html.name) if callable(datos_json) else datos_json)
        _reemplazar(ruta_html, pagina)
    except BaseException:
        _borrar(ruta_json)
        _borrar(ruta_html)
        raise
    return ruta_html, ruta_json


def escribir_volcado(carpetas: Iterable[Path], ahora: datetime, texto: str) -> Path:
    """Guarda un volcado de emergencia en la primera de `carpetas` donde se pueda. Devuelve su ruta.

    Mismo cuidado que el resumen: nombre `volcado-<marca>.json` reservado con creación exclusiva (nunca pisa) y
    escritura atómica. Si no se puede en ninguna carpeta, `OSError` con el último motivo.
    """
    marca = ahora.astimezone(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    ultimo: OSError | None = None
    for carpeta in map(Path, carpetas):
        try:
            carpeta.mkdir(parents=True, exist_ok=True)
            (ruta,) = _reservar(carpeta, f"volcado-{marca}", (".json",))
        except OSError as exc:
            ultimo = exc
            continue
        try:
            _reemplazar(ruta, texto)
        except BaseException as exc:
            _borrar(ruta)
            if not isinstance(exc, OSError):
                raise
            ultimo = exc
            continue
        return ruta
    raise ultimo if ultimo is not None else OSError("no hay carpeta donde guardar el volcado")
