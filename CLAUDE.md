# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WhatsApp Business bot for Clínica SkinMed. Provides role-based assistants (doctor, manager, nurse) for managing medical consultations, schedules, and clinic operations via WhatsApp. The bot authenticates users by phone number against FileMaker and routes messages to role-specific workflow handlers.

## Commands

```bash
# Create and activate mamba environment
mamba create -n skinmed python=3.12 -y
mamba activate skinmed

# Install dependencies
pip install -r requirements.txt

# Start Redis (required)
redis-server

# Run development server
ENVIRONMENT=development uvicorn main:app --reload

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
- `ENVIRONMENT` — Execution environment: `development`, `staging`, `production` (default: `production`)
- `RATE_LIMIT_MAX` — Max messages per rate limit window (default: `30`)
- `RATE_LIMIT_WINDOW` — Rate limit window in seconds (default: `60`)
- `MAX_MESSAGE_LENGTH` — Max accepted text message length (default: `500`)

All required vars are validated at startup via `app/config.py` using `pydantic-settings`. The app refuses to start if any are missing.

## Architecture

**Entry point:** `main.py` — FastAPI app with factory pattern (`create_app()`), lifespan (Redis init, HTTP client init, config validation, logging setup) and endpoints:
- `GET /health` — Simple liveness check (for Railway/monitoring)
- `GET /health/ready` — Deep readiness check (Redis + HTTP client + FileMaker connectivity)
- `GET /webhook` — WhatsApp webhook verification
- `POST /webhook` — Incoming messages with HMAC-SHA256 signature verification, message deduplication, and rate limiting

**Message flow:**
1. WhatsApp webhook delivers message → HMAC signature verified (`app/middleware.py`) → Pydantic validation (`app/schemas.py`)
2. Message deduplication via Redis (1 hour TTL) prevents duplicate processing
3. `AuthService` looks up user by phone number (5-min cache in Redis) via FileMaker
4. `get_workflow_handler(user.role)` dispatches to the correct role handler
5. Unsupported message types (image, audio, etc.) get a friendly rejection
6. Text messages are sanitized (length-limited) before processing
7. Workflow handler processes the message and responds via `WhatsAppService`

**Key layers:**
- `app/config.py` — Centralized config with `pydantic-settings` (`Settings` class with `get_settings()`)
- `app/services/redis.py` — Async Redis client for state management, caching, and rate limiting
- `app/services/http.py` — Shared httpx AsyncClient singleton with connection pooling
- `app/services/filemaker.py` — FileMaker Data API client with 14-min token caching, auto 401-retry, retry with backoff, and circuit breaker protection
- `app/services/whatsapp.py` — WhatsApp Business API client (text, templates) with retry on 5xx/connection errors
- `app/middleware.py` — HMAC-SHA256 webhook signature verification + `SecurityHeadersMiddleware` (HSTS, X-Frame-Options, nosniff, XSS protection)
- `app/auth/` — User model and auth service with phone-based lookup (cached in Redis)
- `app/workflows/` — Role-based handlers dispatched via decorator registration
- `app/workflows/state.py` — Unified workflow state management (get_state/set_state/clear_state)
- `app/formatters/agenda.py` — Formats appointment data for WhatsApp display
- `app/exceptions.py` — Custom exceptions (`ServicioNoDisponibleError`, `FileMakerAuthError`)
- `app/utils/retry.py` — `con_reintentos()` async retry with exponential backoff
- `app/utils/circuit_breaker.py` — `CircuitBreaker` async context manager (closed/open/half-open states)
- `app/logging_config.py` — Structured logging: JSON in production, human-readable in development

## Configuration

Config uses `pydantic-settings` with automatic `.env` file loading. Always access via:

```python
from app.config import get_settings

settings = get_settings()
value = settings.FM_HOST
```

**Never** import config values at module level. Use `get_settings()` inside functions/methods for lazy loading.

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

### Workflow State Management

Use `app/workflows/state.py` for multi-step workflows:

```python
from app.workflows import state as workflow_state

# Save state
await workflow_state.set_state(phone, "waiting_for_date", data={"extra": "info"})

# Get current step
step = await workflow_state.get_step(phone)

# Get additional data
data = await workflow_state.get_data(phone)

# Clear state
await workflow_state.clear_state(phone)
```

## Circuit Breaker

FileMaker calls are protected by a circuit breaker (`app/utils/circuit_breaker.py`):
- Opens after 5 consecutive failures
- Stays open for 30 seconds (rejects calls immediately)
- Transitions to half-open to test recovery
- Returns to closed on success

```python
from app.utils.circuit_breaker import CircuitBreaker, CircuitBreakerAbierto

cb = CircuitBreaker("servicio", umbral_fallos=5, timeout_recuperacion=30)
async with cb:
    resultado = await llamada_externa()
```

## Services

### `RedisService` (`services/redis.py`)
- Initialized/closed in FastAPI lifespan
- Used for caching (FM tokens, users), multi-step state, rate limiting, and message deduplication
- TTLs: FM tokens 14 min, users 5 min, workflow state 30 min, processed messages 1 hour

### `HTTPService` (`services/http.py`)
- Shared httpx AsyncClient singleton
- Automatic connection pooling
- Managed in FastAPI lifespan

### `FileMakerService` (`services/filemaker.py`)
- Auto-retry on 401 (refreshes token and retries)
- Retry with exponential backoff on connection errors
- Circuit breaker protection (opens after 5 failures, 30s recovery)
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
    backoff_base=1.0,
    nombre_operacion="descripcion"
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

Production uses JSON format. Development uses human-readable format (controlled by `ENVIRONMENT` env var).

## Security

### HMAC Webhook Verification
All POST `/webhook` requests pass through HMAC-SHA256 verification in `app/middleware.py:verify_signature()`.

### Security Headers
`SecurityHeadersMiddleware` adds to all responses:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Strict-Transport-Security` (HSTS)
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Cache-Control: no-store`

### Message Deduplication
Webhook messages are deduplicated via Redis (msg ID stored 1 hour) to prevent duplicate processing.

### Input Sanitization
Text messages are truncated to `MAX_MESSAGE_LENGTH` (default 500 chars). User input is never reflected directly in responses.

### Production Mode
In production, FastAPI docs (`/docs`, `/redoc`, `/openapi.json`) are disabled.

## Docker

```bash
# Development with docker-compose
docker-compose up --build

# Production build
docker build -t skinmed-bot .
docker run -p 8000:8000 --env-file .env skinmed-bot
```

The Dockerfile uses multi-stage build and runs as non-root user.

## Health Checks

- `GET /health` — Simple liveness (always returns 200)
- `GET /health/ready` — Readiness check with service statuses:

```json
{
  "status": "ok",
  "servicios": {
    "redis": "ok",
    "http_client": "ok",
    "filemaker": "ok"
  }
}
```

Status 200 if ok, 503 if degraded.

## Rate Limiting

Configured via env vars: `RATE_LIMIT_MAX` messages per `RATE_LIMIT_WINDOW` seconds per phone.

## Language

The codebase, comments, user-facing messages, and commit messages are in **Spanish**. Follow this convention.
