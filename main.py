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
    print(f"[DEBUG] Solicitando token a FileMaker: {url}")
    resp = requests.post(url, auth=(FM_USER, FM_PASS), json={})
    if resp.status_code != 200:
        print(f"[ERROR] No se pudo obtener token FM: {resp.text}")
        resp.raise_for_status()
    token = resp.json()['response']['token']
    print(f"[DEBUG] Token FM obtenido con éxito.")
    return token

def logout_fm(token):
    url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/sessions/{token}"
    requests.delete(url)
    print("[DEBUG] Sesión de FileMaker cerrada.")

def send_wsp(to_phone, text):
    url = f"https://graph.facebook.com/v18.0/{WSP_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WSP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text}
    }
    print(f"[DEBUG] Enviando mensaje a WhatsApp ({to_phone})...")
    resp = requests.post(url, json=payload, headers=headers)
    print(f"[DEBUG] Respuesta de Meta API: {resp.status_code} - {resp.text}")
    return resp.status_code

def parse_agenda(data):
    if not data:
        return "No tienes citas confirmadas para hoy."
    
    nombre_dr = data[0]['fieldData'].get('Recurso Humano::Nombre Lista', 'Doctor/a')
    fecha = data[0]['fieldData'].get('Fecha', '')
    msg = f"*Hola {nombre_dr}*\nAgenda para hoy ({fecha}):\n\n"
    
    ignorar = ["Eliminada", "Disponible", "Bloqueada", "Conjunto"]
    valid_records = [r for r in data if r['fieldData'].get('Tipo') not in ignorar]
    valid_records.sort(key=lambda x: x['fieldData']['Hora'])

    if not valid_records:
        return f"*{nombre_dr}*, no tienes citas agendadas para hoy."

    for reg in valid_records:
        f = reg['fieldData']
        hora = ":".join(f['Hora'].split(":")[:2])
        paciente = f.get('Pacientes::NombreCompleto', 'Sin nombre')
        actividad = f.get('Actividad', 'Cita')
        msg += f"*{hora}* - {paciente}\n   _{actividad}_\n\n"
    return msg

@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Token inválido", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    
    # Log del JSON completo que envía Meta
    print(f"[DEBUG] Webhook recibido: {json.dumps(data)}")

    try:
        value = data['entry'][0]['changes'][0]['value']
        
        # Ignorar si es una confirmación de entrega o lectura (no contiene 'messages')
        if 'messages' not in value:
            print("[DEBUG] El evento no es un mensaje (posible confirmación de entrega). Ignorando.")
            return jsonify({"status": "ignored"}), 200

        # Extraer datos del mensaje
        message = value['messages'][0]
        doctor_phone_raw = message['from']  # Viene como 569XXXXXXXX
        doctor_phone_fm = f"+{doctor_phone_raw}" # Formato +569XXXXXXXX para FileMaker
        
        print(f"[DEBUG] Procesando mensaje de: {doctor_phone_fm}")

        # 1. FileMaker Connection
        token = get_fm_token()
        find_url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/layouts/{LAYOUT}/_find"
        today = datetime.now().strftime("%m/%d/%Y")
        
        query = {
            "query": [
                {"Recurso Humano::Telefono": doctor_phone_fm, "Fecha": today},
                {"Tipo": "no viene", "omit": "true"}
            ]
        }
        
        print(f"[DEBUG] Realizando búsqueda en FM: {json.dumps(query)}")
        resp = requests.post(find_url, json=query, headers={"Authorization": f"Bearer {token}"})
        print(f"[DEBUG] Respuesta FM Data API Status: {resp.status_code}")

        # 2. Lógica de Respuesta
        if resp.status_code == 200:
            print("[DEBUG] Doctor encontrado. Generando agenda...")
            final_msg = parse_agenda(resp.json()['response']['data'])
        elif resp.status_code == 401:
            print("[DEBUG] FM devolvió 401: Número no registrado o sin agenda.")
            final_msg = "Lo sentimos, este número no aparece registrado como un doctor autorizado en Skinmed."
        else:
            print(f"[WARNING] Error inesperado de FM ({resp.status_code}): {resp.text}")
            final_msg = "Error de conexión con la base de datos. Por favor, reintente."

        # 3. Enviar a WhatsApp
        send_wsp(doctor_phone_raw, final_msg)
        logout_fm(token)
            
    except Exception as e:
        print(f"[ERROR CRÍTICO] Fallo en el webhook: {str(e)}")

    return jsonify({"status": "received"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))