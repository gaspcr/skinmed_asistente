import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

# --- CONFIGURACIÓN (RAILWAY) ---
FM_HOST = "fmsk.skinmed.cl"
FM_DB = "Agenda%20v20b"
FM_USER = os.getenv("FM_USER")
FM_PASS = os.getenv("FM_PASS")
LAYOUT = "Numeros_dapi"

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

def send_wsp(to_phone, text):
    """Envía un mensaje de texto vía WhatsApp Cloud API."""
    url = f"https://graph.facebook.com/v18.0/{WSP_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WSP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text}
    }
    resp = requests.post(url, json=payload, headers=headers)
    print(f"Respuesta Meta: {resp.status_code} - {resp.text}")

def parse_agenda(data):
    """Procesa los datos de FileMaker y genera el mensaje de la agenda."""
    if not data:
        return "No tienes citas confirmadas para hoy."

    nombre_dr = data[0]['fieldData'].get('Recurso Humano::Nombre Lista', 'Doctor/a')
    fecha = data[0]['fieldData'].get('Fecha', '')
    
    msg = f"*Hola {nombre_dr}*\nEsta es tu agenda para hoy ({fecha}):\n\n"
    
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

    return msg if count > 0 else f"*{nombre_dr}*, no tienes citas agendadas para hoy."

@app.route("/webhook", methods=["GET"])
def verify():
    """Verificación del webhook por parte de Meta."""
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Token inválido", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    """Recibe el mensaje y consulta FileMaker de inmediato."""
    data = request.get_json()
    
    try:
        # Extraemos el mensaje entrante
        value = data['entry'][0]['changes'][0]['value']
        if 'messages' in value:
            doctor_phone = value['messages'][0]['from'] # Formato: 569XXXXXXXX
            
            # 1. Conectar a FileMaker
            token = get_fm_token()
            find_url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/layouts/{LAYOUT}/_find"
            today = datetime.now().strftime("%m/%d/%Y")
            
            # 2. Buscar por número de teléfono
            query = {
                "query": [
                    {"Recurso Humano::Telefono": f"+{doctor_phone}", "Fecha": today},
                    {"Tipo": "no viene", "omit": "true"}
                ]
            }
            
            resp = requests.post(find_url, json=query, headers={"Authorization": f"Bearer {token}"})
            
            # 3. Lógica de respuesta según el resultado de FileMaker
            if resp.status_code == 200:
                # Doctor encontrado y con registros
                final_msg = parse_agenda(resp.json()['response']['data'])
            elif resp.status_code == 401:
                # Error 401 en FM Data API = No records found
                final_msg = "Lo sentimos, este número no aparece registrado como un doctor autorizado en Skinmed."
            else:
                # Otros errores (servidor, permisos, etc.)
                final_msg = "Hola, estamos experimentando problemas de conexión con la base de datos. Por favor intenta más tarde."
            
            # 4. Enviar respuesta por WhatsApp
            send_wsp(doctor_phone, final_msg)
            
            # 5. Siempre cerrar sesión
            logout_fm(token)
            
    except Exception as e:
        print(f"Error en el proceso: {e}")

    return jsonify({"status": "received"}), 200

if __name__ == "__main__":
    # Railway asigna el puerto automáticamente mediante la variable PORT
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))