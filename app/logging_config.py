"""
Configuracion de logging estructurado.
En produccion usa formato JSON para integracion con herramientas de observabilidad.
En desarrollo usa formato legible para humanos con campos de actividad resaltados.
"""
import logging
import sys

from pythonjsonlogger import json as jsonlogger

# Campos de actividad que se renderizan aparte del mensaje en modo desarrollo
_ACTIVITY_EXTRA_FIELDS = {"event", "phone", "user_name", "role", "msg_type",
                          "content_preview", "mode", "action", "details", "reason"}


class _DevFormatter(logging.Formatter):
    """
    Formatter para desarrollo: muestra el mensaje normal y, si el log
    proviene del logger de actividad (skinmed.activity), añade los campos
    extra relevantes en una línea indentada para facilitar la lectura.
    """

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            k: v for k, v in record.__dict__.items()
            if k in _ACTIVITY_EXTRA_FIELDS and v is not None
        }
        if not extras:
            return base
        # Campos útiles para estadísticas, mostrados debajo del mensaje
        detail_parts = []
        for key in ("event", "mode", "action", "role", "msg_type", "details", "reason"):
            if key in extras:
                detail_parts.append(f"{key}={extras[key]!r}")
        detail_line = "  ↳ " + "  ".join(detail_parts) if detail_parts else ""
        return f"{base}\n{detail_line}" if detail_line else base


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
        # En producción: JSON con campos extra incluidos automáticamente
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
        # En desarrollo: legible con campos de actividad indentados
        formatter = _DevFormatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler.setFormatter(formatter)
    raiz.addHandler(handler)

    # Reducir ruido de librerias externas
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
