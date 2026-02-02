import httpx
from datetime import datetime
import pytz
from app.config import FM_HOST, FM_DB, FM_USER, FM_PASS, LAYOUT

class FileMakerService:
    @staticmethod
    async def get_token(client: httpx.AsyncClient) -> str:
        url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/sessions"
        resp = await client.post(url, auth=(FM_USER, FM_PASS), json={})
        resp.raise_for_status()
        return resp.json()['response']['token']

    @staticmethod
    def parse_agenda(data: list) -> str:
        if not data:
            return "No hay citas agendadas para hoy."
        
        nombre_dr = data[0]['fieldData'].get('Recurso Humano::Nombre Lista')
        msg = f"*Hola {nombre_dr}*\nAgenda para hoy:\n\n"
        
        ignorar = ["Eliminada", "Disponible", "Bloqueada", "Conjunto"]
        validos = [r for r in data if r['fieldData'].get('Tipo') not in ignorar]
        validos.sort(key=lambda x: x['fieldData']['Hora'])

        if not validos:
            return f"*{nombre_dr}*, no tienes citas agendadas hoy."

        for reg in validos:
            f = reg['fieldData']
            hora = ":".join(f['Hora'].split(":")[:2])
            paciente = f.get('Pacientes::NombreCompleto', 'Sin nombre')
            msg += f"*{hora}* - {paciente}\n"
        return msg

    @staticmethod
    async def get_agenda(phone: str) -> str:
        async with httpx.AsyncClient() as client:
            try:
                tz = pytz.timezone("America/Santiago")
                today_str = datetime.now(tz).strftime("%m/%d/%Y")
                
                token = await FileMakerService.get_token(client)
                
                find_url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/layouts/{LAYOUT}/_find"
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}"
                }
                
                query = {
                    "query": [
                        {
                            "Fecha": today_str,
                            "Recurso Humano::Telefono": f"+{phone}"
                        }
                    ]
                }
                
                print(f"DEBUG: Buscando agenda para +{phone}")
                resp = await client.post(find_url, json=query, headers=headers)
                
                if resp.status_code == 200:
                    return FileMakerService.parse_agenda(resp.json()['response']['data'])
                else:
                    print(f"DEBUG: FM Response: {resp.text}")
                    return "No tienes agenda hoy o no se encontraron datos."
            except Exception as e:
                print(f"ERROR: {e}")
                return "Error al consultar la agenda."
