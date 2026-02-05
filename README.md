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
├── workflows/         # Workflows basados en roles
│   ├── base.py        # Clase base WorkflowHandler
│   ├── doctor.py      # Workflow para doctores
│   ├── manager.py     # Workflow para gerentes
│   ├── nurse.py       # Workflow para enfermeras
│   └── role_registry.py # Registro de workflows por rol
├── formatters/        # Formateadores de datos
│   └── agenda.py      # Formateador de agenda médica
├── config.py          # Configuración y variables de entorno
└── schemas.py         # Modelos Pydantic para validación

main.py                # Punto de entrada FastAPI
verify_roles.py        # Script de verificación de roles
```

## Instalación

### Requisitos
- Python 3.8+
- Acceso a FileMaker Server con Data API habilitada
- Cuenta de WhatsApp Business API

### Configuración

1. **Instalar dependencias:**
```bash
pip install -r requirements.txt
```

2. **Variables de entorno:**
Crear archivo `.env` con las siguientes variables:
```bash
# FileMaker Configuration
FILEMAKER_URL=https://your-filemaker-server.com
FILEMAKER_DATABASE=your-database-name
FILEMAKER_USERNAME=your-username
FILEMAKER_PASSWORD=your-password

# WhatsApp Configuration
WHATSAPP_API_URL=https://graph.facebook.com/v18.0
WHATSAPP_TOKEN=your-whatsapp-token
WHATSAPP_PHONE_NUMBER_ID=your-phone-number-id

# Webhook Verification
VERIFY_TOKEN=your-webhook-verify-token
```

3. **Ejecutar servidor:**
```bash
uvicorn main:app --reload
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
- **Arquitectura modular**: Workflows separados por rol para fácil extensibilidad

## Arquitectura de Workflows

El sistema utiliza un **patrón de dispatcher basado en roles** para enrutar mensajes al workflow apropiado:

### Componentes Clave

#### `WorkflowHandler` (Base Class)
Clase abstracta que define la interfaz para todos los workflows:
- `handle_text()`: Procesa mensajes de texto
- `handle_button()`: Procesa interacciones con botones

#### Role Registry
Sistema de registro que mapea roles a sus respectivos handlers:
```python
from app.workflows.role_registry import get_workflow_handler

handler = get_workflow_handler(user.role)
await handler.handle_button(user, phone, button_title, background_tasks)
```

### Workflows por Rol

#### Doctor Workflow (`doctor.py`)
- Consulta de agenda del día
- Información detallada de pacientes
- Gestión de citas

#### Manager Workflow (`manager.py`)
- Reportes administrativos
- Gestión de recursos
- Supervisión de operaciones

#### Nurse Workflow (`nurse.py`)
- Información de boxes
- Coordinación de pacientes
- Soporte operativo

## Servicios Principales

### `FileMakerService`
Gestiona toda la comunicación con la base de datos FileMaker.

**Métodos:**
- `get_token()`: Obtiene/reutiliza token de autenticación (caché 14 min)
- `get_user_by_phone()`: Consulta información de usuario
- `get_agenda()`: Obtiene agenda del día
- `execute_script()`: Ejecuta scripts de FileMaker

### `WhatsAppService`
Maneja el envío de mensajes y plantillas de WhatsApp.

**Métodos:**
- `send_message()`: Envío de mensajes de texto
- `send_template()`: Envío de plantillas aprobadas
- `send_interactive_buttons()`: Envío de botones interactivos

### `AuthService`
Gestiona la autenticación y autorización de usuarios.

**Métodos:**
- `get_user_by_phone()`: Resuelve usuario a partir de teléfono

## Modelos de Datos

### `User` (Pydantic)
```python
phone: str
name: str
role: Role  # DOCTOR | MANAGER | HEAD_NURSE
```

### `WSPPayload` (Pydantic)
Validación de webhooks entrantes de WhatsApp.

### `Role` (Enum)
```python
class Role(str, Enum):
    DOCTOR = "Doctor"
    MANAGER = "Manager"
    HEAD_NURSE = "Head Nurse"
```

## API Endpoints

### `GET /webhook`
Verificación de webhook de WhatsApp.

**Query Parameters:**
- `hub.mode`: Modo de verificación
- `hub.verify_token`: Token de verificación
- `hub.challenge`: Desafío a retornar

### `POST /webhook`
Recepción de mensajes entrantes de WhatsApp.

**Body:** `WSPPayload` con estructura de webhook de WhatsApp

## Flujo de Usuario

1. **Usuario envía mensaje** → Sistema verifica teléfono en FileMaker
2. **Si autorizado** → Obtiene workflow handler según rol
3. **Dispatcher enruta** → Mensaje procesado por workflow específico
4. **Usuario selecciona opción** → Bot procesa según permisos
5. **Respuesta** → Información solicitada o mensaje de trabajo en progreso

## Layouts de FileMaker

- `AuthUsuarios_dapi`: Autenticación (Nombre, ROL, Telefono)
- `Numeros_dapi`: Agenda médica (Fecha, Hora, Paciente, etc.)

## Extensibilidad

### Agregar Nuevo Rol

1. **Crear workflow handler** en `app/workflows/nuevo_rol.py`:
```python
from app.workflows.base import WorkflowHandler

class NuevoRolWorkflow(WorkflowHandler):
    async def handle_text(self, user, phone):
        # Implementar lógica
        pass
    
    async def handle_button(self, user, phone, button_title, background_tasks):
        # Implementar lógica
        pass
```

2. **Registrar en role_registry.py**:
```python
from app.workflows.nuevo_rol import NuevoRolWorkflow

WORKFLOW_REGISTRY = {
    Role.NUEVO_ROL: NuevoRolWorkflow(),
    # ... otros roles
}
```

3. **Actualizar enum de Roles** en `app/auth/models.py`

## Herramientas de Desarrollo

### Verificar Roles
Script para verificar la configuración de roles:
```bash
python verify_roles.py
```

## Licencia
Ver archivo `LICENSE`
