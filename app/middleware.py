"""
Dependencia de verificacion de firma HMAC-SHA256 para el webhook de WhatsApp.
Meta firma cada request con X-Hub-Signature-256 usando el App Secret.
"""
import hashlib
import hmac
import logging

from fastapi import HTTPException, Request

from app.config import WSP_APP_SECRET
from app.schemas import WSPPayload

logger = logging.getLogger(__name__)


async def verify_signature(request: Request) -> WSPPayload:
    """
    Dependencia de FastAPI que verifica la firma HMAC-SHA256 del webhook.
    Retorna el payload parseado si la firma es valida.
    Lanza HTTPException(403) si la firma es invalida o falta.
    """
    body = await request.body()

    signature_header = request.headers.get("X-Hub-Signature-256")
    if not signature_header:
        logger.warning("Webhook recibido sin firma X-Hub-Signature-256")
        raise HTTPException(status_code=403, detail="Firma faltante")

    if not signature_header.startswith("sha256="):
        logger.warning("Formato de firma invalido: %s", signature_header)
        raise HTTPException(status_code=403, detail="Formato de firma invalido")

    firma_recibida = signature_header[7:]

    firma_esperada = hmac.new(
        WSP_APP_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(firma_recibida, firma_esperada):
        logger.warning("Firma HMAC invalida en webhook")
        raise HTTPException(status_code=403, detail="Firma invalida")

    payload = WSPPayload.model_validate_json(body)
    return payload
