from fastapi import BackgroundTasks
from app.workflows.base import WorkflowHandler
from app.services.filemaker import FileMakerService
from app.services.whatsapp import WhatsAppService
from app.formatters.agenda import AgendaFormatter

class DoctorWorkflow(WorkflowHandler):
    async def handle_text(self, user, phone: str):
        await WhatsAppService.send_template(phone, user.name, "respuesta_inicial_doctores")
    
    async def handle_button(self, user, phone: str, button_title: str, background_tasks: BackgroundTasks):
        if button_title == "Revisar mi agenda del día":
            background_tasks.add_task(self._send_agenda, user, phone)
        elif button_title in ["Consultar cita paciente", "Consultar mis boxes"]:
            await WhatsAppService.send_message(phone, "Estamos trabajando en esta opción 🚧")
        else:
            await WhatsAppService.send_message(phone, "Opción no reconocida")
    
    async def _send_agenda(self, user, phone: str):
        agenda_data = await FileMakerService.get_agenda_raw(user.name)
        formatted_msg = AgendaFormatter.format(agenda_data, user.name)
        await WhatsAppService.send_message(phone, formatted_msg)
