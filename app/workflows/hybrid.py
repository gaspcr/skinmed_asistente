"""
Registro del rol medico_gerencia bajo el workflow de gerencia.
Los usuarios con este perfil hibrido usan el ManagerWorkflow,
que incluye la opcion de acceder al perfil de medico.
"""
from app.workflows.role_registry import register_workflow
from app.workflows.manager import ManagerWorkflow


@register_workflow("medico_gerencia")
class HybridManagerWorkflow(ManagerWorkflow):
    """Hereda toda la funcionalidad del ManagerWorkflow."""
    pass
