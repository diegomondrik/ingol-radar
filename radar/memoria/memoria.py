"""La memoria de lo ya visto: un JSON con bloqueo del sistema operativo y guardado atómico.

Formato: {"version": 1, "vistas": {clave: {"fuente", "titulo", "enlace", "primera_vez"}}}

Garantías:
- un archivo roto, de versión desconocida, de forma inesperada o con claves repetidas nunca se
  resetea ni se pisa: se levanta `MemoriaCorrupta` con la ruta y el motivo. Uno que pasa el tope
  de tamaño es otro caso, `MemoriaDemasiadoGrande`: no está roto, hay que podarlo;
- una sola corrida a la vez: `abrir` abre `<ruta>.lock` (lo crea si falta) y le pide al sistema
  operativo un bloqueo exclusivo sobre su byte 0 (Windows: msvcrt.locking; POSIX: fcntl.flock); el
  archivo se mantiene abierto durante todo el `with`. El sistema operativo suelta el bloqueo solo
  cuando el proceso muere (una corrida caída, un corte de luz, un reinicio): no hay candados
  huérfanos y no hay que borrar nada a mano. Si el bloqueo está tomado, `MemoriaOcupada`. El archivo
  `.lock` NO se borra nunca (borrarlo reabriría la carrera: otra corrida podría bloquear un archivo
  nuevo mientras ésta sigue con el viejo); su contenido no importa y la basura que tenga no molesta.
  Dentro de un mismo proceso, además, un registro por ruta evita depender de cómo trate el sistema
  los bloqueos entre descriptores del mismo proceso;
- `guardar` escribe a un temporal único en el mismo directorio, hace flush y fsync y
  reemplaza de una vez: ante cualquier falla el archivo anterior queda como estaba;
- la memoria no crece sin límite: `guardar` poda lo visto hace más de DIAS_DE_MEMORIA días y, si
  aun así pasa TOPE_ARCHIVO, las más viejas hasta que quepa (expuesto en `podadas_por_edad` y
  `podadas_por_tamano`); sólo si ni vacía cabe levanta `MemoriaDemasiadoGrande`.

Limitación conocida: el bloqueo depende de que el sistema de archivos lo soporte. En carpetas
sincronizadas (OneDrive, Dropbox) o en recursos de red (NFS, SMB) el bloqueo puede no valer entre
máquinas distintas, o fallar; si falla se levanta `MemoriaError` con la explicación en vez de
trabajar sin exclusión. La memoria tiene que estar en un disco local de la máquina que corre el radar.
"""

from __future__ import annotations

import errno
import json
import os
import tempfile
import threading
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

if os.name == "nt":
    import msvcrt

    fcntl = None
else:
    import fcntl

    msvcrt = None

VERSION = 1
# Anclada a la raíz del repo (radar/memoria/memoria.py -> raíz), no al directorio desde donde se corre.
RUTA_POR_DEFECTO = Path(__file__).resolve().parents[2] / "generated" / "memoria.json"
TOPE_ARCHIVO = 64 * 1024 * 1024
DIAS_DE_MEMORIA = 365
_ES_WINDOWS = os.name == "nt"
_CAMPOS = ("fuente", "titulo", "enlace", "primera_vez")
# Errores con los que el sistema dice "ese bloqueo ya lo tiene otro" (y no "no puedo bloquear").
_OCUPADO = frozenset(
    {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, errno.EDEADLK, getattr(errno, "EDEADLOCK", errno.EDEADLK)}
)

_EN_USO: set[str] = set()
_CERROJO = threading.Lock()


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _bloquear(fd: int) -> None:
    """Bloqueo exclusivo y sin espera del byte 0. OSError si ya está tomado."""
    if msvcrt is not None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _desbloquear(fd: int) -> None:
    if msvcrt is not None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


class MemoriaError(Exception):
    """Base de los errores propios de la memoria."""


class MemoriaOcupada(MemoriaError):
    """Otra corrida está usando la memoria."""


class MemoriaCorrupta(MemoriaError):
    """El archivo de la memoria no se puede leer o no tiene la forma esperada."""


class MemoriaDemasiadoGrande(MemoriaError):
    """La memoria pasa TOPE_ARCHIVO: no está rota, pero `abrir` no la leería. No se escribió nada."""


def _sin_repetidas(pares) -> dict:
    resultado = {}
    for clave, valor in pares:
        if clave in resultado:
            raise ValueError(f"clave repetida: {clave!r}")
        resultado[clave] = valor
    return resultado


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


def _fecha(entrada: dict) -> datetime:
    primera = datetime.fromisoformat(entrada["primera_vez"])
    return primera.replace(tzinfo=timezone.utc) if primera.tzinfo is None else primera


def _serializar(vistas: dict) -> bytes:
    return json.dumps(
        {"version": VERSION, "vistas": vistas}, ensure_ascii=False, indent=1, sort_keys=True
    ).encode("utf-8", "replace") + b"\n"


def _peso(clave: str, entrada: dict) -> int:
    """Cuántos bytes ocupa una vista en el archivo (con su coma y su salto de línea), exacto.

    En el JSON con sangría de 1 y claves ordenadas, una vista a profundidad 2 es:
    `  "clave": {` + sus campos con 3 espacios + `  }` + `,` y salto de línea. Como cada línea de la
    vista suelta (sangría 1) gana 2 espacios en su lugar, el peso es el de la clave, el de la vista
    suelta, 2 por cada salto de línea de ésta y las constantes de la sangría, el `: ` y la coma con
    su salto de línea.
    """
    suelta = json.dumps(entrada, ensure_ascii=False, indent=1, sort_keys=True)
    texto = json.dumps(clave, ensure_ascii=False)
    return len(texto.encode("utf-8", "replace")) + len(suelta.encode("utf-8", "replace")) + 2 * suelta.count("\n") + 6


class Memoria:
    """La memoria de lo ya visto. Usar siempre como `with Memoria.abrir(ruta) as m:`.

    Flujo: nuevas -> analizar -> marcar sólo lo analizado -> guardar. Lo que no se analizó
    no se marca: así vuelve a salir como nueva en la próxima corrida y no se pierde.

    Después de cada `guardar`, `podadas_por_edad` y `podadas_por_tamano` dicen cuántas vistas se
    borraron en ese guardado.
    """

    def __init__(self, ruta: Path, vistas: dict[str, dict[str, str]], candado: Path, fd: int, clave: str) -> None:
        self.ruta = ruta
        self._vistas = vistas
        self._candado = candado
        self._fd: int | None = fd
        self._clave = clave
        self._abierta = True
        self.podadas_por_edad = 0
        self.podadas_por_tamano = 0

    # ---------------------------------------------------------------- abrir y cerrar

    @classmethod
    def abrir(cls, ruta=RUTA_POR_DEFECTO) -> "Memoria":
        ruta = Path(ruta)
        candado = ruta.with_name(ruta.name + ".lock")
        ruta.parent.mkdir(parents=True, exist_ok=True)
        clave = os.path.normcase(os.path.realpath(candado))
        with _CERROJO:
            if clave in _EN_USO:
                raise cls._ocupada(ruta, candado)
            _EN_USO.add(clave)
        fd = None
        try:
            fd = os.open(candado, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
            try:
                _bloquear(fd)
            except OSError as exc:
                if exc.errno in _OCUPADO:
                    raise cls._ocupada(ruta, candado) from None
                raise MemoriaError(
                    f"El sistema de archivos no permite bloquear {candado} ({exc}): la memoria tiene que "
                    "estar en un disco local, no en una carpeta sincronizada ni en un recurso de red."
                ) from exc
            return cls(ruta, cls._cargar(ruta), candado, fd, clave)
        except BaseException:
            cls._liberar(fd, clave)
            raise

    @staticmethod
    def _ocupada(ruta: Path, candado: Path) -> MemoriaOcupada:
        return MemoriaOcupada(
            f"La memoria {ruta} la está usando otra corrida (bloqueo de {candado}). Esperá a que termine y "
            "reintentá. Si esa corrida se cayó, el sistema operativo ya soltó el bloqueo: no hay que borrar nada."
        )

    @staticmethod
    def _liberar(fd: int | None, clave: str) -> None:
        """Desbloquea y cierra (sin borrar el archivo de candado) y deja la ruta libre en el registro."""
        try:
            if fd is not None:
                try:
                    _desbloquear(fd)
                except OSError:
                    pass  # cerrar el descriptor lo suelta igual
                try:
                    os.close(fd)
                except OSError:
                    pass
        finally:
            with _CERROJO:
                _EN_USO.discard(clave)

    @staticmethod
    def _cargar(ruta: Path) -> dict[str, dict[str, str]]:
        try:
            tamano = ruta.stat().st_size
        except FileNotFoundError:
            return {}
        if tamano > TOPE_ARCHIVO:
            raise MemoriaDemasiadoGrande(
                f"La memoria {ruta} pesa {tamano} bytes y el tope es {TOPE_ARCHIVO}: no está rota pero "
                "no se abre. No se tocó; hay que podarla (borrar las vistas más viejas)."
            )
        try:
            datos = json.loads(ruta.read_bytes().decode("utf-8-sig"), object_pairs_hook=_sin_repetidas)
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise MemoriaCorrupta(
                f"La memoria {ruta} está corrupta: no es JSON legible ({type(exc).__name__}: {exc}). No se tocó."
            ) from exc
        return _validar(datos, ruta)

    def cerrar(self) -> None:
        if self._abierta:
            self._abierta = False
            fd, self._fd = self._fd, None
            self._liberar(fd, self._clave)

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

    @staticmethod
    def _podar_por_edad(vistas: dict, ahora: datetime) -> int:
        limite = ahora - timedelta(days=DIAS_DE_MEMORIA)
        viejas = [clave for clave, entrada in vistas.items() if _fecha(entrada) < limite]
        for clave in viejas:
            del vistas[clave]
        return len(viejas)

    @staticmethod
    def _podar_por_tamano(vistas: dict, contenido: bytes) -> tuple[bytes, int]:
        """Si `contenido` pasa el tope, borra las más viejas hasta que quepa. Devuelve (contenido, cuántas)."""
        if len(contenido) <= TOPE_ARCHIVO:
            return contenido, 0
        orden = sorted(vistas, key=lambda clave: (_fecha(vistas[clave]), clave))  # las más viejas primero
        exceso = len(contenido) - TOPE_ARCHIVO
        borradas = 0
        for clave in orden:  # el peso de cada vista se calcula; no se reserializa todo cientos de veces
            if exceso <= 0:
                break
            exceso -= _peso(clave, vistas[clave])
            del vistas[clave]
            borradas += 1
        contenido = _serializar(vistas)
        while len(contenido) > TOPE_ARCHIVO and borradas < len(orden):
            paso = max(1, len(orden) // 100)  # la estimación se quedó corta: un poco más
            for clave in orden[borradas : borradas + paso]:
                del vistas[clave]
            borradas = min(len(orden), borradas + paso)
            contenido = _serializar(vistas)
        return contenido, borradas

    def guardar(self, ahora: datetime | None = None) -> int:
        """Poda y escribe de forma atómica. Devuelve cuántas vistas podó en total.

        Poda lo visto hace más de DIAS_DE_MEMORIA días (`podadas_por_edad`) y, si el archivo aun así
        pasaría TOPE_ARCHIVO, las más viejas hasta que quepa (`podadas_por_tamano`). Sólo si ni vacío
        cabe (un tope absurdo) levanta `MemoriaDemasiadoGrande`, sin tocar el archivo anterior ni la
        memoria en uso.
        Escritura atómica: temporal único en el mismo directorio, flush, fsync y os.replace.
        """
        if not self._abierta:
            raise MemoriaError("la memoria ya está cerrada")
        trabajo = dict(self._vistas)  # se poda una copia: si algo falla, la memoria en uso no cambia
        por_edad = self._podar_por_edad(trabajo, ahora or _ahora())
        contenido, por_tamano = self._podar_por_tamano(trabajo, _serializar(trabajo))
        if len(contenido) > TOPE_ARCHIVO:
            raise MemoriaDemasiadoGrande(
                f"La memoria {self.ruta} no cabe en {TOPE_ARCHIVO} bytes ni vacía ({len(contenido)}). "
                "No se escribió nada; el archivo anterior sigue como estaba."
            )
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
        self._vistas = trabajo
        self.podadas_por_edad, self.podadas_por_tamano = por_edad, por_tamano
        self._sincronizar_directorio(directorio)
        return por_edad + por_tamano

    @staticmethod
    def _sincronizar_directorio(directorio: Path) -> None:
        """Mejor esfuerzo: el reemplazo ya se hizo, un error acá no debe hacerlo pasar por fallido."""
        if _ES_WINDOWS:
            return
        try:
            fd = os.open(directorio, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass
