"""
Sistema de registro automático de workflows por rol.
Permite agregar nuevos roles sin modificar múltiples archivos.
"""
from typing import Dict, Type, Optional
from app.workflows.base import WorkflowHandler

# Registry global de workflows por rol
_WORKFLOW_REGISTRY: Dict[str, Type[WorkflowHandler]] = {}


def register_workflow(role_name: str):
    """
    Decorador para auto-registrar workflows por rol.
    
    Uso:
        @register_workflow("doctor")
        class DoctorWorkflow(WorkflowHandler):
            ...
    
    Args:
        role_name: Nombre del rol (debe coincidir con el campo ROL en FileMaker)
    """
    def decorator(workflow_class: Type[WorkflowHandler]):
        # Normalizar a lowercase para consistencia
        normalized_role = role_name.lower().strip()
        
        if normalized_role in _WORKFLOW_REGISTRY:
            raise ValueError(
                f"Workflow ya registrado para rol '{normalized_role}'. "
                f"Clase existente: {_WORKFLOW_REGISTRY[normalized_role].__name__}"
            )
        
        _WORKFLOW_REGISTRY[normalized_role] = workflow_class
        return workflow_class
    
    return decorator


def get_workflow_handler(role: str) -> Optional[WorkflowHandler]:
    """
    Obtiene una instancia del workflow handler para un rol específico.
    
    Args:
        role: Nombre del rol
        
    Returns:
        Instancia del WorkflowHandler o None si el rol no está registrado
    """
    normalized_role = role.lower().strip()
    workflow_class = _WORKFLOW_REGISTRY.get(normalized_role)
    
    if workflow_class:
        return workflow_class()
    
    return None


def get_registered_roles() -> list[str]:
    """
    Retorna lista de todos los roles registrados.
    Útil para debugging y validación.
    """
    return list(_WORKFLOW_REGISTRY.keys())


def is_role_registered(role: str) -> bool:
    """
    Verifica si un rol está registrado.
    
    Args:
        role: Nombre del rol
        
    Returns:
        True si el rol está registrado, False en caso contrario
    """
    normalized_role = role.lower().strip()
    return normalized_role in _WORKFLOW_REGISTRY
