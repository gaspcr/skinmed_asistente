"""
Configuración LLM por rol.

Define el dataclass RoleLLMConfig y un registry para que cada rol
registre su configuración LLM de forma declarativa.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Tipo para un handler de tool:
# async def handler(user, phone, arguments) -> str
ToolHandler = Callable[..., Awaitable[str]]

# Tipo para el constructor de contexto del prompt:
# def builder(user) -> dict[str, str]
PromptContextBuilder = Callable[..., Dict[str, str]]


@dataclass(frozen=True)
class RoleLLMConfig:
    """Configuración LLM específica de un rol.

    Atributos:
        role_name:              Nombre del rol (debe coincidir con role_registry).
        system_prompt_template: Template del system prompt con {placeholders}.
        tools:                  Definiciones de tools en formato OpenAI function calling.
        tool_handlers:          Mapeo nombre_funcion -> función async que la ejecuta.
        prompt_context_builder: Función que recibe (user) y retorna dict con
                                los valores para los placeholders del prompt.
    """
    role_name: str
    system_prompt_template: str
    tools: List[Dict[str, Any]]
    tool_handlers: Dict[str, ToolHandler]
    prompt_context_builder: PromptContextBuilder


# ──────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────

_LLM_CONFIGS: Dict[str, RoleLLMConfig] = {}


def register_llm_config(config: RoleLLMConfig) -> None:
    """Registra la configuración LLM de un rol."""
    key = config.role_name.lower().strip()
    if key in _LLM_CONFIGS:
        raise ValueError(
            f"Config LLM ya registrada para rol '{key}'. "
            f"Existente: {_LLM_CONFIGS[key].role_name}"
        )
    _LLM_CONFIGS[key] = config
    logger.info("[LLM_CONFIG] Configuración LLM registrada para rol '%s'", key)


def get_llm_config(role: str) -> Optional[RoleLLMConfig]:
    """Obtiene la configuración LLM para un rol, o None si no está registrada."""
    return _LLM_CONFIGS.get(role.lower().strip())


def get_registered_llm_roles() -> List[str]:
    """Retorna los roles que tienen configuración LLM registrada."""
    return list(_LLM_CONFIGS.keys())
