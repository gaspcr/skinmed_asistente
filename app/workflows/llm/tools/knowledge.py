"""
Tool para consultar la base de conocimientos (documentos de la clínica) usando RAG.
"""
from typing import Any, Dict
import logging

from app.services import vector_db_service

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Definición OpenAI function calling
# ──────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "consultar_documentos_clinica",
        "description": (
            "Busca información en los documentos internos y folletos médicos de la Clínica SkinMed. "
            "Usa esta función SIEMPRE que el doctor pregunte sobre temas médicos, folletos de pacientes, "
            "protocolos, tratamientos específicos o información de la clínica que no sepas."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pregunta": {
                    "type": "string",
                    "description": (
                        "La pregunta detallada o concepto a buscar en la base de datos de la clínica. "
                        "Ejemplo: '¿Cuáles son los tratamientos para la rosácea?' o 'información sobre Acné'."
                    ),
                },
            },
            "required": ["pregunta"],
        },
    },
}

# ──────────────────────────────────────────────
# Handler
# ──────────────────────────────────────────────

async def handle(user, phone: str, arguments: Dict[str, Any]) -> str:
    """Ejecuta la búsqueda semántica en la base de conocimientos."""
    pregunta = arguments.get("pregunta")
    
    if not pregunta:
        return "Error: debes proporcionar una pregunta para buscar en los documentos."
        
    logger.info(f"RAG Search solicitada por {user.name} ({phone}): '{pregunta}'")
    
    # Realizar la búsqueda usando el vector_db_service
    resultados = await vector_db_service.search_knowledge_base(query=pregunta, top_k=3)
    
    return resultados
