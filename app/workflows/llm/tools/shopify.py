"""
Tool para consultar productos de la tienda Shopify de SkinMed.

Permite a doctores y managers buscar información de productos
(stock, precio, descripción, marca) en tiempo real.
"""
from typing import Any, Dict
import logging

from app.services import shopify_service

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Definición OpenAI function calling
# ──────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "consultar_producto_tienda",
        "description": (
            "Busca productos en la tienda online de la Clínica SkinMed. "
            "Usa esta función cuando el usuario pregunte sobre productos de la tienda, "
            "stock disponible, precios, marcas, o información de algún producto cosmético o dermatológico "
            "que se venda en la clínica. Puedes buscar por nombre del producto, marca (ej. SkinCeuticals, "
            "CeraVe, La Roche-Posay), o tipo de producto (ej. serum, protector solar). "
            "Si el usuario quiere ver TODO el catálogo o comparar productos de distintas categorías, "
            "pasa busqueda vacío (\"\")."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "busqueda": {
                    "type": "string",
                    "description": (
                        "Término de búsqueda: nombre del producto, marca, o tipo. "
                        "Ejemplos: 'C E Ferulic', 'SkinCeuticals', 'protector solar', 'serum vitamina c'. "
                        "Dejar vacío para obtener el catálogo completo."
                    ),
                },
            },
            "required": [],
        },
    },
}

# ──────────────────────────────────────────────
# Handler
# ──────────────────────────────────────────────

async def handle(user, phone: str, arguments: Dict[str, Any]) -> str:
    """Ejecuta la búsqueda de productos en Shopify."""
    busqueda = arguments.get("busqueda", "")

    logger.info("Shopify search solicitada por %s (%s): '%s'", user.name, phone, busqueda or "<catálogo completo>")

    return await shopify_service.buscar_productos(query=busqueda)
