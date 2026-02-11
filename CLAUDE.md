# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WhatsApp Business bot for Clínica SkinMed. Provides role-based assistants (doctor, manager, nurse) for managing medical consultations, schedules, and clinic operations via WhatsApp. The bot authenticates users by phone number against FileMaker and routes messages to role-specific workflow handlers.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Start Redis (required)
redis-server

# Run development server
uvicorn main:app --reload

# Verify role registration
python verify_roles.py
```

No test suite or linter is configured.

## Environment Variables

**Required** in `.env`:
- `FM_USER`, `FM_PASS` — FileMaker credentials
- `WSP_TOKEN` — WhatsApp Business API token
- `WSP_PHONE_ID` — WhatsApp phone number ID
- `WSP_VERIFY_TOKEN` — Webhook verification token
- `WSP_APP_SECRET` — WhatsApp App Secret for HMAC-SHA256 webhook signature verification

**Optional** (with defaults):
- `REDIS_URL` — Redis connection URL (default: `redis://localhost:6379/0`)
- `LOG_LEVEL` — Logging level (default: `INFO`)
- `FM_HOST` — FileMaker server host (default: `fmsk.skinmed.cl`)
- `FM_DB` — FileMaker database name (default: `Agenda%20v20b`)
- `FM_AGENDA_LAYOUT` — FileMaker agenda layout (default: `ListadoDeHoras_dapi`)
- `FM_AUTH_LAYOUT` — FileMaker auth layout (default: `AuthUsuarios_dapi`)
- `META_API_VERSION` — Meta Graph API version (default: `v24.0`)

All required vars are validated at startup via `app/config.py:validate()`. The app refuses to start if any are missing.

Hardcoded config (FileMaker host, database, layouts, Meta API version) lives in `app/config.py` with env var overrides.

## Architecture

**Entry point:** `main.py` — FastAPI app with lifespan (Redis init, HTTP client init, config validation, logging setup) and three endpoints:
- `GET /health` — Service health check (for Railway/monitoring)
- `GET /webhook` — WhatsApp webhook verification
- `POST /webhook` — Incoming messages with HMAC-SHA256 signature verification and rate limiting

**Message flow:**
1. WhatsApp webhook delivers message → HMAC signature verified (`app/middleware.py`) → Pydantic validation (`app/schemas.py`)
2. `AuthService` looks up user by phone number (5-min cache in Redis) via FileMaker
3. `get_workflow_handler(user.role)` dispatches to the correct role handler
4. Workflow handler processes the message and responds via `WhatsAppService`

**Key layers:**
- `app/services/redis.py` — Async Redis client for state management, caching, and rate limiting (init/close lifecycle managed by FastAPI lifespan)
- `app/services/http.py` — Shared httpx AsyncClient singleton with connection pooling (init/close in lifespan)
- `app/services/filemaker.py` — FileMaker Data API client with 14-min token caching in Redis, automatic 401-retry, and connection retry with backoff
- `app/services/whatsapp.py` — WhatsApp Business API client (text, templates) with retry on 5xx/connection errors
- `app/middleware.py` — HMAC-SHA256 webhook signature verification (FastAPI dependency)
- `app/auth/` — User model and auth service with phone-based lookup (cached in Redis)
- `app/workflows/` — Role-based handlers dispatched via decorator registration (multi-step state in Redis with 30-min TTL)
- `app/formatters/agenda.py` — Formats appointment data for WhatsApp display
- `app/exceptions.py` — Custom exceptions (`ServicioNoDisponibleError`) to distinguish infrastructure errors from empty data
- `app/utils/retry.py` — `con_reintentos()` async retry utility with exponential backoff
- `app/logging_config.py` — Structured logging setup with configurable level

## Workflow System

Roles register via decorator: `@register_workflow("medico")`. Role names are normalized to lowercase and must match the `ROL` field in FileMaker's `AuthUsuarios_dapi` layout.

All workflow classes extend `WorkflowHandler` (abstract base in `app/workflows/base.py`) and implement:
- `handle_text(user, phone, message_text)` — processes text messages
- `handle_button(user, phone, button_title, background_tasks)` — processes button interactions

**Currently registered:**
- `medico` — Fully implemented
- `gerencia` — Basic stub
- `enfermeria` — Basic stub

**To add a new role:**
1. Create workflow class in `app/workflows/`
2. Decorate with `@register_workflow("role_name")`
3. Import it in `app/workflows/__init__.py`

The decorator auto-registers the workflow on import.

## Services

### `RedisService` (`services/redis.py`)
- Initialized/closed in FastAPI lifespan
- Used for caching (FM tokens, users), multi-step state, and rate limiting
- TTLs: FM tokens 14 min, users 5 min, workflow state 30 min

### `HTTPService` (`services/http.py`)
- Shared httpx AsyncClient singleton
- Automatic connection pooling
- Managed in FastAPI lifespan

### `FileMakerService` (`services/filemaker.py`)
- Auto-retry on 401 (refreshes token and retries)
- Retry with exponential backoff on connection errors
- Layouts: `AuthUsuarios_dapi` (auth), `ListadoDeHoras_dapi` (agenda)

### `WhatsAppService` (`services/whatsapp.py`)
- Automatic retry on 5xx and connection errors
- Methods: `send_message()`, `send_template()`, `send_interactive_buttons()`

### `AuthService` (`auth/service.py`)
- `get_user_by_phone(phone)` — Looks up user, caches for 5 min in Redis

## Code Patterns

### Retries
Use `app/utils/retry.py:con_reintentos()` for operations requiring retry:
```python
from app.utils.retry import con_reintentos

resultado = await con_reintentos(
    funcion_async,
    max_intentos=3,
    delay_inicial=1.0,
    factor_backoff=2.0
)
```

### Exceptions
- Raise `ServicioNoDisponibleError` when external service unavailable
- Caught in `main.py` to send friendly message to user
- Don't use for empty/not found data (return None or empty list)

### Logging
```python
import logging
logger = logging.getLogger(__name__)

logger.info("Informative message")
logger.warning("Warning: %s", variable)
logger.error("Error processing %s", data)
logger.exception("Full error with traceback")
```

### Multi-Step State in Workflows
Use Redis to save state between interactions:
```python
from app.services import redis as redis_svc

# Save state
await redis_svc.set(
    f"workflow:{phone}:estado",
    json.dumps({"paso": "esperando_nombre", "data": {...}}),
    ttl=1800  # 30 min
)

# Retrieve state
estado_json = await redis_svc.get(f"workflow:{phone}:estado")
if estado_json:
    estado = json.loads(estado_json)
```

## Rate Limiting

Configured in `main.py`: 30 messages per minute per phone.
```python
permitido = await redis_svc.verificar_rate_limit(
    f"ratelimit:{sender_phone}",
    limite=30,
    ventana_ttl=60,
)
```

## Security

### HMAC Webhook Verification
All POST `/webhook` requests pass through HMAC-SHA256 verification in `app/middleware.py:verify_signature()`.
- Uses `WSP_APP_SECRET`
- Compares signature in `X-Hub-Signature-256` header
- Rejects requests with invalid signature

## Health Check

`GET /health` returns service status:
```json
{
  "status": "ok",  // or "degraded"
  "servicios": {
    "redis": "ok",
    "http_client": "ok"
  }
}
```

Status 200 if ok, 503 if degraded.

## Language

The codebase, comments, user-facing messages, and commit messages are in **Spanish**. Follow this convention.
