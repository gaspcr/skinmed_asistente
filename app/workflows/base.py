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
