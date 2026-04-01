import logging
import re

import httpx

from app.config import get_settings
from app.services import http as http_svc
from app.utils.retry import con_reintentos

logger = logging.getLogger(__name__)


def _sanitize_template_param(text: str) -> str:
    """Sanitiza texto para parametros de template de WhatsApp.
    La API rechaza newlines, tabs, y 4+ espacios consecutivos."""
    text = text.replace("\n", " | ").replace("\r", " | ").replace("\t", " ")
    text = re.sub(r" {4,}", "   ", text)  # Colapsar 4+ espacios a 3
    return text.strip()


class WhatsAppService:
    @staticmethod
    async def send_message(to_phone: str, text: str):
        settings = get_settings()
        url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{settings.WSP_PHONE_ID}/messages"
        headers = {"Authorization": f"Bearer {settings.WSP_TOKEN}"}
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "text",
            "text": {"body": text}
        }

        async def _enviar():
            client = http_svc.get_client()
            resp = await client.post(url, json=payload, headers=headers)
            logger.info("[WSP] send_message a %s -> status=%d", to_phone, resp.status_code)
            if resp.status_code >= 400:
                logger.error("[WSP] send_message error response: %s", resp.text)
            if resp.status_code >= 500:
                resp.raise_for_status()

        try:
            await con_reintentos(
                _enviar,
                max_intentos=2,
                backoff_base=0.5,
                excepciones_reintentables={httpx.RequestError, httpx.HTTPStatusError},
                nombre_operacion="WhatsApp send_message",
            )
        except Exception as e:
            logger.error("Error al enviar mensaje a %s: %s", to_phone, e)

    @staticmethod
    async def send_template(
        to_phone: str,
        nombre: str,
        template_name: str,
        include_header: bool = True,
        include_body: bool = False,
        header_params: list[str] | None = None,
        body_params: list[str] | None = None,
    ):
        settings = get_settings()
        url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{settings.WSP_PHONE_ID}/messages"
        headers = {"Authorization": f"Bearer {settings.WSP_TOKEN}"}

        template_config = {
            "name": template_name,
            "language": {"code": "es"}
        }

        components = []

        # Header: use explicit header_params if provided, else fall back to nombre
        h_params = header_params if header_params is not None else ([nombre] if include_header else [])
        if h_params:
            components.append({
                "type": "header",
                "parameters": [{"type": "text", "text": _sanitize_template_param(p)} for p in h_params]
            })

        # Body: use explicit body_params if provided, else fall back to nombre
        b_params = body_params if body_params is not None else ([nombre] if include_body else [])
        if b_params:
            components.append({
                "type": "body",
                "parameters": [{"type": "text", "text": _sanitize_template_param(p)} for p in b_params]
            })

        if components:
            template_config["components"] = components

        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "template",
            "template": template_config
        }

        async def _enviar():
            client = http_svc.get_client()
            resp = await client.post(url, json=payload, headers=headers)
            logger.info("[WSP] send_template '%s' a %s -> status=%d", template_name, to_phone, resp.status_code)
            if resp.status_code >= 500:
                resp.raise_for_status()
            elif resp.status_code >= 400:
                logger.error(
                    "[WSP] send_template error response %d: %s",
                    resp.status_code, resp.text
                )

        try:
            await con_reintentos(
                _enviar,
                max_intentos=2,
                backoff_base=0.5,
                excepciones_reintentables={httpx.RequestError, httpx.HTTPStatusError},
                nombre_operacion="WhatsApp send_template",
            )
        except Exception as e:
            logger.error("Error al enviar template '%s' a %s: %s", template_name, to_phone, e)
