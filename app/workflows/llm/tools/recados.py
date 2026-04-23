"""
Tools: revisar_recados y publicar_recado.

Gestión de recados/mensajes del doctor en FileMaker.
"""
import logging
from datetime import datetime
from typing import Any, Dict

import pytz

from app.config import get_settings
from app.services.filemaker import FileMakerService
from app.services.whatsapp import WhatsAppService
from app.formatters.recados import RecadosFormatter

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Definiciones OpenAI function calling
# ──────────────────────────────────────────────

REVISAR_RECADOS_TOOL = {
    "type": "function",
    "function": {
        "name": "revisar_recados",
        "description": "Obtiene los recados pendientes (mensajes/notas) del doctor.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

PUBLICAR_RECADO_TOOL = {
    "type": "function",
    "function": {
        "name": "publicar_recado",
        "description": (
            "Publica un nuevo recado/mensaje. El doctor debe indicar "
            "la categoría y el contenido del recado."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "categoria": {
                    "type": "string",
                    "enum": [
                        "Agendar paciente",
                        "Bloquear agenda",
                        "Enviar receta",
                        "Otros",
                    ],
                    "description": "Categoría del recado",
                },
                "mensaje": {
                    "type": "string",
                    "description": (
                        "Contenido del recado incluyendo nombre del "
                        "paciente si aplica"
                    ),
                },
            },
            "required": ["categoria", "mensaje"],
        },
    },
}


# ──────────────────────────────────────────────
# Handlers
# ──────────────────────────────────────────────

async def handle_revisar(user, phone: str, arguments: Dict[str, Any]) -> str:
    """Consulta recados pendientes y los envía formateados por WhatsApp."""
    recados_data = await FileMakerService.get_recados(user.id)

    # Resolver IDs de pacientes a nombres
    pacient_names: Dict[str, str] = {}
    for record in recados_data:
        pac_id = record.get("fieldData", {}).get("_FK_IDPaciente", "")
        if pac_id and pac_id not in pacient_names:
            try:
                name = await FileMakerService.get_pacient_by_id(pac_id)
                pacient_names[pac_id] = name or "Paciente desconocido"
            except Exception:
                pacient_names[pac_id] = "Paciente desconocido"

    formatted_msg = RecadosFormatter.format(
        recados_data, user.name, user.last_name, pacient_names,
    )

    # Enviar recados formateados directamente por WhatsApp
    await WhatsAppService.send_message(phone, formatted_msg)

    # Retornar resumen breve al LLM
    num_recados = len(recados_data) if recados_data else 0
    return (
        f"Recados enviados al doctor. {num_recados} recado(s) encontrado(s). "
        f"Los recados ya fueron enviados por WhatsApp, NO los repitas. "
        f"Solo agrega un breve comentario conversacional."
    )


async def handle_publicar(user, phone: str, arguments: Dict[str, Any]) -> str:
    """Crea un nuevo recado en FileMaker y notifica si corresponde."""
    categoria = arguments.get("categoria", "Otros")
    mensaje = arguments.get("mensaje", "")

    if not mensaje:
        return "Error: el mensaje del recado no puede estar vacío."

    # Obtener fecha y hora actual en zona horaria de Chile
    tz = pytz.timezone("America/Santiago")
    now = datetime.now(tz)
    fecha = now.strftime("%m-%d-%Y")
    hora = now.strftime("%H:%M:%S")
    fecha_display = now.strftime("%d-%m-%Y")

    # Formatear texto del recado: "autor > fecha > hora\rmensaje"
    texto_formateado = f"{user.name} > {fecha_display} > {hora}\r{mensaje}"

    # Reglas por categoría (misma lógica que legacy)
    guardar_en_fm = categoria != "Bloquear agenda"
    notificar_enfermeria = categoria not in ["Enviar receta", "Agendar paciente"]

    if guardar_en_fm:
        await FileMakerService.create_recado(
            doctor_id=user.id,
            texto=texto_formateado,
            categoria=categoria,
            fecha=fecha,
            hora=hora,
        )

    if notificar_enfermeria:
        settings = get_settings()
        await WhatsAppService.send_template(
            settings.CHIEF_NURSE_PHONE,
            user.name,
            "reenviar_recado_secretaria",
            include_header=False,
            include_body=False,
            header_params=[categoria],
            body_params=[user.name, mensaje],
        )

    # Construir confirmación
    if guardar_en_fm and notificar_enfermeria:
        confirmacion = "Se ha registrado en FileMaker y notificado a enfermería."
    elif guardar_en_fm:
        confirmacion = "Se ha registrado en FileMaker."
    else:
        confirmacion = "Se ha notificado a enfermería."

    return (
        f"Recado procesado exitosamente.\n"
        f"Categoría: {categoria}\n"
        f"Fecha: {fecha_display} — {':'.join(hora.split(':')[:2])}\n"
        f"Recado: {mensaje}\n"
        f"{confirmacion}"
    )
