import httpx
from datetime import datetime, timedelta
import pytz
from typing import Optional
from app.config import FM_HOST, FM_DB, FM_USER, FM_PASS, LAYOUT

class FileMakerService:
    # Token cache (class variable shared across all instances)
    _cached_token: Optional[str] = None
    _token_expires_at: Optional[datetime] = None
    
    @classmethod
    async def get_token(cls, client: httpx.AsyncClient, force_refresh: bool = False) -> str:
        """Get FileMaker token, reusing cached token if still valid."""
        now = datetime.now()
        
        # Check if we have a valid cached token
        if not force_refresh and cls._cached_token and cls._token_expires_at:
            if now < cls._token_expires_at:
                print(f"DEBUG: Reusing cached FileMaker token (expires in {(cls._token_expires_at - now).seconds}s)")
                return cls._cached_token
        
        # Request new token
        print("DEBUG: Requesting new FileMaker token")
        url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/sessions"
        resp = await client.post(url, auth=(FM_USER, FM_PASS), json={})
        resp.raise_for_status()
        
        cls._cached_token = resp.json()['response']['token']
        # FileMaker tokens typically expire after 15 minutes, we'll cache for 14 to be safe
        cls._token_expires_at = now + timedelta(minutes=14)
        
        print(f"DEBUG: New token cached, expires at {cls._token_expires_at.strftime('%H:%M:%S')}")
        return cls._cached_token

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

    @staticmethod
    async def get_user_by_phone(phone: str):
        """Query FileMaker to get user info and role by phone number."""
        from app.config import AUTH_LAYOUT
        from app.auth.models import User, Role
        
        async with httpx.AsyncClient() as client:
            try:
                token = await FileMakerService.get_token(client)
                
                find_url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/layouts/{AUTH_LAYOUT}/_find"
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}"
                }
                
                query = {
                    "query": [
                        {
                            "Telefono": f"+{phone}"
                        }
                    ]
                }
                
                print(f"DEBUG: Buscando usuario con teléfono +{phone}")
                resp = await client.post(find_url, json=query, headers=headers)
                
                if resp.status_code == 200:
                    data = resp.json()['response']['data']
                    if data:
                        user_data = data[0]['fieldData']
                        nombre = user_data.get('Nombre', 'Usuario')
                        rol_str = user_data.get('ROL', '').lower()
                        
                        # Map FileMaker role to our Role enum
                        role_mapping = {
                            'doctor': Role.DOCTOR,
                            'gerente': Role.MANAGER,
                            'enfermera_jefe': Role.HEAD_NURSE,
                            'enfermera jefe': Role.HEAD_NURSE,
                        }
                        
                        role = role_mapping.get(rol_str, Role.DOCTOR)
                        
                        print(f"DEBUG: Usuario encontrado: {nombre}, Rol: {role}")
                        return User(phone=phone, name=nombre, role=role)
                    else:
                        print(f"DEBUG: No se encontró usuario con teléfono +{phone}")
                        return None
                else:
                    print(f"DEBUG: FM Auth Response: {resp.status_code} - {resp.text}")
                    return None
            except Exception as e:
                print(f"ERROR al buscar usuario: {e}")
                return None
