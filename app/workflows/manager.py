import csv
import io
import logging
import re
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta

import pytz
from fastapi import BackgroundTasks

from app.config import get_settings
from app.workflows.base import WorkflowHandler
from app.workflows.role_registry import register_workflow
from app.workflows import state as workflow_state
from app.workflows import session_timer
from app.workflows.llm import engine as llm_engine
from app.services.filemaker import FileMakerService
from app.services.whatsapp import WhatsAppService
from app.services import redis as redis_svc
from app.formatters.agenda import AgendaFormatter
from app.exceptions import ServicioNoDisponibleError

logger = logging.getLogger(__name__)

# Filtros de citas invalidas (compartidos con AgendaFormatter)
_IGNORAR_TIPO = ["Eliminada", "Bloqueada", "No Viene", "Disponible"]
_IGNORAR_ACTIVIDAD = ["RECORDATORIO", "VISITADOR MÉDICO", "LABORATORIO"]

# TTL para el modo doctor activo (2 horas)
_DOCTOR_MODE_TTL = 7200


def _filtrar_citas_validas(data: list) -> list:
    """Filtra citas eliminadas, bloqueadas, etc."""
    return [
        r for r in data
        if r["fieldData"].get("Tipo") not in _IGNORAR_TIPO
        and r["fieldData"].get("Actividad", "").upper() not in _IGNORAR_ACTIVIDAD
        and r["fieldData"].get("Hora", "00:00:00") != "00:00:00"
    ]


def _doctor_mode_key(phone: str) -> str:
    return f"manager:doctor_mode:{phone}"


async def _is_doctor_mode(phone: str) -> bool:
    return await redis_svc.get(_doctor_mode_key(phone)) is not None


async def _set_doctor_mode(phone: str):
    await redis_svc.set(_doctor_mode_key(phone), "1", ttl=_DOCTOR_MODE_TTL)


async def _clear_doctor_mode(phone: str):
    await redis_svc.delete(_doctor_mode_key(phone))


@register_workflow("gerencia")
class ManagerWorkflow(WorkflowHandler):

    def __init__(self):
        # Importar DoctorWorkflow aquí para evitar import circular
        from app.workflows.doctor import DoctorWorkflow
        self._doctor_workflow = DoctorWorkflow()

    async def handle_text(self, user, phone: str, message_text: str = ""):
        texto = message_text.strip().lower()

        # Cancelación de actualización de precios: "cancelar XXXXXXXX"
        if texto.startswith("cancelar "):
            from app.services import price_scheduler
            job_id = message_text.strip().split(" ", 1)[1].strip()
            cancelado = await price_scheduler.cancel_price_update(job_id)
            if cancelado:
                await WhatsAppService.send_message(phone, f"✅ Actualización *{job_id}* cancelada.")
            else:
                await WhatsAppService.send_message(
                    phone,
                    f"No se encontró una actualización pendiente con ID *{job_id}*."
                )
            return

        # "salir" siempre termina TODO, sin importar donde estemos
        if texto == "salir":
            await workflow_state.clear_state(phone)
            await _clear_doctor_mode(phone)
            await llm_engine.clear_llm_state(phone)
            await session_timer.cancel(phone)
            await WhatsAppService.send_message(
                phone,
                "Flujo finalizado. Cuando necesites algo, escribe cualquier mensaje o *menu*."
            )
            return

        # Verificar si estamos en modo doctor
        in_doctor_mode = await _is_doctor_mode(phone)

        if in_doctor_mode:
            # "menu" desde modo doctor -> volver al menu de gerencia
            if texto == "menu":
                await workflow_state.clear_state(phone)
                await _clear_doctor_mode(phone)
                await llm_engine.clear_llm_state(phone)
                await self._send_menu(user, phone)
                return

            # Delegar todo lo demás al workflow de doctor
            await self._doctor_workflow.handle_text(user, phone, message_text)
            return

        # --- Flujo normal de gerencia ---

        if texto == "menu":
            await workflow_state.clear_state(phone)
            await llm_engine.clear_llm_state(phone)
            await self._send_menu(user, phone)
            return

        # ── LLM Mode ──
        settings = get_settings()
        role = getattr(user, "role", "gerencia")

        if settings.LLM_MODE_ENABLED and not await llm_engine.is_legacy_fallback(phone):
            # Verificar modo mantención
            if settings.llm_is_in_maintenance(role, phone):
                logger.info("[MANAGER] LLM en mantención para %s (rol=%s)", phone, role)
                # Caer al flujo legacy abajo
            else:
                logger.info("[MANAGER] Procesando con LLM para %s", phone)
                result = await llm_engine.process_message(user, phone, message_text, role=role)
                if result == "FALLBACK" and settings.llm_has_legacy_fallback(role):
                    logger.info("[MANAGER] LLM señaló fallback, cambiando a legacy para %s", phone)
                    await llm_engine.set_legacy_fallback(phone)
                    # Caer al flujo legacy abajo
                else:
                    return  # LLM manejó el mensaje exitosamente

        # Verificar flujo multi-paso
        step = await workflow_state.get_step(phone)
        if step:
            if step == "waiting_for_doctor_selection":
                await self._handle_doctor_selection(user, phone, message_text)
                return
            elif step == "waiting_for_agenda_date":
                await self._handle_date_input(user, phone, message_text)
                return
            elif step == "waiting_for_price_schedule_time":
                await self._handle_price_schedule_time(user, phone, message_text)
                return
            elif step == "waiting_for_continue":
                if texto in ["si", "sí", "s"]:
                    await workflow_state.clear_state(phone)
                    await self._send_menu(user, phone)
                else:
                    await workflow_state.clear_state(phone)
                    await session_timer.cancel(phone)
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
        elif texto == "3":
            # Activar modo doctor
            if user.role and user.role.lower() == "medico_gerencia":
                await workflow_state.clear_state(phone)
                await _set_doctor_mode(phone)
                await self._doctor_workflow._send_menu(user, phone)
            else:
                await WhatsAppService.send_message(
                    phone,
                    "Esta opción no está disponible para tu perfil."
                )
            return

        # Cualquier otro texto: enviar menu
        await self._send_menu(user, phone)

    async def handle_button(self, user, phone: str, button_title: str, background_tasks: BackgroundTasks):
        # Si estamos en modo doctor, delegar botones
        if await _is_doctor_mode(phone):
            await self._doctor_workflow.handle_button(user, phone, button_title, background_tasks)
            return

        logger.debug("Boton recibido en manager (inesperado): '%s'", button_title)
        await self._send_menu(user, phone)

    async def _send_menu(self, user, phone: str):
        """Envia el menu principal del manager"""
        # Verificar si el usuario tiene perfil hibrido para mostrar opcion 3
        show_doctor_option = user.role and user.role.lower() == "medico_gerencia"

        msg = (
            f"*Hola {user.name} {user.last_name}* \n"
            "Soy tu asistente virtual. Selecciona una opción:\n\n"
            "*1.* Revisar agendas de doctores del día\n"
            "*2.* Revisar agendas de doctores en otra fecha\n"
        )
        if show_doctor_option:
            msg += "*3.* Revisar mi perfil de médico\n"
        msg += "\n_Escribe *menu* para volver al inicio o *salir* para terminar._"

        await WhatsAppService.send_message(phone, msg)

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

    async def handle_document(self, user, phone: str, document):
        """Recibe un CSV de Shopify para programar actualización masiva de precios."""
        mime = getattr(document, "mime_type", "") or ""
        filename = getattr(document, "filename", "") or ""

        if mime != "text/csv" and not filename.lower().endswith(".csv"):
            await WhatsAppService.send_message(
                phone,
                "Solo se aceptan archivos CSV (.csv) para actualización de precios.\n"
                "Por favor envía el archivo exportado desde Shopify."
            )
            return

        try:
            content_bytes = await WhatsAppService.download_media(document.id)
            content = content_bytes.decode("utf-8-sig")  # utf-8-sig maneja BOM de Excel/Shopify
        except Exception as e:
            logger.error("[MANAGER] Error descargando CSV de %s: %s", phone, e)
            await WhatsAppService.send_message(
                phone,
                "No se pudo descargar el archivo. Intenta enviarlo de nuevo."
            )
            return

        # Parsear CSV usando índices para tolerar headers con espacios extra
        try:
            lines = content.splitlines()
            if not lines:
                raise ValueError("Archivo vacío")

            header_reader = csv.reader([lines[0]])
            raw_headers = next(header_reader)
            stripped_headers = [h.strip() for h in raw_headers]

            sku_col = next((i for i, h in enumerate(stripped_headers) if h.lower() == "variant sku"), None)
            price_col = next((i for i, h in enumerate(stripped_headers) if h.lower() == "variant price"), None)

            if sku_col is None or price_col is None:
                await WhatsAppService.send_message(
                    phone,
                    "El CSV no tiene las columnas requeridas.\n\n"
                    "Se esperan: *Variant SKU* y *Variant Price*\n\n"
                    "Usa el export de productos de Shopify."
                )
                return

            variantes = []
            data_reader = csv.reader(io.StringIO(content))
            next(data_reader)  # Saltar encabezado

            for row in data_reader:
                if len(row) <= max(sku_col, price_col):
                    continue
                sku = row[sku_col].strip()
                price = row[price_col].strip()
                if not sku or not price:
                    continue
                try:
                    float(price)
                except ValueError:
                    continue
                variantes.append({"sku": sku, "price": price})

        except Exception as e:
            logger.error("[MANAGER] Error parseando CSV de %s: %s", phone, e)
            await WhatsAppService.send_message(
                phone,
                "No se pudo leer el archivo CSV. Verifica que el formato sea correcto."
            )
            return

        if not variantes:
            await WhatsAppService.send_message(
                phone,
                "El CSV no contiene variantes válidas (SKU y precio requeridos)."
            )
            return

        await workflow_state.set_state(
            phone,
            "waiting_for_price_schedule_time",
            data={"variantes": variantes},
        )
        await WhatsAppService.send_message(
            phone,
            f"*CSV recibido* ✅\n\n"
            f"Se encontraron *{len(variantes)} variante(s)* para actualizar.\n\n"
            f"¿A qué hora quieres ejecutar la actualización? (hora Chile)\n"
            f"Formato: *YYYY-MM-DD HH:MM*\n\n"
            f"Ejemplo: *2026-05-20 20:00*"
        )

    async def _handle_price_schedule_time(self, user, phone: str, message_text: str):
        """Procesa la hora de ejecución para la actualización masiva de precios."""
        from app.services import price_scheduler

        tz_chile = pytz.timezone("America/Santiago")
        texto = message_text.strip()

        try:
            run_at_naive = datetime.strptime(texto, "%Y-%m-%d %H:%M")
            run_at = tz_chile.localize(run_at_naive)
        except ValueError:
            await WhatsAppService.send_message(
                phone,
                "Formato inválido.\n\n"
                "Usa: *YYYY-MM-DD HH:MM*\n"
                "Ejemplo: *2026-05-20 20:00*"
            )
            return

        now = datetime.now(tz_chile)
        if run_at <= now + timedelta(minutes=5):
            await WhatsAppService.send_message(
                phone,
                "La hora debe ser al menos 5 minutos en el futuro."
            )
            return

        state_data = await workflow_state.get_data(phone)
        variantes = state_data.get("variantes", []) if state_data else []

        if not variantes:
            await WhatsAppService.send_message(phone, "Sesión expirada. Por favor envía el CSV de nuevo.")
            await workflow_state.clear_state(phone)
            return

        job_id = uuid.uuid4().hex[:8]
        await price_scheduler.schedule_price_update(job_id, phone, variantes, run_at)
        await workflow_state.clear_state(phone)

        fecha_str = run_at.strftime("%d/%m/%Y a las %H:%M")
        await WhatsAppService.send_message(
            phone,
            f"✅ *Actualización programada*\n\n"
            f"📅 {fecha_str} hora Chile\n"
            f"📦 {len(variantes)} variante(s)\n"
            f"🔑 ID: *{job_id}*\n\n"
            f"Te notificaré cuando se complete.\n"
            f"Para cancelar escribe: *cancelar {job_id}*"
        )

    async def _handle_doctor_selection(self, user, phone: str, message_text: str):
        """Procesa la seleccion de doctor por numero, o si/no para continuar"""
        texto = message_text.strip().lower()
        state_data = await workflow_state.get_data(phone)
        doctor_list = state_data.get("doctors", []) if state_data else []
        saved_date = state_data.get("date") if state_data else None
        display_date = state_data.get("display_date") if state_data else None

        if not doctor_list:
            await workflow_state.clear_state(phone)
            await WhatsAppService.send_message(phone, "Sesión expirada. Intenta de nuevo.")
            return

        # "no" → salir del flujo
        if texto == "no":
            await workflow_state.clear_state(phone)
            await session_timer.cancel(phone)
            await WhatsAppService.send_message(
                phone,
                "¡Hasta luego! Cuando necesites algo, escribe cualquier mensaje o *menu*."
            )
            return

        # "si" / "s" → volver al menu principal
        if texto in ["si", "sí", "s"]:
            await workflow_state.clear_state(phone)
            await self._send_menu(user, phone)
            return

        # Validar que sea un numero valido
        try:
            selection = int(texto)
        except ValueError:
            await WhatsAppService.send_message(
                phone,
                f"Escribe un número entre 1 y {len(doctor_list)}, *si* para volver al menú, o *no* para salir."
            )
            return

        if selection < 1 or selection > len(doctor_list):
            await WhatsAppService.send_message(
                phone,
                f"Opción inválida. Escribe un número entre 1 y {len(doctor_list)}."
            )
            return

        doctor_name = doctor_list[selection - 1]
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

            # Mantener en la misma lista para elegir otro doctor
            await WhatsAppService.send_message(
                phone,
                "Escribe el número de otro doctor para ver su agenda, *si* para volver al menú, o *no* para salir."
            )

        except ServicioNoDisponibleError as e:
            logger.error("Error al obtener agenda del doctor %s: %s", doctor_name, e)
            await WhatsAppService.send_message(
                phone,
                "No se pudo obtener la agenda. Intenta de nuevo en unos minutos."
            )
