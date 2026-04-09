"""
Servicio de LLM con Instructor para Function Calling estructurado.

Utiliza OpenAI + Instructor para forzar al modelo a responder con
clases Pydantic tipadas, eliminando la necesidad de parsing manual.
El historial de conversacion se mantiene en Redis para darle contexto
al modelo entre mensajes.
"""
import logging
from datetime import datetime
from typing import Union, Type

import httpx
import instructor
from openai import AsyncOpenAI

import pytz

from app.config import get_settings
from app.services import redis as redis_svc

logger = logging.getLogger(__name__)

# Cliente de Instructor (singleton lazy)
_client: instructor.AsyncInstructor | None = None


def _get_client() -> instructor.AsyncInstructor:
    """Obtiene o crea el cliente de Instructor parcheado sobre OpenAI."""
    global _client
    if _client is None:
        settings = get_settings()
        openai_client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        _client = instructor.from_openai(openai_client)
        logger.info("Cliente Instructor/OpenAI inicializado")
    return _client


def _get_datetime_context() -> tuple[str, str, str]:
    """Obtiene fecha, día de la semana y hora actual en español (Chile)."""
    tz = pytz.timezone("America/Santiago")
    now = datetime.now(tz)
    fecha_hoy = now.strftime("%d de %B de %Y")
    dia_semana = now.strftime("%A")
    hora_actual = now.strftime("%H:%M")

    dias_map = {
        "Monday": "lunes", "Tuesday": "martes", "Wednesday": "miércoles",
        "Thursday": "jueves", "Friday": "viernes", "Saturday": "sábado",
        "Sunday": "domingo",
    }
    meses_map = {
        "January": "enero", "February": "febrero", "March": "marzo",
        "April": "abril", "May": "mayo", "June": "junio",
        "July": "julio", "August": "agosto", "September": "septiembre",
        "October": "octubre", "November": "noviembre", "December": "diciembre",
    }

    for en, es in meses_map.items():
        fecha_hoy = fecha_hoy.replace(en, es)
    dia_semana = dias_map.get(dia_semana, dia_semana)

    return fecha_hoy, dia_semana, hora_actual


# Instrucciones específicas por rol
_ROLE_INSTRUCTIONS: dict[str, str] = {
    "medico": """Estás hablando con el/la Dr(a). {user_name}, quien es médico de la clínica.

TU COMPORTAMIENTO:
- Responde siempre en español neutro, de forma profesional pero cercana.
- Sé conciso: las respuestas se envían por WhatsApp y deben ser breves.
- Cuando el doctor pida ver su agenda sin especificar fecha, usa la herramienta de agenda de hoy.
- Cuando mencione una fecha (mañana, lunes, 15 de abril, etc.), calcula la fecha exacta usando la fecha actual como referencia y usa la herramienta de agenda.
- El formato de fecha para el sistema es MM-DD-YYYY (mes-día-año). El doctor puede escribir fechas en cualquier formato (dd-mm-yy, texto, etc.), tú SIEMPRE debes convertirlas a MM-DD-YYYY.
- Si el doctor quiere dejar un recado, identifica la categoría correcta y copia su mensaje textualmente. Si no queda claro dar aviso y consultar por la información necesaria.
- Si el doctor solo saluda o hace una pregunta general, responde amablemente y ofrece las opciones disponibles.
- NUNCA inventes datos médicos, agendas o información de pacientes.

ACCIONES DISPONIBLES:
1. Consultar agenda (hoy o cualquier fecha específica)
2. Enviar recado (agendar paciente, enviar receta, bloquear agenda, u otro)
3. Ver recados pendientes
4. Despedirse / terminar la conversación
5. Responder de forma conversacional (saludos, dudas, etc.)""",

    "gerencia": """Estás hablando con {user_name}, quien es parte de la gerencia de la clínica.

TU COMPORTAMIENTO:
- Responde siempre en español neutro, de forma profesional pero cercana.
- Sé conciso: las respuestas se envían por WhatsApp y deben ser breves.
- Cuando pida ver agendas sin especificar fecha, usa la herramienta de agenda de hoy. 
- Cuando mencione una fecha, calcula la fecha exacta usando la fecha actual como referencia.
- El formato de fecha para el sistema es MM-DD-YYYY (mes-día-año). El usuario puede escribir fechas en cualquier formato, tú SIEMPRE debes convertirlas a MM-DD-YYYY.
- Si solo saluda o hace una pregunta general, responde amablemente y ofrece las opciones disponibles.
- NUNCA inventes datos médicos, agendas o información de pacientes.""",
}


def _build_system_prompt(user_name: str, user_role: str) -> str:
    """
    Construye el system prompt del agente para un rol específico.

    Usa instrucciones específicas por rol si existen, o genera
    un prompt genérico como fallback.
    """
    fecha_hoy, dia_semana, hora_actual = _get_datetime_context()

    role_key = user_role.lower()
    role_instructions = _ROLE_INSTRUCTIONS.get(role_key)

    if role_instructions:
        role_block = role_instructions.format(user_name=user_name)
    else:
        role_block = f"""Estás hablando con {user_name}, quien tiene el rol de {user_role} en la clínica.

TU COMPORTAMIENTO:
- Responde siempre en español neutro, de forma profesional pero cercana.
- Sé conciso: las respuestas se envían por WhatsApp y deben ser breves.
- NUNCA inventes datos médicos, agendas o información de pacientes.
- Si no queda claro qué quiere, ofrece las opciones disponibles."""

    return f"""Eres el asistente virtual de la Clínica SkinMed por WhatsApp.
{role_block}

FECHA Y HORA ACTUAL:
- Hoy es {dia_semana}, {fecha_hoy}
- Hora actual: {hora_actual} (hora de Chile)"""


async def classify_intent(
    phone: str,
    message: str,
    user_name: str,
    user_role: str,
    response_model: Type,
) -> Union[object, None]:
    """
    Clasifica la intencion del usuario usando el LLM.

    Envia el historial de conversacion + el mensaje nuevo a OpenAI
    con Instructor, forzando una respuesta tipada con Pydantic.

    Args:
        phone: Numero de telefono (para recuperar historial)
        message: El mensaje de texto del usuario
        user_name: Nombre del doctor
        user_role: Rol del usuario
        response_model: Union de las clases Pydantic permitidas

    Returns:
        Instancia de una de las clases Pydantic del response_model
    """
    settings = get_settings()
    client = _get_client()

    # Construir mensajes con historial
    system_prompt = _build_system_prompt(user_name, user_role)
    messages = [{"role": "system", "content": system_prompt}]

    # Agregar historial de conversacion desde Redis
    history = await redis_svc.get_history(phone)
    messages.extend(history)

    # Agregar el mensaje actual del usuario
    messages.append({"role": "user", "content": message})

    try:
        response = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            response_model=response_model,
            messages=messages,
            temperature=0.3,  # Baja temperatura para decisiones consistentes
            max_retries=2,    # Instructor reintenta si el JSON no valida
        )

        # Guardar en historial: mensaje del usuario
        await redis_svc.push_history(
            phone, "user", message,
            max_messages=settings.LLM_MAX_HISTORY,
        )

        # El wrapper DoctorToolCall tiene .accion con la tool real
        accion = getattr(response, "accion", response)

        # Guardar en historial: respuesta resumida del asistente
        # (guardamos el tipo de accion para contexto, no el JSON completo)
        assistant_summary = _summarize_response(accion)
        await redis_svc.push_history(
            phone, "assistant", assistant_summary,
            max_messages=settings.LLM_MAX_HISTORY,
        )

        logger.info(
            "[LLM] Clasificacion para %s: %s",
            phone, type(accion).__name__,
        )
        return response

    except Exception as e:
        logger.error("[LLM] Error al clasificar intencion de %s: %s", phone, e)
        raise


# Campos de resumen por nombre de clase.
# Orden de prioridad para buscar el campo que resume la respuesta.
_SUMMARY_FIELDS: dict[str, list[str]] = {
    "ConsultarAgenda": ["mensaje_confirmacion"],
    "EnviarRecado": ["mensaje_confirmacion"],
    "VerRecados": ["mensaje_confirmacion"],
    "Despedirse": ["mensaje_despedida"],
    "ResponderConversacion": ["mensaje"],
}


def _summarize_response(response: object) -> str:
    """
    Genera un resumen legible de la respuesta del LLM para guardar en el historial.
    Esto ayuda al modelo a recordar qué hizo en turnos anteriores.
    """
    class_name = type(response).__name__
    fields = _SUMMARY_FIELDS.get(class_name)

    if fields:
        for field in fields:
            value = getattr(response, field, None)
            if value:
                return value

    return f"[Acción: {class_name}]"
