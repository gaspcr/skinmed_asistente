import os
import requests
from flask import Flask, jsonify
from datetime import datetime

app = Flask(__name__)

# --- CONFIGURACIÓN POR VARIABLES DE ENTORNO ---
# Estas se configuran en el panel de Railway
FM_HOST = "fmsk.skinmed.cl"
FM_DB = "Agenda%20v20b"
FM_USER = os.getenv("FM_USER", "API")
FM_PASS = os.getenv("FM_PASS")
LAYOUT = "Numeros_dapi" # Tu layout mixto validado

@app.route("/")
def home():
    return "Bot de Skinmed Operativo. Ve a /test para probar la conexión."

@app.route("/test")
def test_connection():
    try:
        # 1. Obtener Token
        auth_url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/sessions"
        auth_resp = requests.post(auth_url, auth=(FM_USER, FM_PASS), json={})
        auth_resp.raise_for_status()
        token = auth_resp.json()['response']['token']

        # 2. Realizar Consulta (Búsqueda Triple con Omit)
        find_url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/layouts/{LAYOUT}/_find"
        headers = {"Authorization": f"Bearer {token}"}
        
        # Fecha de hoy según tu formato de base de datos
        today = datetime.now().strftime("%m/%d/%Y") 
        
        payload = {
            "query": [
                {
                    "Recurso Humano::Telefono": "+56939129139", # El número de tus pruebas
                    "Fecha": today
                },
                {
                    "Tipo": "no viene",
                    "omit": "true"
                }
            ]
        }
        
        find_resp = requests.post(find_url, json=payload, headers=headers)
        
        # 3. Cerrar Sesión (Importante para no agotar licencias)
        requests.delete(f"{auth_url}/{token}")

        if find_resp.status_code == 200:
            return jsonify({
                "status": "success",
                "message": "Conexión exitosa",
                "data": find_resp.json()['response']['data']
            })
        elif find_resp.status_code == 500: # FileMaker lanza 500 para el error 401
            return jsonify({"status": "no_records", "message": "No hay citas confirmadas para hoy"})
        
        return jsonify({"status": "error", "fm_error": find_resp.json()})

    except Exception as e:
        return jsonify({"status": "critical_error", "details": str(e)})

if __name__ == "__main__":
    # Railway asigna el puerto automáticamente
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))