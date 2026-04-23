"""
Tool parametrizada: consultar_agenda (para gerencia).

A diferencia de la tool de doctores (que envía formateado por WhatsApp),
esta devuelve datos crudos resumidos al LLM para que él interprete
y responda preguntas abiertas del gerente.

Soporta filtros opcionales: doctor, solo_resumen.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List

import pytz

from app.services.filemaker import FileMakerService

logger = logging.getLogger(__name__)

# Filtros de citas inválidas (misma lógica que legacy)
_IGNORAR_TIPO = ["Eliminada", "Bloqueada", "No Viene"]
_IGNORAR_ACTIVIDAD = ["RECORDATORIO", "VISITADOR MÉDICO", "LABORATORIO"]


# ──────────────────────────────────────────────
# Definición OpenAI function calling
# ──────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "consultar_agenda",
        "description": (
            "Consulta las agendas de la clínica para una fecha dada. "
            "Por defecto trae TODOS los doctores. Puedes filtrar por "
            "nombre de doctor para ver solo su agenda. "
            "Usa solo_resumen=true para obtener solo el listado de "
            "doctores con su cantidad de citas (útil para consultas generales). "
            "IMPORTANTE: para fechas relativas ('mañana', 'próximo lunes'), "
            "primero usa calcular_fecha."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fecha": {
                    "type": "string",
                    "description": (
                        "Fecha en formato ISO YYYY-MM-DD. "
                        "Si no se indica, se usa la fecha de hoy."
                    ),
                },
                "doctor": {
                    "type": "string",
                    "description": (
                        "Nombre (o parte del nombre) del doctor para filtrar. "
                        "Búsqueda flexible: 'Ramirez' encontrará 'Dra. Claudia Ramirez'. "
                        "Si no se indica, se muestran todos los doctores."
                    ),
                },
                "solo_resumen": {
                    "type": "boolean",
                    "description": (
                        "Si true, retorna solo el resumen (nombre doctor + cantidad de citas) "
                        "sin el detalle de cada cita. Útil para preguntas como "
                        "'¿qué doctores vienen hoy?' o '¿cuántos pacientes tiene X?'."
                    ),
                },
            },
            "required": [],
        },
    },
}

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _filtrar_citas_validas(data: List[Dict]) -> List[Dict]:
    """Filtra citas eliminadas, bloqueadas, de sistema, etc."""
    return [
        r for r in data
        if r["fieldData"].get("Tipo") not in _IGNORAR_TIPO
        and r["fieldData"].get("Actividad", "").upper() not in _IGNORAR_ACTIVIDAD
        and r["fieldData"].get("Hora", "00:00:00") != "00:00:00"
    ]


def _match_doctor(nombre_completo: str, filtro: str) -> bool:
    """Match flexible: verifica si el filtro está contenido en el nombre del doctor."""
    return filtro.lower().strip() in nombre_completo.lower()


def _agrupar_por_doctor(data: List[Dict]) -> Dict[str, List[Dict]]:
    """Agrupa citas por nombre de doctor, ordenadas por hora."""
    doctors: Dict[str, List[Dict]] = {}
    for record in data:
        fd = record.get("fieldData", {})
        doctor_name = fd.get("Recurso Humano::Nombre Lista", "").strip()
        if not doctor_name:
            continue
        if doctor_name not in doctors:
            doctors[doctor_name] = []
        doctors[doctor_name].append(record)

    # Ordenar citas de cada doctor por hora
    for doctor_name in doctors:
        doctors[doctor_name].sort(key=lambda x: x["fieldData"].get("Hora", ""))

    return doctors


def _formatear_resumen(doctors: Dict[str, List[Dict]], fecha_display: str) -> str:
    """Genera resumen: nombre doctor + Nº citas."""
    if not doctors:
        return f"No hay doctores con agenda para {fecha_display}."

    lines = [f"Agendas para {fecha_display} — {len(doctors)} doctor(es):\n"]
    for name, citas in doctors.items():
        lines.append(f"- {name}: {len(citas)} cita(s)")

    return "\n".join(lines)


def _formatear_detalle(doctors: Dict[str, List[Dict]], fecha_display: str) -> str:
    """Genera detalle completo: doctor + cada cita con hora/paciente/procedimiento."""
    if not doctors:
        return f"No hay doctores con agenda para {fecha_display}."

    lines = [f"Agendas para {fecha_display}:\n"]

    for name, citas in doctors.items():
        lines.append(f"\n{name} — {len(citas)} cita(s):")
        for record in citas:
            fd = record["fieldData"]
            hora = ":".join(fd.get("Hora", "00:00").split(":")[:2])
            tipo = fd.get("Tipo", "")
            paciente_nombre = fd.get("Pacientes::NOMBRE", "")
            paciente_apellido = fd.get("Pacientes::APELLIDO PATERNO", "")
            paciente = f"{paciente_nombre} {paciente_apellido}".strip() or "Sin paciente"
            actividad = fd.get("Actividad", "Sin especificar")

            tipo_tag = ""
            if tipo.lower() == "disponible":
                lines.append(f"  {hora} — Disponible")
                continue
            elif tipo.lower() == "conjunto":
                tipo_tag = " (conjunto)"

            lines.append(f"  {hora} — {paciente} — {actividad}{tipo_tag}")

    return "\n".join(lines)


# ──────────────────────────────────────────────
# Handler
# ──────────────────────────────────────────────

async def handle(user, phone: str, arguments: Dict[str, Any]) -> str:
    """
    Consulta agendas con filtros opcionales.
    Retorna datos crudos como string para que el LLM interprete.
    """
    fecha_input = arguments.get("fecha")
    filtro_doctor = arguments.get("doctor")
    solo_resumen = arguments.get("solo_resumen", False)

    # Resolver fecha
    tz = pytz.timezone("America/Santiago")
    if fecha_input:
        try:
            date_obj = datetime.strptime(fecha_input.strip(), "%Y-%m-%d")
            filemaker_date = date_obj.strftime("%m-%d-%Y")
            fecha_display = date_obj.strftime("%d-%m-%Y")
        except ValueError:
            return "Formato de fecha inválido. Usa YYYY-MM-DD."
    else:
        now = datetime.now(tz)
        filemaker_date = now.strftime("%m-%d-%Y")
        fecha_display = now.strftime("%d-%m-%Y")

    # Consultar FileMaker
    all_data = await FileMakerService.get_agenda_all_doctors(filemaker_date)
    logger.info(
        "[AGENDA_MGR] FM raw: %d registros para %s",
        len(all_data) if all_data else 0, fecha_display,
    )

    # Filtrar citas inválidas
    valid_data = _filtrar_citas_validas(all_data)
    logger.info(
        "[AGENDA_MGR] Post-filtro: %d citas válidas de %d totales",
        len(valid_data), len(all_data) if all_data else 0,
    )

    # Agrupar por doctor
    doctors = _agrupar_por_doctor(valid_data)
    logger.info(
        "[AGENDA_MGR] Doctores con agenda: %d — %s",
        len(doctors),
        {name: len(citas) for name, citas in doctors.items()},
    )

    # Aplicar filtro de doctor si se especificó
    if filtro_doctor:
        filtered = {
            name: citas for name, citas in doctors.items()
            if _match_doctor(name, filtro_doctor)
        }
        logger.info(
            "[AGENDA_MGR] Filtro doctor='%s': %d match(es) de %d",
            filtro_doctor, len(filtered), len(doctors),
        )
        if not filtered:
            # Sugerir doctores similares
            all_names = list(doctors.keys())
            return (
                f"No se encontró un doctor que coincida con '{filtro_doctor}' "
                f"en la agenda del {fecha_display}.\n"
                f"Doctores disponibles: {', '.join(all_names)}"
            )
        doctors = filtered

    # Formatear resultado
    if solo_resumen:
        result = _formatear_resumen(doctors, fecha_display)
    else:
        result = _formatear_detalle(doctors, fecha_display)

    logger.info(
        "[AGENDA_MGR] Resultado (%d chars): %s",
        len(result),
        result[:500] + "..." if len(result) > 500 else result,
    )
    return result
