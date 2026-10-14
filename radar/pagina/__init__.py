"""La página del resumen: HTML autocontenido sin JavaScript, con su JSON de numeración y su escritura segura."""

from .escritura import escribir_resumen
from .vista import Corrida, Pagina, construir

__all__ = ["Corrida", "Pagina", "construir", "escribir_resumen"]
