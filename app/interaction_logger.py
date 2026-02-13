"""
Interaction Logger — Registro estructurado de todas las interacciones.

Cada evento incluye timestamp, tipo de evento, telefono del usuario,
y contexto especifico del evento. Usa un logger dedicado 'interaction'
separado del logger de aplicacion.
"""
import logging
import time
from typing import Optional

logger = logging.getLogger("interaction")


def _log(event: str, **kwargs):
    """Emite un log de interaccion con formato estandarizado."""
    parts = [f"event={event}"]
    for key, value in kwargs.items():
        if value is not None:
            parts.append(f"{key}={value}")
    logger.info(" | ".join(parts))


# --- Mensajes ---

def log_message_received(
    phone: str,
    msg_type: str,
    content: str = "",
    msg_id: str = "",
):
    """Log de mensaje recibido desde WhatsApp."""
    _log(
        "message_received",
        phone=phone,
        direction="inbound",
        msg_type=msg_type,
        msg_id=msg_id,
        content=content[:200] if content else "",
    )


def log_message_sent(
    phone: str,
    msg_type: str = "text",
    content: str = "",
    template_name: str = None,
):
    """Log de mensaje enviado al usuario via WhatsApp."""
    _log(
        "message_sent",
        phone=phone,
        direction="outbound",
        msg_type=msg_type,
        template=template_name,
        content=content[:200] if content else "",
    )


def log_message_duplicate(phone: str, msg_id: str):
    """Log de mensaje duplicado ignorado."""
    _log("message_duplicate", phone=phone, msg_id=msg_id)


# --- Botones ---

def log_button_click(
    phone: str,
    user_name: str,
    role: str,
    button_title: str,
):
    """Log de click en boton interactivo."""
    _log(
        "button_click",
        phone=phone,
        user=user_name,
        role=role,
        button=button_title,
    )


# --- Autenticacion ---

def log_auth(
    phone: str,
    user_name: Optional[str] = None,
    role: Optional[str] = None,
    status: str = "success",
):
    """Log de evento de autenticacion. status: success, not_found, cached, error."""
    _log(
        "auth",
        phone=phone,
        user=user_name,
        role=role,
        status=status,
    )


# --- API Calls ---

def log_api_call(
    service: str,
    operation: str,
    duration_ms: float,
    status: str = "ok",
    detail: str = None,
):
    """Log de llamada a API externa (FileMaker, WhatsApp, etc.)."""
    _log(
        "api_call",
        service=service,
        operation=operation,
        duration_ms=round(duration_ms, 1),
        status=status,
        detail=detail,
    )


# --- Rate Limiting ---

def log_rate_limit(phone: str):
    """Log de rate limit excedido."""
    _log("rate_limit_exceeded", phone=phone)


# --- Mensajes no soportados ---

def log_unsupported_message(phone: str, msg_type: str):
    """Log de tipo de mensaje no soportado."""
    _log("unsupported_message", phone=phone, msg_type=msg_type)


# --- Workflow Actions ---

def log_workflow_action(
    phone: str,
    user_name: str,
    role: str,
    action: str,
    detail: str = None,
):
    """Log de accion dentro de un workflow (consulta agenda, ingreso fecha, etc.)."""
    _log(
        "workflow_action",
        phone=phone,
        user=user_name,
        role=role,
        action=action,
        detail=detail,
    )


# --- Timer context para medir duracion de API calls ---

class ApiTimer:
    """Context manager para medir duracion de llamadas a APIs externas."""

    def __init__(self, service: str, operation: str):
        self.service = service
        self.operation = operation
        self.start = None
        self.duration_ms = 0

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.duration_ms = (time.perf_counter() - self.start) * 1000
        status = "ok" if exc_type is None else "error"
        detail = str(exc_val) if exc_val else None
        log_api_call(
            self.service,
            self.operation,
            self.duration_ms,
            status=status,
            detail=detail,
        )
        return False  # No suprimir excepciones
