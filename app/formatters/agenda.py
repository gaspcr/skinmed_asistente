from typing import List, Dict

class AgendaFormatter:
    @staticmethod
    def format(data: List[Dict], doctor_name: str) -> str:
        if not data:
            return "No hay citas agendadas para hoy."
        
        msg = f"*Hola {doctor_name}*\nAgenda para hoy:\n\n"
        
        ignorar_tipo = ["Eliminada", "Bloqueada", "No Viene"]
        ignorar_actividad = ["RECORDATORIO", "VISITADOR MÉDICO", "LABORATORIO"]
        
        validos = [
            r for r in data 
            if r['fieldData'].get('Tipo') not in ignorar_tipo 
            and r['fieldData'].get('Actividad', '').upper() not in ignorar_actividad
        ]
        validos.sort(key=lambda x: x['fieldData']['Hora'])

        if not validos:
            return f"*{doctor_name}*, no tienes citas agendadas hoy."

        for reg in validos:
            f = reg['fieldData']
            hora = ":".join(f['Hora'].split(":")[:2])
            
            nombre = f.get('Pacientes::NOMBRE', '')
            apellido = f.get('Pacientes::APELLIDO PATERNO', '')
            paciente = f"{nombre} {apellido}".strip() or 'Sin paciente'
            
            motivo = f.get('Actividad', 'Sin motivo')
            tipo = f.get('Tipo', 'Desconocido')
            
            conjunto_tag = " 🔗" if tipo.lower() == "conjunto" else ""
            
            msg += f"⏰ *{hora}* — {paciente}\n"
            msg += f"📋 {motivo}{conjunto_tag}\n"
        
        return msg
