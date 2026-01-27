import os
import requests
from flask import Flask, request, jsonify, render_template_string
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

def send_wsp(to_phone, text):
    """Envía el mensaje final al doctor vía WhatsApp."""
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
        return "⚠️ No tienes citas confirmadas para hoy."

    nombre_dr = data[0]['fieldData'].get('Recurso Humano::Nombre Lista', 'Doctor')
    fecha = data[0]['fieldData'].get('Fecha', '')
    
    msg = f"👨‍⚕️ *Hola {nombre_dr}*\n📅 Agenda: {fecha}\n\n"
    
    # Filtros de exclusión basados en tu JSON
    ignorar = ["Eliminada", "Disponible", "Bloqueada", "Conjunto"]
    data.sort(key=lambda x: x['fieldData']['Hora']) # Orden cronológico

    count = 0
    for reg in data:
        f = reg['fieldData']
        if f.get('Tipo') not in ignorar:
            count += 1
            hora = ":".join(f['Hora'].split(":")[:2]) # HH:MM
            paciente = f.get('Pacientes::NombreCompleto', 'Sin nombre')
            actividad = f.get('Actividad', 'Cita')
            msg += f"🕒 *{hora}* - {paciente}\n   _{actividad}_\n\n"

    return msg if count > 0 else f"👨‍⚕️ *{nombre_dr}*, hoy no tienes citas confirmadas."

@app.route("/webhook", methods=["GET"])
def verify():
    """Validación requerida por Meta para activar el Webhook."""
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Token de verificación inválido", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    """Recibe el mensaje de WhatsApp y procesa la agenda."""
    data = request.get_json()
    
    try:
        # Extraer teléfono del remitente
        value = data['entry'][0]['changes'][0]['value']
        if 'messages' in value:
            doctor_phone = value['messages'][0]['from'] # Formato: 569XXXXXXXX
            
            # Consultar FileMaker
            token = get_fm_token()
            find_url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/layouts/{LAYOUT}/_find"
            today = datetime.now().strftime("%m/%d/%Y") # Formato MM/DD/YYYY
            
            query = {
                "query": [
                    {"Recurso Humano::Telefono": f"+{doctor_phone}", "Fecha": today},
                    {"Tipo": "no viene", "omit": "true"}
                ]
            }
            
            resp = requests.post(find_url, json=query, headers={"Authorization": f"Bearer {token}"})
            
            # Construir y enviar respuesta
            if resp.status_code == 200:
                final_msg = parse_agenda(resp.json()['response']['data'])
            else:
                final_msg = "⚠️ No se encontró una agenda vinculada a este número para hoy."
            
            send_wsp(doctor_phone, final_msg)
            logout_fm(token)
            
    except Exception as e:
        print(f"Error procesando webhook: {e}")

    return jsonify({"status": "received"}), 200

@app.route("/test")
def test_fm():
    """Ruta de diagnóstico para ver el JSON crudo de FileMaker."""
    try:
        # 1. Obtener Token
        token = get_fm_token()
        
        # 2. Intentar la búsqueda que vimos en Postman
        find_url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/layouts/{LAYOUT}/_find"
        today = datetime.now().strftime("%m/%d/%Y") # Formato MM/DD/YYYY
        
        # Usamos el número del Dr. Walter Gubelin para el test
        query = {
            "query": [
                {"Recurso Humano::Telefono": "+56939129139", "Fecha": today},
                {"Tipo": "no viene", "omit": "true"}
            ]
        }
        
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.post(find_url, json=query, headers=headers)
        
        # 3. Cerrar sesión inmediatamente
        logout_fm(token)

        # Devolvemos el JSON exacto que entrega FileMaker para validar campos
        return jsonify({
            "status_code_fm": resp.status_code,
            "response_raw": resp.json()
        })

    except Exception as e:
        return jsonify({"error": str(e), "tip": "Revisa las variables FM_PASS y FM_USER en Railway"})

@app.route("/agenda")
def test_fm():
    try:
        token = get_fm_token()
        find_url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/layouts/{LAYOUT}/_find"
        today = datetime.now().strftime("%m/%d/%Y")
        
        query = {
            "query": [
                {"Recurso Humano::Telefono": "+56939129139", "Fecha": today},
                {"Tipo": "no viene", "omit": "true"}
            ]
        }
        
        resp = requests.post(find_url, json=query, headers={"Authorization": f"Bearer {token}"})
        logout_fm(token)

        if resp.status_code != 200:
            return f"<h1>⚠️ Error {resp.status_code}</h1><p>No se encontraron datos para hoy.</p>"

        data = resp.json()['response']['data']
        # Filtros y limpieza
        ignorar = ["Eliminada", "Disponible", "Bloqueada", "Conjunto"]
        agenda_limpia = []
        
        for reg in data:
            f = reg['fieldData']
            if f.get('Tipo') not in ignorar:
                agenda_limpia.append({
                    "hora": ":".join(f['Hora'].split(":")[:2]),
                    "paciente": f.get("Pacientes::NombreCompleto", "---"),
                    "actividad": f.get("Actividad", "---"),
                    "tipo": f.get("Tipo")
                })
        
        # Ordenar por hora
        agenda_limpia.sort(key=lambda x: x['hora'])

        # Plantilla HTML con CSS "Skinmed Style"
        html_template = """
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f4f7f6; color: #333; padding: 20px; }
                .container { max-width: 800px; margin: auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }
                h1 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }
                .meta { margin-bottom: 20px; font-style: italic; color: #7f8c8d; }
                table { width: 100%; border-collapse: collapse; margin-top: 20px; }
                th { background: #3498db; color: white; padding: 12px; text-align: left; }
                td { padding: 12px; border-bottom: 1px solid #eee; }
                tr:hover { background: #f9f9f9; }
                .badge { padding: 4px 8px; border-radius: 4px; font-size: 0.85em; font-weight: bold; }
                .badge-pabellon { background: #e74c3c; color: white; }
                .badge-control { background: #2ecc71; color: white; }
                .badge-consulta { background: #3498db; color: white; }
                .badge-recordatorio { background: #f1c40f; color: black; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🩺 Agenda Asistente Skinmed</h1>
                <p class="meta">Visualización de prueba para <b>{{ doctor }}</b> - {{ fecha }}</p>
                <table>
                    <thead>
                        <tr>
                            <th>Hora</th>
                            <th>Paciente</th>
                            <th>Actividad</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for cita in agenda %}
                        <tr>
                            <td><b>{{ cita.hora }}</b></td>
                            <td>{{ cita.paciente }}</td>
                            <td>
                                <span class="badge 
                                    {% if 'PABELLON' in cita.actividad %}badge-pabellon
                                    {% elif 'CONTROL' in cita.actividad %}badge-control
                                    {% elif 'RECORDATORIO' in cita.actividad %}badge-recordatorio
                                    {% else %}badge-consulta{% endif %}">
                                    {{ cita.actividad }}
                                </span>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </body>
        </html>
        """
        
        return render_template_string(
            html_template, 
            agenda=agenda_limpia, 
            doctor=data[0]['fieldData']['Recurso Humano::Nombre Lista'],
            fecha=today
        )

    except Exception as e:
        return f"<h1 style='color:red;'>CRITICAL ERROR</h1><pre>{str(e)}</pre>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))