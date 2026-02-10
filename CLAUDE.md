# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WhatsApp Business bot for Clínica SkinMed. Provides role-based assistants (doctor, manager, nurse) for managing medical consultations, schedules, and clinic operations via WhatsApp. The bot authenticates users by phone number against FileMaker and routes messages to role-specific workflow handlers.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server
uvicorn main:app --reload

# Verify role registration
python verify_roles.py
```

No test suite or linter is configured.

## Environment Variables

Required in `.env`:
- `FM_USER`, `FM_PASS` — FileMaker credentials
- `WSP_TOKEN` — WhatsApp Business API token
- `WSP_PHONE_ID` — WhatsApp phone number ID
- `WSP_VERIFY_TOKEN` — Webhook verification token
- `WSP_APP_SECRET` — WhatsApp App Secret for HMAC-SHA256 webhook signature verification

Optional:
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

**Entry point:** `main.py` — FastAPI app with lifespan (Redis init, HTTP client init, config validation, logging setup) and three endpoints: `GET /health` (service health check), `GET /webhook` (WhatsApp verification) and `POST /webhook` (incoming messages with HMAC-SHA256 signature verification and rate limiting).

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

Currently registered: `medico` (implemented), `gerencia` (stub), `enfermeria` (stub).

To add a new role: create a workflow class in `app/workflows/`, decorate with `@register_workflow("role_name")`, and import it in `app/workflows/__init__.py`.

## Language

The codebase, comments, user-facing messages, and commit messages are in Spanish. Follow this convention.
