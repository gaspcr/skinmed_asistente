import logging

import httpx

from app.config import get_settings
from app.services import http as http_svc
from app.utils.retry import con_reintentos
from app.interaction_logger import log_message_sent

logger = logging.getLogger(__name__)


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
            log_message_sent(to_phone, msg_type="text", content=text)
        except Exception as e:
            logger.error("Error al enviar mensaje a %s: %s", to_phone, e)

    @staticmethod
    async def send_template(to_phone: str, nombre: str, template_name: str, include_header: bool = True, include_body: bool = False):
        settings = get_settings()
        url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{settings.WSP_PHONE_ID}/messages"
        headers = {"Authorization": f"Bearer {settings.WSP_TOKEN}"}

        template_config = {
            "name": template_name,
            "language": {"code": "es"}
        }

        components = []

        if include_header:
            components.append({
                "type": "header",
                "parameters": [
                    {
                        "type": "text",
                        "text": nombre
                    }
                ]
            })

        if include_body:
            components.append({
                "type": "body",
                "parameters": [
                    {
                        "type": "text",
                        "text": nombre
                    }
                ]
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
            if resp.status_code >= 500:
                resp.raise_for_status()
            elif resp.status_code >= 400:
                logger.error(
                    "WhatsApp API retorno %d: %s",
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
            log_message_sent(to_phone, msg_type="template", template_name=template_name)
        except Exception as e:
            logger.error("Error al enviar template '%s' a %s: %s", template_name, to_phone, e)
