"""
Workflow del rol TENS: sube fotos de pacientes a la ficha en FileMaker
via un token de un solo uso generado desde el desktop de FileMaker.

El TENS nunca ve datos identificables del paciente por WhatsApp: solo
recibe un token opaco (UUID), que este workflow usa para resolver el
paciente real contra FileMaker en el momento de subir la primera foto.

Una solicitud puede incluir varias fotos (un "set"): la primera foto
resuelve el token, crea el set en FileMaker e invalida el token; las
fotos siguientes se agregan al mismo set sin volver a tocar el token.
El TENS cierra la sesion escribiendo "listo".
"""
import logging

from fastapi import BackgroundTasks

from app.config import get_settings
from app.workflows.base import WorkflowHandler
from app.workflows.role_registry import register_workflow
from app.workflows import state as workflow_state
from app.services.filemaker import FileMakerService
from app.services.whatsapp import WhatsAppService
from app.exceptions import ServicioNoDisponibleError

logger = logging.getLogger(__name__)

PASO_ESPERANDO_FOTO = "esperando_foto"
PASO_RECIBIENDO_FOTOS = "recibiendo_fotos"

PALABRAS_CIERRE_SESION = {
    "listo", "fin", "listo.", "fin.", "terminado", "terminado.", "salir", "salir.",
}


async def procesar_foto_tens(user, phone: str, image):
    """
    Logica compartida de subida de foto via token de un solo uso.
    Se usa desde TensWorkflow y desde cualquier otro rol autorizado
    (ver Settings.TENS_FOTO_ROLES_PERMITIDOS) que tambien pueda recibir
    y completar una solicitud de foto.
    """
    step = await workflow_state.get_step(phone)
    data = await workflow_state.get_data(phone) or {}

    if step not in (PASO_ESPERANDO_FOTO, PASO_RECIBIENDO_FOTOS):
        await WhatsAppService.send_message(
            phone,
            "No hay ninguna solicitud de foto pendiente. Genera una nueva "
            "desde la ficha del paciente en FileMaker."
        )
        return

    mime = getattr(image, "mime_type", "") or ""
    if not mime.startswith("image/"):
        await WhatsAppService.send_message(
            phone,
            "Solo se aceptan imágenes. Intenta enviar la foto de nuevo."
        )
        return

    try:
        contenido = await WhatsAppService.download_media(image.id)
    except Exception as e:
        logger.error("[TENS] Error descargando imagen de %s: %s", phone, e)
        await WhatsAppService.send_message(
            phone,
            "No se pudo descargar la imagen. Intenta enviarla de nuevo."
        )
        return

    settings = get_settings()

    # Primera foto de la sesion: resolver el token, crear el set en
    # FileMaker e invalidar el token (su trabajo ya esta hecho - el resto
    # de las fotos de esta sesion no vuelven a tocar la tabla de tokens).
    if step == PASO_ESPERANDO_FOTO:
        token = data.get("token")
        if not token:
            await WhatsAppService.send_message(
                phone,
                "No hay ninguna solicitud de foto pendiente. Genera una nueva "
                "desde la ficha del paciente en FileMaker."
            )
            return

        try:
            info = await FileMakerService.resolver_token_foto(token)
        except ServicioNoDisponibleError as e:
            logger.error("[TENS] Error resolviendo token de foto: %s", e)
            await WhatsAppService.send_message(
                phone,
                "No se pudo verificar la solicitud. Intenta de nuevo en unos minutos."
            )
            return

        if not info:
            await WhatsAppService.send_message(
                phone,
                "La solicitud de foto expiró o ya fue utilizada. Genera una "
                "nueva desde la ficha del paciente en FileMaker."
            )
            await workflow_state.clear_state(phone)
            return

        if not info.get("paciente_numero_id"):
            logger.error(
                "[TENS] Token %s resuelto sin paciente_numero_id (registro TokensFoto incompleto)",
                token,
            )
            await WhatsAppService.send_message(
                phone,
                "No se pudo verificar la solicitud (datos incompletos). Contacta a soporte."
            )
            await workflow_state.clear_state(phone)
            return

        responsable = f"{user.name or ''} {user.last_name or ''}".strip()

        try:
            set_pk = await FileMakerService.crear_set_fotos(info["paciente_numero_id"], responsable)
            await FileMakerService.invalidar_token_foto(info["record_id"])
        except ServicioNoDisponibleError as e:
            logger.error("[TENS] Error creando set de fotos: %s", e)
            await WhatsAppService.send_message(
                phone,
                "No se pudo iniciar la subida de fotos. Intenta de nuevo."
            )
            return

        data = {"set_pk": set_pk, "count": 0}

    set_pk = data["set_pk"]
    siguiente_numero = data.get("count", 0) + 1
    filename = f"foto_{siguiente_numero}.jpg"

    try:
        await FileMakerService.agregar_foto_a_set(set_pk, contenido, filename, mime)
    except ServicioNoDisponibleError as e:
        logger.error("[TENS] Error subiendo foto a FileMaker: %s", e)
        # Mantener la sesion viva: el set ya existe, puede reintentar esta
        # foto sin perder las que ya subio.
        await workflow_state.set_state(
            phone, PASO_RECIBIENDO_FOTOS, data=data, ttl=settings.TENS_TOKEN_TTL_SECONDS
        )
        await WhatsAppService.send_message(
            phone,
            "No se pudo subir esta foto a la ficha del paciente. Intenta enviarla de nuevo."
        )
        return

    data["count"] = siguiente_numero
    await workflow_state.set_state(
        phone, PASO_RECIBIENDO_FOTOS, data=data, ttl=settings.TENS_TOKEN_TTL_SECONDS
    )
    await WhatsAppService.send_message(
        phone,
        f"✅ Foto {siguiente_numero} subida. Envía más fotos de este paciente o "
        "escribe *listo* para terminar."
    )


async def manejar_texto_sesion_fotos_tens(phone: str, message_text: str) -> bool:
    """
    Maneja el cierre ("listo") de una sesion de fotos en curso.
    Se llama al inicio de handle_text de cualquier rol que tambien pueda
    completar solicitudes de foto (ver Settings.TENS_FOTO_ROLES_PERMITIDOS).

    Returns:
        True si el mensaje fue consumido por la sesion de fotos (el
        llamador no debe seguir procesandolo); False si no hay sesion de
        fotos en curso y el mensaje debe seguir su flujo normal.
    """
    step = await workflow_state.get_step(phone)
    if step != PASO_RECIBIENDO_FOTOS:
        return False

    texto = message_text.strip().lower()
    data = await workflow_state.get_data(phone) or {}
    count = data.get("count", 0)

    if texto in PALABRAS_CIERRE_SESION:
        await workflow_state.clear_state(phone)
        plural = "foto" if count == 1 else "fotos"
        await WhatsAppService.send_message(
            phone,
            f"✅ Sesión cerrada. Se subieron {count} {plural} a la ficha del paciente."
        )
        return True

    await WhatsAppService.send_message(
        phone,
        f"Llevas {count} foto(s) subidas en esta sesión. Envía más fotos de este "
        "paciente o escribe *listo* para terminar."
    )
    return True


@register_workflow("tens")
class TensWorkflow(WorkflowHandler):
    async def handle_text(self, user, phone: str, message_text: str = ""):
        if await manejar_texto_sesion_fotos_tens(phone, message_text):
            return

        await WhatsAppService.send_message(
            phone,
            "Este chat recibe solicitudes de foto automáticas desde la ficha "
            "del paciente en FileMaker. Cuando llegue una solicitud, responde "
            "con la foto correspondiente."
        )

    async def handle_button(self, user, phone: str, button_title: str, background_tasks: BackgroundTasks):
        await WhatsAppService.send_message(
            phone,
            "Este chat no utiliza botones. Cuando llegue una solicitud de "
            "foto, responde con la foto correspondiente."
        )

    async def handle_image(self, user, phone: str, image):
        await procesar_foto_tens(user, phone, image)
