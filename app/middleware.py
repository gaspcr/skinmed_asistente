"""
Middleware de seguridad y verificacion para la aplicacion.

Incluye:
- Verificacion de firma HMAC-SHA256 para webhooks de WhatsApp
- Headers de seguridad HTTP
"""
import hashlib
import hmac
import logging

from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.schemas import WSPPayload

logger = logging.getLogger(__name__)


# --- HMAC Webhook Verification ---


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
        get_settings().WSP_APP_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(firma_recibida, firma_esperada):
        logger.warning("Firma HMAC invalida en webhook")
        raise HTTPException(status_code=403, detail="Firma invalida")

    payload = WSPPayload.model_validate_json(body)
    return payload


# --- Security Headers Middleware ---


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Agrega headers de seguridad HTTP a todas las respuestas.
    Previene ataques comunes como clickjacking, MIME sniffing, y XSS.
    """

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        return response
