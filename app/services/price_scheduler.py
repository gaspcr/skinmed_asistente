"""
Scheduler de actualizaciones masivas de precios Shopify.

Usa APScheduler AsyncIOScheduler para ejecutar jobs a una hora exacta (hora Chile).
Los jobs se persisten en Redis para sobrevivir reinicios de Railway.
"""
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.services import redis as redis_svc
from app.services import shopify_service
from app.services.whatsapp import WhatsAppService

logger = logging.getLogger(__name__)

_TZ_CHILE = pytz.timezone("America/Santiago")
_scheduler = AsyncIOScheduler(timezone=_TZ_CHILE)

_JOB_KEY_PREFIX = "price_job:"
_JOBS_INDEX_KEY = "price_jobs:index"
_JOB_TTL = 172800  # 48 horas


# ──────────────────────────────────────────────
# Lifecycle
# ──────────────────────────────────────────────

async def init_scheduler():
    """Arranca el scheduler y recupera jobs pendientes desde Redis."""
    _scheduler.start()
    await _recover_pending_jobs()
    logger.info("[PRICE_SCHEDULER] Scheduler iniciado")


def shutdown_scheduler():
    """Cierra el scheduler limpiamente."""
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[PRICE_SCHEDULER] Scheduler cerrado")


# ──────────────────────────────────────────────
# API pública
# ──────────────────────────────────────────────

async def schedule_price_update(
    job_id: str,
    phone: str,
    variantes: List[Dict[str, str]],
    run_at: datetime,
) -> None:
    """
    Programa una actualización masiva de precios para run_at (datetime con tz Chile).
    Persiste el job en Redis con TTL de 48h para recovery ante reinicios.
    """
    job_data: Dict[str, Any] = {
        "phone": phone,
        "variantes": variantes,
        "run_at": run_at.isoformat(),
        "status": "pending",
        "created_at": datetime.now(_TZ_CHILE).isoformat(),
    }
    await redis_svc.set_json(f"{_JOB_KEY_PREFIX}{job_id}", job_data, ttl=_JOB_TTL)
    await _add_to_index(job_id)

    _scheduler.add_job(
        _ejecutar_actualizacion,
        trigger="date",
        run_date=run_at,
        args=[job_id, phone],
        id=job_id,
        replace_existing=True,
        misfire_grace_time=300,  # Tolera hasta 5 min de retraso por restart
    )
    logger.info("[PRICE_SCHEDULER] Job %s programado para %s (%d variantes)",
                job_id, run_at.strftime("%Y-%m-%d %H:%M %Z"), len(variantes))


async def cancel_price_update(job_id: str) -> bool:
    """
    Cancela un job programado. Retorna True si existía y estaba pendiente.
    """
    job_data = await redis_svc.get_json(f"{_JOB_KEY_PREFIX}{job_id}")
    if not job_data or job_data.get("status") != "pending":
        return False

    try:
        _scheduler.remove_job(job_id)
    except Exception:
        pass

    job_data["status"] = "cancelado"
    await redis_svc.set_json(f"{_JOB_KEY_PREFIX}{job_id}", job_data, ttl=3600)
    await _remove_from_index(job_id)
    logger.info("[PRICE_SCHEDULER] Job %s cancelado", job_id)
    return True


# ──────────────────────────────────────────────
# Ejecución del job
# ──────────────────────────────────────────────

async def _ejecutar_actualizacion(job_id: str, phone: str):
    """Ejecuta la actualización masiva de precios y notifica al gerente."""
    try:
        job_data = await redis_svc.get_json(f"{_JOB_KEY_PREFIX}{job_id}")
        if not job_data:
            logger.error("[PRICE_SCHEDULER] Job %s no encontrado en Redis", job_id)
            return

        if job_data.get("status") != "pending":
            logger.warning("[PRICE_SCHEDULER] Job %s no está pendiente (status=%s), abortando",
                           job_id, job_data.get("status"))
            return

        # Marcar como corriendo
        job_data["status"] = "running"
        await redis_svc.set_json(f"{_JOB_KEY_PREFIX}{job_id}", job_data, ttl=_JOB_TTL)

        variantes: List[Dict[str, str]] = job_data.get("variantes", [])
        logger.info("[PRICE_SCHEDULER] Ejecutando job %s con %d variantes", job_id, len(variantes))

        # Construir mapa SKU → variant_id (pagina todos los productos una vez)
        try:
            sku_map = await shopify_service.construir_mapa_sku_a_variant_id()
        except Exception as e:
            logger.error("[PRICE_SCHEDULER] Error construyendo mapa SKU: %s", e)
            await WhatsAppService.send_message(
                phone,
                f"❌ Error en actualización (job *{job_id}*): no se pudo conectar a Shopify.\n"
                "Revisa la configuración e intenta de nuevo."
            )
            job_data["status"] = "fallido"
            await redis_svc.set_json(f"{_JOB_KEY_PREFIX}{job_id}", job_data, ttl=3600)
            await _remove_from_index(job_id)
            return

        # Actualizar precios variante por variante
        exitos = 0
        fallos: List[str] = []

        for row in variantes:
            sku = row.get("sku", "")
            price = row.get("price", "")
            variant_id = sku_map.get(sku)

            if not variant_id:
                fallos.append(f"{sku} (SKU no encontrado)")
                continue

            ok = await actualizar_precio_variante(variant_id, price)
            if ok:
                exitos += 1
            else:
                fallos.append(sku)

        # Notificar resultado
        total = len(variantes)
        if fallos:
            fallos_preview = ", ".join(fallos[:10])
            if len(fallos) > 10:
                fallos_preview += f" y {len(fallos) - 10} más"
            msg = (
                f"⚠️ *Actualización completada con errores* (job *{job_id}*)\n\n"
                f"✅ Actualizados: {exitos}/{total}\n"
                f"❌ Fallos: {fallos_preview}"
            )
        else:
            msg = (
                f"✅ *Actualización de precios completada* (job *{job_id}*)\n\n"
                f"Se actualizaron *{exitos}* variantes correctamente."
            )

        await WhatsAppService.send_message(phone, msg)

        # Persistir resultado final
        status_final = "completado_con_fallos" if fallos else "completado"
        job_data.update({"status": status_final, "exitos": exitos, "fallos": fallos})
        await redis_svc.set_json(f"{_JOB_KEY_PREFIX}{job_id}", job_data, ttl=3600)
        await _remove_from_index(job_id)
        logger.info("[PRICE_SCHEDULER] Job %s finalizado: %d/%d éxitos", job_id, exitos, total)

    except Exception as e:
        logger.exception("[PRICE_SCHEDULER] Error inesperado ejecutando job %s", job_id)
        try:
            await WhatsAppService.send_message(
                phone,
                f"❌ Error inesperado en la actualización de precios (job *{job_id}*).\n"
                "Contacta al administrador del sistema."
            )
        except Exception:
            pass


async def actualizar_precio_variante(variant_id: str, price: str) -> bool:
    """Wrapper local para acceder a shopify_service desde el scheduler."""
    return await shopify_service.actualizar_precio_variante(variant_id, price)


# ──────────────────────────────────────────────
# Índice Redis + recovery
# ──────────────────────────────────────────────

async def _add_to_index(job_id: str):
    index: List[str] = await redis_svc.get_json(_JOBS_INDEX_KEY) or []
    if job_id not in index:
        index.append(job_id)
    await redis_svc.set_json(_JOBS_INDEX_KEY, index)


async def _remove_from_index(job_id: str):
    index: List[str] = await redis_svc.get_json(_JOBS_INDEX_KEY) or []
    index = [j for j in index if j != job_id]
    await redis_svc.set_json(_JOBS_INDEX_KEY, index)


async def _recover_pending_jobs():
    """Al startup: relee Redis y reagenda jobs que no se hayan ejecutado."""
    index: List[str] = await redis_svc.get_json(_JOBS_INDEX_KEY) or []
    if not index:
        return

    now = datetime.now(_TZ_CHILE)
    recovered = 0

    for job_id in list(index):
        job_data = await redis_svc.get_json(f"{_JOB_KEY_PREFIX}{job_id}")
        if not job_data or job_data.get("status") != "pending":
            await _remove_from_index(job_id)
            continue

        run_at = datetime.fromisoformat(job_data["run_at"])
        phone = job_data["phone"]

        if run_at <= now:
            # Ya pasó la hora: ejecutar inmediatamente en background
            logger.warning("[PRICE_SCHEDULER] Job %s atrasado, ejecutando ahora", job_id)
            asyncio.create_task(_ejecutar_actualizacion(job_id, phone))
        else:
            _scheduler.add_job(
                _ejecutar_actualizacion,
                trigger="date",
                run_date=run_at,
                args=[job_id, phone],
                id=job_id,
                replace_existing=True,
                misfire_grace_time=300,
            )
        recovered += 1

    if recovered:
        logger.info("[PRICE_SCHEDULER] %d job(s) recuperados al startup", recovered)
