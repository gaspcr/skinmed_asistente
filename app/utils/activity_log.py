"""
Registro de actividad estructurado para estadísticas y reportes.

Cada función emite un log.info con campos extra consistentes.
En producción (JSON) los campos son queryables directamente.
En desarrollo se renderizan de forma legible.

Eventos registrados:
  - user_session   : usuario autenticado (quién entró)
  - msg_received   : mensaje recibido (tipo y preview)
  - action_taken   : acción ejecutada (modo, herramienta, detalles)
  - llm_fallback   : fallo del LLM con causa
"""
import logging

_logger = logging.getLogger("skinmed.activity")


def log_user_session(phone: str, name: str, role: str) -> None:
    """
    Registra que un usuario fue autenticado y está activo.
    Emitir una vez por mensaje, al inicio del procesamiento.
    """
    _logger.info(
        "SESIÓN | %s (%s) [%s]",
        name, phone, role,
        extra={
            "event": "user_session",
            "phone": phone,
            "user_name": name,
            "role": role,
        },
    )


def log_message_received(phone: str, msg_type: str, content: str) -> None:
    """
    Registra el mensaje entrante con preview del contenido.
    Para texto: primeros 80 chars. Para botones: título completo.
    """
    preview = content[:80] + ("…" if len(content) > 80 else "")
    _logger.info(
        "MENSAJE | %s — tipo=%s | \"%s\"",
        phone, msg_type, preview,
        extra={
            "event": "msg_received",
            "phone": phone,
            "msg_type": msg_type,
            "content_preview": preview,
        },
    )


def log_action_taken(
    phone: str,
    mode: str,
    action: str,
    details: str | None = None,
) -> None:
    """
    Registra la acción tomada tras procesar el mensaje.

    Args:
        phone:   Número de teléfono del usuario.
        mode:    Origen de la decisión: 'llm', 'legacy', 'button', 'state'.
        action:  Nombre de la herramienta o paso ejecutado.
                 Ejemplos: 'ConsultarAgendaHoy', 'waiting_for_date',
                           'EnviarRecado:Bloquear agenda', 'menu'.
        details: Info adicional opcional (fecha consultada, categoría, etc.).
    """
    msg = "ACCIÓN  | %s — [%s] %s"
    args = [phone, mode.upper(), action]
    if details:
        msg += " | %s"
        args.append(details)

    _logger.info(
        msg,
        *args,
        extra={
            "event": "action_taken",
            "phone": phone,
            "mode": mode,
            "action": action,
            "details": details,
        },
    )


def log_llm_fallback(phone: str, reason: str) -> None:
    """Registra que el LLM falló y se usó el menú como fallback."""
    _logger.warning(
        "FALLBACK | %s — LLM falló, mostrando menú | %s",
        phone, reason,
        extra={
            "event": "llm_fallback",
            "phone": phone,
            "reason": reason,
        },
    )
