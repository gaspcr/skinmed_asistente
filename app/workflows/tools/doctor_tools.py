"""
Herramientas (tools) Pydantic para el flujo del médico.

Cada clase representa una accion que la IA puede invocar.
Instructor forzara al LLM a devolver exactamente una de estas clases,
con los campos validados por Pydantic.

El campo `tipo` en cada clase actúa como discriminador del Union, lo que
permite a Instructor comunicar correctamente el schema al LLM via function
calling y a Pydantic resolver la instancia correcta sin ambigüedad.
"""
import re
from typing import Annotated, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator


class ConsultarAgenda(BaseModel):
    """
    Usar SOLO cuando el doctor pide explícitamente ver su agenda o sus citas.
    Ejemplos válidos: 'qué tengo hoy', 'mi agenda', 'tengo algo mañana',
    'citas del lunes', 'agenda del 15 de abril', 'qué tengo esta semana'.
    NO usar para saludos, preguntas generales ni conversación sin una solicitud
    clara de ver agenda. Si hay duda, usar ResponderConversacion.
    """
    tipo: Literal["consultar_agenda"] = "consultar_agenda"
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
    Usar SOLO cuando el doctor quiere dejar un recado Y ya indicó el contenido
    en su mensaje. Incluye: agendar paciente, enviar receta, bloquear agenda, u otro.
    Ejemplos válidos: 'quiero agendar a Juan Pérez para mañana',
    'bloquear el viernes por la tarde', 'enviar receta a paciente X'.
    Si el doctor solo dice 'quiero dejar un recado' sin más detalle,
    usar ResponderConversacion para pedir el contenido primero.
    """
    tipo: Literal["enviar_recado"] = "enviar_recado"
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
        "'Otros' para cualquier otro recado."
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
    Usar cuando el doctor pide explícitamente ver sus recados o mensajes pendientes.
    Ejemplos: 'mis recados', 'tengo recados?', 'mensajes pendientes', 'ver avisos'.
    """
    tipo: Literal["ver_recados"] = "ver_recados"
    mensaje_confirmacion: str = Field(
        description="Mensaje breve confirmando que se buscarán sus recados. "
        "Ejemplo: 'Buscando tus recados vigentes...'"
    )


class Despedirse(BaseModel):
    """
    Usar cuando el doctor indica claramente que termina la conversación.
    Ejemplos: 'chao', 'hasta luego', 'adiós', 'eso era todo', 'listo gracias'.
    No usar si solo dice 'gracias' en medio de una conversación activa —
    en ese caso usar ResponderConversacion.
    """
    tipo: Literal["despedirse"] = "despedirse"
    mensaje_despedida: str = Field(
        description="Mensaje de despedida amable y breve. "
        "Ejemplo: 'Hasta luego, Dr(a). Cuando necesites algo, escríbeme.'"
    )


class ResponderConversacion(BaseModel):
    """
    DEFAULT: usar esta herramienta en cualquier caso que no encaje claramente
    en las otras. Incluye saludos ('hola', 'buenos días'), preguntas generales
    ('en qué me ayudas', 'qué puedes hacer'), agradecimientos en medio de
    conversación, o cuando faltan datos para completar otra acción.
    Siempre responder amablemente y ofrecer las opciones disponibles.
    Ante la duda, SIEMPRE preferir esta herramienta sobre las demás.
    """
    tipo: Literal["responder_conversacion"] = "responder_conversacion"
    mensaje: str = Field(
        description="Respuesta conversacional amable. Si el doctor saluda, "
        "responder el saludo y ofrecer ayuda. Si no queda claro qué quiere o "
        "falta información para completar la acción, preguntar lo necesario y "
        "listar brevemente las opciones: revisar agenda, enviar recado, ver recados."
    )


class DoctorToolCall(BaseModel):
    """
    Wrapper concreto que contiene la acción elegida por la IA.
    Pasar esta clase como response_model a Instructor evita que cree su
    propio wrapper interno Response(content=...) alrededor de Unions.
    Acceder al resultado via response.accion.
    """
    accion: Annotated[
        Union[
            ConsultarAgenda,
            EnviarRecado,
            VerRecados,
            Despedirse,
            ResponderConversacion,
        ],
        Field(discriminator="tipo"),
    ]
