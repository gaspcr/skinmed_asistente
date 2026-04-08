"""
Herramientas (tools) Pydantic para el flujo del médico.

Cada clase representa una accion que la IA puede invocar.
Instructor forzara al LLM a devolver exactamente una de estas clases,
con los campos validados por Pydantic, eliminando la necesidad de
regex o parsing manual.
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field


class ConsultarAgendaHoy(BaseModel):
    """
    El médico quiere ver su agenda del día de hoy.
    Usar cuando el doctor pide ver 'mi agenda', 'mis citas de hoy',
    'qué tengo hoy', o similar sin especificar otra fecha.
    """
    mensaje_confirmacion: str = Field(
        description="Mensaje breve confirmando que se buscará su agenda de hoy. "
        "Ejemplo: 'Buscando tu agenda de hoy...'"
    )


class ConsultarAgendaOtraFecha(BaseModel):
    """
    El médico quiere ver su agenda de un día específico distinto a hoy.
    Usar cuando menciona una fecha concreta como 'mañana', 'el lunes',
    'el 15 de abril', '05-04-26', 'la próxima semana', etc.
    """
    fecha: str = Field(
        description="La fecha extraída en formato MM-DD-YYYY (formato gringo). "
        "Si el doctor dice 'mañana', calcular la fecha real. "
        "Si dice 'el lunes' sin mes, asumir el lunes más próximo."
    )
    mensaje_confirmacion: str = Field(
        description="Mensaje breve confirmando la fecha consultada. "
        "Ejemplo: 'Buscando tu agenda para el 15 de abril...'"
    )


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
        "'Bloquear agenda' si quiere bloquear horarios, "
        "'Otros' para todo lo demás."
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
