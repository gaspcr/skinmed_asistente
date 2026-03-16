"""
Workflow híbrido para usuarios con rol MEDICO_GERENCIA.
Permite elegir entre el perfil de médico y el de gerencia.
"""
import logging

from fastapi import BackgroundTasks

from app.workflows.base import WorkflowHandler
from app.workflows.role_registry import register_workflow
from app.workflows import state as workflow_state
from app.workflows.doctor import DoctorWorkflow
from app.workflows.manager import ManagerWorkflow
from app.services.whatsapp import WhatsAppService
from app.services import redis as redis_svc

logger = logging.getLogger(__name__)

# Instancias reutilizables de cada workflow
_doctor_workflow = DoctorWorkflow()
_manager_workflow = ManagerWorkflow()

# TTL para el perfil activo (2 horas)
_PROFILE_TTL = 7200


def _profile_key(phone: str) -> str:
    return f"hybrid:profile:{phone}"


async def _get_active_profile(phone: str) -> str | None:
    return await redis_svc.get(_profile_key(phone))


async def _set_active_profile(phone: str, profile: str):
    await redis_svc.set(_profile_key(phone), profile, ttl=_PROFILE_TTL)


async def _clear_active_profile(phone: str):
    await redis_svc.delete(_profile_key(phone))


@register_workflow("medico_gerencia")
class HybridWorkflow(WorkflowHandler):

    async def handle_text(self, user, phone: str, message_text: str = ""):
        texto = message_text.strip().lower()

        # "perfiles" siempre vuelve al selector de perfil
        if texto == "perfiles":
            await workflow_state.clear_state(phone)
            await _clear_active_profile(phone)
            await self._send_profile_selector(user, phone)
            return

        # Verificar si ya tiene un perfil activo
        active_profile = await _get_active_profile(phone)

        if active_profile:
            # "salir" vuelve al selector de perfiles
            if texto == "salir":
                await workflow_state.clear_state(phone)
                await _clear_active_profile(phone)
                await self._send_profile_selector(user, phone)
                return

            # Delegar al workflow correspondiente
            if active_profile == "medico":
                await _doctor_workflow.handle_text(user, phone, message_text)
            else:
                await _manager_workflow.handle_text(user, phone, message_text)
            return

        # Si esta esperando seleccion de perfil
        step = await workflow_state.get_step(phone)
        if step == "waiting_for_profile":
            if texto == "1":
                await workflow_state.clear_state(phone)
                await _set_active_profile(phone, "medico")
                await _doctor_workflow._send_menu(user, phone)
            elif texto == "2":
                await workflow_state.clear_state(phone)
                await _set_active_profile(phone, "gerencia")
                await _manager_workflow._send_menu(user, phone)
            else:
                await WhatsAppService.send_message(
                    phone,
                    "Por favor escribe *1* o *2* para seleccionar un perfil."
                )
            return

        # Default: mostrar selector de perfil
        await self._send_profile_selector(user, phone)

    async def handle_button(self, user, phone: str, button_title: str, background_tasks: BackgroundTasks):
        active_profile = await _get_active_profile(phone)

        if active_profile == "medico":
            await _doctor_workflow.handle_button(user, phone, button_title, background_tasks)
        elif active_profile == "gerencia":
            await _manager_workflow.handle_button(user, phone, button_title, background_tasks)
        else:
            await self._send_profile_selector(user, phone)

    async def _send_profile_selector(self, user, phone: str):
        """Envia el selector de perfil"""
        await workflow_state.set_state(phone, "waiting_for_profile")
        await WhatsAppService.send_message(
            phone,
            f"*Hola {user.name} {user.last_name}*\n"
            "Tienes acceso a dos perfiles. Selecciona con cuál deseas trabajar:\n\n"
            "*1.* Perfil Médico\n"
            "*2.* Perfil Gerencia\n\n"
            "_Escribe *perfiles* en cualquier momento para volver a esta selección._"
        )
