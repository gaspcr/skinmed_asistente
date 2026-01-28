import os
import pytz
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, Request, Response, BackgroundTasks, HTTPException
from pydantic import BaseModel
import httpx

app = FastAPI(title="Bot Clínica SkinMed")

# --- CONFIGURACIÓN (Variables de Entorno) ---
FM_HOST = "fmsk.skinmed.cl"
FM_DB = "Agenda%20v20b"
FM_USER = os.getenv("FM_USER")
FM_PASS = os.getenv("FM_PASS")
LAYOUT = "Numeros_dapi"

WSP_TOKEN = os.getenv("WSP_TOKEN")
WSP_PHONE_ID = os.getenv("WSP_PHONE_ID")
VERIFY_TOKEN = os.getenv("WSP_VERIFY_TOKEN")

# --- MODELOS PYDANTIC (Validación de Datos) ---

class Text(BaseModel):
    body: str

class Message(BaseModel):
    from_: str = ""  # 'from' es palabra reservada en Python
    id: str
    text: Optional[Text] = None
    type: str

    class Config:
        # Esto permite mapear el campo 'from' del JSON a 'from_'
        fields = {'from_': 'from'}

class Value(BaseModel):
    messaging_product: str
    messages: Optional[List[Message]] = None

class Change(BaseModel):
    value: Value
    field: str

class Entry(BaseModel):
    id: str
    changes: List[Change]

class WSPPayload(BaseModel):
    object: str
    entry: List[Entry]

# --- LÓGICA DE NEGOCIO ---

def parse_agenda(data: list):
    """Procesa los datos de FileMaker para crear el mensaje de texto."""
    if not data:
        return "No hay citas agendadas para hoy."
    
    nombre_dr = data[0]['fieldData'].get('Recurso Humano::Nombre Lista', 'Doctor/a')
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

# --- CLIENTES ASÍNCRONOS ---

async def get_fm_token(client: httpx.AsyncClient):
    url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/sessions"
    resp = await client.post(url, auth=(FM_USER, FM_PASS), json={})
    resp.raise_for_status()
    return resp.json()['response']['token']

async def send_wsp_msg(to_phone: str, text: str):
    url = f"https://graph.facebook.com/v18.0/{WSP_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WSP_TOKEN}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text}
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload, headers=headers)

async def process_doctor_request(phone: str):
    """Toda la lógica pesada ocurre aquí en segundo plano."""
    async with httpx.AsyncClient() as client:
        try:
            # 1. Preparar Fecha
            tz_chile = pytz.timezone('America/Santiago')
            today = datetime.now(tz_chile).strftime("%m/%d/%Y")
            
            # 2. FileMaker Auth
            token = await get_fm_token(client)
            
            # 3. Buscar en FileMaker
            find_url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/layouts/{LAYOUT}/_find"
            query = {
                "query": [
                    {"Recurso Humano::Telefono": f"+{phone}", "Fecha": today},
                    {"Recurso Humano::Telefono": f" +{phone}", "Fecha": today}
                ]
            }
            
            headers = {"Authorization": f"Bearer {token}"}
            resp = await client.post(find_url, json=query, headers=headers)
            
            if resp.status_code == 200:
                final_msg = parse_agenda(resp.json()['response']['data'])
            else:
                final_msg = "Lo sentimos, este número no está registrado o no tiene agenda hoy."

            # 4. Responder por WhatsApp
            await send_wsp_msg(phone, final_msg)
            
            # 5. Cerrar sesión FM
            await client.delete(f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/sessions/{token}")
            
        except Exception as e:
            print(f"Error procesando agenda: {e}")

# --- ENDPOINTS ---

@app.get("/webhook")
async def verify(request: Request):
    """Verificación del webhook de Meta."""
    params = request.query_params
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(status_code=403, detail="Token de verificación inválido")

@app.post("/webhook")
async def webhook(payload: WSPPayload, background_tasks: BackgroundTasks):
    """Recepción de mensajes de WhatsApp."""
    # Pydantic ya validó que el JSON es correcto hasta aquí
    try:
        # Extraemos el mensaje (asumiendo que viene uno)
        change = payload.entry[0].changes[0].value
        if change.messages:
            msg = change.messages[0]
            doctor_phone = msg.from_
            
            # AGREGAR TAREA A SEGUNDO PLANO
            # Respondemos 200 OK a Meta inmediatamente y seguimos trabajando
            background_tasks.add_task(process_doctor_request, doctor_phone)
            
    except Exception as e:
        print(f"Error en webhook: {e}")
        
    return {"status": "ok"}