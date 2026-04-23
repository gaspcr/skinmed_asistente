"""
Tool: revisar_agenda.

Consulta la agenda de citas de un doctor y envía el resultado
formateado directamente por WhatsApp.
"""
from datetime import datetime
from typing import Any, Dict

from app.services.filemaker import FileMakerService
from app.services.whatsapp import WhatsAppService
from app.formatters.agenda import AgendaFormatter


# ──────────────────────────────────────────────
# Definición OpenAI function calling
# ──────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "revisar_agenda",
        "description": (
            "Consulta la agenda de citas del doctor para un día específico. "
            "Si no se indica fecha, muestra la agenda de hoy. IMPORTANTE: si "
            "el doctor pide agenda para una fecha relativa (mañana, próximo "
            "lunes, etc.), primero usa calcular_fecha para obtener la fecha exacta."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fecha": {
                    "type": "string",
                    "description": (
                        "Fecha en formato ISO YYYY-MM-DD (ejemplo: 2026-04-15). "
                        "Si no se indica, se usa la fecha de hoy."
                    ),
                }
            },
            "required": [],
        },
    },
}


# ──────────────────────────────────────────────
# Handler
# ──────────────────────────────────────────────

async def handle(user, phone: str, arguments: Dict[str, Any]) -> str:
    """Consulta la agenda y envía el resultado formateado por WhatsApp."""
    fecha_input = arguments.get("fecha")
    filemaker_date = None

    if fecha_input:
        # El LLM envía YYYY-MM-DD (ISO), convertir a mm-dd-yyyy para FileMaker
        try:
            date_obj = datetime.strptime(fecha_input.strip(), "%Y-%m-%d")
            filemaker_date = date_obj.strftime("%m-%d-%Y")
        except ValueError:
            # Fallback: intentar parsear dd-mm-yy por si el LLM usa ese formato
            try:
                parts = fecha_input.strip().split("-")
                if len(parts) == 3:
                    day, month, year = parts
                    full_year = f"20{year}" if len(year) == 2 else year
                    date_obj = datetime.strptime(f"{day}-{month}-{full_year}", "%d-%m-%Y")
                    filemaker_date = date_obj.strftime("%m-%d-%Y")
                else:
                    return "Formato de fecha inválido."
            except ValueError:
                return "Fecha inválida. Verifica que el día y mes sean correctos."

    agenda_data = await FileMakerService.get_agenda_raw(user.id, filemaker_date)
    formatted_msg, glossary = AgendaFormatter.format(agenda_data, user.name)

    # Enviar agenda formateada directamente por WhatsApp
    await WhatsAppService.send_message(phone, formatted_msg)
    if glossary:
        await WhatsAppService.send_message(phone, glossary)

    # Retornar resumen breve al LLM para que formule su respuesta conversacional
    return "Agenda enviada al doctor. Informa y pregunta en qué más puedes ayudar."
