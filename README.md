# SkinMed Asistente

Bot de WhatsApp para la gestión de consultas médicas en Clínica SkinMed.

## Objetivo

Este bot proporciona un asistente virtual para el personal médico de la clínica, permitiendo:
- Consultar agendas médicas del día
- Verificar información de pacientes
- Gestionar boxes y recursos
- Control de acceso basado en roles (Doctores, Gerentes, Enfermeras)

## Arquitectura

```
app/
├── services/          # Servicios de integración externa
│   ├── filemaker.py   # API de FileMaker (base de datos)
│   └── whatsapp.py    # API de WhatsApp Business
├── auth/              # Sistema de autenticación
│   ├── models.py      # Modelos de Usuario y Roles
│   └── service.py     # Lógica de autenticación
├── config.py          # Configuración y variables de entorno
└── schemas.py         # Modelos Pydantic para validación

main.py                # Punto de entrada FastAPI
```

## Funcionalidades

### 🔐 Autenticación
- Sistema de roles basado en FileMaker
- Verificación automática por número de teléfono
- Acceso diferenciado según rol (Doctor/Gerente/Enfermera)

### 📅 Gestión de Agenda
- Consulta de agenda diaria del doctor
- Filtrado de citas válidas (excluye eliminadas/bloqueadas)
- Formato optimizado para WhatsApp

### 🚀 Optimizaciones
- **Caché de tokens**: Reutilización de tokens de FileMaker (14 min)
- **Respuestas asíncronas**: Procesamiento en background
- **Rate limiting**: Prevención de sobrecarga de APIs

## Clases Principales

### `FileMakerService`
Gestiona toda la comunicación con la base de datos FileMaker.

**Métodos:**
- `get_token()`: Obtiene/reutiliza token de autenticación
- `get_user_by_phone()`: Consulta información de usuario
- `get_agenda()`: Obtiene agenda del día
- `parse_agenda()`: Formatea datos para WhatsApp

### `WhatsAppService`
Maneja el envío de mensajes y plantillas de WhatsApp.

**Métodos:**
- `send_message()`: Envío de mensajes de texto
- `send_template()`: Envío de plantillas aprobadas

### `AuthService`
Gestiona la autenticación y autorización de usuarios.

**Métodos:**
- `get_user_by_phone()`: Resuelve usuario a partir de teléfono

### Modelos de Datos

#### `User` (Pydantic)
```python
phone: str
name: str
role: Role  # DOCTOR | MANAGER | HEAD_NURSE
```

#### `WSPPayload` (Pydantic)
Validación de webhooks entrantes de WhatsApp.

## Uso

### Variables de Entorno
```bash
FM_USER=<usuario_filemaker>
FM_PASS=<contraseña_filemaker>
WSP_TOKEN=<token_whatsapp_business>
WSP_PHONE_ID=<id_telefono_whatsapp>
WSP_VERIFY_TOKEN=<token_verificacion_webhook>
```

### Ejecución
```bash
uvicorn main:app --reload
```

### Endpoints
- `GET /webhook`: Verificación de webhook de WhatsApp
- `POST /webhook`: Recepción de mensajes entrantes

## Flujo de Usuario

1. **Usuario envía mensaje** → Sistema verifica teléfono en FileMaker
2. **Si autorizado** → Envía template según rol
3. **Usuario selecciona opción** → Bot procesa según permisos
4. **Respuesta** → Información solicitada o mensaje de trabajo en progreso

## Layouts de FileMaker

- `AuthUsuarios_dapi`: Autenticación (Nombre, ROL, Telefono)
- `Numeros_dapi`: Agenda médica (Fecha, Hora, Paciente, etc.)
