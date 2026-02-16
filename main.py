import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, BackgroundTasks, HTTPException, Depends
from fastapi.responses import JSONResponse

from app.config import get_settings, validate
from app.logging_config import setup_logging
from app.schemas import WSPPayload
from app.auth.service import AuthService
from app.services.whatsapp import WhatsAppService
from app.services import redis as redis_svc
from app.services import http as http_svc
from app.middleware import verify_signature, SecurityHeadersMiddleware
from app.exceptions import ServicioNoDisponibleError
from app.workflows import doctor, manager, nurse
from app.workflows.role_registry import get_workflow_handler

logger = logging.getLogger(__name__)

# Tipos de mensaje soportados por el bot
TIPOS_MENSAJE_SOPORTADOS = {"text", "interactive", "button"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL, settings.ENVIRONMENT)
    logger.info("Iniciando Bot Clinica SkinMed")
    validate()
    await redis_svc.init(settings.REDIS_URL)
    await http_svc.init()
    logger.info("Servicios inicializados correctamente")

    yield

    # --- Shutdown ---
    await http_svc.close()
    await redis_svc.close()
    logger.info("Servicios cerrados correctamente")


def create_app() -> FastAPI:
    """Factory de la aplicacion FastAPI."""
    settings = get_settings()

    app_kwargs = {
        "title": "Bot Clínica SkinMed",
        "lifespan": lifespan,
    }

    # Deshabilitar documentacion en produccion
    if settings.is_production:
        app_kwargs["docs_url"] = None
        app_kwargs["redoc_url"] = None
        app_kwargs["openapi_url"] = None

    application = FastAPI(**app_kwargs)

    # Agregar middleware de headers de seguridad
    application.add_middleware(SecurityHeadersMiddleware)

    return application


app = create_app()


def extract_button_title(msg) -> str:
    """Extrae el titulo del boton de un mensaje interactivo o de boton."""
    if msg.type == "interactive":
        return msg.interactive.button_reply.title
    elif msg.type == "button":
        return msg.button.text
    return ""


# --- Health Checks ---


@app.get("/health")
async def health_check():
    """Health check basico (liveness) para Railway/monitoreo."""
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness_check():
    """Readiness check profundo: verifica conectividad con todos los servicios."""
    estado = {"status": "ok", "servicios": {}}

    # Check Redis
    try:
        await redis_svc.get("health:ping")
        estado["servicios"]["redis"] = "ok"
    except Exception as e:
        estado["servicios"]["redis"] = f"error: {e}"
        estado["status"] = "degraded"

    # Check HTTP Client
    try:
        http_svc.get_client()
        estado["servicios"]["http_client"] = "ok"
    except RuntimeError:
        estado["servicios"]["http_client"] = "error: no inicializado"
        estado["status"] = "degraded"

    # Check FileMaker (intenta obtener token)
    try:
        from app.services.filemaker import FileMakerService
        token = await FileMakerService.get_token()
        estado["servicios"]["filemaker"] = "ok" if token else "error: sin token"
    except Exception as e:
        estado["servicios"]["filemaker"] = f"error: {e}"
        estado["status"] = "degraded"

    status_code = 200 if estado["status"] == "ok" else 503
    return JSONResponse(content=estado, status_code=status_code)


# --- Webhook ---


@app.get("/webhook")
async def verify(request: Request):
    """Verificacion del webhook de WhatsApp."""
    settings = get_settings()
    params = request.query_params
    if params.get("hub.verify_token") == settings.WSP_VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(status_code=403, detail="Token de verificación inválido")


async def _process_message(msg, background_tasks: BackgroundTasks):
    """
    Procesa un mensaje individual de WhatsApp.
    Ejecutado en background para responder rapido al webhook.
    """
    settings = get_settings()
    sender_phone = msg.sender_phone

    try:
        # Rate limiting
        permitido = await redis_svc.verificar_rate_limit(
            f"ratelimit:{sender_phone}",
            limite=settings.RATE_LIMIT_MAX,
            ventana_ttl=settings.RATE_LIMIT_WINDOW,
        )
        if not permitido:
            logger.warning("Rate limit excedido para %s", sender_phone)
            return

        # Autenticacion
        user = await AuthService.get_user_by_phone(sender_phone)
        if not user:
            return

        # Verificar handler del rol
        handler = get_workflow_handler(user.role)
        if not handler:
            await WhatsAppService.send_message(
                sender_phone,
                f"Lo siento, tu rol '{user.role}' no está configurado en el sistema."
            )
            return

        # Manejo de tipos de mensaje no soportados
        if msg.type not in TIPOS_MENSAJE_SOPORTADOS:
            await WhatsAppService.send_message(
                sender_phone,
                "Lo siento, este tipo de mensaje no es soportado. "
                "Por favor envía un mensaje de texto."
            )
            return

        # Procesar segun tipo
        if msg.type == "text":
            message_text = msg.text.body if msg.text and hasattr(msg.text, 'body') else ""

            # Sanitizar: limitar longitud
            if len(message_text) > settings.MAX_MESSAGE_LENGTH:
                message_text = message_text[:settings.MAX_MESSAGE_LENGTH]
                logger.info(
                    "Mensaje truncado para %s (largo original > %d)",
                    sender_phone,
                    settings.MAX_MESSAGE_LENGTH,
                )

            await handler.handle_text(user, sender_phone, message_text)

        elif msg.type in ["interactive", "button"]:
            btn_title = extract_button_title(msg)
            await handler.handle_button(user, sender_phone, btn_title, background_tasks)

    except ServicioNoDisponibleError as e:
        logger.error("Servicio externo no disponible: %s", e)
        try:
            await WhatsAppService.send_message(
                sender_phone,
                "Lo sentimos, el sistema no está disponible. Intenta de nuevo en unos minutos."
            )
        except Exception:
            pass
    except Exception as e:
        logger.exception("Error procesando mensaje de %s", sender_phone)


@app.post("/webhook")
async def webhook(
    payload: WSPPayload = Depends(verify_signature),
    background_tasks: BackgroundTasks = None,
):
    """Recibe y procesa mensajes del webhook de WhatsApp."""
    try:
        change = payload.entry[0].changes[0].value

        if change.messages:
            msg = change.messages[0]

            # Idempotencia: verificar si ya procesamos este mensaje
            msg_id = msg.id
            ya_procesado = await redis_svc.get(f"msg:processed:{msg_id}")
            if ya_procesado:
                logger.debug("Mensaje %s ya procesado, ignorando", msg_id)
                return {"status": "already_processed"}

            # Marcar como procesado (TTL 1 hora)
            await redis_svc.set(f"msg:processed:{msg_id}", "1", ttl=3600)

            # Procesar mensaje en background para responder rapido
            await _process_message(msg, background_tasks)

    except Exception as e:
        logger.exception("Error en webhook")

    return {"status": "ok"}
