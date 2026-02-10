import logging
import sys

from app.config import LOG_LEVEL


def setup_logging():
    """
    Configura el sistema de logging estructurado.
    Nivel configurable via variable de entorno LOG_LEVEL (default: INFO).
    """
    nivel = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

    formato = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formato)

    raiz = logging.getLogger()
    raiz.setLevel(nivel)
    raiz.addHandler(handler)
