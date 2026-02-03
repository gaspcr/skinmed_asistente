from fastapi import BackgroundTasks
from app.workflows.base import WorkflowHandler
from app.workflows.role_registry import register_workflow
from app.services.whatsapp import WhatsAppService

@register_workflow("enfermera_jefe")
class NurseWorkflow(WorkflowHandler):
    async def handle_text(self, user, phone: str):
        await WhatsAppService.send_message(phone, f"Hola {user.name}. Panel de enfermería en construcción.")
    
    async def handle_button(self, user, phone: str, button_title: str, background_tasks: BackgroundTasks):
        await WhatsAppService.send_message(phone, "Funcionalidad de enfermería en desarrollo 🚧")
