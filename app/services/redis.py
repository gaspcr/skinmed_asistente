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
