from abc import ABC, abstractmethod
from fastapi import BackgroundTasks

class WorkflowHandler(ABC):
    @abstractmethod
    async def handle_text(self, user, phone: str, message_text: str = ""):
        """Handle initial text message from user"""
        pass

    @abstractmethod
    async def handle_button(self, user, phone: str, button_title: str, background_tasks: BackgroundTasks):
        """Handle button click from user"""
        pass

    async def handle_document(self, user, phone: str, document):
        """Handle document message — rechaza por defecto; los roles que lo soporten deben sobreescribir."""
        from app.services.whatsapp import WhatsAppService
        await WhatsAppService.send_message(
            phone,
            "Este tipo de archivo no está disponible en tu rol."
        )

    async def handle_image(self, user, phone: str, image):
        """Handle image message — rechaza por defecto; los roles que lo soporten deben sobreescribir."""
        from app.services.whatsapp import WhatsAppService
        await WhatsAppService.send_message(
            phone,
            "Este tipo de archivo no está disponible en tu rol."
        )
