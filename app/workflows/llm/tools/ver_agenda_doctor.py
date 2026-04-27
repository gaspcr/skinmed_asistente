"""
Tool: ver_agenda_doctor (para gerencia).

Para consultas directas del tipo "dame la agenda del Dr. X para mañana".
Usa el AgendaFormatter + glosario, igual que el workflow legacy del doctor,
y envía el mensaje formateado directamente por WhatsApp.

La diferencia con consultar_agenda:
- consultar_agenda: devuelve datos crudos al LLM para análisis/comparación.
- ver_agenda_doctor: formatea y envía la agenda directamente (con glosario),
  dejando al LLM solo el rol de confirmar y preguntar si necesita algo más.
"""
import logging
from datetime import datetime
from typing import Any, Dict

import pytz

from app.services.filemaker import FileMakerService
from app.services.whatsapp import WhatsAppService
from app.formatters.agenda import AgendaFormatter

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Definición OpenAI function calling
# ──────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "ver_agenda_doctor",
        "description": (
            "Muestra la agenda completa y formateada de UN doctor específico para una fecha. "
            "Úsala cuando el usuario pida ver o mostrar la agenda de un doctor en particular "
            "(ej: 'dame la agenda de la Dra. Fernanda', 'muéstrame la agenda del Dr. Walter para mañana'). "
            "NO uses esta función para análisis comparativos, ocupación o preguntas que involucren "
            "múltiples doctores — para eso usa consultar_agenda. "
            "IMPORTANTE: para fechas relativas ('mañana', 'próximo lunes'), primero usa calcular_fecha."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "doctor": {
                    "type": "string",
                    "description": (
                        "Nombre o apellido del doctor (búsqueda flexible). "
                        "Ejemplo: 'Fernanda' encontrará 'Dra. Fernanda Cuca R'."
                    ),
                },
                "fecha": {
                    "type": "string",
                    "description": (
                        "Fecha en formato YYYY-MM-DD. "
                        "Si no se indica, se usa la fecha de hoy."
                    ),
                },
            },
            "required": ["doctor"],
        },
    },
}


# ──────────────────────────────────────────────
# Handler
# ──────────────────────────────────────────────

# Filtros de citas inválidas
_IGNORAR_TIPO = ["Eliminada", "Bloqueada", "No Viene"]
_IGNORAR_ACTIVIDAD = ["RECORDATORIO", "VISITADOR MÉDICO", "LABORATORIO"]


def _match_doctor(nombre_completo: str, filtro: str) -> bool:
    return filtro.lower().strip() in nombre_completo.lower()


async def handle(user, phone: str, arguments: Dict[str, Any]) -> str:
    """
    Obtiene la agenda formateada de un doctor y la envía directamente por WhatsApp.
    Retorna un resumen breve al LLM para su respuesta conversacional.
    """
    filtro_doctor = arguments.get("doctor", "")
    fecha_input = arguments.get("fecha")

    # Resolver fecha
    tz = pytz.timezone("America/Santiago")
    if fecha_input:
        try:
            date_obj = datetime.strptime(fecha_input.strip(), "%Y-%m-%d")
            filemaker_date = date_obj.strftime("%m-%d-%Y")
            fecha_display = date_obj.strftime("%d-%m-%Y")
        except ValueError:
            return "Formato de fecha inválido. Usa YYYY-MM-DD."
    else:
        now = datetime.now(tz)
        filemaker_date = now.strftime("%m-%d-%Y")
        fecha_display = now.strftime("%d-%m-%Y")

    # Obtener agenda completa del día
    all_data = await FileMakerService.get_agenda_all_doctors(filemaker_date)

    if not all_data:
        return f"No hay agenda registrada para {fecha_display}."

    # Filtrar por doctor (match fuzzy)
    doctor_data = [
        r for r in all_data
        if _match_doctor(r.get("fieldData", {}).get("Recurso Humano::Nombre Lista", ""), filtro_doctor)
    ]

    # Encontrar el nombre real del doctor para mostrar
    doctor_name = ""
    for r in doctor_data:
        nombre = r.get("fieldData", {}).get("Recurso Humano::Nombre Lista", "").strip()
        if nombre:
            doctor_name = nombre
            break

    if not doctor_data:
        # Listar doctores disponibles para ayudar al usuario
        all_names = list({
            r.get("fieldData", {}).get("Recurso Humano::Nombre Lista", "").strip()
            for r in all_data
            if r.get("fieldData", {}).get("Recurso Humano::Nombre Lista", "").strip()
        })
        logger.info(
            "[VER_AGENDA] Doctor '%s' no encontrado para %s. Disponibles: %s",
            filtro_doctor, fecha_display, all_names,
        )
        return (
            f"No se encontró un doctor que coincida con '{filtro_doctor}' "
            f"para el {fecha_display}.\n"
            f"Doctores disponibles: {', '.join(sorted(all_names))}"
        )

    logger.info(
        "[VER_AGENDA] Formateando agenda de '%s' para %s (%d registros)",
        doctor_name, fecha_display, len(doctor_data),
    )

    # Formatear con el AgendaFormatter
    formatted_msg, glossary = AgendaFormatter.format(doctor_data, doctor_name)

    # Construir un único mensaje: header propio + cuerpo de citas + glosario
    # El formatter devuelve "Hola Dr(a). X\nAgenda para día solicitado:\n\nHH:MM..."
    # Eliminamos el header del formatter y usamos el nuestro
    cuerpo = formatted_msg
    for prefijo in [
        f"*Hola Dr(a). {doctor_name}*\nAgenda para día solicitado:\n\n",
        f"*Hola Dr(a). {doctor_name}*\nNo tienes citas agendadas día solicitado.",
    ]:
        if cuerpo.startswith(prefijo):
            cuerpo = cuerpo[len(prefijo):]
            break

    mensaje_final = f"*Agenda de {doctor_name}* — {fecha_display}\n\n{cuerpo.strip()}"
    if glossary:
        mensaje_final += f"\n\n*Glosario:*\n{glossary}"

    await WhatsAppService.send_message(phone, mensaje_final)

    # Indicar al LLM que NO envíe nada más — la agenda es la respuesta completa
    return (
        f"[AGENDA_ENVIADA] Agenda de {doctor_name} para {fecha_display} enviada correctamente. "
        f"El mensaje ya incluye las citas y el glosario. "
        f"NO envíes ningún mensaje adicional. NO repitas la agenda. NO preguntes si necesita algo más. "
        f"Tu turno ha terminado con esta tool call."
    )

