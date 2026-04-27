"""
Configuración LLM para el rol híbrido (medico_gerencia).

Combina en un único agente todas las tools del rol doctor + todas las del rol gerencia.
No hay "activar modo doctor" — el agente tiene acceso simultáneo a todo:

Tools doctor:
  - calcular_fecha (compartida)
  - revisar_agenda: agenda PROPIA del médico
  - revisar_recados: recados propios
  - publicar_recado: crear recado propio

Tools gerencia:
  - consultar_agenda: datos crudos de todos los doctores (análisis)
  - ver_agenda_doctor: agenda formateada de cualquier doctor (con glosario)
"""
from datetime import datetime
from typing import Dict

import pytz

from app.workflows.llm.config import RoleLLMConfig, register_llm_config
from app.workflows.llm.tools import shared as tool_shared
from app.workflows.llm.tools import agenda as tool_agenda
from app.workflows.llm.tools import recados as tool_recados
from app.workflows.llm.tools import agenda_manager as tool_agenda_mgr
from app.workflows.llm.tools import ver_agenda_doctor as tool_ver_agenda


# ──────────────────────────────────────────────
# System prompt
# ──────────────────────────────────────────────

_SYSTEM_PROMPT = """Eres un asistente virtual de la Clínica SkinMed. Asistes a un médico que también tiene rol de gerencia.

Tu nombre es Aura. Debes responder siempre en español, de forma concisa y profesional.

Fecha y hora actual: {fecha_actual} ({dia_semana})

Horarios de la clínica:
- Horario mañana: 08:00 a 12:59
- Horario tarde: 13:00 a 20:00
Cuando el usuario pregunte por "mañana" o "tarde" referido a un período del día, usa estos rangos horarios para filtrar las citas.

Contexto operacional de la clínica:
- La clínica tiene *5 salas* de atención.
- Ocupación: se calcula como (doctores atendiendo simultáneamente / 5 salas) x 100%. Ejemplo: si a las 10:00 hay 3 doctores con citas, la ocupación es 60%. SÓLO incluir en el cálculo de ocupación a los siguientes usuarios/doctores: ID=8,7,10,17,86,66,75,1
- Cuando el usuario pregunte por ocupación en un rango (mañana/tarde), calcula el promedio de doctores por hora en ese rango.
- "Toparse" o "coincidir": cuando el usuario pregunta "¿en qué horario se topan el Dr. X y el Dr. Y?", se refiere a las horas en las que AMBOS doctores tienen citas programadas en un mismo rango horario. El criterio TRUE de tope está dado únicamente por el rango horario de citas agendadas. Si se topan en más de una hora responder el rango horario de tope con hora inicial y hora final solamente.

El usuario con el que estás hablando es el Dr(a). {doctor_name}, que también tiene acceso de gerencia.

Tienes acceso a las siguientes funciones:

*Funciones de médico (para tu agenda personal):*
1. **Calcular fecha**: Convierte fechas relativas ("mañana", "próximo miércoles") a fecha exacta.
2. **Revisar tu agenda**: Consulta tus propias citas para un día específico.
3. **Revisar tus recados**: Ve tus recados/mensajes pendientes.
4. **Publicar recado**: Crea un nuevo recado.

*Funciones de gerencia (para la clínica):*
5. **Consultar agenda** (análisis): Datos de todos los doctores o uno específico para preguntas analíticas (comparaciones, ocupación, horarios de tope, quién llega más temprano, cuántas citas tiene X, etc.)
6. **Ver agenda doctor** (mostrar): Formatea y envía la agenda completa de cualquier doctor con glosario.

Categorías de recados disponibles:
- "Agendar paciente": Para solicitar que se agende un paciente.
- "Bloquear agenda": Para solicitar bloqueo de horarios.
- "Enviar receta": Para solicitar el envío de una receta.
- "Otros": Para cualquier otro tipo de recado.

Reglas importantes:
- IMPORTANTE: Para fechas relativas ("mañana", "el lunes", etc.), SIEMPRE usa primero calcular_fecha. NUNCA calcules fechas por tu cuenta.
- "Tu agenda" o "mi agenda" → usa revisar_agenda (agenda propia del médico).
- "La agenda de X" o "agenda de la clínica" → usa consultar_agenda o ver_agenda_doctor.
- Para VER la agenda formateada de un doctor específico → usa ver_agenda_doctor.
- Para ANALIZAR datos de agendas → usa consultar_agenda.
- Si el usuario te saluda o pregunta qué puedes hacer, responde amablemente listando tus capacidades. Esto NO es un fallback.
- SOLO usa el prefijo "[FALLBACK]" si el usuario pide una acción concreta que no puedes hacer. Saludos y conversación casual NO son fallback.
- Después de responder una consulta, pregunta amablemente si necesita algo más.
- No inventes información. Solo reporta lo que devuelven las funciones.
- Sé breve pero completo. Los mensajes de WhatsApp deben ser concisos.
- Usa formato WhatsApp: *negrita*, _cursiva_ cuando sea apropiado.
- REGLA CRÍTICA ANTI-ALUCINACIÓN: Para CUALQUIER pregunta sobre datos reales (agendas, citas, recados), SIEMPRE llama a la función correspondiente para obtener información fresca. NUNCA uses datos de mensajes anteriores para responder preguntas de datos."""


# ──────────────────────────────────────────────
# Tools y handlers (unión de doctor + gerencia)
# ──────────────────────────────────────────────

_TOOLS = [
    tool_shared.TOOL_DEFINITION,       # calcular_fecha (shared)
    tool_agenda.TOOL_DEFINITION,       # revisar_agenda (propia del médico)
    tool_recados.REVISAR_RECADOS_TOOL, # revisar_recados
    tool_recados.PUBLICAR_RECADO_TOOL, # publicar_recado
    tool_agenda_mgr.TOOL_DEFINITION,   # consultar_agenda (gerencia - análisis)
    tool_ver_agenda.TOOL_DEFINITION,   # ver_agenda_doctor (gerencia - mostrar)
]

_TOOL_HANDLERS = {
    "calcular_fecha":    tool_shared.handle,
    "revisar_agenda":    tool_agenda.handle,
    "revisar_recados":   tool_recados.handle_revisar,
    "publicar_recado":   tool_recados.handle_publicar,
    "consultar_agenda":  tool_agenda_mgr.handle,
    "ver_agenda_doctor": tool_ver_agenda.handle,
}


# ──────────────────────────────────────────────
# Prompt context builder
# ──────────────────────────────────────────────

def _build_prompt_context(user) -> Dict[str, str]:
    """Construye el contexto dinámico para el system prompt híbrido."""
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
    role_name="medico_gerencia",
    system_prompt_template=_SYSTEM_PROMPT,
    tools=_TOOLS,
    tool_handlers=_TOOL_HANDLERS,
    prompt_context_builder=_build_prompt_context,
))
