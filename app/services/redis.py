"""
Servicio de Redis para manejo de estado y caches.
Provee conexion asincrona y metodos utilitarios para get/set con TTL.
"""
import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_redis: Optional[aioredis.Redis] = None


async def init(url: str):
    """Inicializa la conexion a Redis."""
    global _redis
    _redis = aioredis.from_url(url, decode_responses=True)
    await _redis.ping()
    logger.info("Conexion a Redis establecida")


async def close():
    """Cierra la conexion a Redis."""
    global _redis
    if _redis:
        await _redis.close()
        _redis = None
        logger.info("Conexion a Redis cerrada")


def _get_client() -> aioredis.Redis:
    """Retorna el cliente Redis, lanzando error si no esta inicializado."""
    if _redis is None:
        raise RuntimeError("Redis no inicializado. Llamar a init() primero.")
    return _redis


async def get(key: str) -> Optional[str]:
    """Obtiene un valor string por clave."""
    return await _get_client().get(key)


async def set(key: str, value: str, ttl: Optional[int] = None):
    """Guarda un valor string con TTL opcional (en segundos)."""
    if ttl:
        await _get_client().setex(key, ttl, value)
    else:
        await _get_client().set(key, value)


async def delete(key: str):
    """Elimina una clave."""
    await _get_client().delete(key)


async def get_json(key: str) -> Optional[Any]:
    """Obtiene y deserializa un valor JSON."""
    raw = await get(key)
    if raw is None:
        return None
    return json.loads(raw)


async def set_json(key: str, value: Any, ttl: Optional[int] = None):
    """Serializa y guarda un valor como JSON con TTL opcional."""
    await set(key, json.dumps(value), ttl=ttl)


async def verificar_rate_limit(key: str, limite: int, ventana_ttl: int) -> bool:
    """
    Verifica rate limit usando contador simple en Redis.

    Args:
        key: Clave de Redis (ej: 'ratelimit:5691234567')
        limite: Numero maximo de operaciones permitidas en la ventana
        ventana_ttl: Duracion de la ventana en segundos

    Returns:
        True si la operacion esta permitida, False si se excede el limite
    """
    cliente = _get_client()
    conteo = await cliente.incr(key)
    if conteo == 1:
        await cliente.expire(key, ventana_ttl)
    return conteo <= limite


# --- Historial de conversacion para LLM ---

_HISTORY_TTL = 3600  # 1 hora de TTL para historial


def _history_key(phone: str) -> str:
    """Clave Redis para el historial de conversacion de un usuario."""
    return f"llm:history:{phone}"


async def push_history(phone: str, role: str, content: str, max_messages: int = 10):
    """
    Agrega un mensaje al historial de conversacion en Redis.

    Args:
        phone: Numero de telefono del usuario
        role: Rol del mensaje ('user' o 'assistant')
        content: Contenido del mensaje
        max_messages: Maximo de mensajes a retener
    """
    cliente = _get_client()
    key = _history_key(phone)
    entry = json.dumps({"role": role, "content": content})
    await cliente.rpush(key, entry)
    await cliente.ltrim(key, -max_messages, -1)
    await cliente.expire(key, _HISTORY_TTL)


async def get_history(phone: str) -> list[dict]:
    """
    Obtiene el historial de conversacion desde Redis.

    Returns:
        Lista de dicts con 'role' y 'content', en orden cronologico.
    """
    cliente = _get_client()
    key = _history_key(phone)
    raw_list = await cliente.lrange(key, 0, -1)
    return [json.loads(entry) for entry in raw_list]


async def clear_history(phone: str):
    """Limpia el historial de conversacion de un usuario."""
    await delete(_history_key(phone))

