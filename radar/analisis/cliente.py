"""Corre `claude -p` sin herramientas y devuelve su respuesta de forma fija, verificada.

Qué garantiza cada llamada:
- `subprocess.run` con una lista de argumentos, nunca con shell.
- El mensaje de usuario (que trae datos de internet) va por la ENTRADA ESTÁNDAR, no en la línea
  de comandos. Verificado con claude 2.1.292: `-p` sin argumento de prompt lo lee de stdin.
- Sin herramientas (`--tools ""`), sin servidores MCP de afuera, sin configuración de usuario,
  proyecto ni local, sin guardar la sesión, desde un directorio temporal vacío.
- La salida se acepta sólo si: código 0, JSON válido, `is_error` falso, `subtype` "success",
  `permission_denials` vacío y `structured_output` presente. Cualquier otra cosa es una falla de
  la llamada, con su motivo.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

from radar.lectura.limpieza import limpiar_texto

from .errores import ClaudeNoDisponible, ErrorClaude

# Los modelos se piden por ROL (criterio, construccion, mecanico), nunca por nombre en el código
# que decide. Este es el único lugar donde un rol se vuelve el alias de familia que entiende el
# adaptador de claude: cambiar de modelo es cambiar este dato, no el análisis.
MODELO_POR_ROL = {"criterio": "opus"}
ROL_ANALISIS = "criterio"

VARIABLE_CLAUDE = "RADAR_CLAUDE"
TIMEOUT_S = 240
MAX_SALIDA = 5_000_000  # bytes de stdout que se aceptan
MAX_LINEA_DE_COMANDOS = 30_000  # Windows corta en 32 767 caracteres


def _es_lote_de_windows(ruta: str) -> bool:
    return ruta.lower().endswith((".cmd", ".bat"))


def _ruta_valida(ruta: str) -> str:
    """Un `.cmd` o `.bat` pasa por cmd.exe, que reinterpreta `&`, `%` y `^` del prompt de sistema."""
    if _es_lote_de_windows(ruta):
        raise ClaudeNoDisponible(
            f"{ruta} es un .cmd/.bat: pasaría los argumentos por cmd.exe. "
            f"Apuntá {VARIABLE_CLAUDE} a claude.exe (por ejemplo ~/.local/bin/claude.exe)."
        )
    return ruta


def _en_directorio_actual(ruta: str) -> bool:
    try:
        return Path(ruta).resolve().parent == Path.cwd().resolve()
    except OSError:
        return False


def localizar_claude(
    entorno: Mapping[str, str] | None = None,
    which: Callable[..., str | None] = shutil.which,
    home: Path | None = None,
) -> str:
    """Ruta de claude: `RADAR_CLAUDE`, después el PATH, después `~/.local/bin`. Si no, error claro."""
    entorno = os.environ if entorno is None else entorno
    indicada = entorno.get(VARIABLE_CLAUDE, "").strip()
    if indicada:
        if not Path(indicada).is_file():
            raise ClaudeNoDisponible(f"{VARIABLE_CLAUDE} apunta a {indicada!r}, que no es un archivo")
        return _ruta_valida(indicada)
    # En Windows `which` mira primero el directorio actual: un claude.exe suelto ahí no se toma.
    en_path = which("claude")
    if en_path and not _en_directorio_actual(en_path):
        return _ruta_valida(en_path)
    base = (home if home is not None else Path.home()) / ".local" / "bin"
    for nombre in ("claude.exe", "claude"):
        candidato = base / nombre
        if candidato.is_file():
            return _ruta_valida(str(candidato))
    raise ClaudeNoDisponible(
        f"no encuentro claude: ni en {VARIABLE_CLAUDE}, ni en el PATH, ni en {base}. "
        f"Instalalo o apuntá {VARIABLE_CLAUDE} a su ruta."
    )


def armar_comando(exe: str, sistema: str, esquema: dict, rol: str = ROL_ANALISIS) -> list[str]:
    """La lista de argumentos exacta. El prompt de usuario NO está acá: va por stdin."""
    try:
        modelo = MODELO_POR_ROL[rol]
    except KeyError:
        raise ErrorClaude(f"el rol {rol!r} no tiene modelo asignado") from None
    return [
        exe,
        "-p",
        "--tools",
        "",
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--no-session-persistence",
        "--system-prompt",
        sistema,
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(esquema, ensure_ascii=False, separators=(",", ":")),
        "--model",
        modelo,
    ]


def _motivo_de_stderr(crudo: bytes) -> str:
    texto = crudo.decode("utf-8", errors="replace")
    return limpiar_texto(texto, 300) or "sin mensaje"


def verificar_salida(crudo: bytes) -> dict:
    """De la salida de claude al objeto `structured_output`, o `ErrorClaude` con el motivo."""
    if len(crudo) > MAX_SALIDA:
        raise ErrorClaude(f"la salida de claude pesa más de {MAX_SALIDA} bytes")
    try:
        datos = json.loads(crudo.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ErrorClaude("la salida de claude no es JSON válido") from None
    if not isinstance(datos, dict):
        raise ErrorClaude("la salida de claude no es un objeto JSON")
    if datos.get("is_error") is not False:
        raise ErrorClaude("claude informó un error (is_error no es false)")
    if datos.get("subtype") != "success":
        raise ErrorClaude(f"claude terminó con subtype {str(datos.get('subtype'))[:40]!r}, no 'success'")
    denegados = datos.get("permission_denials")
    if denegados != []:
        raise ErrorClaude("claude intentó usar una herramienta (permission_denials no está vacío)")
    salida = datos.get("structured_output")
    if not isinstance(salida, dict):
        raise ErrorClaude("claude no devolvió structured_output")
    return salida


def ejecutar(
    usuario: str,
    sistema: str,
    esquema: dict,
    *,
    rol: str = ROL_ANALISIS,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    entorno: Mapping[str, str] | None = None,
    timeout: float = TIMEOUT_S,
) -> dict:
    """Una llamada a claude. Devuelve el objeto de `structured_output` o lanza `ErrorClaude`."""
    exe = localizar_claude(entorno)
    comando = armar_comando(exe, sistema, esquema, rol)
    if sum(len(a) + 1 for a in comando) > MAX_LINEA_DE_COMANDOS:
        raise ErrorClaude("el prompt de sistema es demasiado largo para la línea de comandos")
    with tempfile.TemporaryDirectory(prefix="radar-claude-", ignore_cleanup_errors=True) as vacio:
        try:
            proceso = run(
                comando,
                input=usuario.encode("utf-8"),
                capture_output=True,
                cwd=vacio,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ErrorClaude(f"claude no terminó en {timeout:g} s") from None
        except OSError as exc:
            raise ErrorClaude(f"no se pudo correr claude: {limpiar_texto(str(exc), 200)}") from None
    if proceso.returncode != 0:
        raise ErrorClaude(f"claude salió con código {proceso.returncode}: {_motivo_de_stderr(proceso.stderr or b'')}")
    return verificar_salida(proceso.stdout or b"")
