from typing import List, Dict, Optional, Tuple

# Mapeo de concepto de cobro / actividad a abreviación
_ABREVIACIONES = {
    "ÁC. HIALURÓNICO": "AH",
    "AC. HIALURONICO MKT": "AH",
    "BOTOX": "BX",
    "BOTOX + AC. HIALURÓNICO": "BX+AH",
    "BOTOX MKT": "BXMK",
    "CÉLULAS MADRE": "CEL",
    "CO2 (PH)": "CO2",
    "CONSULTA": "CM",
    "CONTROL": "CTRL",
    "CURACION": "CUR",
    "DOUBLO": "DBL",
    "DOUBLO 2.0": "DBL",
    "DOUBLO MKT": "DBLMK",
    "DYSPORT": "DYS",
    "EDGE ONE (CO2 FRAX)": "CO2",
    "EXOSOMAS": "MESO",
    "HARMONYCA": "HCA",
    "IPL (CLEARLIGHT)": "IPL",
    "IPL + NOBLEN": "IPL",
    "IPL + Noblen": "IPL",
    "LUMENIS IPL/YAG": "LUM",
    "MAPEO DIGITAL": "MAP",
    "MESOTERAPIA": "MESO",
    "NOBLEEN": "NOB",
    "NUCLEOFILL": "AH",
    "OP1": "OP",
    "OP2": "OP",
    "OP3": "OP",
    "PicoLo + Nobleen": "PIC",
    "PICOLO LASER": "PIC",
    "PLASMA": "PRP",
    "PROTOCOLO": "PROT",
    "PRP": "PRP",
    "RADIESSE": "AH",
    "RENAS II": "REN",
    "SCULPTRA": "SCU",
    "TELECONSULTA": "TC",
    "THULIUM": "THU",
    "TRASPLANTE DE PELO": "TXP",
    "VENUS LEGACY": "VL",
    "VENUS VIVA": "VV",
    "SOP": "SOPRANO",
    "ALEX": "ALEXANDRITA",
    "VENUS": "VENUS",
    "CRIO": "CRIOLIPOLOSIS",
    "HIFU": "DOUBLO",
    "LIMP": "LIMPIEZA FACIAL"
}

# Mapeo inverso: abreviación → nombre legible (uno representativo por abreviación)
_GLOSARIO = {
    "AH": "Ác. Hialurónico",
    "BX": "Botox",
    "BX+AH": "Botox + Ác. Hialurónico",
    "BXMK": "Botox MKT",
    "CEL": "Células Madre",
    "CO2": "CO2",
    "CM": "Consulta",
    "CTRL": "Control",
    "CUR": "Curación",
    "DBL": "Doublo",
    "DBLMK": "Doublo MKT",
    "DYS": "Dysport",
    "HCA": "Harmonyca",
    "IPL": "IPL",
    "LUM": "Lumenis IPL/YAG",
    "MAP": "Mapeo Digital",
    "MESO": "Mesoterapia",
    "NOB": "Nobleen",
    "OP": "OP",
    "PIC": "Picolo Laser",
    "PRP": "PRP",
    "PROT": "Protocolo",
    "REN": "Renas II",
    "SCU": "Sculptra",
    "TC": "Teleconsulta",
    "THU": "Thulium",
    "TXP": "Trasplante de Pelo",
    "VL": "Venus Legacy",
    "VV": "Venus Viva",
    "SOP": "SOPRANO",
    "ALEX": "ALEXANDRITA",
    "VENUS": "VENUS",
    "CRIO": "CRIOLIPOLOSIS",
    "HIFU": "DOUBLO",
    "LIMP": "LIMPIEZA FACIAL"
}


class AgendaFormatter:
    @staticmethod
    def _abreviar(actividad: str) -> str:
        """Retorna la abreviación de la actividad, o el original si no existe."""
        return _ABREVIACIONES.get(actividad, _ABREVIACIONES.get(actividad.upper(), actividad))

    @staticmethod
    def format(data: List[Dict], doctor_name: str) -> Tuple[str, Optional[str]]:
        """Formatea la agenda y retorna (mensaje_agenda, mensaje_glosario).
        El glosario es None si no hay abreviaciones que mostrar."""
        if not data:
            return "No hay citas agendadas para hoy.", None
        
        msg = f"*Hola Dr(a). {doctor_name}*\nAgenda para hoy:\n\n"
        
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
            return f"*Hola Dr(a). {doctor_name}*\nNo tienes citas agendadas hoy.", None

        abreviaturas_usadas = set()

        for reg in validos:
            f = reg['fieldData']
            hora = ":".join(f['Hora'].split(":")[:2])
            tipo = f.get('Tipo', 'Desconocido')
            
            if tipo == "Disponible":
                msg += f"*{hora}* - Disponible\n"
                continue

            nombre = f.get('Pacientes::NOMBRE', '')
            apellido = f.get('Pacientes::APELLIDO PATERNO', '')
            paciente = f"{nombre} {apellido}".strip().title() or 'Sin paciente'
            
            motivo_raw = f.get('Actividad', 'Sin motivo')
            motivo = AgendaFormatter._abreviar(motivo_raw)
            conjunto_tag = "(conj)" if tipo.lower() == "conjunto" else ""

            # Registrar abreviatura solo si fue abreviada (distinto al original)
            if motivo != motivo_raw:
                abreviaturas_usadas.add(motivo)
            
            msg += f"*{hora}* - {paciente} - *{motivo}{conjunto_tag}*\n"

        # Generar glosario solo con las abreviaturas usadas
        glossary = None
        if abreviaturas_usadas:
            lines = []
            for abr in sorted(abreviaturas_usadas):
                nombre_completo = _GLOSARIO.get(abr, abr)
                lines.append(f"*{abr}*: {nombre_completo}")
            glossary = "\n".join(lines)

        return msg, glossary


