# SkinMed Asistente

Bot de WhatsApp para la gestión de consultas médicas en Clínica SkinMed.

## Objetivo

Este bot proporciona un asistente virtual para el personal médico de la clínica, permitiendo:
- Consultar agendas médicas del día
- Verificar información de pacientes
- Gestionar boxes y recursos
- Control de acceso basado en roles (médicos, gerencia, enfermería)

## Arquitectura

```
app/
├── services/          # Servicios de integración externa
│   ├── filemaker.py   # API de FileMaker con caché de tokens (Redis)
│   ├── whatsapp.py    # API de WhatsApp Business con retries
│   ├── redis.py       # Cliente Redis para caché y rate limiting
│   └── http.py        # Cliente HTTP compartido con connection pooling
├── auth/              # Sistema de autenticación
│   ├── models.py      # Modelo de Usuario
│   └── service.py     # Lógica de autenticación (caché 5 min en Redis)
├── workflows/         # Workflows basados en roles
│   ├── base.py        # Clase base WorkflowHandler
│   ├── doctor.py      # Workflow para médicos (implementado)
│   ├── manager.py     # Workflow para gerencia (stub)
│   ├── nurse.py       # Workflow para enfermería (stub)
│   └── role_registry.py # Sistema de registro con decoradores
├── formatters/        # Formateadores de datos
│   └── agenda.py      # Formateador de agenda médica
├── utils/             # Utilidades
│   └── retry.py       # Utilidad de reintentos con backoff exponencial
├── middleware.py      # Verificación HMAC-SHA256 de webhooks
├── exceptions.py      # Excepciones personalizadas
├── logging_config.py  # Configuración de logging estructurado
├── config.py          # Configuración y validación de variables de entorno
└── schemas.py         # Modelos Pydantic para validación

main.py                # Punto de entrada FastAPI con lifespan
verify_roles.py        # Script de verificación de roles
```

## Instalación

### Requisitos
- Python 3.8+
- Redis (para caché y rate limiting)
- Acceso a FileMaker Server con Data API habilitada
- Cuenta de WhatsApp Business API

### Configuración

1. **Instalar dependencias:**
```bash
pip install -r requirements.txt
```

2. **Variables de entorno:**
Crear archivo `.env` con las siguientes variables:

**Requeridas:**
```bash
# FileMaker Configuration
FM_USER=your-username
FM_PASS=your-password

# WhatsApp Configuration
WSP_TOKEN=your-whatsapp-token
WSP_PHONE_ID=your-phone-number-id
WSP_VERIFY_TOKEN=your-webhook-verify-token
WSP_APP_SECRET=your-app-secret
```

**Opcionales (con defaults):**
```bash
# FileMaker (defaults configurados)
FM_HOST=fmsk.skinmed.cl
FM_DB=Agenda%20v20b
FM_AGENDA_LAYOUT=ListadoDeHoras_dapi
FM_AUTH_LAYOUT=AuthUsuarios_dapi

# WhatsApp
META_API_VERSION=v24.0

# Redis
REDIS_URL=redis://localhost:6379/0

# Logging
LOG_LEVEL=INFO
```

3. **Iniciar Redis:**
```bash
redis-server
```

4. **Ejecutar servidor:**
```bash
uvicorn main:app --reload
```

## Funcionalidades

### 🔐 Autenticación y Seguridad
- Sistema de roles dinámico basado en FileMaker
- Verificación automática por número de teléfono
- **Caché de usuarios** en Redis (5 minutos)
- **Verificación HMAC-SHA256** de webhooks de WhatsApp
- **Rate limiting**: 30 mensajes por minuto por teléfono

### 📅 Gestión de Agenda
- Consulta de agenda diaria del médico
- Filtrado de citas válidas (excluye eliminadas/bloqueadas)
- Formato optimizado para WhatsApp

### 🚀 Optimizaciones y Resiliencia
- **Caché de tokens FileMaker**: Redis con TTL de 14 minutos
- **Connection pooling**: Cliente HTTP compartido (httpx AsyncClient)
- **Reintentos automáticos**: Con backoff exponencial en servicios externos
- **Lifespan management**: Inicialización y cierre limpio de recursos
- **Health checks**: Endpoint `/health` para monitoreo
- **Logging estructurado**: Configuración centralizada con niveles

## Arquitectura de Workflows

El sistema utiliza un **patrón de registro basado en decoradores** para enrutar mensajes al workflow apropiado:

### Sistema de Registro con Decoradores

Los workflows se registran automáticamente usando decoradores:

```python
from app.workflows.base import WorkflowHandler
from app.workflows.role_registry import register_workflow

@register_workflow("medico")
class DoctorWorkflow(WorkflowHandler):
    async def handle_text(self, user, phone, message_text):
        # Implementación
        pass
    
    async def handle_button(self, user, phone, button_title, background_tasks):
        # Implementación
        pass
```

### Componentes Clave

#### `WorkflowHandler` (Base Class)
Clase abstracta que define la interfaz para todos los workflows:
- `handle_text(user, phone, message_text)`: Procesa mensajes de texto
- `handle_button(user, phone, button_title, background_tasks)`: Procesa interacciones con botones

#### Role Registry
Sistema de registro automático que mapea roles a sus respectivos handlers:
```python
from app.workflows.role_registry import get_workflow_handler

handler = get_workflow_handler(user.role)  # user.role = "medico" → DoctorWorkflow
await handler.handle_text(user, phone, message_text)
```

**Funciones útiles:**
- `get_workflow_handler(role)`: Obtiene instancia del handler
- `get_registered_roles()`: Lista todos los roles registrados
- `is_role_registered(role)`: Verifica si un rol está registrado

### Workflows Implementados

#### Doctor Workflow (`doctor.py`) ✅
- **Registro**: `@register_workflow("medico")`
- Consulta de agenda del día
- Información detallada de pacientes
- Gestión multi-paso con estado en Redis (TTL 30 min)

#### Manager Workflow (`manager.py`) 🚧
- **Registro**: `@register_workflow("gerencia")`
- Stub básico (pendiente de implementación completa)

#### Nurse Workflow (`nurse.py`) 🚧
- **Registro**: `@register_workflow("enfermeria")`
- Stub básico (pendiente de implementación completa)

## Servicios Principales

### `RedisService` (`services/redis.py`)
Cliente Redis asíncrono para estado y caché.

**Métodos:**
- `init(url)`: Inicializa conexión
- `close()`: Cierra conexión
- `get(key)`, `set(key, value, ttl)`: Operaciones básicas
- `verificar_rate_limit(key, limite, ventana_ttl)`: Rate limiting

### `HTTPService` (`services/http.py`)
Cliente HTTP compartido con connection pooling.

**Métodos:**
- `init()`: Inicializa cliente httpx
- `close()`: Cierra conexiones
- `get_client()`: Obtiene instancia del cliente

### `FileMakerService` (`services/filemaker.py`)
Gestiona toda la comunicación con FileMaker Data API.

**Métodos:**
- `get_token()`: Obtiene/reutiliza token (caché Redis 14 min)
- `get_user_by_phone(phone)`: Consulta usuario desde `AuthUsuarios_dapi`
- `get_agenda(doctor_name)`: Obtiene agenda desde `ListadoDeHoras_dapi`
- **Auto-retry**: Reintenta en 401 (token expirado) y errores de conexión

### `WhatsAppService` (`services/whatsapp.py`)
Maneja el envío de mensajes a WhatsApp Business API.

**Métodos:**
- `send_message(to, text)`: Envío de mensajes de texto
- `send_template(to, template_name, language, components)`: Plantillas
- `send_interactive_buttons(to, body_text, buttons)`: Botones interactivos
- **Auto-retry**: Reintenta en 5xx y errores de conexión

### `AuthService` (`auth/service.py`)
Gestiona la autenticación y autorización de usuarios.

**Métodos:**
- `get_user_by_phone(phone)`: Resuelve usuario (caché Redis 5 min)

## Modelos de Datos

### `User` (Pydantic)
```python
phone: str
name: str
role: str  # Rol dinámico desde FileMaker (validado por registry)
```

### `WSPPayload` (Pydantic)
Validación de webhooks entrantes de WhatsApp.

## API Endpoints

### `GET /health`
Health check para monitoring (Railway/similar).

**Respuesta:**
```json
{
  "status": "ok",
  "servicios": {
    "redis": "ok",
    "http_client": "ok"
  }
}
```

### `GET /webhook`
Verificación de webhook de WhatsApp.

**Query Parameters:**
- `hub.mode`: Modo de verificación
- `hub.verify_token`: Token de verificación
- `hub.challenge`: Desafío a retornar

### `POST /webhook`
Recepción de mensajes entrantes de WhatsApp.

**Seguridad:**
- Verificación HMAC-SHA256 de firma de webhook
- Rate limiting (30 msg/min por teléfono)

**Body:** `WSPPayload` con estructura de webhook de WhatsApp

## Flujo de Usuario

1. **Usuario envía mensaje** → WhatsApp webhook entrega mensaje
2. **Verificación HMAC** → Middleware valida firma del webhook
3. **Rate limiting** → Verifica límites por teléfono
4. **Autenticación** → AuthService busca usuario en FileMaker (caché Redis 5 min)
5. **Dispatch a workflow** → `get_workflow_handler(user.role)` obtiene handler
6. **Procesamiento** → Workflow procesa mensaje según tipo (texto/botón)
7. **Respuesta** → WhatsAppService envía respuesta

## Layouts de FileMaker

- `AuthUsuarios_dapi`: Autenticación (Nombre, ROL, Telefono)
- `ListadoDeHoras_dapi`: Agenda médica (Fecha, Hora, Paciente, Estado, etc.)

## Extensibilidad

### Agregar Nuevo Rol

1. **Crear workflow handler** en `app/workflows/nuevo_rol.py`:
```python
from app.workflows.base import WorkflowHandler
from app.workflows.role_registry import register_workflow

@register_workflow("nuevo_rol")  # Debe coincidir con campo ROL en FileMaker
class NuevoRolWorkflow(WorkflowHandler):
    async def handle_text(self, user, phone, message_text):
        # Implementar lógica
        pass
    
    async def handle_button(self, user, phone, button_title, background_tasks):
        # Implementar lógica
        pass
```

2. **Importar en `app/workflows/__init__.py`**:
```python
from . import nuevo_rol  # Auto-registra al importar
```

¡Eso es todo! El decorador `@register_workflow` registra automáticamente el workflow.

## Manejo de Errores

### Excepciones Personalizadas
- `ServicioNoDisponibleError`: Indica que un servicio externo no está disponible
  - Se captura en `main.py` para enviar mensaje amigable al usuario

### Logging
- Configuración centralizada en `logging_config.py`
- Nivel configurable vía `LOG_LEVEL` env var
- Logs estructurados para facilitar debugging

## Herramientas de Desarrollo

### Verificar Roles
Script para verificar la configuración de roles:
```bash
python verify_roles.py
```

## Despliegue

El bot está diseñado para desplegarse fácilmente en plataformas como Railway, Render, o similar.

**Requisitos:**
- Servicio Redis (Railway provee add-ons)
- Variables de entorno configuradas
- Health check en `/health`

## Licencia
Ver archivo `LICENSE`
