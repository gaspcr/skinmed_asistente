import logging
import re
from datetime import datetime

from fastapi import BackgroundTasks

from app.workflows.base import WorkflowHandler
from app.workflows.role_registry import register_workflow
from app.workflows import state as workflow_state
from app.services.filemaker import FileMakerService
from app.services.whatsapp import WhatsAppService
from app.formatters.agenda import AgendaFormatter
from app.formatters.recados import RecadosFormatter
from app.exceptions import ServicioNoDisponibleError

logger = logging.getLogger(__name__)

@register_workflow("medico")
class DoctorWorkflow(WorkflowHandler):

    async def handle_text(self, user, phone: str, message_text: str = ""):
        # Verificar si el usuario esta en un flujo multi-paso
        step = await workflow_state.get_step(phone)
        if step:
            if step == "waiting_for_date":
                await self._handle_date_input(user, phone, message_text)
                return

        # Default: enviar plantilla inicial
        await WhatsAppService.send_template(phone, user.name, "respuesta_inicial_doctores_uso_interno")

    async def handle_button(self, user, phone: str, button_title: str, background_tasks: BackgroundTasks):
        logger.debug("Boton recibido: '%s'", button_title)

        # Botones de plantilla inicial (respuesta_inicial_doctores_uso_interno)
        if button_title == "Revisar agenda del día":
            background_tasks.add_task(self._send_agenda, user, phone, None)

        elif button_title == "Revisar agenda otro día":
            await workflow_state.set_state(phone, "waiting_for_date")
            await WhatsAppService.send_message(
                phone,
                "Para revisar tu agenda en otro día, indícanos la fecha en formato *dd-mm-yy*\n\nEjemplo: 05-02-26"
            )

        elif button_title == "Enviar recado":
            logger.debug("Enviando plantilla recados por boton: '%s'", button_title)
            await WhatsAppService.send_template(phone, user.name, "recados_de_doctores", include_header=False, include_body=True)

        elif button_title == "Revisar mis recados":
            background_tasks.add_task(self._send_recados, user, phone)

        # Botones de plantilla recados (recados_de_doctores)
        elif button_title in ["Agendar paciente", "Bloquear agenda (día)"]:
            await WhatsAppService.send_message(phone, "Sistema de recados en construcción 🚧")

        else:
            logger.warning("Boton no reconocido: '%s'", button_title)
            await WhatsAppService.send_message(phone, "Opción no reconocida. Por favor intenta de nuevo.")

    async def _handle_date_input(self, user, phone: str, message_text: str):
        """Procesa la entrada de fecha del usuario para consulta de agenda"""
        date_pattern = r'^(\d{2})-(\d{2})-(\d{2})$'
        match = re.match(date_pattern, message_text.strip())

        if not match:
            await WhatsAppService.send_message(
                phone,
                "❌ Formato de fecha inválido. Por favor usa el formato *dd-mm-yy*\n\nEjemplo: 05-02-26"
            )
            return

        day, month, year = match.groups()

        try:
            full_year = f"20{year}"
            date_obj = datetime.strptime(f"{day}-{month}-{full_year}", "%d-%m-%Y")
            filemaker_date = date_obj.strftime("%m-%d-%Y")

            await workflow_state.clear_state(phone)
            await self._send_agenda(user, phone, filemaker_date)

        except ValueError:
            await WhatsAppService.send_message(
                phone,
                "❌ Fecha inválida. Verifica que el día y mes sean correctos.\n\nEjemplo: 05-02-26"
            )
            await workflow_state.clear_state(phone)

    async def _send_agenda(self, user, phone: str, date: str = None):
        """Envia agenda del dia o de una fecha especifica"""
        try:
            agenda_data = await FileMakerService.get_agenda_raw(user.id, date)
            formatted_msg = AgendaFormatter.format(agenda_data, user.name)
            await WhatsAppService.send_message(phone, formatted_msg)
        except ServicioNoDisponibleError as e:
            logger.error("Servicio no disponible al consultar agenda: %s", e)
            await WhatsAppService.send_message(
                phone,
                "Lo sentimos, el sistema no está disponible en este momento. "
                "Por favor intenta de nuevo en unos minutos."
            )

    async def _send_recados(self, user, phone: str):
        """Obtiene y envia los recados del doctor"""
        try:
            recados_data = await FileMakerService.get_recados(user.id)

            # Resolver IDs de pacientes a nombres
            pacient_names = {}
            for record in recados_data:
                pac_id = record.get("fieldData", {}).get("_FK_IDPaciente", "")
                if pac_id and pac_id not in pacient_names:
                    try:
                        name = await FileMakerService.get_pacient_by_id(pac_id)
                        pacient_names[pac_id] = name or "Paciente desconocido"
                    except Exception:
                        pacient_names[pac_id] = "Paciente desconocido"

            formatted_msg = RecadosFormatter.format(recados_data, user.name, pacient_names)
            await WhatsAppService.send_message(phone, formatted_msg)
        except ServicioNoDisponibleError as e:
            logger.error("Servicio no disponible al consultar recados: %s", e)
            await WhatsAppService.send_message(
                phone,
                "Lo sentimos, el sistema no está disponible en este momento. "
                "Por favor intenta de nuevo en unos minutos."
            )
