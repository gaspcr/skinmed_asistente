"""
Tool LLM para iniciar el flujo de actualización masiva de precios Shopify.
"""
from typing import Any, Dict

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "iniciar_actualizacion_precios",
        "description": (
            "Inicia el flujo para actualizar masivamente los precios de la tienda Shopify vía CSV. "
            "Úsala cuando el usuario quiera actualizar, cambiar o modificar precios de productos de la tienda. "
            "Después de llamar esta función, el sistema pedirá al usuario que envíe el archivo CSV."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


async def handle(user, phone: str, arguments: Dict[str, Any]) -> str:
    from app.workflows import state as workflow_state
    await workflow_state.set_state(phone, "waiting_for_csv")
    return (
        "Flujo iniciado correctamente. Pide al usuario que envíe el archivo CSV "
        "exportado desde el panel de administración de Shopify. "
        "El archivo debe contener las columnas 'Variant SKU' y 'Variant Price'."
    )
