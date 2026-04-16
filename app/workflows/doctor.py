import logging
import re
from datetime import datetime

import pytz
from fastapi import BackgroundTasks

from app.config import get_settings
from app.workflows import llm_agent

from app.workflows.base import WorkflowHandler
from app.workflows.role_registry import register_workflow
from app.workflows import state as workflow_state
from app.workflows import session_timer
from app.services.filemaker import FileMakerService
from app.services.whatsapp import WhatsAppService
from app.formatters.agenda import AgendaFormatter
from app.formatters.recados import RecadosFormatter
from app.exceptions import ServicioNoDisponibleError

logger = logging.getLogger(__name__)

@register_workflow("medico")
class DoctorWorkflow(WorkflowHandler):

    async def handle_text(self, user, phone: str, message_text: str = ""):
        texto = message_text.strip().lower()
        logger.info("[DOCTOR] handle_text: phone=%s, texto='%s', user_role=%s", phone, texto, getattr(user, 'role', 'N/A'))

        # Comandos globales: menu y salir (siempre disponibles)
        if texto == "menu":
            logger.info("[DOCTOR] Comando 'menu' recibido de %s", phone)
            await workflow_state.clear_state(phone)
            await llm_agent.clear_llm_state(phone)
            await self._send_menu(user, phone)
            return

        if texto == "salir":
            logger.info("[DOCTOR] Comando 'salir' recibido de %s", phone)
            await workflow_state.clear_state(phone)
            await llm_agent.clear_llm_state(phone)
            await session_timer.cancel(phone)
            await WhatsAppService.send_message(
                phone,
                "Flujo finalizado. Cuando necesites algo, escribe cualquier mensaje o *menu* para volver al inicio."
            )
            return

        # ── LLM Mode ──
        # Si LLM está habilitado y la sesión no está en fallback, procesar con LLM
        settings = get_settings()
        if settings.LLM_MODE_ENABLED and not await llm_agent.is_legacy_fallback(phone):
            # TODO: Filtro temporal de debug — ELIMINAR cuando LLM esté listo para producción
            _LLM_DEBUG_PHONE = "56948776414"
            if phone != _LLM_DEBUG_PHONE:
                logger.info("[DOCTOR] LLM en mantenimiento, legacy mode para %s", phone)
                await WhatsAppService.send_message(
                    phone,
                    "🔧 El asistente está en modo mantenimiento. "
                    "Por favor usa los botones del menú para navegar."
                )
                # Caer al flujo legacy abajo
            else:
                logger.info("[DOCTOR] Procesando con LLM para %s", phone)
                result = await llm_agent.process_message(user, phone, message_text)
                if result == "FALLBACK":
                    logger.info("[DOCTOR] LLM señaló fallback, cambiando a legacy para %s", phone)
                    await llm_agent.set_legacy_fallback(phone)
                    # Caer al flujo legacy abajo
                else:
                    return  # LLM manejó el mensaje exitosamente

        # ── Legacy Mode ──
        # Verificar si el usuario esta en un flujo multi-paso
        step = await workflow_state.get_step(phone)
        logger.info("[DOCTOR] Step actual para %s: %s", phone, step)
        if step:
            if step == "waiting_for_date":
                logger.info("[DOCTOR] Procesando date input de %s", phone)
                await self._handle_date_input(user, phone, message_text)
                return
            elif step == "waiting_for_recado":
                logger.info("[DOCTOR] Procesando recado input de %s", phone)
                await self._handle_recado_input(user, phone, message_text)
                return
            elif step == "waiting_for_continue":
                logger.info("[DOCTOR] En waiting_for_continue, respuesta='%s' de %s", texto, phone)
                if texto in ["si", "sí", "s"]:
                    await workflow_state.clear_state(phone)
                    await self._send_menu(user, phone)
                elif texto in ["no", "n"]:
                    await workflow_state.clear_state(phone)
                    await session_timer.cancel(phone)
                    await WhatsAppService.send_message(
                        phone,
                        "Hasta luego. Cuando necesites algo, escribe cualquier mensaje o *menu*."
                    )
                else:
                    logger.warning("[DOCTOR] Respuesta no reconocida en waiting_for_continue: '%s' de %s", texto, phone)
                return

        # Default: enviar plantilla inicial + mensaje de ayuda
        logger.info("[DOCTOR] Sin step activo, enviando menu inicial a %s", phone)
        await self._send_menu(user, phone)
        # Enviar mensaje de ayuda después del template inicial
        await WhatsAppService.send_message(
            phone,
            "_Puedes escribir *menu* en cualquier momento para volver al inicio o *salir* para terminar el flujo._"
        )

    async def _send_menu(self, user, phone: str):
        """Envia la plantilla inicial"""
        full_name = f"{user.name} {user.last_name}".strip()
        logger.info("[DOCTOR] _send_menu: enviando template 'respuesta_inicial_doctores_uso_interno' a %s (nombre=%s)", phone, full_name)
        await WhatsAppService.send_template(phone, full_name, "respuesta_inicial_doctores_uso_interno")
        logger.info("[DOCTOR] _send_menu: template enviado (o intentado) para %s", phone)

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
            await WhatsAppService.send_template(phone, f'{user.name} {user.last_name}', "sistema_recados", include_header=False, include_body=True)

        elif button_title == "Revisar mis recados":
            background_tasks.add_task(self._send_recados, user, phone)

        # Botones de plantilla recados (recados_de_doctores)
        elif button_title == "Agendar Paciente":
            await workflow_state.set_state(phone, "waiting_for_recado", data={"categoria": "Agendar paciente"})
            await WhatsAppService.send_template(
                phone, f'{user.name} {user.last_name}', "agendar_paciente_doctores",
                include_header=False, include_body=False,
            )

        elif button_title == "Otro (varios)":
            await workflow_state.set_state(phone, "waiting_for_recado", data={"categoria": "Otros"})
            await WhatsAppService.send_message(
                phone,
                "*Categoría: Otros*\n\n"
                "Por favor escribe tu recado incluyendo el *nombre del paciente*.\n"
                "Lo ingresaremos a FileMaker y dejaremos aviso a gerencia.\n\n"
                "_Escribe tu mensaje a continuación:_"
            )

        elif button_title == "Bloquear Agenda":
            await workflow_state.set_state(phone, "waiting_for_recado", data={"categoria": "Bloquear agenda"})
            await WhatsAppService.send_template(
                phone, f'{user.name} {user.last_name}', "bloquear_agenda_dia_doctores",
                include_header=False, include_body=False,
            )

        elif button_title == "Enviar Recetas":
            await workflow_state.set_state(phone, "waiting_for_recado", data={"categoria": "Enviar receta"})
            await WhatsAppService.send_template(
                phone, f'{user.name} {user.last_name}', "enviar_receta_doctores",
                include_header=False, include_body=False,
            )

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
                "Formato de fecha inválido. Por favor usa el formato *dd-mm-yy*\n\nEjemplo: 05-02-26"
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
                "Fecha inválida. Verifica que el día y mes sean correctos.\n\nEjemplo: 05-02-26"
            )
            await workflow_state.clear_state(phone)

    async def _handle_recado_input(self, user, phone: str, message_text: str):
        """Procesa el texto del recado y lo crea en FileMaker"""
        # Obtener datos del estado (categoria)
        state_data = await workflow_state.get_data(phone)
        categoria = state_data.get("categoria", "Otros") if state_data else "Otros"

        # Limpiar estado antes de procesar
        await workflow_state.clear_state(phone)

        # Obtener fecha y hora actual en zona horaria de Chile
        tz = pytz.timezone("America/Santiago")
        now = datetime.now(tz)
        fecha = now.strftime("%m-%d-%Y")
        hora = now.strftime("%H:%M:%S")
        fecha_display = now.strftime("%d-%m-%Y")

        # Formatear texto del recado: "autor > fecha > hora\rmensaje"
        texto_formateado = f"{user.name} > {fecha_display} > {hora}\r{message_text}"

        # Reglas por categoria:
        # - Enviar receta:    solo FileMaker
        # - Agendar paciente: solo FileMaker
        # - Bloquear agenda:  solo notificar enfermeria
        # - Otros:            FileMaker + notificar enfermeria
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
                    body_params=[user.name, message_text],
                )

            # Confirmacion al doctor
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
                f"Recado: {message_text}\n\n"
                f"{confirmacion}"
            )
            await self._ask_continue(phone)

        except ServicioNoDisponibleError as e:
            logger.error("Error al crear recado: %s", e)
            await WhatsAppService.send_message(
                phone,
                "No se pudo registrar el recado. "
                "Por favor intenta de nuevo en unos minutos."
            )

    async def _ask_continue(self, phone: str):
        """Pregunta al doctor si desea hacer algo mas"""
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
            await self._ask_continue(phone)
        except ServicioNoDisponibleError as e:
            logger.error("Servicio no disponible al consultar recados: %s", e)
            await WhatsAppService.send_message(
                phone,
                "Lo sentimos, el sistema no está disponible en este momento. "
                "Por favor intenta de nuevo en unos minutos."
            )
