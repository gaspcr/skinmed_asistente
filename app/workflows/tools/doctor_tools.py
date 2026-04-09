"""
Herramientas (tools) Pydantic para el flujo del médico.

Cada clase representa una accion que la IA puede invocar.
Instructor forzara al LLM a devolver exactamente una de estas clases,
con los campos validados por Pydantic.
"""
import re
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator


class ConsultarAgenda(BaseModel):
    """
    El médico quiere ver su agenda.
    Usar para cualquier consulta de agenda: hoy, mañana, una fecha específica,
    'la próxima semana', 'el lunes', '15 de abril', '05-04-26', etc.
    Si el doctor no especifica fecha, se asume hoy (dejar fecha en None).
    """
    fecha: Optional[str] = Field(
        default=None,
        description="Fecha a consultar en formato MM-DD-YYYY (mes-día-año). "
        "Dejar en null/None si el doctor quiere ver la agenda de HOY. "
        "Si menciona cualquier otra fecha (mañana, el lunes, 15 de abril, etc.), "
        "convertirla siempre a MM-DD-YYYY. Ejemplo: 15 de abril de 2026 → 04-15-2026."
    )
    mensaje_confirmacion: str = Field(
        description="Mensaje breve confirmando la consulta. "
        "Ejemplos: 'Buscando tu agenda de hoy...' / 'Buscando tu agenda para el 15 de abril...'"
    )

    @field_validator("fecha")
    @classmethod
    def validar_formato_fecha(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not re.match(r"^\d{2}-\d{2}-\d{4}$", v):
            raise ValueError(
                f"Formato de fecha inválido: '{v}'. "
                "Debe ser MM-DD-YYYY (mes-día-año). Ejemplo: 04-15-2026."
            )
        month, day, year = v.split("-")
        if not (1 <= int(month) <= 12 and 1 <= int(day) <= 31):
            raise ValueError(
                f"Fecha fuera de rango: mes={month}, día={day}. "
                "El mes debe ser 01-12 y el día 01-31."
            )
        return v


class EnviarRecado(BaseModel):
    """
    El médico quiere dejar un recado o mensaje.
    Incluye: agendar paciente, enviar receta, bloquear agenda, u otro recado general.
    """
    categoria: Literal[
        "Agendar paciente",
        "Enviar receta",
        "Bloquear agenda",
        "Otros"
    ] = Field(
        description="Categoría del recado. Elegir según el contenido: "
        "'Agendar paciente' si quiere agendar a alguien, "
        "'Enviar receta' si quiere enviar una receta, "
        "'Bloquear agenda' si quiere bloquear horarios o días, "
    )
    texto_recado: str = Field(
        description="El contenido tal cual del recado del doctor, "
        "sin modificar ni resumir. Copiar textualmente lo que escribió."
    )
    mensaje_confirmacion: str = Field(
        description="Mensaje breve confirmando que el recado será procesado. "
        "Ejemplo: 'Registrando tu recado de agendar paciente...'"
    )


class VerRecados(BaseModel):
    """
    El médico quiere revisar sus recados pendientes/vigentes.
    Usar cuando pide ver 'mis recados', 'tengo recados', 'mensajes pendientes', etc.
    """
    mensaje_confirmacion: str = Field(
        description="Mensaje breve confirmando que se buscarán sus recados. "
        "Ejemplo: 'Buscando tus recados vigentes...'"
    )


class Despedirse(BaseModel):
    """
    El médico quiere terminar la conversación.
    Usar cuando dice 'chao', 'gracias', 'eso era todo', 'salir', 'adiós', etc.
    """
    mensaje_despedida: str = Field(
        description="Mensaje de despedida amable y breve. "
        "Ejemplo: 'Hasta luego, Dr(a). Cuando necesites algo, escríbeme.'"
    )


class ResponderConversacion(BaseModel):
    """
    Respuesta conversacional general cuando el doctor no está pidiendo
    ninguna acción específica. Usar para saludos, agradecimientos,
    preguntas generales sobre el sistema, o cuando no queda claro qué quiere.
    Siempre incluir una sugerencia de las acciones disponibles.
    """
    mensaje: str = Field(
        description="Respuesta conversacional amable. Si el doctor saluda, "
        "responder el saludo y ofrecer ayuda. Si no queda claro qué quiere, "
        "listar brevemente las opciones: revisar agenda, enviar recado, ver recados."
    )
