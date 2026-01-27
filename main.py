import os
import requests
import json
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

# --- CONFIGURACIÓN ---
FM_HOST = "fmsk.skinmed.cl"
FM_DB = "Agenda%20v20b"
FM_USER = os.getenv("FM_USER")
FM_PASS = os.getenv("FM_PASS")
LAYOUT = "Numeros_dapi"

WSP_TOKEN = os.getenv("WSP_TOKEN")
WSP_PHONE_ID = os.getenv("WSP_PHONE_ID")
VERIFY_TOKEN = os.getenv("WSP_VERIFY_TOKEN")

def get_fm_token():
    url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/sessions"
    resp = requests.post(url, auth=(FM_USER, FM_PASS), json={})
    resp.raise_for_status()
    return resp.json()['response']['token']

def logout_fm(token):
    url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/sessions/{token}"
    requests.delete(url)

def send_wsp(to_phone, text):
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
    if not data:
        return "No tienes citas para hoy."
    nombre_dr = data[0]['fieldData'].get('Recurso Humano::Nombre Lista', 'Doctor/a')
    fecha = data[0]['fieldData'].get('Fecha', '')
    msg = f"*Hola {nombre_dr}*\nAgenda para hoy ({fecha}):\n\n"
    ignorar = ["Eliminada", "Disponible", "Bloqueada", "Conjunto"]
    valid_records = [r for r in data if r['fieldData'].get('Tipo') not in ignorar]
    valid_records.sort(key=lambda x: x['fieldData']['Hora'])
    if not valid_records: return f"*{nombre_dr}*, no tienes citas confirmadas hoy."
    for reg in valid_records:
        f = reg['fieldData']
        hora = ":".join(f['Hora'].split(":")[:2])
        paciente = f.get('Pacientes::NombreCompleto', 'Sin nombre')
        msg += f"*{hora}* - {paciente}\n"
    return msg

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    try:
        value = data['entry'][0]['changes'][0]['value']
        if 'messages' in value:
            message = value['messages'][0]
            doctor_phone_raw = message['from']
            doctor_phone_fm = f"+{doctor_phone_raw}"
            
            token = get_fm_token()
            find_url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/layouts/{LAYOUT}/_find"
            
            # --- CAMBIO CLAVE: Formato de fecha día/mes/año ---
            today = datetime.now().strftime("%d/%m/%Y") 
            
            query = {
                "query": [
                    {"Recurso Humano::Telefono": doctor_phone_fm, "Fecha": today},
                    {"Tipo": "no viene", "omit": "true"}
                ]
            }
            
            resp = requests.post(find_url, json=query, headers={"Authorization": f"Bearer {token}"})
            
            # --- MANEJO DE ERROR 500 (No records found) ---
            if resp.status_code == 200:
                final_msg = parse_agenda(resp.json()['response']['data'])
            elif resp.status_code == 500 or resp.status_code == 401:
                # Si es 500, revisamos si el mensaje interno de FM es "401"
                error_data = resp.json()
                internal_code = error_data.get('messages', [{}])[0].get('code')
                if internal_code == "401":
                    final_msg = f"El número {doctor_phone_fm} no registra agenda para hoy ({today})."
                else:
                    final_msg = "Error interno de base de datos."
            else:
                final_msg = "Servicio temporalmente no disponible."

            send_wsp(doctor_phone_raw, final_msg)
            logout_fm(token)
            
    except Exception as e:
        print(f"Error: {e}")
    return jsonify({"status": "received"}), 200

@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Token inválido", 403

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))