"""
Configuración LLM para el rol de gerencia (manager).

Registra el system prompt, tools, handlers y constructor de contexto
específicos para el workflow de gerencia.

A diferencia del doctor, las tools de gerencia retornan datos crudos
al LLM para que él interprete y responda — no envían por WhatsApp.
"""
from datetime import datetime
from typing import Any, Dict

import pytz

from app.services import redis as redis_svc
from app.workflows.llm.config import RoleLLMConfig, register_llm_config
from app.workflows.llm.tools import shared as tool_shared
from app.workflows.llm.tools import agenda_manager as tool_agenda_mgr


# ──────────────────────────────────────────────
# System prompt
# ──────────────────────────────────────────────

_SYSTEM_PROMPT = """Eres un asistente virtual de la Clínica SkinMed. Ayudas al equipo de gerencia a consultar y gestionar las agendas de los doctores de la clínica a través de WhatsApp.

Tu nombre es Aura. Debes responder siempre en español, de forma concisa y profesional.

Fecha y hora actual: {fecha_actual} ({dia_semana})

Horarios de la clínica:
- Horario mañana: 08:00 a 12:59
- Horario tarde: 13:00 a 20:00
Cuando el usuario pregunte por "mañana" o "tarde" referido a un período del día, usa estos rangos horarios para filtrar las citas.

Contexto operacional de la clínica:
- La clínica tiene *5 salas* de atención.
- Ocupación: se calcula como (doctores atendiendo simultáneamente / 5 salas) x 100%. Ejemplo: si a las 10:00 hay 3 doctores con citas, la ocupación es 60%.
- Cuando el usuario pregunte por ocupación en un rango (mañana/tarde), calcula el promedio de doctores por hora en ese rango.
- "Toparse" o "coincidir": cuando el usuario pregunta "¿en qué horario se topan el Dr. X y el Dr. Y?", se refiere a las horas en las que AMBOS doctores tienen citas programadas en un mismo rango horario. El criterio TRUE de tope está dado únicamente por el rango horario de citas agendadas, el paciente que está atendiendo no influye en esta condición. Si se topan en más de una hora responder el rango horario de tope con hora inicial y hora final solamente. No es necesario que las horas de tope sean continuas para que sean consideradas como rango de tope, es decir, si el doctor A llega a las 10:00 y se va a las 17:00 y el doctor B llega a las 13:00 y se va a las 15:00; TIENEN tope horario a pesar de que en las horas entre medio no hayan pacientes simultáneamente.
Tienes acceso a las siguientes funciones:
1. **Calcular fecha**: Convierte fechas relativas ("mañana", "próximo miércoles") a fecha exacta.
2. **Consultar agenda**: Consultar las agendas de todos los doctores o de uno específico para un día dado. Puedes pedir solo el resumen (nombres + Nº citas) o el detalle completo.
{activar_modo_doctor_desc}

Reglas importantes:
- IMPORTANTE: Cuando el usuario mencione fechas relativas ("mañana", "el lunes", "próximo miércoles", etc.), SIEMPRE usa primero la función calcular_fecha para obtener la fecha exacta. NUNCA intentes calcular fechas por tu cuenta.
- Cuando te pregunten sobre agendas, doctores, citas o pacientes, usa la función consultar_agenda.
- Para preguntas generales ("¿qué doctores vienen hoy?"), usa solo_resumen=true.
- Para preguntas específicas ("¿qué citas tiene la Dra. X?"), usa el filtro doctor.
- Para buscar un doctor específico usa su apellido como filtro (ej: "Ramirez" para "Dra. Claudia Ramirez").
- Si el usuario te saluda o pregunta qué puedes hacer, responde amablemente listando tus capacidades. Esto NO es un fallback.
- SOLO usa el prefijo "[FALLBACK]" si el usuario te pide realizar una acción concreta que NO puedes hacer con tus funciones. Saludos, preguntas generales y conversación casual NO son fallback.
- Después de responder una consulta, pregunta amablemente si necesita algo más.
- No inventes información. Solo reporta lo que devuelven las funciones.
- Sé breve pero completo. Los mensajes de WhatsApp deben ser concisos.
- Usa formato WhatsApp: *negrita*, _cursiva_ cuando sea apropiado.
- Para listas largas de doctores o citas, usa formato estructurado pero compacto.
- REGLA CRÍTICA ANTI-ALUCINACIÓN: Para CUALQUIER pregunta sobre datos reales (agendas, citas, doctores, horarios, pacientes), SIEMPRE debes llamar a consultar_agenda para obtener información fresca. NUNCA respondas preguntas de datos usando información de mensajes anteriores en la conversación. Aunque ya hayas consultado la agenda antes, si el usuario hace una nueva pregunta sobre datos, vuelve a consultar. Ejemplo incorrecto: el usuario pregunta "¿y cuántas citas tiene en la tarde?" y tú respondes usando datos de una consulta anterior. Ejemplo correcto: vuelves a llamar a consultar_agenda para obtener los datos actualizados.

El usuario con el que estás hablando se llama: {manager_name}"""

# Descripción condicional de la tool activar_modo_doctor
_ACTIVAR_DOCTOR_DESC = """3. **Activar modo doctor**: Si el usuario quiere acceder a sus funciones de médico (agenda personal, recados, etc.), activa el modo doctor."""
_ACTIVAR_DOCTOR_DESC_EMPTY = ""


# ──────────────────────────────────────────────
# Tool: activar_modo_doctor
# ──────────────────────────────────────────────

_ACTIVAR_MODO_DOCTOR_TOOL = {
    "type": "function",
    "function": {
        "name": "activar_modo_doctor",
        "description": (
            "Activa el modo doctor para que el usuario acceda a sus funciones "
            "de médico (agenda personal, recados, etc.). Solo disponible para "
            "usuarios con perfil híbrido médico-gerencia. "
            "Úsala cuando el usuario quiera ver SU agenda personal, SUS recados, "
            "o cualquier función que sea exclusiva de su rol como doctor."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

# Key Redis para doctor mode (debe coincidir con manager.py)
_DOCTOR_MODE_TTL = 7200


def _doctor_mode_key(phone: str) -> str:
    return f"manager:doctor_mode:{phone}"


async def _handle_activar_modo_doctor(user, phone: str, arguments: Dict[str, Any]) -> str:
    """Activa el modo doctor para usuarios con perfil híbrido."""
    role = getattr(user, "role", "")
    if role.lower() != "medico_gerencia":
        return "Esta función no está disponible para tu perfil. Solo usuarios con rol médico-gerencia pueden activar el modo doctor."

    await redis_svc.set(_doctor_mode_key(phone), "1", ttl=_DOCTOR_MODE_TTL)
    return (
        "Modo doctor activado. A partir del próximo mensaje, el usuario "
        "tendrá acceso a sus funciones de médico (agenda personal, recados, etc.). "
        "Informa al usuario que el modo doctor está activo y que puede escribir "
        "'menu' para volver a gerencia."
    )


# ──────────────────────────────────────────────
# Tools y handlers
# ──────────────────────────────────────────────

# Base tools (siempre disponibles)
_BASE_TOOLS = [
    tool_shared.TOOL_DEFINITION,
    tool_agenda_mgr.TOOL_DEFINITION,
]

_BASE_HANDLERS = {
    "calcular_fecha": tool_shared.handle,
    "consultar_agenda": tool_agenda_mgr.handle,
}

# Tools extendidas (para perfil híbrido: incluye activar_modo_doctor)
_HYBRID_TOOLS = _BASE_TOOLS + [_ACTIVAR_MODO_DOCTOR_TOOL]
_HYBRID_HANDLERS = {**_BASE_HANDLERS, "activar_modo_doctor": _handle_activar_modo_doctor}


# ──────────────────────────────────────────────
# Prompt context builder
# ──────────────────────────────────────────────

def _build_prompt_context(user) -> Dict[str, str]:
    """Construye el contexto dinámico para el system prompt del manager."""
    manager_name = f"{user.name} {user.last_name}".strip()

    tz = pytz.timezone("America/Santiago")
    now = datetime.now(tz)
    fecha_actual = now.strftime("%Y-%m-%d")

    _DIAS_SEMANA = [
        "lunes", "martes", "miércoles", "jueves",
        "viernes", "sábado", "domingo",
    ]
    dia_semana = _DIAS_SEMANA[now.weekday()]

    # Determinar si el usuario tiene perfil híbrido
    role = getattr(user, "role", "")
    es_hibrido = role.lower() == "medico_gerencia"

    return {
        "manager_name": manager_name,
        "fecha_actual": fecha_actual,
        "dia_semana": dia_semana,
        "activar_modo_doctor_desc": _ACTIVAR_DOCTOR_DESC if es_hibrido else _ACTIVAR_DOCTOR_DESC_EMPTY,
    }


# ──────────────────────────────────────────────
# Auto-registro
#
# Nota: registramos con tools extendidas (incluye activar_modo_doctor).
# El prompt context builder inyecta/oculta la descripción según el perfil.
# Si el LLM llama activar_modo_doctor sin ser híbrido, el handler rechaza.
# ──────────────────────────────────────────────

register_llm_config(RoleLLMConfig(
    role_name="gerencia",
    system_prompt_template=_SYSTEM_PROMPT,
    tools=_HYBRID_TOOLS,
    tool_handlers=_HYBRID_HANDLERS,
    prompt_context_builder=_build_prompt_context,
))
