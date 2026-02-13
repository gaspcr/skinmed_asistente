"""
Configuracion de logging estructurado.
En produccion usa formato JSON para integracion con herramientas de observabilidad.
En desarrollo usa formato legible para humanos.
Los logs se persisten en archivos dentro de la carpeta 'logs/' con rotacion diaria.
"""
import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler

from pythonjsonlogger import json as jsonlogger


LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")


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

    # --- Formatter ---
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

    # --- Handler: stdout ---
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    raiz.addHandler(stdout_handler)

    # --- Handler: archivos con rotacion diaria (best-effort) ---
    try:
        os.makedirs(LOG_DIR, exist_ok=True)

        # Log general
        log_file = os.path.join(LOG_DIR, "app.log")
        file_handler = TimedRotatingFileHandler(
            filename=log_file,
            when="midnight",
            interval=1,
            backupCount=30,  # Mantener 30 dias de logs
            encoding="utf-8",
        )
        file_handler.suffix = "%Y-%m-%d"
        file_handler.setFormatter(formatter)
        raiz.addHandler(file_handler)

        # Log dedicado para interacciones
        interaction_file = os.path.join(LOG_DIR, "interactions.log")
        interaction_handler = TimedRotatingFileHandler(
            filename=interaction_file,
            when="midnight",
            interval=1,
            backupCount=90,  # Mantener 90 dias de interacciones
            encoding="utf-8",
        )
        interaction_handler.suffix = "%Y-%m-%d"
        interaction_handler.setFormatter(formatter)

        interaction_logger = logging.getLogger("interaction")
        interaction_logger.addHandler(interaction_handler)
    except (PermissionError, OSError) as e:
        raiz.warning("No se pudo crear logs en disco (%s). Usando solo stdout.", e)

    # Reducir ruido de librerias externas
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
