import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, BackgroundTasks, HTTPException, Depends
from fastapi.responses import JSONResponse

from app.config import VERIFY_TOKEN, REDIS_URL, validate
from app.logging_config import setup_logging
from app.schemas import WSPPayload
from app.auth.service import AuthService
from app.services.whatsapp import WhatsAppService
from app.services import redis as redis_svc
from app.services import http as http_svc
from app.middleware import verify_signature
from app.exceptions import ServicioNoDisponibleError
from app.workflows import doctor, manager, nurse
from app.workflows.role_registry import get_workflow_handler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    setup_logging()
    logger.info("Iniciando Bot Clinica SkinMed")
    validate()
    await redis_svc.init(REDIS_URL)
    await http_svc.init()
    logger.info("Servicios inicializados correctamente")

    yield

    # --- Shutdown ---
    await http_svc.close()
    await redis_svc.close()
    logger.info("Servicios cerrados correctamente")


app = FastAPI(title="Bot Clínica SkinMed", lifespan=lifespan)

def extract_button_title(msg) -> str:
    """Extract button title from interactive or button message"""
    if msg.type == "interactive":
        return msg.interactive.button_reply.title
    elif msg.type == "button":
        return msg.button.text
    return ""

@app.get("/health")
async def health_check():
    """Endpoint de health check para Railway."""
    estado = {"status": "ok", "servicios": {}}

    try:
        await redis_svc.get("health:ping")
        estado["servicios"]["redis"] = "ok"
    except Exception as e:
        estado["servicios"]["redis"] = f"error: {e}"
        estado["status"] = "degraded"

    try:
        http_svc.get_client()
        estado["servicios"]["http_client"] = "ok"
    except RuntimeError:
        estado["servicios"]["http_client"] = "error: no inicializado"
        estado["status"] = "degraded"

    status_code = 200 if estado["status"] == "ok" else 503
    return JSONResponse(content=estado, status_code=status_code)

@app.get("/webhook")
async def verify(request: Request):
    params = request.query_params
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(status_code=403, detail="Token de verificación inválido")

@app.post("/webhook")
async def webhook(
    payload: WSPPayload = Depends(verify_signature),
    background_tasks: BackgroundTasks = None,
):
    sender_phone = None
    try:
        change = payload.entry[0].changes[0].value

        if change.messages:
            msg = change.messages[0]
            sender_phone = msg.sender_phone

            # Rate limiting: 30 mensajes por minuto por telefono
            permitido = await redis_svc.verificar_rate_limit(
                f"ratelimit:{sender_phone}",
                limite=30,
                ventana_ttl=60,
            )
            if not permitido:
                logger.warning("Rate limit excedido para %s", sender_phone)
                return {"status": "rate_limited"}

            user = await AuthService.get_user_by_phone(sender_phone)
            if not user:
                return {"status": "ignored", "reason": "unauthorized"}

            handler = get_workflow_handler(user.role)
            if not handler:
                await WhatsAppService.send_message(
                    sender_phone,
                    f"Lo siento, tu rol '{user.role}' no está configurado en el sistema."
                )
                return {"status": "error", "reason": "no_handler_for_role", "role": user.role}

            if msg.type == "text":
                message_text = msg.text.body if msg.text and hasattr(msg.text, 'body') else ""
                await handler.handle_text(user, sender_phone, message_text)

            elif msg.type in ["interactive", "button"]:
                btn_title = extract_button_title(msg)
                await handler.handle_button(user, sender_phone, btn_title, background_tasks)

    except ServicioNoDisponibleError as e:
        logger.error("Servicio externo no disponible: %s", e)
        if sender_phone:
            try:
                await WhatsAppService.send_message(
                    sender_phone,
                    "Lo sentimos, el sistema no está disponible. Intenta de nuevo en unos minutos."
                )
            except Exception:
                pass
    except Exception as e:
        logger.exception("Error en webhook")

    return {"status": "ok"}
