import os
import requests
import json
from flask import Flask, request, jsonify
from datetime import datetime
import pytz # Para asegurar la hora de Chile

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

def send_wsp(to_phone, text):
    url = f"https://graph.facebook.com/v18.0/{WSP_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WSP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text}
    }
    resp = requests.post(url, json=payload, headers=headers)
    print(f"[WSP] Status: {resp.status_code} - Resp: {resp.text}")

def parse_agenda(data):
    nombre_dr = data[0]['fieldData'].get('Recurso Humano::Nombre Lista', 'Doctor/a')
    msg = f"*Hola {nombre_dr}*\nAgenda para hoy:\n\n"
    
    ignorar = ["Eliminada", "Disponible", "Bloqueada", "Conjunto"]
    # Filtramos y ordenamos
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

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    print(f"[DEBUG] Raw Payload: {json.dumps(data)}")

    try:
        if 'messages' in data['entry'][0]['changes'][0]['value']:
            message = data['entry'][0]['changes'][0]['value']['messages'][0]
            doctor_phone_raw = message['from'] 
            
            # 1. Obtener fecha actual en formato MM/DD/YYYY (como en tu Postman)
            tz_chile = pytz.timezone('America/Santiago')
            today = datetime.now(tz_chile).strftime("%m/%d/%Y") 
            
            token = get_fm_token()
            find_url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/layouts/{LAYOUT}/_find"
            
            # Probamos buscar con y sin el espacio por si acaso
            query = {
                "query": [
                    {"Recurso Humano::Telefono": f"+{doctor_phone_raw}", "Fecha": today},
                    {"Recurso Humano::Telefono": f" +{doctor_phone_raw}", "Fecha": today}
                ]
            }
            
            print(f"[FM] Buscando: {doctor_phone_raw} para fecha {today}")
            resp = requests.post(find_url, json=query, headers={"Authorization": f"Bearer {token}"})
            
            if resp.status_code == 200:
                final_msg = parse_agenda(resp.json()['response']['data'])
            else:
                # Si no encuentra (401 o 500 con error 401 interno)
                print(f"[FM] No encontrado o error: {resp.status_code} - {resp.text}")
                final_msg = "Lo sentimos, este número no está registrado o no tiene agenda hoy."

            send_wsp(doctor_phone_raw, final_msg)
            requests.delete(f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/sessions/{token}")
            
    except Exception as e:
        print(f"[ERROR] {str(e)}")

    return jsonify({"status": "received"}), 200

@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Error", 403

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))