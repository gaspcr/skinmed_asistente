"""
Test script to verify agenda filtering with real FileMaker data
"""
import json

# Sample data from Walter's agenda on 02-02-2026
sample_data = [
    {
        "fieldData": {
            "Recurso Humano::Nombre": "Walter",
            "Actividad": "",
            "Salas::Nombre": "",
            "Hora": "09:00:00",
            "Fecha": "02/02/2026",
            "Pacientes::NOMBRE": "",
            "Pacientes::APELLIDO PATERNO": "",
            "Tipo": "Eliminada"
        },
        "recordId": "1747640"
    },
    {
        "fieldData": {
            "Recurso Humano::Nombre": "Walter",
            "Actividad": "CONSULTA",
            "Salas::Nombre": "SALA 5",
            "Hora": "11:00:00",
            "Fecha": "02/02/2026",
            "Pacientes::NOMBRE": "MARIA JOSE",
            "Pacientes::APELLIDO PATERNO": "JORDAN",
            "Tipo": "No Viene"
        },
        "recordId": "1747644"
    },
    {
        "fieldData": {
            "Recurso Humano::Nombre": "Walter",
            "Actividad": "",
            "Salas::Nombre": "",
            "Hora": "11:30:00",
            "Fecha": "02/02/2026",
            "Pacientes::NOMBRE": "",
            "Pacientes::APELLIDO PATERNO": "",
            "Tipo": "Disponible"
        },
        "recordId": "1747645"
    },
    {
        "fieldData": {
            "Recurso Humano::Nombre": "Walter",
            "Actividad": "CONSULTA",
            "Salas::Nombre": "SALA 5",
            "Hora": "12:00:00",
            "Fecha": "02/02/2026",
            "Pacientes::NOMBRE": "CAROL",
            "Pacientes::APELLIDO PATERNO": "WEIL",
            "Tipo": "Ocupada"
        },
        "recordId": "1747646"
    },
    {
        "fieldData": {
            "Recurso Humano::Nombre": "Walter",
            "Actividad": "OP1",
            "Salas::Nombre": "PABELLÓN",
            "Hora": "13:00:00",
            "Fecha": "02/02/2026",
            "Pacientes::NOMBRE": "MARCELO",
            "Pacientes::APELLIDO PATERNO": "QUEZADA",
            "Tipo": "Ocupada"
        },
        "recordId": "1747648"
    },
    {
        "fieldData": {
            "Recurso Humano::Nombre": "Walter",
            "Actividad": "RECORDATORIO",
            "Salas::Nombre": "SIN SALA",
            "Hora": "00:00:00",
            "Fecha": "02/02/2026",
            "Pacientes::NOMBRE": "MARIA DE LOS ANGELES",
            "Pacientes::APELLIDO PATERNO": "ICAZA",
            "Tipo": "Ocupada"
        },
        "recordId": "1748042"
    },
    {
        "fieldData": {
            "Recurso Humano::Nombre": "Walter",
            "Actividad": "VISITADOR MÉDICO",
            "Salas::Nombre": "SIN SALA",
            "Hora": "10:28:00",
            "Fecha": "02/02/2026",
            "Pacientes::NOMBRE": "LABORATORIO ",
            "Pacientes::APELLIDO PATERNO": "",
            "Tipo": "Ocupada"
        },
        "recordId": "1748132"
    },
    {
        "fieldData": {
            "Recurso Humano::Nombre": "Walter",
            "Actividad": "CONSULTA",
            "Salas::Nombre": "SALA 5",
            "Hora": "11:00:00",
            "Fecha": "02/02/2026",
            "Pacientes::NOMBRE": "MARIA JOSE",
            "Pacientes::APELLIDO PATERNO": "JORDAN",
            "Tipo": "Ocupada"
        },
        "recordId": "1747944"
    },
    {
        "fieldData": {
            "Recurso Humano::Nombre": "Walter",
            "Actividad": "CONTROL",
            "Salas::Nombre": "SALA CURACIONES",
            "Hora": "12:20:00",
            "Fecha": "02/02/2026",
            "Pacientes::NOMBRE": "SALVADOR",
            "Pacientes::APELLIDO PATERNO": "RIVERA",
            "Tipo": "Ocupada"
        },
        "recordId": "1748001"
    },
    {
        "fieldData": {
            "Recurso Humano::Nombre": "Walter",
            "Actividad": "LABORATORIO",
            "Salas::Nombre": "SIN SALA",
            "Hora": "14:21:00",
            "Fecha": "02/02/2026",
            "Pacientes::NOMBRE": "LABORATORIO ",
            "Pacientes::APELLIDO PATERNO": "",
            "Tipo": "No Viene"
        },
        "recordId": "1748176"
    }
]

# Import the formatter
import sys
sys.path.insert(0, '/Users/rodrigougartekunisky/Desktop/skinmed_asistente')
from app.formatters.agenda import AgendaFormatter

# Test the formatter
result = AgendaFormatter.format(sample_data, "Walter")
print(result)
print("\n" + "="*50)
print("EXPECTED OUTPUT:")
print("="*50)
print("""
*Hola Walter*
Agenda para hoy:

*11:00* - MARIA JOSE JORDAN
  📋 CONSULTA

*12:00* - CAROL WEIL
  📋 CONSULTA

*12:20* - SALVADOR RIVERA
  📋 CONTROL

*13:00* - MARCELO QUEZADA
  📋 OP1
""")
