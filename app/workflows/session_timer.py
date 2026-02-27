"""
Timer de inactividad de sesión.

Mecanismo: cada interacción del usuario guarda un timestamp en Redis.
Se lanza un asyncio task que espera SESSION_TIMEOUT_SECONDS y luego
verifica si el timestamp sigue siendo el mismo (= no hubo actividad).
Si no cambió, envía mensaje de cierre y limpia el estado del workflow.
"""
import asyncio
import logging
import time
from typing import Dict

from app.services import redis as redis_svc
from app.workflows import state as workflow_state
from app.services.whatsapp import WhatsAppService
from app.config import get_settings

logger = logging.getLogger(__name__)

# Tareas de timeout activas por teléfono (para poder cancelarlas)
_active_timers: Dict[str, asyncio.Task] = {}


def _key(phone: str) -> str:
    return f"session:activity:{phone}"


async def touch(phone: str):
    """Registra actividad del usuario. Llamar en cada interacción."""
    ts = str(time.time())
    # TTL de 3x el timeout para limpieza automática
    settings = get_settings()
    ttl = settings.SESSION_TIMEOUT_SECONDS * 3
    await redis_svc.set(_key(phone), ts, ttl=ttl)
    return ts


def schedule_timeout(phone: str):
    """Programa un task de timeout. Cancela cualquier timer previo."""
    # Cancelar timer anterior si existe
    old_task = _active_timers.pop(phone, None)
    if old_task and not old_task.done():
        old_task.cancel()

    task = asyncio.create_task(_timeout_check(phone))
    _active_timers[phone] = task

    # Limpiar referencia cuando termine
    task.add_done_callback(lambda t: _active_timers.pop(phone, None))


async def cancel(phone: str):
    """Cancela el timer de timeout para un teléfono (ej: al escribir 'salir')."""
    task = _active_timers.pop(phone, None)
    if task and not task.done():
        task.cancel()
    # Limpiar timestamp para que un timer re-creado por main.py no dispare
    await redis_svc.delete(_key(phone))
    logger.debug("Timer de inactividad cancelado para %s", phone)


async def _timeout_check(phone: str):
    """Espera el timeout y verifica si hubo actividad nueva."""
    settings = get_settings()
    timeout = settings.SESSION_TIMEOUT_SECONDS

    # Capturar el timestamp actual antes de esperar
    ts_before = await redis_svc.get(_key(phone))

    try:
        await asyncio.sleep(timeout)
    except asyncio.CancelledError:
        return

    # Verificar si el timestamp cambió (= hubo actividad nueva)
    ts_after = await redis_svc.get(_key(phone))

    if ts_after is None:
        # Timestamp eliminado (el usuario salió explícitamente)
        return

    if ts_before != ts_after:
        # Hubo actividad nueva, otro timer se encargará
        return

    # No hubo actividad → timeout
    logger.info("Timeout de inactividad para %s (%ds)", phone, timeout)

    # Limpiar estado y timestamp
    await workflow_state.clear_state(phone)
    await redis_svc.delete(_key(phone))

    await WhatsAppService.send_message(
        phone,
        "La sesión se cerró por inactividad. "
        "Cuando necesites algo, escribe cualquier mensaje para volver a comenzar."
    )
