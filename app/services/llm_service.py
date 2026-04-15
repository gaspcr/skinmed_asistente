"""
Servicio de LLM: wrapper para OpenAI Chat Completions API con function calling.
Usa el cliente httpx existente del proyecto para evitar dependencias adicionales.
"""
import json
import logging
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.services import http as http_svc
from app.exceptions import ServicioNoDisponibleError

logger = logging.getLogger(__name__)

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"


async def chat_completion(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.3,
) -> Dict[str, Any]:
    """
    Llama a la API de OpenAI Chat Completions con soporte para function calling.

    Args:
        messages: Lista de mensajes (role/content) para enviar al modelo.
        tools: Lista de definiciones de herramientas (function calling).
        temperature: Temperatura para la generación (default bajo para consistencia).

    Returns:
        Dict con la respuesta completa del modelo (message con content y/o tool_calls).

    Raises:
        ServicioNoDisponibleError: Si la API falla o no responde.
    """
    settings = get_settings()

    if not settings.OPENAI_API_KEY:
        raise ServicioNoDisponibleError("OpenAI", "OPENAI_API_KEY no configurada")

    client = http_svc.get_client()

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
    }

    payload: Dict[str, Any] = {
        "model": settings.OPENAI_MODEL,
        "messages": messages,
        "temperature": temperature,
    }

    if tools:
        payload["tools"] = tools

    try:
        resp = await client.post(
            OPENAI_API_URL,
            json=payload,
            headers=headers,
            timeout=30.0,
        )

        if resp.status_code == 200:
            data = resp.json()
            return data["choices"][0]["message"]

        # Errores conocidos de la API
        if resp.status_code == 429:
            logger.warning("OpenAI rate limit alcanzado")
            raise ServicioNoDisponibleError("OpenAI", "Rate limit alcanzado")

        if resp.status_code >= 500:
            logger.error("Error del servidor OpenAI: HTTP %d", resp.status_code)
            raise ServicioNoDisponibleError("OpenAI", f"Error del servidor: HTTP {resp.status_code}")

        # Otros errores
        logger.error("Error inesperado de OpenAI: HTTP %d — %s", resp.status_code, resp.text[:200])
        raise ServicioNoDisponibleError("OpenAI", f"HTTP {resp.status_code}")

    except ServicioNoDisponibleError:
        raise
    except Exception as e:
        logger.error("Error de conexion con OpenAI: %s", e)
        raise ServicioNoDisponibleError("OpenAI", f"Error de conexion: {e}")
