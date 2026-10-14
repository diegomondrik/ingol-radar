"""La lista fija de fuentes públicas (propuesta, parte 4; `material/fuentes-2026-10-13.md`)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .analizar import analizar_atom, analizar_rss
from .modelos import Extraccion
from .paginas import (
    analizar_anthropic_ingenieria,
    analizar_anthropic_noticias,
    analizar_claude_plataforma,
    analizar_metr,
)


@dataclass(frozen=True)
class Fuente:
    """Una fuente fija. `analizador(fuente, texto, ahora)` la convierte en novedades; `tipo` es informativo."""

    id: str
    nombre: str
    url: str
    tipo: str  # "atom" | "rss" | "pagina"
    analizador: Callable[..., Extraccion]


FUENTES: tuple[Fuente, ...] = (
    Fuente("anthropic-noticias", "Anthropic, noticias", "https://www.anthropic.com/news", "pagina", analizar_anthropic_noticias),
    Fuente("anthropic-ingenieria", "Anthropic, ingeniería", "https://www.anthropic.com/engineering", "pagina", analizar_anthropic_ingenieria),
    Fuente("claude-code", "Claude Code, versiones", "https://github.com/anthropics/claude-code/releases.atom", "atom", analizar_atom),
    Fuente("claude-plataforma", "Notas de la plataforma Claude", "https://platform.claude.com/docs/en/release-notes/overview", "pagina", analizar_claude_plataforma),
    Fuente("openai", "OpenAI, noticias", "https://openai.com/news/rss.xml", "rss", analizar_rss),
    Fuente("codex", "Codex, versiones", "https://github.com/openai/codex/releases.atom", "atom", analizar_atom),
    Fuente("google-dev", "Google, blog para desarrolladores", "https://developers.googleblog.com/feeds/posts/default", "atom", analizar_atom),
    Fuente("github-changelog", "GitHub, novedades", "https://github.blog/changelog/feed/", "rss", analizar_rss),
    Fuente("ibm-youtube", "IBM Technology (YouTube)", "https://www.youtube.com/feeds/videos.xml?channel_id=UCKWaEZ-_VweaEx1j62do_vQ", "atom", analizar_atom),
    Fuente("metr", "METR", "https://metr.org/blog/", "pagina", analizar_metr),
    Fuente("owasp-genai", "OWASP GenAI", "https://genai.owasp.org/feed/", "rss", analizar_rss),
    Fuente("openssf", "OpenSSF", "https://openssf.org/feed/", "rss", analizar_rss),
    Fuente("simon-willison", "Simon Willison", "https://simonwillison.net/atom/everything/", "atom", analizar_atom),
    Fuente("martin-fowler", "martinfowler.com", "https://martinfowler.com/feed.atom", "atom", analizar_atom),
)  # fmt: skip


def por_id(identificador: str) -> Fuente:
    for fuente in FUENTES:
        if fuente.id == identificador:
            return fuente
    raise KeyError(identificador)
