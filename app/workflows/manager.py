import logging
import re
from collections import OrderedDict
from datetime import datetime

import pytz
from fastapi import BackgroundTasks

from app.workflows.base import WorkflowHandler
from app.workflows.role_registry import register_workflow
from app.workflows import state as workflow_state
from app.services.filemaker import FileMakerService
from app.services.whatsapp import WhatsAppService
from app.formatters.agenda import AgendaFormatter
from app.exceptions import ServicioNoDisponibleError

logger = logging.getLogger(__name__)

# Filtros de citas invalidas (compartidos con AgendaFormatter)
_IGNORAR_TIPO = ["Eliminada", "Bloqueada", "No Viene", "Disponible"]
_IGNORAR_ACTIVIDAD = ["RECORDATORIO", "VISITADOR MÉDICO", "LABORATORIO"]


def _filtrar_citas_validas(data: list) -> list:
    """Filtra citas eliminadas, bloqueadas, etc."""
    return [
        r for r in data
        if r["fieldData"].get("Tipo") not in _IGNORAR_TIPO
        and r["fieldData"].get("Actividad", "").upper() not in _IGNORAR_ACTIVIDAD
        and r["fieldData"].get("Hora", "00:00:00") != "00:00:00"
    ]


@register_workflow("gerencia")
class ManagerWorkflow(WorkflowHandler):

    async def handle_text(self, user, phone: str, message_text: str = ""):
        texto = message_text.strip().lower()

        # Comandos globales
        if texto == "menu":
            await workflow_state.clear_state(phone)
            await self._send_menu(user, phone)
            return

        if texto == "salir":
            await workflow_state.clear_state(phone)
            await WhatsAppService.send_message(
                phone,
                "Flujo finalizado. Cuando necesites algo, escribe cualquier mensaje o *menu*."
            )
            return

        # Verificar flujo multi-paso
        step = await workflow_state.get_step(phone)
        if step:
            if step == "waiting_for_doctor_selection":
                await self._handle_doctor_selection(user, phone, message_text)
                return
            elif step == "waiting_for_agenda_date":
                await self._handle_date_input(user, phone, message_text)
                return
            elif step == "waiting_for_continue":
                if texto in ["si", "sí", "s"]:
                    await workflow_state.clear_state(phone)
                    await self._send_menu(user, phone)
                else:
                    await workflow_state.clear_state(phone)
                    await WhatsAppService.send_message(
                        phone,
                        "¡Hasta luego! Cuando necesites algo, escribe cualquier mensaje o *menu*."
                    )
                return

        # Opciones del menu principal
        if texto == "1":
            await self._show_doctors_agenda(user, phone)
            return
        elif texto == "2":
            await workflow_state.set_state(phone, "waiting_for_agenda_date")
            await WhatsAppService.send_message(
                phone,
                "Indica la fecha que deseas consultar en formato *dd-mm-yy*\n\nEjemplo: 05-03-26"
            )
            return

        # Cualquier otro texto: enviar menu
        await self._send_menu(user, phone)

    async def handle_button(self, user, phone: str, button_title: str, background_tasks: BackgroundTasks):
        logger.debug("Boton recibido en manager (inesperado): '%s'", button_title)
        await self._send_menu(user, phone)

    async def _send_menu(self, user, phone: str):
        """Envia el menu principal del manager"""
        await WhatsAppService.send_message(
            phone,
            f"*Hola {user.name} {user.last_name}* \n"
            "Soy tu asistente virtual. Selecciona una opción:\n\n"
            "*1.* Revisar agendas de doctores del día\n"
            "*2.* Revisar agendas de doctores en otra fecha\n\n"
            "_Escribe *menu* para volver al inicio o *salir* para terminar._"
        )

    async def _ask_continue(self, phone: str):
        """Pregunta si desea hacer algo mas"""
        await workflow_state.set_state(phone, "waiting_for_continue")
        await WhatsAppService.send_message(
            phone,
            "¿Deseas hacer algo más? Responde *si* o *no*."
        )

    async def _handle_date_input(self, user, phone: str, message_text: str):
        """Procesa la entrada de fecha para consulta de agenda"""
        date_pattern = r'^(\d{2})-(\d{2})-(\d{2})$'
        match = re.match(date_pattern, message_text.strip())

        if not match:
            await WhatsAppService.send_message(
                phone,
                "Formato de fecha inválido. Por favor usa el formato *dd-mm-yy*\n\nEjemplo: 05-03-26"
            )
            return

        day, month, year = match.groups()

        try:
            full_year = f"20{year}"
            date_obj = datetime.strptime(f"{day}-{month}-{full_year}", "%d-%m-%Y")
            filemaker_date = date_obj.strftime("%m-%d-%Y")
            display_date = date_obj.strftime("%d-%m-%Y")

            await workflow_state.clear_state(phone)
            await self._show_doctors_agenda(user, phone, date=filemaker_date, display_date=display_date)

        except ValueError:
            await WhatsAppService.send_message(
                phone,
                "Fecha inválida. Verifica que el día y mes sean correctos.\n\nEjemplo: 05-03-26"
            )
            await workflow_state.clear_state(phone)

    async def _show_doctors_agenda(self, user, phone: str, date: str = None, display_date: str = None):
        """Muestra lista de doctores con agenda para una fecha (hoy por defecto)"""
        label = f"el {display_date}" if display_date else "hoy"

        try:
            all_data = await FileMakerService.get_agenda_all_doctors(date)

            if not all_data:
                await WhatsAppService.send_message(
                    phone,
                    f"No hay agendas registradas para {label}."
                )
                await self._ask_continue(phone)
                return

            valid_data = _filtrar_citas_validas(all_data)

            # Agrupar citas por doctor
            doctors: OrderedDict[str, list] = OrderedDict()
            for record in valid_data:
                fd = record.get("fieldData", {})
                doctor_name = fd.get("Recurso Humano::Nombre Lista", "").strip()
                if not doctor_name:
                    continue
                if doctor_name not in doctors:
                    doctors[doctor_name] = []
                doctors[doctor_name].append(record)

            if not doctors:
                await WhatsAppService.send_message(
                    phone,
                    f"No se encontraron doctores con agenda para {label}."
                )
                await self._ask_continue(phone)
                return

            # Guardar estado para la seleccion
            doctor_list = list(doctors.keys())
            state_data = {"doctors": doctor_list}
            if date:
                state_data["date"] = date
                state_data["display_date"] = display_date
            await workflow_state.set_state(
                phone, "waiting_for_doctor_selection",
                data=state_data
            )

            # Construir mensaje con lista numerada
            msg = f"*Doctores con agenda {label}* ({len(doctor_list)}):\n\n"
            for i, name in enumerate(doctor_list, 1):
                n_citas = len(doctors[name])
                msg += f"*{i}.* {name} — _{n_citas} cita(s)_\n"
            msg += "\n_Escribe el número del doctor para ver su agenda:_"

            await WhatsAppService.send_message(phone, msg)

        except ServicioNoDisponibleError as e:
            logger.error("Error al obtener agendas: %s", e)
            await WhatsAppService.send_message(
                phone,
                "No se pudo obtener la agenda. Intenta de nuevo en unos minutos."
            )

    async def _handle_doctor_selection(self, user, phone: str, message_text: str):
        """Procesa la seleccion de doctor por numero"""
        state_data = await workflow_state.get_data(phone)
        doctor_list = state_data.get("doctors", []) if state_data else []
        saved_date = state_data.get("date") if state_data else None
        display_date = state_data.get("display_date") if state_data else None

        if not doctor_list:
            await workflow_state.clear_state(phone)
            await WhatsAppService.send_message(phone, "Sesión expirada. Intenta de nuevo.")
            return

        # Validar que sea un numero valido
        try:
            selection = int(message_text.strip())
        except ValueError:
            await WhatsAppService.send_message(
                phone,
                f"Por favor escribe un número entre 1 y {len(doctor_list)}."
            )
            return

        if selection < 1 or selection > len(doctor_list):
            await WhatsAppService.send_message(
                phone,
                f"Opción inválida. Escribe un número entre 1 y {len(doctor_list)}."
            )
            return

        doctor_name = doctor_list[selection - 1]
        await workflow_state.clear_state(phone)

        label = f"el {display_date}" if display_date else "hoy"

        # Obtener agenda solo de ese doctor
        try:
            all_data = await FileMakerService.get_agenda_all_doctors(saved_date)

            # Filtrar solo citas de este doctor
            doctor_data = [
                r for r in all_data
                if r.get("fieldData", {}).get("Recurso Humano::Nombre Lista", "").strip() == doctor_name
            ]

            formatted_msg, glossary = AgendaFormatter.format(doctor_data, doctor_name)

            # Reemplazar "Dr(a)." prefix
            formatted_msg = formatted_msg.replace(
                f"*Hola Dr(a). {doctor_name}*",
                f"*Agenda de {doctor_name} para {label}*"
            )

            await WhatsAppService.send_message(phone, formatted_msg)
            if glossary:
                await WhatsAppService.send_message(phone, f"*Glosario:*\n{glossary}")

            await self._ask_continue(phone)

        except ServicioNoDisponibleError as e:
            logger.error("Error al obtener agenda del doctor %s: %s", doctor_name, e)
            await WhatsAppService.send_message(
                phone,
                "No se pudo obtener la agenda. Intenta de nuevo en unos minutos."
            )
