import httpx
from app.config import META_API_VERSION, WSP_PHONE_ID, WSP_TOKEN

class WhatsAppService:
    @staticmethod
    async def send_message(to_phone: str, text: str):
        url = f"https://graph.facebook.com/{META_API_VERSION}/{WSP_PHONE_ID}/messages"
        headers = {"Authorization": f"Bearer {WSP_TOKEN}"}
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "text",
            "text": {"body": text}
        }
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, headers=headers)

    @staticmethod
    async def send_template(to_phone: str, nombre: str, template_name: str):
        url = f"https://graph.facebook.com/{META_API_VERSION}/{WSP_PHONE_ID}/messages"
        headers = {"Authorization": f"Bearer {WSP_TOKEN}"}
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": "es"},
                "components": [
                    {
                        "type": "header",
                        "parameters": [
                            {
                                "type": "text",
                                "text": nombre
                            }
                        ]
                    }
                ]
            }
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
