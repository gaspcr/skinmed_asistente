"""
Configuración LLM para el rol de doctores (medico).

Registra el system prompt, tools, handlers y constructor de contexto
específicos para el workflow de doctores.
"""
from datetime import datetime
from typing import Any, Dict

import pytz

from app.workflows.llm.config import RoleLLMConfig, register_llm_config
from app.workflows.llm.tools import shared as tool_shared
from app.workflows.llm.tools import agenda as tool_agenda
from app.workflows.llm.tools import recados as tool_recados


# ──────────────────────────────────────────────
# System prompt
# ──────────────────────────────────────────────

_SYSTEM_PROMPT = """Eres un asistente virtual de la Clínica SkinMed. Ayudas a los doctores a gestionar su agenda y recados a través de WhatsApp.

Tu nombre es Aura. Debes responder siempre en español, de forma concisa y profesional.

Fecha y hora actual: {fecha_actual} ({dia_semana})

Tienes acceso a las siguientes funciones:
1. **Calcular fecha**: Convierte fechas relativas ("mañana", "próximo miércoles") a fecha exacta.
2. **Revisar agenda**: Consultar las citas del doctor para un día específico.
3. **Revisar recados**: Ver los recados/mensajes pendientes del doctor.
4. **Publicar recado**: Crear un nuevo recado con una categoría específica.

Las categorías de recados disponibles son:
- "Agendar paciente": Para solicitar que se agende un paciente.
- "Bloquear agenda": Para solicitar bloqueo de horarios.
- "Enviar receta": Para solicitar el envío de una receta.
- "Otros": Para cualquier otro tipo de recado.

Reglas importantes:
- IMPORTANTE: Cuando el doctor mencione fechas relativas ("mañana", "el lunes", "próximo miércoles", etc.), SIEMPRE usa primero la función calcular_fecha para obtener la fecha exacta. NUNCA intentes calcular fechas por tu cuenta.
- Cuando el doctor quiera ver su agenda, usa la función revisar_agenda. Si menciona una fecha relativa, primero llama a calcular_fecha y luego usa el resultado en revisar_agenda.
- Cuando el doctor quiera ver sus recados/mensajes, usa la función revisar_recados.
- Cuando el doctor quiera dejar un recado o mensaje, usa la función publicar_recado. Asegúrate de identificar la categoría correcta y el contenido del mensaje.
- Si el doctor te saluda, pregunta en qué puedes ayudar, o pregunta qué puedes hacer, responde amablemente listando tus capacidades (revisar agenda, revisar recados, publicar recado). Esto NO es un fallback.
- SOLO usa el prefijo "[FALLBACK]" si el doctor te pide realizar una acción concreta que NO puedes hacer con tus funciones (por ejemplo: "recetame un medicamento", "llama a un paciente", etc.). Saludos, preguntas generales y conversación casual NO son fallback.
- Después de completar una acción, pregunta amablemente si necesita algo más.
- No inventes información. Solo reporta lo que devuelven las funciones.
- Sé breve. Los mensajes de WhatsApp deben ser concisos.
- Usa formato WhatsApp: *negrita*, _cursiva_ cuando sea apropiado.

El doctor con el que estás hablando se llama: {doctor_name}"""


# ──────────────────────────────────────────────
# Tools
# ──────────────────────────────────────────────

_TOOLS = [
    tool_shared.TOOL_DEFINITION,
    tool_agenda.TOOL_DEFINITION,
    tool_recados.REVISAR_RECADOS_TOOL,
    tool_recados.PUBLICAR_RECADO_TOOL,
]


# ──────────────────────────────────────────────
# Tool handlers
# ──────────────────────────────────────────────

_TOOL_HANDLERS = {
    "calcular_fecha": tool_shared.handle,
    "revisar_agenda": tool_agenda.handle,
    "revisar_recados": tool_recados.handle_revisar,
    "publicar_recado": tool_recados.handle_publicar,
}


# ──────────────────────────────────────────────
# Prompt context builder
# ──────────────────────────────────────────────

def _build_prompt_context(user) -> Dict[str, str]:
    """Construye el contexto dinámico para el system prompt del doctor."""
    doctor_name = f"{user.name} {user.last_name}".strip()

    tz = pytz.timezone("America/Santiago")
    now = datetime.now(tz)
    fecha_actual = now.strftime("%Y-%m-%d")

    _DIAS_SEMANA = [
        "lunes", "martes", "miércoles", "jueves",
        "viernes", "sábado", "domingo",
    ]
    dia_semana = _DIAS_SEMANA[now.weekday()]

    return {
        "doctor_name": doctor_name,
        "fecha_actual": fecha_actual,
        "dia_semana": dia_semana,
    }


# ──────────────────────────────────────────────
# Auto-registro
# ──────────────────────────────────────────────

register_llm_config(RoleLLMConfig(
    role_name="medico",
    system_prompt_template=_SYSTEM_PROMPT,
    tools=_TOOLS,
    tool_handlers=_TOOL_HANDLERS,
    prompt_context_builder=_build_prompt_context,
))
