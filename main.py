import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

# --- CONFIGURACIÓN (VÍA VARIABLES DE ENTORNO EN RAILWAY) ---
FM_HOST = "fmsk.skinmed.cl"
FM_DB = "Agenda%20v20b"
FM_USER = os.getenv("FM_USER")
FM_PASS = os.getenv("FM_PASS")
LAYOUT = "Numeros_dapi"

# Configuración WhatsApp Meta
WSP_TOKEN = os.getenv("WSP_TOKEN")
WSP_PHONE_ID = os.getenv("WSP_PHONE_ID")
VERIFY_TOKEN = os.getenv("WSP_VERIFY_TOKEN")

def get_fm_token():
    """Inicia sesión en FileMaker Data API."""
    url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/sessions"
    resp = requests.post(url, auth=(FM_USER, FM_PASS), json={})
    resp.raise_for_status()
    return resp.json()['response']['token']

def logout_fm(token):
    """Cierra la sesión de FileMaker."""
    url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/sessions/{token}"
    requests.delete(url)

def send_template_agenda(to_phone):
    """Envía la plantilla interactiva 'revisar_agenda'."""
    url = f"https://graph.facebook.com/v18.0/{WSP_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WSP_TOKEN}", "Content-Type": "application/json"}
    
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "template",
        "template": {
            "name": "revisar_agenda",
            "language": {"code": "es_CHL"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": "Doctor/a"} # Valor para {{nombre}}
                    ]
                }
            ]
        }
    }
    resp = requests.post(url, json=payload, headers=headers)
    return resp.status_code

def send_wsp_text(to_phone, text):
    """Envía un mensaje de texto plano con la agenda."""
    url = f"https://graph.facebook.com/v18.0/{WSP_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WSP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text}
    }
    requests.post(url, json=payload, headers=headers)

def parse_agenda(data):
    """Limpia el JSON y genera un mensaje legible."""
    if not data:
        return "No tienes citas confirmadas para hoy."

    nombre_dr = data[0]['fieldData'].get('Recurso Humano::Nombre Lista', 'Doctor')
    fecha = data[0]['fieldData'].get('Fecha', '')
    
    msg = f"*Hola {nombre_dr}*\nAgenda: {fecha}\n\n"
    
    ignorar = ["Eliminada", "Disponible", "Bloqueada", "Conjunto"]
    data.sort(key=lambda x: x['fieldData']['Hora'])

    count = 0
    for reg in data:
        f = reg['fieldData']
        if f.get('Tipo') not in ignorar:
            count += 1
            hora = ":".join(f['Hora'].split(":")[:2])
            paciente = f.get('Pacientes::NombreCompleto', 'Sin nombre')
            actividad = f.get('Actividad', 'Cita')
            msg += f"*{hora}* - {paciente}\n   _{actividad}_\n\n"

    return msg if count > 0 else f"*{nombre_dr}*, hoy no tienes citas confirmadas."

@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Token de verificación inválido", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    
    try:
        value = data['entry'][0]['changes'][0]['value']
        if 'messages' in value:
            message = value['messages'][0]
            doctor_phone = message['from']
            msg_type = message.get('type')

            # --- PASO 1: Si el usuario escribe texto, enviamos la PLANTILLA ---
            if msg_type == 'text':
                send_template_agenda(doctor_phone)
            
            # --- PASO 2: Si el usuario presiona el BOTÓN de la plantilla ---
            elif msg_type == 'button':
                # Consultar FileMaker solo cuando se pulsa el botón
                token = get_fm_token()
                find_url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/layouts/{LAYOUT}/_find"
                today = datetime.now().strftime("%m/%d/%Y")
                
                query = {
                    "query": [
                        {"Recurso Humano::Telefono": f"+{doctor_phone}", "Fecha": today},
                        {"Tipo": "no viene", "omit": "true"}
                    ]
                }
                
                resp = requests.post(find_url, json=query, headers={"Authorization": f"Bearer {token}"})
                
                if resp.status_code == 200:
                    final_msg = parse_agenda(resp.json()['response']['data'])
                else:
                    final_msg = "No se encontró una agenda vinculada a este número para hoy en Skinmed."
                
                send_wsp_text(doctor_phone, final_msg)
                logout_fm(token)
            
    except Exception as e:
        print(f"Error procesando webhook: {e}")

    return jsonify({"status": "received"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))