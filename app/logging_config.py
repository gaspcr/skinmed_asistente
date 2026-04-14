"""
Configuracion de logging estructurado.
En produccion usa formato JSON para integracion con herramientas de observabilidad.
En desarrollo usa formato legible para humanos.
"""
import logging
import sys

from pythonjsonlogger import json as jsonlogger


def setup_logging(log_level: str = "INFO", environment: str = "production"):
    """
    Configura el sistema de logging.

    Args:
        log_level: Nivel de logging (DEBUG, INFO, WARNING, ERROR)
        environment: Entorno de ejecucion (production, development, staging)
    """
    nivel = getattr(logging, log_level.upper(), logging.INFO)
    raiz = logging.getLogger()
    raiz.setLevel(nivel)

    # Limpiar handlers previos para evitar duplicados en recargas
    raiz.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

    if environment == "production":
        formatter = jsonlogger.JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
            rename_fields={
                "asctime": "timestamp",
                "levelname": "level",
                "name": "logger",
            },
        )
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler.setFormatter(formatter)
    raiz.addHandler(handler)

    # Reducir ruido de librerias externas
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
