"""
Tool compartida: calcular_fecha.

Utilizada por múltiples roles para convertir referencias relativas
de fecha a fechas exactas basándose en la zona horaria de Chile.
"""
from datetime import datetime, timedelta
from typing import Any, Dict

import pytz


# ──────────────────────────────────────────────
# Definición OpenAI function calling
# ──────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "calcular_fecha",
        "description": (
            "Calcula una fecha exacta a partir de una referencia relativa. "
            "Usa esta función SIEMPRE que necesites convertir expresiones como "
            "'mañana', 'próximo miércoles', 'en 3 días', etc. a una fecha real."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "dias_offset": {
                    "type": "integer",
                    "description": (
                        "Número de días desde hoy. Ejemplo: 1 para mañana, "
                        "2 para pasado mañana, -1 para ayer, 0 para hoy."
                    ),
                },
                "dia_semana": {
                    "type": "string",
                    "enum": [
                        "lunes", "martes", "miercoles", "jueves",
                        "viernes", "sabado", "domingo",
                    ],
                    "description": (
                        "Día de la semana para buscar la próxima ocurrencia. "
                        "Ejemplo: 'miercoles' para el próximo miércoles."
                    ),
                },
            },
            "required": [],
        },
    },
}


# ──────────────────────────────────────────────
# Handler
# ──────────────────────────────────────────────

_DIAS_MAP = {
    "lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2,
    "jueves": 3, "viernes": 4, "sabado": 5, "sábado": 5, "domingo": 6,
}
_DIAS_NOMBRES = [
    "lunes", "martes", "miércoles", "jueves",
    "viernes", "sábado", "domingo",
]


async def handle(user, phone: str, arguments: Dict[str, Any]) -> str:
    """Calcula una fecha exacta basándose en la fecha actual de Chile."""
    tz = pytz.timezone("America/Santiago")
    now = datetime.now(tz)
    today = now.date()

    dias_offset = arguments.get("dias_offset")
    dia_semana = arguments.get("dia_semana")

    if dias_offset is not None:
        target = today + timedelta(days=int(dias_offset))
    elif dia_semana:
        target_weekday = _DIAS_MAP.get(dia_semana.lower().strip())
        if target_weekday is None:
            return f"Día de la semana no reconocido: '{dia_semana}'"
        # Calcular próxima ocurrencia (si hoy es ese día, ir a la próxima semana)
        days_ahead = target_weekday - today.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        target = today + timedelta(days=days_ahead)
    else:
        target = today

    dia_nombre = _DIAS_NOMBRES[target.weekday()]
    fecha_iso = target.strftime("%Y-%m-%d")
    fecha_display = target.strftime("%d de %B de %Y")

    return (
        f"Fecha calculada: {fecha_iso} ({dia_nombre}, {fecha_display}). "
        f"Usa este valor en formato YYYY-MM-DD para llamar a otras funciones."
    )
