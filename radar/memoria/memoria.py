"""La memoria de lo ya visto: un JSON con candado entre procesos y guardado atómico.

Formato: {"version": 1, "vistas": {clave: {"fuente", "titulo", "enlace", "primera_vez"}}}

Garantías:
- un archivo roto, de versión desconocida o de forma inesperada nunca se resetea ni se pisa:
  se levanta `MemoriaCorrupta` con la ruta y el motivo;
- el candado (`<ruta>.lock`, creación exclusiva) está en disco, así que vale entre procesos;
  no se roba solo: si quedó de una corrida caída, el mensaje dice qué archivo borrar;
- `guardar` escribe a un temporal único en el mismo directorio, hace flush y fsync y
  reemplaza de una vez: ante cualquier falla el archivo anterior queda como estaba.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

VERSION = 1
RUTA_POR_DEFECTO = Path("generated") / "memoria.json"
TOPE_ARCHIVO = 64 * 1024 * 1024
_CAMPOS = ("fuente", "titulo", "enlace", "primera_vez")


class MemoriaError(Exception):
    """Base de los errores propios de la memoria."""


class MemoriaOcupada(MemoriaError):
    """Otra corrida tiene el candado de la memoria."""


class MemoriaCorrupta(MemoriaError):
    """El archivo de la memoria no se puede leer o no tiene la forma esperada."""


def _validar(datos, ruta: Path) -> dict[str, dict[str, str]]:
    def corrupta(motivo: str) -> MemoriaCorrupta:
        return MemoriaCorrupta(
            f"La memoria {ruta} está corrupta: {motivo}. No se tocó; revisala o movela a mano."
        )

    if not isinstance(datos, dict) or set(datos) != {"version", "vistas"}:
        raise corrupta('se esperaba un objeto con exactamente "version" y "vistas"')
    version = datos["version"]
    if type(version) is not int or version != VERSION:
        raise corrupta(f"versión desconocida: {version!r} (se entiende la {VERSION})")
    vistas = datos["vistas"]
    if not isinstance(vistas, dict):
        raise corrupta('"vistas" no es un objeto')
    for clave, entrada in vistas.items():
        if not isinstance(entrada, dict) or set(entrada) != set(_CAMPOS):
            raise corrupta(f"la entrada {clave!r} no tiene exactamente los campos {list(_CAMPOS)}")
        if not all(isinstance(entrada[c], str) for c in _CAMPOS):
            raise corrupta(f"la entrada {clave!r} tiene un campo que no es texto")
        try:
            datetime.fromisoformat(entrada["primera_vez"])
        except ValueError:
            raise corrupta(f"la entrada {clave!r} tiene una fecha ilegible en primera_vez") from None
    return vistas


class Memoria:
    """Usar siempre como `with Memoria.abrir(ruta) as m:`."""

    def __init__(self, ruta: Path, vistas: dict[str, dict[str, str]], candado: Path) -> None:
        self.ruta = ruta
        self._vistas = vistas
        self._candado = candado
        self._abierta = True

    # ---------------------------------------------------------------- abrir y cerrar

    @classmethod
    def abrir(cls, ruta=RUTA_POR_DEFECTO) -> "Memoria":
        ruta = Path(ruta)
        candado = ruta.with_name(ruta.name + ".lock")
        ruta.parent.mkdir(parents=True, exist_ok=True)
        fd = cls._tomar_candado(ruta, candado)
        try:
            try:
                os.write(fd, f"pid={os.getpid()}\n".encode())
            finally:
                os.close(fd)
            return cls(ruta, cls._cargar(ruta), candado)
        except BaseException:
            cls._soltar(candado)  # sólo el candado que acabamos de crear nosotros
            raise

    @staticmethod
    def _tomar_candado(ruta: Path, candado: Path) -> int:
        """Crea `<ruta>.lock` con O_EXCL. Si ya existe, `MemoriaOcupada`."""
        intentos = 20 if os.name == "nt" else 1
        for intento in range(intentos):
            try:
                return os.open(candado, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                break
            except PermissionError:
                # En Windows, crear un archivo cuyo borrado está pendiente (otra corrida lo está
                # soltando justo ahora) da "acceso denegado" en vez de "ya existe": se reintenta un
                # momento; un permiso realmente negado sigue negado y sale como el error que es.
                if intento + 1 < intentos:
                    time.sleep(0.005)
                    continue
                if not os.path.lexists(candado):
                    raise
                break
        raise MemoriaOcupada(
            f"La memoria {ruta} está en uso: existe el candado {candado}. Si no hay otra corrida "
            f"andando, quedó de una corrida caída: borrá el archivo {candado} y reintentá."
        )

    @staticmethod
    def _cargar(ruta: Path) -> dict[str, dict[str, str]]:
        try:
            tamano = ruta.stat().st_size
        except FileNotFoundError:
            return {}
        if tamano > TOPE_ARCHIVO:
            raise MemoriaCorrupta(f"La memoria {ruta} está corrupta: pesa más de {TOPE_ARCHIVO} bytes. No se tocó.")
        try:
            datos = json.loads(ruta.read_bytes().decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise MemoriaCorrupta(
                f"La memoria {ruta} está corrupta: no es JSON legible ({type(exc).__name__}). No se tocó."
            ) from exc
        return _validar(datos, ruta)

    @staticmethod
    def _soltar(candado: Path) -> None:
        try:
            os.unlink(candado)
        except FileNotFoundError:
            pass

    def cerrar(self) -> None:
        if self._abierta:
            self._abierta = False
            self._soltar(self._candado)

    def __enter__(self) -> "Memoria":
        return self

    def __exit__(self, *_exc) -> None:
        self.cerrar()

    # ------------------------------------------------------------------------- uso

    def __len__(self) -> int:
        return len(self._vistas)

    def __contains__(self, clave: str) -> bool:
        return clave in self._vistas

    def nuevas(self, novedades: Iterable) -> list:
        """Las novedades que no están vistas, sin repetidas dentro de la misma tanda."""
        resultado, en_tanda = [], set()
        for n in novedades:
            if n.clave in self._vistas or n.clave in en_tanda:
                continue
            en_tanda.add(n.clave)
            resultado.append(n)
        return resultado

    def marcar(self, novedades: Iterable, cuando: datetime) -> int:
        """Marca como vistas las novedades (en memoria; `guardar` las escribe). Devuelve cuántas se agregaron."""
        if cuando.tzinfo is None:
            raise ValueError("`cuando` necesita zona horaria")
        instante = cuando.astimezone(timezone.utc).isoformat()
        agregadas = 0
        for n in novedades:
            if n.clave in self._vistas:
                continue
            self._vistas[n.clave] = {
                "fuente": n.fuente,
                "titulo": n.titulo,
                "enlace": n.enlace,
                "primera_vez": instante,
            }
            agregadas += 1
        return agregadas

    def guardar(self) -> None:
        """Escritura atómica: temporal único en el mismo directorio, flush, fsync y os.replace."""
        if not self._abierta:
            raise MemoriaError("la memoria ya está cerrada")
        contenido = json.dumps(
            {"version": VERSION, "vistas": self._vistas}, ensure_ascii=False, indent=1, sort_keys=True
        ).encode("utf-8", "replace") + b"\n"
        directorio = self.ruta.parent
        fd, temporal = tempfile.mkstemp(dir=directorio, prefix=self.ruta.name + ".", suffix=".tmp")
        try:
            try:
                archivo = os.fdopen(fd, "wb")
            except BaseException:
                os.close(fd)  # si fdopen falla, el descriptor sigue siendo nuestro
                raise
            with archivo:
                archivo.write(contenido)
                archivo.flush()
                os.fsync(archivo.fileno())
            os.replace(temporal, self.ruta)
        except BaseException:
            try:
                os.unlink(temporal)
            except FileNotFoundError:
                pass
            raise
        self._sincronizar_directorio(directorio)

    @staticmethod
    def _sincronizar_directorio(directorio: Path) -> None:
        """Mejor esfuerzo: el reemplazo ya se hizo, un error acá no debe hacerlo pasar por fallido."""
        if os.name != "posix":
            return
        try:
            fd = os.open(directorio, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass
