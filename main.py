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
META_API_VERSION = 'v24.0'


class Text(BaseModel):
    body: str

class Profile(BaseModel):
    name: str

class Contact(BaseModel):
    profile: Profile
    wa_id: str

class Button(BaseModel):
    text: str
    payload: Optional[str] = None

class ButtonReply(BaseModel):
    id: str
    title: str

class Interactive(BaseModel):
    type: str
    button_reply: Optional[ButtonReply] = None

class Message(BaseModel):
    sender_phone: str = Field(alias="from") 
    id: str
    text: Optional[Text] = None
    interactive: Optional[Interactive] = None
    button: Optional[Button] = None
    type: str

    model_config = {"populate_by_name": True}

class Value(BaseModel):
    messaging_product: str
    messages: Optional[List[Message]] = None
    contacts: Optional[List[Contact]] = None

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

async def get_fm_token(client: httpx.AsyncClient):
    url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/sessions"
    resp = await client.post(url, auth=(FM_USER, FM_PASS), json={})
    resp.raise_for_status()
    return resp.json()['response']['token']

async def send_wsp_msg(to_phone: str, text: str):
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

async def send_initial_template(to_phone: str, nombre: str):
    url = f"https://graph.facebook.com/{META_API_VERSION}/{WSP_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WSP_TOKEN}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "template",
        "template": {
            "name": "respuesta_inicial_doctores",
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
    print(f"DEBUG: Enviando template a {url}")
    print(f"DEBUG: PHONE_ID: {WSP_PHONE_ID}")
    print(f"DEBUG: Payload: {payload}")
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=headers)
        print(f"DEBUG: Status Code: {resp.status_code}")
        print(f"DEBUG: Response Body: {resp.text}")
        resp.raise_for_status()

async def process_doctor_request(phone: str):
    print(f"🚀 Iniciando procesamiento para el teléfono: {phone}")
    async with httpx.AsyncClient() as client:
        try:
            tz = pytz.timezone("America/Santiago")
            today_str = datetime.now(tz).strftime("%m/%d/%Y")

            token = await get_fm_token(client)
            print(f"🔑 Token de FileMaker obtenido correctamente")

            find_url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/layouts/{LAYOUT}/_find"
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}"
            }

            query = {
                "query": [
                    {
                        "Fecha": '01/30/2026',
                        "Recurso Humano::Telefono": f"{phone}"
                    }
                ]
            }

            print(f"🔎 Buscando en FileMaker con query: {query}")
            resp = await client.post(find_url, json=query, headers=headers)
            
            print(f"📊 Respuesta FileMaker Status: {resp.status_code}")
            
            if resp.status_code == 200:
                final_msg = parse_agenda(resp.json()['response']['data'])
            else:
                print(f"⚠️ No se encontró agenda. Detalle: {resp.text}")
                final_msg = "Lo sentimos, no tienes agenda hoy."

            print(f"📤 Intentando enviar mensaje por WSP a {phone}...")
            await send_wsp_msg(phone, final_msg)
            print(f"✅ Proceso finalizado con éxito")
            
        except Exception as e:
            print(f"❌ ERROR CRÍTICO en la tarea de fondo: {str(e)}")

@app.get("/webhook")
async def verify(request: Request):
    params = request.query_params
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(status_code=403, detail="Token de verificación inválido")

@app.post("/webhook")
async def webhook(payload: WSPPayload, background_tasks: BackgroundTasks):
    try:
        change = payload.entry[0].changes[0].value
        
        # Obtener nombre del doctor si viene en los contactos
        doctor_name = "Doctor"
        if change.contacts:
            doctor_name = change.contacts[0].profile.name

        if change.messages:
            msg = change.messages[0]
            doctor_phone = msg.sender_phone 
            
            print(f"✅ Mensaje detectado de: {doctor_phone} | Tipo: {msg.type}")

            if msg.type == "text":
                await send_initial_template(doctor_phone, doctor_name)

            elif msg.type == "interactive":
                btn_title = msg.interactive.button_reply.title
                print(f"🔘 Botón Interactivo presionado: {btn_title}")
                
                if btn_title == "Revisar mi agenda del día":
                    background_tasks.add_task(process_doctor_request, doctor_phone)
                elif btn_title in ["Consultar cita paciente", "Consultar mis boxes"]:
                    await send_wsp_msg(doctor_phone, "Estamos trabajando en esta opción 🚧")
                else:
                    await send_wsp_msg(doctor_phone, "Opción no reconocida")

            elif msg.type == "button":
                btn_title = msg.button.text
                print(f"🔘 Botón Template presionado: {btn_title}")

                if btn_title == "Revisar mi agenda del día":
                    background_tasks.add_task(process_doctor_request, doctor_phone)
                elif btn_title in ["Consultar cita paciente", "Consultar mis boxes"]:
                    await send_wsp_msg(doctor_phone, "Estamos trabajando en esta opción 🚧")
                else:
                    await send_wsp_msg(doctor_phone, "Opción no reconocida")
            
    except Exception as e:
        print(f"❌ Error en webhook: {e}")
        
    return {"status": "ok"}