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
    async def send_template(to_phone: str, nombre: str, template_name: str, include_header: bool = True, include_body: bool = False):
        url = f"https://graph.facebook.com/{META_API_VERSION}/{WSP_PHONE_ID}/messages"
        headers = {"Authorization": f"Bearer {WSP_TOKEN}"}
        
        template_config = {
            "name": template_name,
            "language": {"code": "es"}
        }
        
        components = []
        
        # Add header component if requested
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
        
        # Add body component if requested
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
        
        # Only add components if there are any
        if components:
            template_config["components"] = components
        
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "template",
            "template": template_config
        }
        
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                print(f"ERROR: WhatsApp API returned {e.response.status_code}")
                print(f"ERROR: Response body: {e.response.text}")
                print(f"ERROR: Request payload: {payload}")
                raise


