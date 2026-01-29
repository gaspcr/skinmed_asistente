import asyncio
import httpx
from unittest.mock import MagicMock, patch
from main import send_initial_template, WSP_TOKEN, WSP_PHONE_ID, META_API_VERSION

async def verify_send_initial_template():
    # Mock endpoint
    expected_url = f"https://graph.facebook.com/{META_API_VERSION}/{WSP_PHONE_ID}/messages"
    
    # Test data
    test_phone = "56912345678"
    test_nombre = "Dr. Test"
    
    # Mock httpx.AsyncClient
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = MagicMock(status_code=200)
        mock_client_cls.return_value = mock_client
        
        # Call the function
        await send_initial_template(test_phone, test_nombre)
        
        # Verify the call
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        
        # Check URL and Headers
        assert call_args[0][0] == expected_url
        headers = call_args[1]['headers']
        assert headers['Authorization'] == f"Bearer {WSP_TOKEN}"
        
        # Check Payload
        payload = call_args[1]['json']
        assert payload['messaging_product'] == "whatsapp"
        assert payload['to'] == test_phone
        assert payload['type'] == "template"
        assert payload['template']['name'] == "revisar_agenda"
        assert payload['template']['language']['code'] == "es_CHL"
        
        components = payload['template']['components']
        assert len(components) == 1
        assert components[0]['type'] == "body"
        
        parameters = components[0]['parameters']
        assert len(parameters) == 1
        assert parameters[0]['type'] == "text"
        assert parameters[0]['text'] == test_nombre
        
        print("✅ send_initial_template verification passed!")

if __name__ == "__main__":
    asyncio.run(verify_send_initial_template())
