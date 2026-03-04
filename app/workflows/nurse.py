"""
Workflow de enfermería — redirige al workflow de gerencia (manager),
ya que ambos roles comparten la misma funcionalidad.
"""
from app.workflows.role_registry import register_workflow
from app.workflows.manager import ManagerWorkflow


@register_workflow("enfermeria")
class NurseWorkflow(ManagerWorkflow):
    """Hereda toda la funcionalidad del ManagerWorkflow."""
    pass
