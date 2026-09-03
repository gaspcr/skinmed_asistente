import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, BackgroundTasks, HTTPException, Depends
from fastapi.responses import JSONResponse

from app.config import get_settings, validate
from app.logging_config import setup_logging
from app.schemas import WSPPayload, SolicitudFotoRequest
from app.auth.service import AuthService
from app.services.whatsapp import WhatsAppService
from app.services import redis as redis_svc
from app.services import http as http_svc
from app.services import postgres as pg_svc
from app.middleware import verify_signature, verify_internal_api_key, SecurityHeadersMiddleware
from app.exceptions import ServicioNoDisponibleError
from app.workflows import doctor, manager, hybrid, tens
from app.workflows.role_registry import get_workflow_handler
from app.workflows import session_timer
from app.workflows import state as workflow_state
from app.services import price_scheduler

logger = logging.getLogger(__name__)

# Tipos de mensaje soportados por el bot
TIPOS_MENSAJE_SOPORTADOS = {"text", "interactive", "button", "document", "image"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL, settings.ENVIRONMENT)
    logger.info("Iniciando Bot Clinica SkinMed")
    validate()
    await redis_svc.init(settings.REDIS_URL)
    await http_svc.init()
    await pg_svc.init(
        settings.DATABASE_URL,
        min_size=settings.PG_POOL_MIN_SIZE,
        max_size=settings.PG_POOL_MAX_SIZE,
    )
    await price_scheduler.init_scheduler()
    logger.info("Servicios inicializados correctamente")

    yield

    # --- Shutdown ---
    price_scheduler.shutdown_scheduler()
    await pg_svc.close()
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

    # Check Postgres (opcional: si no esta configurado, no degrada)
    settings = get_settings()
    if settings.DATABASE_URL:
        try:
            estado["servicios"]["postgres"] = "ok" if await pg_svc.ping() else "error: ping fallo"
            if estado["servicios"]["postgres"] != "ok":
                estado["status"] = "degraded"
        except Exception as e:
            estado["servicios"]["postgres"] = f"error: {e}"
            estado["status"] = "degraded"
    else:
        estado["servicios"]["postgres"] = "not_configured"

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


async def _avisar_tipo_no_soportado(phone: str, role: str) -> bool:
    """
    Decide si vale la pena responder "tipo de mensaje no soportado".

    Al enviar varias fotos juntas, WhatsApp a veces entrega alguna con un
    tipo que no procesamos, y el aviso solo ensucia el chat de quien esta
    subiendo fotos (las demas si se suben). En una sesion de fotos activa,
    y en los chats de tens/enfermeria - que existen solo para eso -, se
    registra en el log y se deja pasar en silencio.
    """
    from app.workflows.tens import PASOS_SESION_FOTOS

    if role.lower().strip() in ("tens", "enfermeria"):
        return False

    return await workflow_state.get_step(phone) not in PASOS_SESION_FOTOS


async def _process_message(msg, background_tasks: BackgroundTasks):
    """
    Procesa un mensaje individual de WhatsApp.
    Ejecutado en background para responder rapido al webhook.
    """
    settings = get_settings()
    sender_phone = msg.sender_phone

    if not sender_phone:
        logger.warning(
            "[MAIN] Mensaje sin telefono (usuario con username de WhatsApp sin "
            "interaccion reciente); no se puede identificar. from_user_id=%s",
            msg.from_user_id,
        )
        return

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
            logger.warning("[MAIN] Usuario no encontrado para phone=%s", sender_phone)
            return

        logger.info("[MAIN] Usuario autenticado: phone=%s, role=%s, name=%s", sender_phone, user.role, user.name)

        # Verificar handler del rol
        handler = get_workflow_handler(user.role)
        if not handler:
            logger.error("[MAIN] No hay handler para rol='%s' de phone=%s", user.role, sender_phone)
            await WhatsAppService.send_message(
                sender_phone,
                f"Lo siento, tu rol '{user.role}' no está configurado en el sistema."
            )
            return

        logger.info("[MAIN] Handler resuelto: %s para phone=%s", type(handler).__name__, sender_phone)

        # Manejo de tipos de mensaje no soportados
        if msg.type not in TIPOS_MENSAJE_SOPORTADOS:
            logger.info("[MAIN] Tipo de mensaje no soportado: '%s' de phone=%s", msg.type, sender_phone)
            if await _avisar_tipo_no_soportado(sender_phone, user.role):
                await WhatsAppService.send_message(
                    sender_phone,
                    "Lo siento, este tipo de mensaje no es soportado. "
                    "Por favor envía un mensaje de texto."
                )
            return

        # Registrar actividad y programar timeout de inactividad
        await session_timer.touch(sender_phone)
        session_timer.schedule_timeout(sender_phone)

        # Procesar segun tipo
        if msg.type == "text":
            message_text = msg.text.body if msg.text and hasattr(msg.text, 'body') else ""
            logger.info("[MAIN] Mensaje de texto recibido de %s: '%s'", sender_phone, message_text[:50])

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
            logger.info("[MAIN] Boton recibido de %s: '%s'", sender_phone, btn_title)
            await handler.handle_button(user, sender_phone, btn_title, background_tasks)

        elif msg.type == "document":
            logger.info("[MAIN] Documento recibido de %s: filename=%s mime=%s",
                        sender_phone,
                        getattr(msg.document, "filename", "?"),
                        getattr(msg.document, "mime_type", "?"))
            await handler.handle_document(user, sender_phone, msg.document)

        elif msg.type == "image":
            logger.info("[MAIN] Imagen recibida de %s: mime=%s",
                        sender_phone,
                        getattr(msg.image, "mime_type", "?"))
            await handler.handle_image(user, sender_phone, msg.image)

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
    body: bytes = Depends(verify_signature),
    background_tasks: BackgroundTasks = None,
):
    """Recibe y procesa mensajes del webhook de WhatsApp."""
    try:
        # Parseo del payload dentro del try: si Meta manda un shape
        # inesperado (p. ej. campos nuevos de BSUID/usernames), respondemos
        # 200 igual en vez de un 500 no controlado, para evitar reintentos.
        payload = WSPPayload.model_validate_json(body)
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
        else:
            field = payload.entry[0].changes[0].field
            logger.debug("Webhook sin mensajes, field='%s'", field)

    except Exception as e:
        logger.exception("Error en webhook")

    return {"status": "ok"}


# --- Endpoints internos ---


@app.post("/internal/tens/solicitud-foto")
async def solicitar_foto_tens(
    body: SolicitudFotoRequest,
    _=Depends(verify_internal_api_key),
):
    """
    Endpoint interno llamado por el script de FileMaker para disparar la
    solicitud de foto al telefono elegido en la ficha del paciente. Recibe
    solo el token (UUID) y el telefono; nunca datos identificables del
    paciente.
    """
    settings = get_settings()

    # El telefono viene de un campo de FileMaker (elegido de la lista de
    # usuarios), asi que puede llegar como "+56912345678" o con espacios.
    # WhatsApp identifica al remitente solo con digitos, y esa forma es la
    # que usa la clave de estado del workflow: normalizar para que calcen.
    telefono = "".join(c for c in body.telefono if c.isdigit()).lstrip("0")
    if not telefono:
        raise HTTPException(status_code=422, detail="Telefono invalido")

    # Defensa en profundidad: confirmar que el telefono corresponde a un rol
    # autorizado a recibir/completar solicitudes de foto (ver TENS_FOTO_ROLES_PERMITIDOS)
    tens_user = await AuthService.get_user_by_phone(telefono)
    if not tens_user or not settings.tens_foto_rol_permitido(tens_user.role):
        raise HTTPException(status_code=404, detail="Telefono no corresponde a un usuario autorizado para recibir solicitudes de foto")

    await workflow_state.set_state(
        telefono,
        "esperando_foto",
        data={"token": body.token},
        ttl=settings.TENS_TOKEN_TTL_SECONDS,
    )
    await WhatsAppService.send_message(
        telefono,
        f"📸 Solicitud de foto pendiente.\nSesión: {body.token}\n"
        "Responde a este chat con las fotos del paciente. "
        "Cuando termines escribe *listo*."
    )
    return {"status": "ok"}
