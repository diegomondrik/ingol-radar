"""La memoria de lo ya visto: un JSON con candado entre procesos y guardado atómico.

Formato: {"version": 1, "vistas": {clave: {"fuente", "titulo", "enlace", "primera_vez"}}}

Garantías:
- un archivo roto, de versión desconocida, de forma inesperada o con claves repetidas nunca se
  resetea ni se pisa: se levanta `MemoriaCorrupta` con la ruta y el motivo. Uno que pasa el tope
  de tamaño es otro caso, `MemoriaDemasiadoGrande`: no está roto, hay que podarlo;
- el candado (`<ruta>.lock`, creación exclusiva) está en disco, así que vale entre procesos. Guarda
  el pid de quien lo tiene: si ese proceso ya no existe (una corrida caída, un corte de luz, un
  reinicio) el candado es huérfano y se toma solo; si el pid está vivo o no se puede leer, es
  `MemoriaOcupada` y el mensaje dice qué archivo borrar;
- `guardar` escribe a un temporal único en el mismo directorio, hace flush y fsync y
  reemplaza de una vez: ante cualquier falla el archivo anterior queda como estaba;
- la memoria no crece sin límite: `guardar` poda lo visto hace más de DIAS_DE_MEMORIA días.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = 1
# Anclada a la raíz del repo (radar/memoria/memoria.py -> raíz), no al directorio desde donde se corre.
RUTA_POR_DEFECTO = Path(__file__).resolve().parents[2] / "generated" / "memoria.json"
TOPE_ARCHIVO = 64 * 1024 * 1024
DIAS_DE_MEMORIA = 365
_CAMPOS = ("fuente", "titulo", "enlace", "primera_vez")
_ES_WINDOWS = os.name == "nt"
_PID = re.compile(rb"pid=([0-9]{1,10})\n?")
_PID_MAXIMO = 2**32 - 1
_TAMANO_MAXIMO_CANDADO = 64
_STILL_ACTIVE = 259
_ERROR_PARAMETRO_INVALIDO = 87  # OpenProcess: no existe un proceso con ese pid


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class MemoriaError(Exception):
    """Base de los errores propios de la memoria."""


class MemoriaOcupada(MemoriaError):
    """Otra corrida tiene el candado de la memoria."""


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


def _pid_de(contenido: bytes) -> int | None:
    """El pid que guarda un candado, o None si el contenido no es exactamente `pid=<número>`."""
    m = _PID.fullmatch(contenido)
    if m is None:
        return None
    pid = int(m.group(1))
    return pid if 0 < pid <= _PID_MAXIMO else None


def _proceso_vivo(pid: int) -> bool:
    """False sólo si se SABE que el proceso ya no existe; ante la duda, True (el candado se respeta).

    En Windows no se usa os.kill (ahí termina el proceso): se abre con OpenProcess y se mira el
    código de salida. En POSIX, os.kill(pid, 0) no manda ninguna señal, sólo comprueba.
    """
    if _ES_WINDOWS:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.GetExitCodeProcess.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        manejador = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not manejador:
            return ctypes.get_last_error() != _ERROR_PARAMETRO_INVALIDO
        try:
            codigo = wintypes.DWORD()
            if not k32.GetExitCodeProcess(manejador, ctypes.byref(codigo)):
                return True
            return codigo.value == _STILL_ACTIVE
        finally:
            k32.CloseHandle(manejador)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError, OverflowError):
        return True  # existe pero no es nuestro, o no se sabe: se respeta
    return True


class Memoria:
    """La memoria de lo ya visto. Usar siempre como `with Memoria.abrir(ruta) as m:`.

    Flujo: nuevas -> analizar -> marcar sólo lo analizado -> guardar. Lo que no se analizó
    no se marca: así vuelve a salir como nueva en la próxima corrida y no se pierde.
    """

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

    @classmethod
    def _tomar_candado(cls, ruta: Path, candado: Path) -> int:
        """Crea `<ruta>.lock` con O_EXCL, o retira antes uno huérfano. Si no, `MemoriaOcupada`."""
        for _ in range(3):
            try:
                return cls._crear_candado(candado)
            except FileExistsError:
                pass
            if not cls._retirar_huerfano(candado):
                break
        raise MemoriaOcupada(
            f"La memoria {ruta} está en uso: existe el candado {candado}. Si no hay otra corrida "
            f"andando, quedó de una corrida caída: borrá el archivo {candado} y reintentá."
        )

    @staticmethod
    def _crear_candado(candado: Path) -> int:
        """O_EXCL. `FileExistsError` si ya está tomado."""
        intentos = 20 if _ES_WINDOWS else 1
        for intento in range(intentos):
            try:
                return os.open(candado, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o644)
            except PermissionError:
                # En Windows, crear un archivo cuyo borrado está pendiente (otra corrida lo está
                # soltando justo ahora) da "acceso denegado" en vez de "ya existe": se reintenta un
                # momento; un permiso realmente negado sigue negado y sale como el error que es.
                if intento + 1 < intentos:
                    time.sleep(0.005)
                    continue
                if os.path.lexists(candado):
                    raise FileExistsError(str(candado)) from None
                raise
        raise AssertionError("inalcanzable")  # pragma: no cover

    @staticmethod
    def _retirar_huerfano(candado: Path) -> bool:
        """Retira un candado cuyo pid ya no existe. True si se puede volver a intentar tomarlo.

        Se retira con un rename atómico a un nombre único, no con un borrado: de varias corridas
        que lo ven huérfano al mismo tiempo, sólo una consigue el rename. Después se verifica que
        lo retirado es lo mismo que se juzgó muerto; si en el medio otra corrida ya lo había
        tomado de nuevo (ventana muy chica), se lo devuelve y se respeta.
        """
        try:
            with open(candado, "rb") as archivo:
                contenido = archivo.read(_TAMANO_MAXIMO_CANDADO + 1)
        except FileNotFoundError:
            return True  # lo soltaron mientras tanto: a intentar de nuevo
        except OSError:
            return False
        pid = _pid_de(contenido)
        if pid is None or _proceso_vivo(pid):
            return False
        retirado = candado.with_name(f"{candado.name}.huerfano.{uuid.uuid4().hex}")
        try:
            os.rename(candado, retirado)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        try:
            igual = retirado.read_bytes() == contenido
            if not igual:
                try:
                    os.link(retirado, candado)  # devolverlo: era de otra corrida, ya viva
                except OSError:
                    pass
            return igual
        except OSError:
            return False
        finally:
            try:
                os.unlink(retirado)
            except OSError:
                pass

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

    @staticmethod
    def _soltar(candado: Path) -> None:
        """Borra el candado propio. En Windows, otra corrida que justo lo está leyendo para ver si
        es huérfano impide el borrado un instante (acceso denegado): se reintenta hasta ~1 s."""
        intentos = 100 if _ES_WINDOWS else 1
        for intento in range(intentos):
            try:
                os.unlink(candado)
                return
            except FileNotFoundError:
                return
            except PermissionError:
                if intento + 1 == intentos:
                    raise
                time.sleep(0.01)

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

    def _podar(self, ahora: datetime) -> int:
        limite = ahora - timedelta(days=DIAS_DE_MEMORIA)
        viejas = []
        for clave, entrada in self._vistas.items():
            primera = datetime.fromisoformat(entrada["primera_vez"])
            if primera.tzinfo is None:
                primera = primera.replace(tzinfo=timezone.utc)
            if primera < limite:
                viejas.append(clave)
        for clave in viejas:
            del self._vistas[clave]
        return len(viejas)

    def guardar(self, ahora: datetime | None = None) -> int:
        """Poda lo visto hace más de DIAS_DE_MEMORIA días y escribe de forma atómica. Devuelve cuántas podó.

        Si el resultado pasa TOPE_ARCHIVO (un archivo que `abrir` no leería), levanta
        `MemoriaDemasiadoGrande` sin tocar el archivo anterior.
        Escritura atómica: temporal único en el mismo directorio, flush, fsync y os.replace.
        """
        if not self._abierta:
            raise MemoriaError("la memoria ya está cerrada")
        podadas = self._podar(ahora or _ahora())
        contenido = json.dumps(
            {"version": VERSION, "vistas": self._vistas}, ensure_ascii=False, indent=1, sort_keys=True
        ).encode("utf-8", "replace") + b"\n"
        if len(contenido) > TOPE_ARCHIVO:
            raise MemoriaDemasiadoGrande(
                f"La memoria {self.ruta} pasaría de {TOPE_ARCHIVO} bytes ({len(contenido)}) y `abrir` no la "
                "leería. No se escribió nada; el archivo anterior sigue como estaba."
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
        self._sincronizar_directorio(directorio)
        return podadas

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
