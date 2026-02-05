from fastapi import BackgroundTasks
from app.workflows.base import WorkflowHandler
from app.workflows.role_registry import register_workflow
from app.services.filemaker import FileMakerService
from app.services.whatsapp import WhatsAppService
from app.formatters.agenda import AgendaFormatter
from datetime import datetime
from typing import Dict
import re

@register_workflow("medico")
class DoctorWorkflow(WorkflowHandler):
    # State management for multi-step interactions
    _user_states: Dict[str, str] = {}
    
    async def handle_text(self, user, phone: str, message_text: str = ""):
        # Check if user is in a multi-step flow
        if phone in self._user_states:
            state = self._user_states[phone]
            
            if state == "waiting_for_date":
                await self._handle_date_input(user, phone, message_text)
                return
        
        # Default: send initial template
        await WhatsAppService.send_template(phone, user.name, "respuesta_inicial_doctores_uso_interno")
    
    async def handle_button(self, user, phone: str, button_title: str, background_tasks: BackgroundTasks):
        # Debug: log the button title
        print(f"DEBUG: Doctor workflow - Received button: '{button_title}'")
        
        # Handle buttons from initial template (respuesta_inicial_doctores_uso_interno)
        if button_title == "Revisar agenda del día":
            background_tasks.add_task(self._send_agenda, user, phone, None)
        
        elif button_title == "Revisar agenda otro día":
            # Set state to wait for date input
            self._user_states[phone] = "waiting_for_date"
            await WhatsAppService.send_message(
                phone, 
                "Para revisar tu agenda en otro día, indícanos la fecha en formato *dd-mm-yy*\n\nEjemplo: 05-02-26"
            )
        
        elif button_title in ["Enviar recado", "Enviar recados", "Revisar mis recados"]:
            # Send recados template to show additional options
            print(f"DEBUG: Sending recados template for button: '{button_title}'")
            await WhatsAppService.send_template(phone, user.name, "recados_de_doctores", include_header=False, include_body=True)
        
        # Handle buttons from recados template (recados_de_doctores)
        elif button_title in ["Agendar paciente", "Bloquear agenda (día)"]:
            await WhatsAppService.send_message(phone, "Sistema de recados en construcción 🚧")
        
        else:
            print(f"DEBUG: Unrecognized button: '{button_title}'")
            await WhatsAppService.send_message(phone, f"Opción no reconocida: {button_title}. Por favor intenta de nuevo.")
    
    async def _handle_date_input(self, user, phone: str, message_text: str):
        """Handle user's date input for agenda lookup"""
        # Parse date format: dd-mm-yy
        date_pattern = r'^(\d{2})-(\d{2})-(\d{2})$'
        match = re.match(date_pattern, message_text.strip())
        
        if not match:
            await WhatsAppService.send_message(
                phone,
                "❌ Formato de fecha inválido. Por favor usa el formato *dd-mm-yy*\n\nEjemplo: 05-02-26"
            )
            return
        
        day, month, year = match.groups()
        
        # Validate date
        try:
            # Assuming 20XX for year
            full_year = f"20{year}"
            date_obj = datetime.strptime(f"{day}-{month}-{full_year}", "%d-%m-%Y")
            
            # Format for FileMaker: mm-dd-yyyy
            filemaker_date = date_obj.strftime("%m-%d-%Y")
            
            # Clear state
            del self._user_states[phone]
            
            # Send agenda for that date
            await self._send_agenda(user, phone, filemaker_date)
            
        except ValueError:
            await WhatsAppService.send_message(
                phone,
                "❌ Fecha inválida. Verifica que el día y mes sean correctos.\n\nEjemplo: 05-02-26"
            )
            # Clear state on error
            del self._user_states[phone]
    
    async def _send_agenda(self, user, phone: str, date: str = None):
        """Send agenda for today or specific date"""
        agenda_data = await FileMakerService.get_agenda_raw(user.name, date)
        formatted_msg = AgendaFormatter.format(agenda_data, user.name)
        await WhatsAppService.send_message(phone, formatted_msg)
