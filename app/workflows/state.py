"""
Abstraccion del estado de workflows multi-paso.
Centraliza la logica de guardar/recuperar/limpiar estado en Redis
para evitar claves ad-hoc dispersas en los workflows.
"""
import json
import logging
from typing import Any, Dict, Optional

from app.services import redis as redis_svc

logger = logging.getLogger(__name__)

# TTL por defecto para estado de workflow (30 minutos)
DEFAULT_TTL = 1800


def _key(phone: str) -> str:
    """Genera la clave Redis para el estado de un workflow."""
    return f"workflow:state:{phone}"


async def get_state(phone: str) -> Optional[Dict[str, Any]]:
    """
    Obtiene el estado actual del workflow para un telefono.

    Returns:
        Dict con el estado o None si no existe/expiró.
    """
    raw = await redis_svc.get(_key(phone))
    if raw is None:
        return None

    try:
        state = json.loads(raw)
        if isinstance(state, dict):
            return state
        # Compatibilidad: si el estado es un string simple (ej. "waiting_for_date"),
        # lo envolvemos en un dict
        return {"step": state}
    except (json.JSONDecodeError, TypeError):
        # Si no es JSON valido, tratarlo como step simple
        return {"step": raw}


async def set_state(
    phone: str,
    step: str,
    data: Optional[Dict[str, Any]] = None,
    ttl: int = DEFAULT_TTL,
):
    """
    Guarda el estado del workflow.

    Args:
        phone: Numero de telefono del usuario
        step: Nombre del paso actual (ej: "waiting_for_date")
        data: Datos adicionales del paso (opcional)
        ttl: Tiempo de vida en segundos (default: 30 min)
    """
    state = {"step": step}
    if data:
        state["data"] = data

    await redis_svc.set(_key(phone), json.dumps(state), ttl=ttl)
    logger.debug("Estado workflow guardado para %s: paso=%s", phone, step)


async def clear_state(phone: str):
    """Limpia el estado del workflow para un telefono."""
    await redis_svc.delete(_key(phone))
    logger.debug("Estado workflow limpiado para %s", phone)


async def get_step(phone: str) -> Optional[str]:
    """
    Atajo para obtener solo el nombre del paso actual.

    Returns:
        Nombre del paso o None si no hay estado.
    """
    state = await get_state(phone)
    if state is None:
        return None
    return state.get("step")


async def get_data(phone: str) -> Optional[Dict[str, Any]]:
    """
    Atajo para obtener solo los datos del paso actual.

    Returns:
        Dict con datos adicionales o None.
    """
    state = await get_state(phone)
    if state is None:
        return None
    return state.get("data")
