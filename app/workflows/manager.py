from fastapi import BackgroundTasks
from app.workflows.base import WorkflowHandler
from app.workflows.role_registry import register_workflow
from app.services.whatsapp import WhatsAppService
from app.interaction_logger import log_workflow_action

@register_workflow("gerencia")
class ManagerWorkflow(WorkflowHandler):
    async def handle_text(self, user, phone: str, message_text: str = ""):
        log_workflow_action(phone, user.name, "gerencia", "text_received", message_text)
        await WhatsAppService.send_message(phone, f"Hola Gerente {user.name}. Panel en construcción.")
    
    async def handle_button(self, user, phone: str, button_title: str, background_tasks: BackgroundTasks):
        log_workflow_action(phone, user.name, "gerencia", "button_received", button_title)
        await WhatsAppService.send_message(phone, "Funcionalidad de gerente en desarrollo 🚧")
