import os
import pytz
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, Request, Response, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
import httpx

app = FastAPI(title="Bot Clínica SkinMed")

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
    # 'alias' captura el nombre real en el JSON de WhatsApp
    sender_phone: str = Field(alias="from") 
    id: str
    text: Optional[Text] = None
    type: str

    # Esta configuración permite que Pydantic maneje bien los alias
    model_config = {"populate_by_name": True}

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
    print(f"🚀 Iniciando procesamiento para el teléfono: {phone}")
    async with httpx.AsyncClient() as client:
        try:
            # ... tu lógica de fecha ...
            token = await get_fm_token(client)
            print(f"🔑 Token de FileMaker obtenido correctamente")

            # ... lógica de búsqueda ...
            print(f"🔎 Buscando en FileMaker con query: {phone}")
            resp = await client.post(find_url, json=query, headers=headers)
            
            print(f"📊 Respuesta FileMaker Status: {resp.status_code}")
            
            if resp.status_code == 200:
                final_msg = parse_agenda(resp.json()['response']['data'])
            else:
                print(f"⚠️ No se encontró agenda. Detalle: {resp.text}")
                final_msg = "Lo sentimos, no tienes agenda hoy."

            # ENVÍO DE WHATSAPP
            print(f"📤 Intentando enviar mensaje por WSP a {phone}...")
            # Asegúrate de que esta URL y el WSP_PHONE_ID sean correctos
            await send_wsp_msg(phone, final_msg)
            print(f"✅ Proceso finalizado con éxito")
            
        except Exception as e:
            print(f"❌ ERROR CRÍTICO en la tarea de fondo: {str(e)}")

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
    try:
        change = payload.entry[0].changes[0].value
        if change.messages:
            msg = change.messages[0]
            
            # CAMBIO AQUÍ: Usamos el nuevo nombre 'sender_phone'
            doctor_phone = msg.sender_phone 
            
            print(f"✅ Mensaje detectado de: {doctor_phone}")
            background_tasks.add_task(process_doctor_request, doctor_phone)
            
    except Exception as e:
        # Esto es lo que imprimió el error que viste en Railway
        print(f"❌ Error en webhook: {e}")
        
    return {"status": "ok"}