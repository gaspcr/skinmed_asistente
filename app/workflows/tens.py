"""
Workflow del rol TENS/ENFERMERIA: sube fotos de pacientes a la ficha en
FileMaker via un token de un solo uso generado desde el desktop de FileMaker.

Quien toma las fotos nunca ve datos identificables del paciente por WhatsApp:
solo recibe un token opaco (UUID), que este workflow usa para resolver el
paciente real contra FileMaker en el momento de subir la primera foto.

Una solicitud puede incluir varias fotos (un "set"): la primera foto
resuelve el token, crea el set en FileMaker e invalida el token; las
fotos siguientes se agregan al mismo set sin volver a tocar el token.

Cierre de sesion: al escribir "listo"/"salir" el bot pide las iniciales
(ej. "AnSa") y recien ahi cierra. Las iniciales se escriben en
SetFotosPaciente::Responsable de toma de fotos, reemplazando el nombre del
usuario del telefono - los celulares de fotos son compartidos entre varias
enfermeras, asi que el telefono no identifica a quien tomo las fotos, y las
iniciales mantienen el mismo estandar que los sets creados a mano en
FileMaker.

Si nadie cierra la sesion, esta caduca sola por TTL en Redis
(TENS_SESION_TTL_SECONDS, 30 min por defecto, renovado con cada foto).

Cuando WhatsApp entrega varias fotos de un mismo envio (album), cada una
llega como un webhook independiente casi al mismo tiempo. Sin un lock,
todas leerian el mismo estado "esperando primera foto" antes de que
cualquiera alcance a actualizarlo, y competirian por el mismo token
(la primera lo consume, el resto lo encuentra ya invalidado). El lock
por telefono serializa el procesamiento para que cada foto vea el
estado que dejo la anterior.
"""
import asyncio
import logging
import re

from fastapi import BackgroundTasks

from app.config import get_settings
from app.workflows.base import WorkflowHandler
from app.workflows.role_registry import register_workflow
from app.workflows import state as workflow_state
from app.services import redis as redis_svc
from app.services.filemaker import FileMakerService
from app.services.whatsapp import WhatsAppService
from app.exceptions import ServicioNoDisponibleError

logger = logging.getLogger(__name__)

PASO_ESPERANDO_FOTO = "esperando_foto"
PASO_RECIBIENDO_FOTOS = "recibiendo_fotos"
PASO_ESPERANDO_INICIALES = "esperando_iniciales"

# Pasos en los que una foto entrante pertenece a una sesion de fotos.
PASOS_SESION_FOTOS = (
    PASO_ESPERANDO_FOTO,
    PASO_RECIBIENDO_FOTOS,
    PASO_ESPERANDO_INICIALES,
)

PALABRAS_CIERRE_SESION = {
    "listo", "fin", "listo.", "fin.", "terminado", "terminado.", "salir", "salir.",
}

# Iniciales tipo "AnSa": solo letras (con tildes/ñ), sin espacios ni numeros.
PATRON_INICIALES = re.compile(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2,12}$")

LOCK_TTL_SEGUNDOS = 30
LOCK_ESPERA_MAX_SEGUNDOS = 20
LOCK_INTERVALO_REINTENTO = 0.3


async def procesar_foto_tens(user, phone: str, image):
    """
    Punto de entrada publico: serializa el procesamiento de fotos por
    telefono (ver nota de modulo sobre el lock) y delega en
    _procesar_foto_tens_interno.
    """
    lock_key = f"tens:foto:lock:{phone}"
    esperado = 0.0
    adquirido = False
    while esperado < LOCK_ESPERA_MAX_SEGUNDOS:
        adquirido = await redis_svc.acquire_lock(lock_key, ttl=LOCK_TTL_SEGUNDOS)
        if adquirido:
            break
        await asyncio.sleep(LOCK_INTERVALO_REINTENTO)
        esperado += LOCK_INTERVALO_REINTENTO

    if not adquirido:
        logger.warning("[TENS] No se pudo adquirir lock de foto para %s (timeout)", phone)
        await WhatsAppService.send_message(
            phone,
            "Estamos procesando otra foto tuya en este momento. Intenta enviar "
            "esta de nuevo en unos segundos."
        )
        return

    try:
        await _procesar_foto_tens_interno(user, phone, image)
    finally:
        await redis_svc.release_lock(lock_key)


async def _procesar_foto_tens_interno(user, phone: str, image):
    """
    Logica compartida de subida de foto via token de un solo uso.
    Se usa desde TensWorkflow y desde cualquier otro rol autorizado
    (ver Settings.TENS_FOTO_ROLES_PERMITIDOS) que tambien pueda recibir
    y completar una solicitud de foto.
    """
    step = await workflow_state.get_step(phone)
    data = await workflow_state.get_data(phone) or {}

    if step not in PASOS_SESION_FOTOS:
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

        # Responsable provisorio: el usuario del telefono. Se reemplaza por
        # las iniciales reales al cerrar la sesion (ver _cerrar_con_iniciales).
        responsable = f"{user.name or ''} {user.last_name or ''}".strip()

        try:
            set_info = await FileMakerService.crear_set_fotos(info["paciente_numero_id"], responsable)
            await FileMakerService.invalidar_token_foto(info["record_id"])
        except ServicioNoDisponibleError as e:
            logger.error("[TENS] Error creando set de fotos: %s", e)
            await WhatsAppService.send_message(
                phone,
                "No se pudo iniciar la subida de fotos. Intenta de nuevo."
            )
            return

        data = {
            "set_pk": set_info["set_pk"],
            "set_record_id": set_info["record_id"],
            "count": 0,
        }

    set_pk = data["set_pk"]
    siguiente_numero = data.get("count", 0) + 1
    filename = f"foto_{siguiente_numero}.jpg"

    try:
        await FileMakerService.agregar_foto_a_set(set_pk, contenido, filename, mime)
    except ServicioNoDisponibleError as e:
        logger.error("[TENS] Error subiendo foto a FileMaker: %s", e)
        # Mantener la sesion viva: el set ya existe, puede reintentar esta
        # foto sin perder las que ya subio.
        await _guardar_sesion(phone, PASO_RECIBIENDO_FOTOS, data)
        await WhatsAppService.send_message(
            phone,
            "No se pudo subir esta foto a la ficha del paciente. Intenta enviarla de nuevo."
        )
        return

    data["count"] = siguiente_numero
    await _guardar_sesion(phone, PASO_RECIBIENDO_FOTOS, data)
    await WhatsAppService.send_message(
        phone,
        f"✅ Foto {siguiente_numero} subida. Envía más fotos de este paciente o "
        "escribe *listo* para terminar."
    )


async def _guardar_sesion(phone: str, paso: str, data: dict):
    """Guarda el estado de la sesion de fotos renovando su TTL (30 min)."""
    settings = get_settings()
    await workflow_state.set_state(
        phone, paso, data=data, ttl=settings.TENS_SESION_TTL_SECONDS
    )


async def manejar_texto_sesion_fotos_tens(phone: str, message_text: str) -> bool:
    """
    Maneja el texto durante una sesion de fotos en curso: el cierre
    ("listo"/"salir") y la captura de iniciales que lo confirma.
    Se llama al inicio de handle_text de cualquier rol que tambien pueda
    completar solicitudes de foto (ver Settings.TENS_FOTO_ROLES_PERMITIDOS).

    Returns:
        True si el mensaje fue consumido por la sesion de fotos (el
        llamador no debe seguir procesandolo); False si no hay sesion de
        fotos en curso y el mensaje debe seguir su flujo normal.
    """
    step = await workflow_state.get_step(phone)
    if step not in (PASO_RECIBIENDO_FOTOS, PASO_ESPERANDO_INICIALES):
        return False

    texto = message_text.strip()
    data = await workflow_state.get_data(phone) or {}
    count = data.get("count", 0)

    if step == PASO_ESPERANDO_INICIALES:
        await _cerrar_con_iniciales(phone, texto, data)
        return True

    if texto.lower() in PALABRAS_CIERRE_SESION:
        if count == 0:
            # Nunca se subio una foto: no hay set que firmar.
            await workflow_state.clear_state(phone)
            await WhatsAppService.send_message(
                phone,
                "Sesión cerrada. No se subió ninguna foto a la ficha del paciente."
            )
            return True

        await _guardar_sesion(phone, PASO_ESPERANDO_INICIALES, data)
        plural = "foto" if count == 1 else "fotos"
        await WhatsAppService.send_message(
            phone,
            f"Llevas {count} {plural} subidas. Para finalizar escribe tus "
            "iniciales (por ejemplo *AnSa*), así quedan registradas como "
            "responsable de las fotos."
        )
        return True

    await WhatsAppService.send_message(
        phone,
        f"Llevas {count} foto(s) subidas en esta sesión. Envía más fotos de este "
        "paciente o escribe *listo* para terminar."
    )
    return True


async def _cerrar_con_iniciales(phone: str, texto: str, data: dict):
    """
    Cierra la sesion escribiendo las iniciales como responsable del set.
    Si el texto no parece iniciales, o FileMaker falla, la sesion se
    mantiene abierta esperando otro intento (las fotos ya estan subidas).
    """
    count = data.get("count", 0)

    if texto.lower() in PALABRAS_CIERRE_SESION or not PATRON_INICIALES.match(texto):
        await _guardar_sesion(phone, PASO_ESPERANDO_INICIALES, data)
        await WhatsAppService.send_message(
            phone,
            "Escribe solo tus iniciales para finalizar, por ejemplo *AnSa*."
        )
        return

    set_record_id = data.get("set_record_id")
    if set_record_id:
        try:
            await FileMakerService.actualizar_responsable_set(set_record_id, texto)
        except ServicioNoDisponibleError as e:
            logger.error("[TENS] Error registrando iniciales del set: %s", e)
            await _guardar_sesion(phone, PASO_ESPERANDO_INICIALES, data)
            await WhatsAppService.send_message(
                phone,
                "No se pudieron registrar tus iniciales en este momento. "
                "Las fotos ya están subidas; vuelve a escribirlas en unos segundos."
            )
            return
    else:
        # Sesion creada por una version anterior del flujo: no hay recordId
        # del set que actualizar. Se cierra igual para no dejarla colgada.
        logger.warning("[TENS] Sesion de %s sin set_record_id; cierro sin registrar iniciales", phone)

    await workflow_state.clear_state(phone)
    plural = "foto" if count == 1 else "fotos"
    await WhatsAppService.send_message(
        phone,
        f"✅ Sesión cerrada. Se subieron {count} {plural} a la ficha del "
        f"paciente, con responsable *{texto}*."
    )


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


@register_workflow("enfermeria")
class EnfermeriaWorkflow(TensWorkflow):
    """
    Los celulares con que enfermeria toma las fotos estan registrados en
    AuthUsuarios_dapi con ROL = "ENFERMERIA" y usan exactamente el mismo
    flujo de fotos que el rol tens.
    """
    pass
