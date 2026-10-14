"""La lista fija de fuentes públicas (propuesta, parte 4; `material/fuentes-2026-10-13.md`)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .modelos import Extraccion
from .paginas import (
    analizar_anthropic_ingenieria,
    analizar_anthropic_noticias,
    analizar_claude_plataforma,
    analizar_metr,
)


@dataclass(frozen=True)
class Fuente:
    id: str
    nombre: str
    url: str
    tipo: str  # "atom" | "rss" | "pagina"
    analizador: Callable[..., Extraccion] | None = None  # sólo las páginas

    def __post_init__(self) -> None:
        if self.tipo not in ("atom", "rss", "pagina"):
            raise ValueError(f"tipo desconocido: {self.tipo!r}")
        if (self.tipo == "pagina") != (self.analizador is not None):
            raise ValueError("las páginas llevan analizador; los feeds no")
        if not self.url.startswith("https://"):
            raise ValueError("las fuentes se leen sólo por https")


FUENTES: tuple[Fuente, ...] = (
    Fuente("anthropic-noticias", "Anthropic, noticias", "https://www.anthropic.com/news", "pagina", analizar_anthropic_noticias),
    Fuente("anthropic-ingenieria", "Anthropic, ingeniería", "https://www.anthropic.com/engineering", "pagina", analizar_anthropic_ingenieria),
    Fuente("claude-code", "Claude Code, versiones", "https://github.com/anthropics/claude-code/releases.atom", "atom"),
    Fuente("claude-plataforma", "Notas de la plataforma Claude", "https://platform.claude.com/docs/en/release-notes/overview", "pagina", analizar_claude_plataforma),
    Fuente("openai", "OpenAI, noticias", "https://openai.com/news/rss.xml", "rss"),
    Fuente("codex", "Codex, versiones", "https://github.com/openai/codex/releases.atom", "atom"),
    Fuente("google-dev", "Google, blog para desarrolladores", "https://developers.googleblog.com/feeds/posts/default", "atom"),
    Fuente("github-changelog", "GitHub, novedades", "https://github.blog/changelog/feed/", "rss"),
    Fuente("ibm-youtube", "IBM Technology (YouTube)", "https://www.youtube.com/feeds/videos.xml?channel_id=UCKWaEZ-_VweaEx1j62do_vQ", "atom"),
    Fuente("metr", "METR", "https://metr.org/blog/", "pagina", analizar_metr),
    Fuente("owasp-genai", "OWASP GenAI", "https://genai.owasp.org/feed/", "rss"),
    Fuente("openssf", "OpenSSF", "https://openssf.org/feed/", "rss"),
    Fuente("simon-willison", "Simon Willison", "https://simonwillison.net/atom/everything/", "atom"),
    Fuente("martin-fowler", "martinfowler.com", "https://martinfowler.com/feed.atom", "atom"),
)  # fmt: skip


def por_id(identificador: str) -> Fuente:
    for fuente in FUENTES:
        if fuente.id == identificador:
            return fuente
    raise KeyError(identificador)
