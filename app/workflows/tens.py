"""
Workflow del rol TENS: sube fotos de pacientes a la ficha en FileMaker
via un token de un solo uso generado desde el desktop de FileMaker.

El TENS nunca ve datos identificables del paciente por WhatsApp: solo
recibe un token opaco (UUID), que este workflow usa para resolver el
paciente real contra FileMaker en el momento de subir la foto.
"""
import logging

from fastapi import BackgroundTasks

from app.workflows.base import WorkflowHandler
from app.workflows.role_registry import register_workflow
from app.workflows import state as workflow_state
from app.services.filemaker import FileMakerService
from app.services.whatsapp import WhatsAppService
from app.exceptions import ServicioNoDisponibleError

logger = logging.getLogger(__name__)

PASO_ESPERANDO_FOTO = "esperando_foto"


async def procesar_foto_tens(user, phone: str, image):
    """
    Logica compartida de subida de foto via token de un solo uso.
    Se usa desde TensWorkflow y desde cualquier otro rol autorizado
    (ver Settings.TENS_FOTO_ROLES_PERMITIDOS) que tambien pueda recibir
    y completar una solicitud de foto.
    """
    step = await workflow_state.get_step(phone)
    data = await workflow_state.get_data(phone)

    if step != PASO_ESPERANDO_FOTO or not data or not data.get("token"):
        await WhatsAppService.send_message(
            phone,
            "No hay ninguna solicitud de foto pendiente. Genera una nueva "
            "desde la ficha del paciente en FileMaker."
        )
        return

    token = data["token"]

    mime = getattr(image, "mime_type", "") or ""
    if not mime.startswith("image/"):
        await WhatsAppService.send_message(
            phone,
            "Solo se aceptan imágenes. Intenta enviar la foto de nuevo."
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

    try:
        contenido = await WhatsAppService.download_media(image.id)
    except Exception as e:
        logger.error("[TENS] Error descargando imagen de %s: %s", phone, e)
        await WhatsAppService.send_message(
            phone,
            "No se pudo descargar la imagen. Intenta enviarla de nuevo."
        )
        return

    responsable = f"{user.name or ''} {user.last_name or ''}".strip()
    filename = f"{token}.jpg"

    try:
        await FileMakerService.subir_foto_paciente(
            info["paciente_fk"], contenido, filename, mime, responsable
        )
        await FileMakerService.invalidar_token_foto(info["record_id"])
    except ServicioNoDisponibleError as e:
        logger.error("[TENS] Error subiendo foto a FileMaker: %s", e)
        await WhatsAppService.send_message(
            phone,
            "No se pudo subir la foto a la ficha del paciente. Intenta de nuevo."
        )
        return

    await workflow_state.clear_state(phone)
    await WhatsAppService.send_message(
        phone,
        "✅ Foto subida correctamente a la ficha del paciente."
    )


@register_workflow("tens")
class TensWorkflow(WorkflowHandler):
    async def handle_text(self, user, phone: str, message_text: str = ""):
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
