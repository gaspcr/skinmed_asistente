"""
Workflow del médico con soporte dual: modo LLM y modo estricto (legacy).

Modo LLM (LLM_ENABLED=True):
  Usa inteligencia artificial para clasificar la intención del doctor
  y ejecutar la acción correspondiente via Function Calling.

Modo estricto (LLM_ENABLED=False):
  Usa la máquina de estados original con menús, regex y flujos fijos.
  Útil como fallback si hay problemas con la API de OpenAI.

Los botones de WhatsApp funcionan igual en ambos modos.
"""
import logging
import re
from datetime import datetime

import pytz
from fastapi import BackgroundTasks

from app.config import get_settings

from app.workflows.base import WorkflowHandler
from app.workflows.role_registry import register_workflow
from app.workflows import state as workflow_state
from app.workflows import session_timer
from app.workflows.tools.doctor_tools import (
    ConsultarAgenda,
    EnviarRecado,
    VerRecados,
    Despedirse,
    ResponderConversacion,
    DoctorToolCall,
)
from app.services.filemaker import FileMakerService
from app.services.whatsapp import WhatsAppService
from app.services import llm as llm_svc
from app.services import redis as redis_svc
from app.formatters.agenda import AgendaFormatter
from app.formatters.recados import RecadosFormatter
from app.exceptions import ServicioNoDisponibleError
from app.utils.activity_log import log_action_taken, log_llm_fallback

logger = logging.getLogger(__name__)


@register_workflow("medico")
class DoctorWorkflow(WorkflowHandler):

    def _is_llm_enabled(self) -> bool:
        """Verifica si el modo LLM está habilitado via variable de entorno."""
        settings = get_settings()
        return settings.LLM_ENABLED

    # =========================================================================
    # ENTRY POINT — delega al modo correcto
    # =========================================================================

    async def handle_text(self, user, phone: str, message_text: str = ""):
        texto = message_text.strip().lower()

        # "salir" siempre funciona igual en ambos modos
        if texto == "salir":
            log_action_taken(phone, "state", "salir")
            await workflow_state.clear_state(phone)
            await session_timer.cancel(phone)
            if self._is_llm_enabled():
                await redis_svc.clear_history(phone)
            await WhatsAppService.send_message(
                phone,
                "Flujo finalizado. Cuando necesites algo, escribe cualquier mensaje o *menu* para volver al inicio."
            )
            return

        # Flujos multi-paso pendientes (compartidos entre ambos modos)
        step = await workflow_state.get_step(phone)
        if step == "waiting_for_recado":
            await self._handle_recado_input(user, phone, message_text)
            return
        if step == "waiting_for_date":
            await self._handle_date_input(user, phone, message_text)
            return

        # Delegar según modo
        if self._is_llm_enabled():
            await self._handle_text_llm(user, phone, message_text)
        else:
            await self._handle_text_legacy(user, phone, message_text, step)

    # =========================================================================
    # MODO LLM — clasificación por inteligencia artificial
    # =========================================================================

    async def _handle_text_llm(self, user, phone: str, message_text: str):
        """Procesa texto usando LLM + Function Calling."""
        try:
            result = await llm_svc.classify_intent(
                phone=phone,
                message=message_text,
                user_name=f"{user.name} {user.last_name}".strip(),
                user_role="médico",
                response_model=DoctorToolCall,
            )
            response = result.accion
        except Exception as e:
            log_llm_fallback(phone, str(e))
            await WhatsAppService.send_message(
                phone,
                "No pude procesar tu mensaje en este momento. "
                "Te muestro las opciones disponibles:"
            )
            await self._send_menu(user, phone)
            return

        # Ejecutar la acción según la herramienta elegida por la IA
        if isinstance(response, ConsultarAgenda):
            details = response.fecha or "hoy"
            log_action_taken(phone, "llm", "ConsultarAgenda", details=details)
            await WhatsAppService.send_message(phone, response.mensaje_confirmacion)
            await self._send_agenda(user, phone, response.fecha)

        elif isinstance(response, EnviarRecado):
            log_action_taken(phone, "llm", "EnviarRecado", details=response.categoria)
            await WhatsAppService.send_message(phone, response.mensaje_confirmacion)
            await self._process_recado(user, phone, response)

        elif isinstance(response, VerRecados):
            log_action_taken(phone, "llm", "VerRecados")
            await WhatsAppService.send_message(phone, response.mensaje_confirmacion)
            await self._send_recados(user, phone)

        elif isinstance(response, Despedirse):
            log_action_taken(phone, "llm", "Despedirse")
            await workflow_state.clear_state(phone)
            await session_timer.cancel(phone)
            await redis_svc.clear_history(phone)
            await WhatsAppService.send_message(phone, response.mensaje_despedida)

        elif isinstance(response, ResponderConversacion):
            log_action_taken(phone, "llm", "ResponderConversacion")
            await WhatsAppService.send_message(phone, response.mensaje)

    # =========================================================================
    # MODO ESTRICTO (LEGACY) — máquina de estados original
    # =========================================================================

    async def _handle_text_legacy(self, user, phone: str, message_text: str, step: str = None):
        """Procesa texto usando la máquina de estados original (sin LLM)."""
        texto = message_text.strip().lower()

        # Comando global: menu
        if texto == "menu":
            log_action_taken(phone, "legacy", "menu")
            await workflow_state.clear_state(phone)
            await self._send_menu(user, phone)
            return

        # Verificar flujo multi-paso
        if step:
            if step == "waiting_for_date":
                await self._handle_date_input(user, phone, message_text)
                return
            elif step == "waiting_for_continue":
                if texto in ["si", "sí", "s"]:
                    log_action_taken(phone, "legacy", "continuar:si")
                    await workflow_state.clear_state(phone)
                    await self._send_menu(user, phone)
                elif texto in ["no", "n"]:
                    log_action_taken(phone, "legacy", "continuar:no")
                    await workflow_state.clear_state(phone)
                    await session_timer.cancel(phone)
                    await WhatsAppService.send_message(
                        phone,
                        "Hasta luego. Cuando necesites algo, escribe cualquier mensaje o *menu*."
                    )
                return

        # Default: enviar plantilla inicial + mensaje de ayuda
        log_action_taken(phone, "legacy", "menu:inicial")
        await self._send_menu(user, phone)
        await WhatsAppService.send_message(
            phone,
            "_Puedes escribir *menu* en cualquier momento para volver al inicio o *salir* para terminar el flujo._"
        )

    async def _handle_date_input(self, user, phone: str, message_text: str):
        """Procesa la entrada de fecha del usuario (modo legacy)."""
        date_pattern = r'^(\d{2})-(\d{2})-(\d{2})$'
        match = re.match(date_pattern, message_text.strip())

        if not match:
            await WhatsAppService.send_message(
                phone,
                "Formato de fecha inválido. Por favor usa el formato *dd-mm-yy*\n\nEjemplo: 05-02-26"
            )
            return

        day, month, year = match.groups()

        try:
            full_year = f"20{year}"
            date_obj = datetime.strptime(f"{day}-{month}-{full_year}", "%d-%m-%Y")
            filemaker_date = date_obj.strftime("%m-%d-%Y")

            log_action_taken(phone, "legacy", "ConsultarAgendaOtraFecha", details=filemaker_date)
            await workflow_state.clear_state(phone)
            await self._send_agenda(user, phone, filemaker_date)

        except ValueError:
            await WhatsAppService.send_message(
                phone,
                "Fecha inválida. Verifica que el día y mes sean correctos.\n\nEjemplo: 05-02-26"
            )
            await workflow_state.clear_state(phone)

    # =========================================================================
    # MENÚ Y BOTONES (compartidos entre ambos modos)
    # =========================================================================

    async def _send_menu(self, user, phone: str):
        """Envia la plantilla inicial"""
        full_name = f"{user.name} {user.last_name}".strip()
        await WhatsAppService.send_template(phone, full_name, "respuesta_inicial_doctores_uso_interno")

    async def handle_button(self, user, phone: str, button_title: str, background_tasks: BackgroundTasks):
        """
        Maneja botones de plantillas de WhatsApp.
        Los botones funcionan con lógica directa (sin LLM) en ambos modos
        porque el título del botón es determinístico.
        """
        logger.debug("Boton recibido: '%s'", button_title)

        # Registrar la interacción del botón en el historial LLM
        if self._is_llm_enabled():
            await redis_svc.push_history(phone, "user", f"[Botón: {button_title}]")

        # Botones de plantilla inicial (respuesta_inicial_doctores_uso_interno)
        if button_title == "Revisar agenda del día":
            log_action_taken(phone, "button", "ConsultarAgendaHoy")
            background_tasks.add_task(self._send_agenda, user, phone, None)

        elif button_title == "Revisar agenda otro día":
            log_action_taken(phone, "button", "ConsultarAgendaOtraFecha:esperando_fecha")
            await workflow_state.set_state(phone, "waiting_for_date")
            await WhatsAppService.send_message(
                phone,
                "Para revisar tu agenda en otro día, indícanos la fecha en formato *dd-mm-yy*\n\nEjemplo: 05-02-26"
            )

        elif button_title == "Enviar recado":
            log_action_taken(phone, "button", "EnviarRecado:menu")
            await WhatsAppService.send_template(phone, f'{user.name} {user.last_name}', "sistema_recados", include_header=False, include_body=True)

        elif button_title == "Revisar mis recados":
            log_action_taken(phone, "button", "VerRecados")
            background_tasks.add_task(self._send_recados, user, phone)

        # Botones de plantilla recados (recados_de_doctores)
        elif button_title == "Agendar Paciente":
            log_action_taken(phone, "button", "EnviarRecado:esperando_texto", details="Agendar paciente")
            await workflow_state.set_state(phone, "waiting_for_recado", data={"categoria": "Agendar paciente"})
            await WhatsAppService.send_template(
                phone, f'{user.name} {user.last_name}', "agendar_paciente_doctores",
                include_header=False, include_body=False,
            )

        elif button_title == "Otro (varios)":
            log_action_taken(phone, "button", "EnviarRecado:esperando_texto", details="Otros")
            await workflow_state.set_state(phone, "waiting_for_recado", data={"categoria": "Otros"})
            await WhatsAppService.send_message(
                phone,
                "*Categoría: Otros*\n\n"
                "Por favor escribe tu recado incluyendo el *nombre del paciente*.\n"
                "Lo ingresaremos a FileMaker y dejaremos aviso a gerencia.\n\n"
                "_Escribe tu mensaje a continuación:_"
            )

        elif button_title == "Bloquear Agenda":
            log_action_taken(phone, "button", "EnviarRecado:esperando_texto", details="Bloquear agenda")
            await workflow_state.set_state(phone, "waiting_for_recado", data={"categoria": "Bloquear agenda"})
            await WhatsAppService.send_template(
                phone, f'{user.name} {user.last_name}', "bloquear_agenda_dia_doctores",
                include_header=False, include_body=False,
            )

        elif button_title == "Enviar Recetas":
            log_action_taken(phone, "button", "EnviarRecado:esperando_texto", details="Enviar receta")
            await workflow_state.set_state(phone, "waiting_for_recado", data={"categoria": "Enviar receta"})
            await WhatsAppService.send_template(
                phone, f'{user.name} {user.last_name}', "enviar_receta_doctores",
                include_header=False, include_body=False,
            )

        else:
            logger.warning("Boton no reconocido: '%s'", button_title)
            await WhatsAppService.send_message(phone, "Opción no reconocida. Por favor intenta de nuevo.")

    # =========================================================================
    # ACCIONES DE NEGOCIO (compartidas entre ambos modos)
    # =========================================================================

    async def _process_recado(self, user, phone: str, recado: EnviarRecado):
        """Procesa un recado detectado directamente por la IA (sin flujo de botones)."""
        await self._execute_recado(user, phone, recado.categoria, recado.texto_recado)

    async def _handle_recado_input(self, user, phone: str, message_text: str):
        """Procesa el texto del recado cuando viene del flujo de botones."""
        state_data = await workflow_state.get_data(phone)
        categoria = state_data.get("categoria", "Otros") if state_data else "Otros"
        log_action_taken(phone, "state", "EnviarRecado:texto_recibido", details=categoria)
        await workflow_state.clear_state(phone)
        await self._execute_recado(user, phone, categoria, message_text)

    async def _execute_recado(self, user, phone: str, categoria: str, texto: str):
        """Lógica compartida para procesar un recado (LLM y botones)."""
        tz = pytz.timezone("America/Santiago")
        now = datetime.now(tz)
        fecha = now.strftime("%m-%d-%Y")
        hora = now.strftime("%H:%M:%S")
        fecha_display = now.strftime("%d-%m-%Y")

        texto_formateado = f"{user.name} > {fecha_display} > {hora}\r{texto}"

        guardar_en_fm = categoria != "Bloquear agenda"
        notificar_enfermeria = categoria not in ["Enviar receta", "Agendar paciente"]

        try:
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
                    body_params=[user.name, texto],
                )

            if guardar_en_fm and notificar_enfermeria:
                confirmacion = "Se ha registrado en FileMaker y notificado a enfermería."
            elif guardar_en_fm:
                confirmacion = "Se ha registrado en FileMaker."
            else:
                confirmacion = "Se ha notificado a enfermería."

            await WhatsAppService.send_message(
                phone,
                "*Recado procesado exitosamente*\n\n"
                f"Categoría: {categoria}\n"
                f"{fecha_display} — {':'.join(hora.split(':')[:2])}\n\n"
                f"Recado: {texto}\n\n"
                f"{confirmacion}"
            )

            # En modo legacy, preguntar si desea hacer algo mas
            if not self._is_llm_enabled():
                await self._ask_continue(phone)

        except ServicioNoDisponibleError as e:
            logger.error("Error al crear recado: %s", e)
            await WhatsAppService.send_message(
                phone,
                "No se pudo registrar el recado. "
                "Por favor intenta de nuevo en unos minutos."
            )

    async def _ask_continue(self, phone: str):
        """Pregunta al doctor si desea hacer algo mas (solo modo legacy)."""
        await workflow_state.set_state(phone, "waiting_for_continue")
        await WhatsAppService.send_message(
            phone,
            "¿Deseas hacer algo más? Responde *si* o *no*."
        )

    async def _send_agenda(self, user, phone: str, date: str = None):
        """Envia agenda del dia o de una fecha especifica"""
        try:
            agenda_data = await FileMakerService.get_agenda_raw(user.id, date)
            formatted_msg, glossary = AgendaFormatter.format(agenda_data, user.name)
            await WhatsAppService.send_message(phone, formatted_msg)
            if glossary:
                await WhatsAppService.send_message(phone, glossary)
            # En modo legacy, preguntar si desea hacer algo mas
            if not self._is_llm_enabled():
                await self._ask_continue(phone)
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

            formatted_msg = RecadosFormatter.format(recados_data, user.name, user.last_name, pacient_names)
            await WhatsAppService.send_message(phone, formatted_msg)
            # En modo legacy, preguntar si desea hacer algo mas
            if not self._is_llm_enabled():
                await self._ask_continue(phone)
        except ServicioNoDisponibleError as e:
            logger.error("Servicio no disponible al consultar recados: %s", e)
            await WhatsAppService.send_message(
                phone,
                "Lo sentimos, el sistema no está disponible en este momento. "
                "Por favor intenta de nuevo en unos minutos."
            )
