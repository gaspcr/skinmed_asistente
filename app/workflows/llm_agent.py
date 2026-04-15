"""
Agente LLM para el workflow de doctores.

Orquesta la conversación con GPT-4o-mini usando function calling.
Mantiene historial en Redis y ejecuta herramientas contra FileMaker.
"""
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytz

from app.config import get_settings
from app.services import redis as redis_svc
from app.services import llm_service
from app.services.filemaker import FileMakerService
from app.services.whatsapp import WhatsAppService
from app.formatters.agenda import AgendaFormatter
from app.formatters.recados import RecadosFormatter
from app.exceptions import ServicioNoDisponibleError

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Redis keys
# ──────────────────────────────────────────────

_HISTORY_TTL = 1800  # 30 min
_MAX_HISTORY_MESSAGES = 20  # Limitar historial para controlar tokens


def _history_key(phone: str) -> str:
    return f"llm:history:{phone}"


def _fallback_key(phone: str) -> str:
    return f"llm:fallback:{phone}"


# ──────────────────────────────────────────────
# Fallback state
# ──────────────────────────────────────────────

async def is_legacy_fallback(phone: str) -> bool:
    """Verifica si la sesión está en modo legacy fallback."""
    val = await redis_svc.get(_fallback_key(phone))
    return val == "1"


async def set_legacy_fallback(phone: str):
    """Marca la sesión como legacy fallback para el resto de la conversación."""
    await redis_svc.set(_fallback_key(phone), "1", ttl=_HISTORY_TTL)
    logger.info("[LLM_AGENT] Sesión %s marcada como legacy fallback", phone)


async def clear_llm_state(phone: str):
    """Limpia todo el estado LLM (historial + fallback flag)."""
    await redis_svc.delete(_history_key(phone))
    await redis_svc.delete(_fallback_key(phone))
    logger.debug("[LLM_AGENT] Estado LLM limpiado para %s", phone)


# ──────────────────────────────────────────────
# Conversation history
# ──────────────────────────────────────────────

async def _get_history(phone: str) -> List[Dict[str, Any]]:
    """Obtiene el historial de conversación desde Redis."""
    raw = await redis_svc.get(_history_key(phone))
    if raw is None:
        return []
    try:
        history = json.loads(raw)
        return history if isinstance(history, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


async def _save_history(phone: str, history: List[Dict[str, Any]]):
    """Guarda el historial de conversación en Redis, limitando tamaño."""
    # Mantener solo los últimos N mensajes (sin contar el system prompt)
    if len(history) > _MAX_HISTORY_MESSAGES:
        history = history[-_MAX_HISTORY_MESSAGES:]
    await redis_svc.set(_history_key(phone), json.dumps(history), ttl=_HISTORY_TTL)


async def _append_to_history(phone: str, message: Dict[str, Any]):
    """Agrega un mensaje al historial."""
    history = await _get_history(phone)
    history.append(message)
    await _save_history(phone, history)


# ──────────────────────────────────────────────
# System prompt
# ──────────────────────────────────────────────

_SYSTEM_PROMPT = """Eres un asistente virtual de la Clínica SkinMed. Ayudas a los doctores a gestionar su agenda y recados a través de WhatsApp.

Tu nombre es Asistente SkinMed. Debes responder siempre en español, de forma concisa y profesional.

Tienes acceso a las siguientes funciones:
1. **Revisar agenda**: Consultar las citas del doctor para un día específico.
2. **Revisar recados**: Ver los recados/mensajes pendientes del doctor.
3. **Publicar recado**: Crear un nuevo recado con una categoría específica.

Las categorías de recados disponibles son:
- "Agendar paciente": Para solicitar que se agende un paciente.
- "Bloquear agenda": Para solicitar bloqueo de horarios.
- "Enviar receta": Para solicitar el envío de una receta.
- "Otros": Para cualquier otro tipo de recado.

Reglas importantes:
- Cuando el doctor quiera ver su agenda, usa la función revisar_agenda.
- Cuando el doctor quiera ver sus recados/mensajes, usa la función revisar_recados.
- Cuando el doctor quiera dejar un recado o mensaje, usa la función publicar_recado. Asegúrate de identificar la categoría correcta y el contenido del mensaje.
- Si el doctor te pide algo que NO puedes hacer con las funciones disponibles, responde EXACTAMENTE con el prefijo "[FALLBACK]" seguido de un mensaje amable indicando que no puedes ayudar con eso.
- Después de completar una acción, pregunta amablemente si necesita algo más.
- No inventes información. Solo reporta lo que devuelven las funciones.
- Sé breve. Los mensajes de WhatsApp deben ser concisos.
- Usa formato WhatsApp: *negrita*, _cursiva_ cuando sea apropiado.

El doctor con el que estás hablando se llama: {doctor_name}"""


# ──────────────────────────────────────────────
# Tool definitions (OpenAI function calling format)
# ──────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "revisar_agenda",
            "description": "Consulta la agenda de citas del doctor para un día específico. Si no se indica fecha, muestra la agenda de hoy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fecha": {
                        "type": "string",
                        "description": "Fecha en formato dd-mm-yy (ejemplo: 05-02-26). Si no se indica, se usa la fecha de hoy.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "revisar_recados",
            "description": "Obtiene los recados pendientes (mensajes/notas) del doctor.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publicar_recado",
            "description": "Publica un nuevo recado/mensaje. El doctor debe indicar la categoría y el contenido del recado.",
            "parameters": {
                "type": "object",
                "properties": {
                    "categoria": {
                        "type": "string",
                        "enum": [
                            "Agendar paciente",
                            "Bloquear agenda",
                            "Enviar receta",
                            "Otros",
                        ],
                        "description": "Categoría del recado",
                    },
                    "mensaje": {
                        "type": "string",
                        "description": "Contenido del recado incluyendo nombre del paciente si aplica",
                    },
                },
                "required": ["categoria", "mensaje"],
            },
        },
    },
]


# ──────────────────────────────────────────────
# Tool execution
# ──────────────────────────────────────────────

async def _execute_tool(tool_name: str, arguments: Dict[str, Any], user) -> str:
    """
    Ejecuta una herramienta y retorna el resultado como string para el LLM.

    Args:
        tool_name: Nombre de la función a ejecutar.
        arguments: Argumentos de la función (parseados del JSON del LLM).
        user: Objeto User con datos del doctor.

    Returns:
        String con el resultado de la herramienta.
    """
    try:
        if tool_name == "revisar_agenda":
            return await _tool_revisar_agenda(user, arguments)
        elif tool_name == "revisar_recados":
            return await _tool_revisar_recados(user)
        elif tool_name == "publicar_recado":
            return await _tool_publicar_recado(user, arguments)
        else:
            return f"Error: función '{tool_name}' no reconocida."
    except ServicioNoDisponibleError as e:
        logger.error("[LLM_AGENT] Error en herramienta %s: %s", tool_name, e)
        return "Error: el sistema FileMaker no está disponible en este momento. Por favor intenta de nuevo en unos minutos."
    except Exception as e:
        logger.exception("[LLM_AGENT] Error inesperado en herramienta %s", tool_name)
        return f"Error inesperado al ejecutar la función: {e}"


async def _tool_revisar_agenda(user, arguments: Dict[str, Any]) -> str:
    """Ejecuta la consulta de agenda."""
    fecha_input = arguments.get("fecha")
    filemaker_date = None

    if fecha_input:
        # Parsear dd-mm-yy a mm-dd-yyyy para FileMaker
        try:
            parts = fecha_input.strip().split("-")
            if len(parts) == 3:
                day, month, year = parts
                full_year = f"20{year}" if len(year) == 2 else year
                date_obj = datetime.strptime(f"{day}-{month}-{full_year}", "%d-%m-%Y")
                filemaker_date = date_obj.strftime("%m-%d-%Y")
            else:
                return "Formato de fecha inválido. Usa dd-mm-yy (ejemplo: 05-02-26)."
        except ValueError:
            return "Fecha inválida. Verifica que el día y mes sean correctos."

    agenda_data = await FileMakerService.get_agenda_raw(user.id, filemaker_date)
    formatted_msg, glossary = AgendaFormatter.format(agenda_data, user.name)

    result = formatted_msg
    if glossary:
        result += f"\n\n{glossary}"

    return result


async def _tool_revisar_recados(user) -> str:
    """Ejecuta la consulta de recados."""
    recados_data = await FileMakerService.get_recados(user.id)

    # Resolver IDs de pacientes a nombres
    pacient_names = {}
    for record in recados_data:
        pac_id = record.get("fieldData", {}).get("_FK_IDPaciente", "")
        if pac_id and pac_id not in pacient_names:
            try:
                name = await FileMakerService.get_pacient_by_id(pac_id)
                pacient_names[pac_id] = name or "Paciente desconocido"
            except Exception:
                pacient_names[pac_id] = "Paciente desconocido"

    return RecadosFormatter.format(recados_data, user.name, user.last_name, pacient_names)


async def _tool_publicar_recado(user, arguments: Dict[str, Any]) -> str:
    """Ejecuta la creación de un recado."""
    categoria = arguments.get("categoria", "Otros")
    mensaje = arguments.get("mensaje", "")

    if not mensaje:
        return "Error: el mensaje del recado no puede estar vacío."

    # Obtener fecha y hora actual en zona horaria de Chile
    tz = pytz.timezone("America/Santiago")
    now = datetime.now(tz)
    fecha = now.strftime("%m-%d-%Y")
    hora = now.strftime("%H:%M:%S")
    fecha_display = now.strftime("%d-%m-%Y")

    # Formatear texto del recado: "autor > fecha > hora\rmensaje"
    texto_formateado = f"{user.name} > {fecha_display} > {hora}\r{mensaje}"

    # Reglas por categoría (misma lógica que legacy)
    guardar_en_fm = categoria != "Bloquear agenda"
    notificar_enfermeria = categoria not in ["Enviar receta", "Agendar paciente"]

    if guardar_en_fm:
        await FileMakerService.create_recado(
            doctor_id=user.id,
            texto=texto_formateado,
            categoria=categoria,
            fecha=fecha,
            hora=hora,
        )

    if notificar_enfermeria:
        settings = get_settings()
        await WhatsAppService.send_template(
            settings.CHIEF_NURSE_PHONE,
            user.name,
            "reenviar_recado_secretaria",
            include_header=False,
            include_body=False,
            header_params=[categoria],
            body_params=[user.name, mensaje],
        )

    # Construir confirmación
    if guardar_en_fm and notificar_enfermeria:
        confirmacion = "Se ha registrado en FileMaker y notificado a enfermería."
    elif guardar_en_fm:
        confirmacion = "Se ha registrado en FileMaker."
    else:
        confirmacion = "Se ha notificado a enfermería."

    return (
        f"Recado procesado exitosamente.\n"
        f"Categoría: {categoria}\n"
        f"Fecha: {fecha_display} — {':'.join(hora.split(':')[:2])}\n"
        f"Recado: {mensaje}\n"
        f"{confirmacion}"
    )


# ──────────────────────────────────────────────
# Main agent loop
# ──────────────────────────────────────────────

_FALLBACK_MARKER = "[FALLBACK]"


async def process_message(user, phone: str, message_text: str) -> str:
    """
    Procesa un mensaje del doctor a través del agente LLM.

    Args:
        user: Objeto User con datos del doctor.
        phone: Número de teléfono.
        message_text: Texto del mensaje del usuario.

    Returns:
        "OK" si el LLM manejó el mensaje exitosamente.
        "FALLBACK" si el LLM no pudo manejar el mensaje y se debe cambiar a legacy.
    """
    doctor_name = f"{user.name} {user.last_name}".strip()

    # Construir system prompt personalizado
    system_msg = {
        "role": "system",
        "content": _SYSTEM_PROMPT.format(doctor_name=doctor_name),
    }

    # Obtener historial existente
    history = await _get_history(phone)

    # Agregar mensaje del usuario
    user_msg = {"role": "user", "content": message_text}
    history.append(user_msg)

    # Construir mensajes completos (system + history)
    messages = [system_msg] + history

    try:
        # Llamar al LLM
        assistant_response = await llm_service.chat_completion(
            messages=messages,
            tools=TOOLS,
        )

        # Procesar tool calls si existen (agent loop)
        max_iterations = 5  # Prevenir loops infinitos
        iteration = 0

        while assistant_response.get("tool_calls") and iteration < max_iterations:
            iteration += 1

            # Agregar respuesta del asistente al historial
            history.append(_serialize_assistant_message(assistant_response))

            # Ejecutar cada tool call
            for tool_call in assistant_response["tool_calls"]:
                func_name = tool_call["function"]["name"]
                func_args_raw = tool_call["function"]["arguments"]

                try:
                    func_args = json.loads(func_args_raw) if isinstance(func_args_raw, str) else func_args_raw
                except json.JSONDecodeError:
                    func_args = {}

                logger.info(
                    "[LLM_AGENT] Ejecutando tool: %s(%s) para %s",
                    func_name, func_args, phone,
                )

                result = await _execute_tool(func_name, func_args, user)

                # Agregar resultado al historial
                tool_result_msg = {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": result,
                }
                history.append(tool_result_msg)

            # Llamar al LLM de nuevo con los resultados
            messages = [system_msg] + history
            assistant_response = await llm_service.chat_completion(
                messages=messages,
                tools=TOOLS,
            )

        # Respuesta final del LLM (sin tool calls)
        final_content = assistant_response.get("content", "")

        # Verificar si el LLM señala fallback
        if final_content and _FALLBACK_MARKER in final_content:
            logger.info("[LLM_AGENT] LLM señaló fallback para %s", phone)
            # No guardar este intercambio en el historial
            return "FALLBACK"

        # Agregar respuesta final al historial
        history.append({"role": "assistant", "content": final_content})
        await _save_history(phone, history)

        # Enviar respuesta al usuario
        if final_content:
            await WhatsAppService.send_message(phone, final_content)
        else:
            logger.warning("[LLM_AGENT] LLM retornó contenido vacío para %s", phone)
            await WhatsAppService.send_message(
                phone,
                "Lo siento, no pude procesar tu solicitud. ¿Puedes intentar de nuevo?"
            )

        return "OK"

    except ServicioNoDisponibleError as e:
        logger.error("[LLM_AGENT] Servicio no disponible: %s — activando fallback para %s", e, phone)
        return "FALLBACK"
    except Exception as e:
        logger.exception("[LLM_AGENT] Error inesperado procesando mensaje de %s", phone)
        return "FALLBACK"


def _serialize_assistant_message(response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Serializa la respuesta del asistente para guardar en historial.
    Necesario porque tool_calls tienen estructura específica.
    """
    msg: Dict[str, Any] = {"role": "assistant"}

    content = response.get("content")
    if content:
        msg["content"] = content
    else:
        msg["content"] = None

    if response.get("tool_calls"):
        msg["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["function"]["name"],
                    "arguments": tc["function"]["arguments"],
                },
            }
            for tc in response["tool_calls"]
        ]

    return msg
