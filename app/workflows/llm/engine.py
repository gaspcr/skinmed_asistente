"""
Motor LLM genérico — agnóstico al rol.

Orquesta la conversación con el LLM usando function calling.
Mantiene historial en Redis y ejecuta herramientas delegando
al RoleLLMConfig correspondiente.
"""
import json
import logging
from datetime import datetime
from typing import Any, Dict, List

import pytz

from app.config import get_settings
from app.services import redis as redis_svc
from app.services import llm_service
from app.services.whatsapp import WhatsAppService
from app.exceptions import ServicioNoDisponibleError
from app.workflows.llm.config import get_llm_config

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────

_HISTORY_TTL = 1800  # 30 min
_MAX_HISTORY_MESSAGES = 20
_FALLBACK_MARKER = "[FALLBACK]"


# ──────────────────────────────────────────────
# Redis keys
# ──────────────────────────────────────────────

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
    logger.info("[LLM_ENGINE] Sesión %s marcada como legacy fallback", phone)


async def clear_llm_state(phone: str):
    """Limpia todo el estado LLM (historial + fallback flag)."""
    await redis_svc.delete(_history_key(phone))
    await redis_svc.delete(_fallback_key(phone))
    logger.debug("[LLM_ENGINE] Estado LLM limpiado para %s", phone)


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
    if len(history) > _MAX_HISTORY_MESSAGES:
        history = history[-_MAX_HISTORY_MESSAGES:]
    await redis_svc.set(_history_key(phone), json.dumps(history), ttl=_HISTORY_TTL)


# ──────────────────────────────────────────────
# Tool execution (delegada al config del rol)
# ──────────────────────────────────────────────

async def _execute_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    user,
    phone: str,
    tool_handlers: Dict[str, Any],
) -> str:
    """
    Ejecuta una herramienta usando los handlers registrados en el RoleLLMConfig.

    Args:
        tool_name: Nombre de la función a ejecutar.
        arguments: Argumentos de la función.
        user: Objeto User.
        phone: Número de teléfono.
        tool_handlers: Mapeo nombre → handler del RoleLLMConfig.

    Returns:
        String con el resultado de la herramienta.
    """
    handler = tool_handlers.get(tool_name)
    if handler is None:
        return f"Error: función '{tool_name}' no reconocida."

    try:
        return await handler(user, phone, arguments)
    except ServicioNoDisponibleError as e:
        logger.error("[LLM_ENGINE] Error en herramienta %s: %s", tool_name, e)
        return (
            "Error: el sistema FileMaker no está disponible en este momento. "
            "Por favor intenta de nuevo en unos minutos."
        )
    except Exception as e:
        logger.exception("[LLM_ENGINE] Error inesperado en herramienta %s", tool_name)
        return f"Error inesperado al ejecutar la función: {e}"


# ──────────────────────────────────────────────
# Main agent loop
# ──────────────────────────────────────────────

async def process_message(user, phone: str, message_text: str, role: str) -> str:
    """
    Procesa un mensaje a través del agente LLM usando la configuración del rol.

    Args:
        user: Objeto User con datos del usuario.
        phone: Número de teléfono.
        message_text: Texto del mensaje del usuario.
        role: Nombre del rol (para buscar el RoleLLMConfig).

    Returns:
        "OK" si el LLM manejó el mensaje exitosamente.
        "FALLBACK" si el LLM no pudo manejar el mensaje.
    """
    config = get_llm_config(role)
    if config is None:
        logger.error("[LLM_ENGINE] No hay config LLM registrada para rol '%s'", role)
        return "FALLBACK"

    # Construir contexto para el prompt usando el builder del rol
    prompt_context = config.prompt_context_builder(user)

    # Construir system prompt personalizado
    system_msg = {
        "role": "system",
        "content": config.system_prompt_template.format(**prompt_context),
    }

    # Obtener historial existente
    history = await _get_history(phone)

    # Agregar mensaje del usuario
    user_msg = {"role": "user", "content": message_text}
    logger.info(
        "[LLM_DEBUG] Mensaje usuario [%s] para %s: %s",
        role, phone, message_text,
    )
    history.append(user_msg)

    # Construir mensajes completos (system + history)
    messages = [system_msg] + history

    try:
        # Llamar al LLM
        assistant_response = await llm_service.chat_completion(
            messages=messages,
            tools=config.tools,
        )

        # Log de la respuesta inicial de OpenAI
        _log_llm_response(assistant_response, phone, role, step="initial")

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
                    func_args = (
                        json.loads(func_args_raw)
                        if isinstance(func_args_raw, str)
                        else func_args_raw
                    )
                except json.JSONDecodeError:
                    func_args = {}

                logger.info(
                    "[LLM_ENGINE] Ejecutando tool: %s(%s) para %s [rol=%s]",
                    func_name, func_args, phone, role,
                )

                result = await _execute_tool(
                    func_name, func_args, user, phone, config.tool_handlers,
                )

                # Log del resultado para debugging
                result_preview = result[:500] + "..." if len(result) > 500 else result
                logger.info(
                    "[LLM_DEBUG] Tool result %s -> %s [%s]",
                    func_name, result_preview, phone,
                )

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
                tools=config.tools,
            )

            # Log de la respuesta post-tool de OpenAI
            _log_llm_response(assistant_response, phone, role, step=f"post-tool-{iteration}")

        # Respuesta final del LLM (sin tool calls)
        final_content = assistant_response.get("content", "")

        # Verificar si el LLM señala fallback
        if final_content and _FALLBACK_MARKER in final_content:
            logger.info("[LLM_ENGINE] LLM señaló fallback para %s [rol=%s]", phone, role)
            # No guardar este intercambio en el historial
            return "FALLBACK"

        # Agregar respuesta final al historial
        history.append({"role": "assistant", "content": final_content})
        await _save_history(phone, history)

        # Verificar si una tool ya envió el mensaje completo (ej: ver_agenda_doctor)
        # Revisamos si la string [AGENDA_ENVIADA] está en final_content o en el resultado de la tool
        agenda_enviada = False
        if final_content and "[AGENDA_ENVIADA]" in final_content:
            agenda_enviada = True
        else:
            # Buscar en los mensajes desde la última interacción del usuario
            for msg in reversed(history):
                if msg.get("role") == "user":
                    break
                if msg.get("role") == "tool" and "[AGENDA_ENVIADA]" in str(msg.get("content", "")):
                    agenda_enviada = True
                    break

        if agenda_enviada:
            logger.info(
                "[LLM_ENGINE] Tool ya envió el mensaje completo para %s — suprimiendo respuesta del LLM",
                phone,
            )
            return "OK"

        # Enviar respuesta al usuario
        if final_content:
            logger.info(
                "[LLM_DEBUG] Respuesta final LLM (%d chars) para %s: %s",
                len(final_content), phone,
                final_content[:300] + "..." if len(final_content) > 300 else final_content,
            )
            await WhatsAppService.send_message(phone, final_content)
        else:
            logger.warning("[LLM_ENGINE] LLM retornó contenido vacío para %s", phone)
            await WhatsAppService.send_message(
                phone,
                "Lo siento, no pude procesar tu solicitud. ¿Puedes intentar de nuevo?",
            )

        return "OK"

    except ServicioNoDisponibleError as e:
        logger.error(
            "[LLM_ENGINE] Servicio no disponible: %s — activando fallback para %s",
            e, phone,
        )
        return "FALLBACK"
    except Exception:
        logger.exception(
            "[LLM_ENGINE] Error inesperado procesando mensaje de %s [rol=%s]",
            phone, role,
        )
        return "FALLBACK"


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _serialize_assistant_message(response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Serializa la respuesta del asistente para guardar en historial.
    Necesario porque tool_calls tienen estructura específica.
    """
    msg: Dict[str, Any] = {"role": "assistant"}

    content = response.get("content")
    msg["content"] = content if content else None

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


def _log_llm_response(response: Dict[str, Any], phone: str, role: str, step: str):
    """
    Loguea la respuesta de OpenAI de forma legible para debugging.

    Muestra:
    - Si el modelo devolvió tool_calls (con nombre y argumentos)
    - Si el modelo devolvió content directo (truncado)
    - El paso del agent loop (initial, post-tool-1, etc.)
    """
    tool_calls = response.get("tool_calls")
    content = response.get("content")

    if tool_calls:
        calls_summary = ", ".join(
            f"{tc['function']['name']}({tc['function']['arguments']})"
            for tc in tool_calls
        )
        logger.info(
            "[LLM_DEBUG] OpenAI response [%s] step=%s: TOOL_CALLS=[%s] content=%s [%s]",
            role, step, calls_summary,
            repr(content[:100]) if content else "null",
            phone,
        )
    elif content:
        preview = content[:300] + "..." if len(content) > 300 else content
        logger.info(
            "[LLM_DEBUG] OpenAI response [%s] step=%s: CONTENT (%d chars)=%s [%s]",
            role, step, len(content), preview, phone,
        )
    else:
        logger.warning(
            "[LLM_DEBUG] OpenAI response [%s] step=%s: EMPTY (no content, no tool_calls) [%s]",
            role, step, phone,
        )
