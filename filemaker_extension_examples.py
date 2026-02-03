# Example: Extending FileMakerService for new workflows

# En app/services/filemaker.py, agrega:

@staticmethod
async def find_patient_appointment(patient_name: str, doctor_name: str = None) -> list:
    """
    Buscar cita de un paciente
    
    Args:
        patient_name: Nombre del paciente
        doctor_name: Opcional, filtrar por doctor específico
    
    Returns:
        Lista de citas encontradas (raw FileMaker data)
    """
    async with httpx.AsyncClient() as client:
        try:
            token = await FileMakerService.get_token(client)
            
            find_url = f"https://{FM_HOST}/fmi/data/v1/databases/{FM_DB}/layouts/{LAYOUT}/_find"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}"
            }
            
            query_fields = {
                "Pacientes::NOMBRE": patient_name,
            }
            
            if doctor_name:
                query_fields["Recurso Humano::Nombre"] = doctor_name
            
            query = {"query": [query_fields]}
            
            resp = await client.post(find_url, json=query, headers=headers)
            
            if resp.status_code == 200:
                return resp.json()['response']['data']
            else:
                return []
        except Exception as e:
            print(f"ERROR buscando paciente: {e}")
            return []

# Luego en app/workflows/doctor.py:

async def _consult_patient(self, user, phone):
    # Aquí podrías pedir el nombre del paciente al usuario
    # Por ahora, ejemplo con placeholder:
    await WhatsAppService.send_message(
        phone, 
        "Por favor envía el nombre del paciente que deseas consultar"
    )
    # Necesitarías guardar estado para la siguiente respuesta
    # (esto requiere implementar state management)
