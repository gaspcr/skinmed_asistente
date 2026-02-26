from typing import List, Dict

# Mapeo de concepto de cobro / actividad a abreviación
_ABREVIACIONES = {
    "ÁC. HIALURÓNICO": "AH",
    "AC. HIALURONICO MKT": "AH",
    "BOTOX": "BX",
    "BOTOX + AC. HIALURÓNICO": "BX+AH",
    "BOTOX MKT": "BXMK",
    "CÉLULAS MADRE": "CM",
    "CO2 (PH)": "CO2",
    "CONSULTA": "CN",
    "CONTROL": "CTRL",
    "CURACION": "CUR",
    "DOUBLO": "DBL",
    "DOUBLO 2.0": "DBL",
    "DOUBLO MKT": "DBLMK",
    "DYSPORT": "DYS",
    "EDGE ONE (CO2 FRAX)": "CO2",
    "EXOSOMAS": "EXO",
    "HARMONYCA": "HCA",
    "IPL (CLEARLIGHT)": "IPL",
    "IPL + NOBLEN": "IPL",
    "IPL + Noblen": "IPL",
    "LUMENIS IPL/YAG": "IPL",
    "MAPEO DIGITAL": "MDG",
    "MESOTERAPIA": "MES",
    "NOBLEEN": "NOB",
    "NUCLEOFILL": "AH",
    "OP1": "OP",
    "OP2": "OP",
    "OP3": "OP",
    "PicoLo + Nobleen": "PIC",
    "PICOLO LASER": "PIC",
    "PLASMA": "PMA",
    "PROTOCOLO": "PROT",
    "PRP": "PRP",
    "RADIESSE": "AH",
    "RENAS II": "REN",
    "SCULPTRA": "SCUP",
    "TELECONSULTA": "TC",
    "THULIUM": "THU",
    "TRASPLANTE DE PELO": "TXP",
    "VENUS LEGACY": "VL",
    "VENUS VIVA": "VV",
}


class AgendaFormatter:
    @staticmethod
    def _abreviar(actividad: str) -> str:
        """Retorna la abreviación de la actividad, o el original si no existe."""
        return _ABREVIACIONES.get(actividad, _ABREVIACIONES.get(actividad.upper(), actividad))

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
            and r['fieldData'].get('Hora', '00:00:00') != '00:00:00'
        ]
        validos.sort(key=lambda x: x['fieldData']['Hora'])

        if not validos:
            return f"*{doctor_name}*, no tienes citas agendadas hoy."

        for reg in validos:
            f = reg['fieldData']
            hora = ":".join(f['Hora'].split(":")[:2])
            tipo = f.get('Tipo', 'Desconocido')
            
            if tipo == "Disponible":
                msg += f"*{hora}* — Disponible\n"
                continue

            nombre = f.get('Pacientes::NOMBRE', '')
            apellido = f.get('Pacientes::APELLIDO PATERNO', '')
            paciente = f"{nombre} {apellido}".strip().title() or 'Sin paciente'
            
            motivo_raw = f.get('Actividad', 'Sin motivo')
            motivo = AgendaFormatter._abreviar(motivo_raw)
            conjunto_tag = " (conj)" if tipo.lower() == "conjunto" else ""
            
            msg += f"*{hora}* — {paciente} — {motivo}{conjunto_tag}\n"
        
        return msg

