"""
Tool LLM `buscar_productos`: busqueda hibrida (semantica + FTS) sobre el
catalogo Shopify almacenado en Postgres.

Devuelve al LLM una lista de productos candidatos en formato compacto
(no envia por WhatsApp directamente) para que el modelo redacte la respuesta
final en el tono adecuado.
"""
import json
import logging
from typing import Any, Dict

from app.services import products as products_svc
from app.services import shopify_service

logger = logging.getLogger(__name__)


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "buscar_productos",
        "description": (
            "Busca productos del catalogo de la tienda Shopify de la clinica "
            "usando lenguaje natural. Maneja sinonimos, descripciones vagas y "
            "matches parciales (ej: '¿tienen algo para acne?', "
            "'crema con ácido hialuronico', 'protector solar para piel grasa'). "
            "Usala cuando el usuario pregunte si existe un producto o este "
            "buscando algo especifico de la tienda. Devuelve hasta `limit` "
            "productos ordenados por relevancia, con precio y stock consultados "
            "en vivo a Shopify (campo `total_inventory` con unidades disponibles "
            "y `in_stock` booleano). Si `data_freshness` es 'cached' los datos "
            "de precio/stock pueden estar desactualizados; si es 'live' son "
            "actuales. Nunca asumas falta de stock si `data_freshness` es 'cached'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Consulta en lenguaje natural. Puede incluir intencion "
                        "(\"para acne\"), atributos (\"sin fragancia\"), o "
                        "nombre/marca parcial (\"la crema rosa de SkinCeuticals\")."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Numero maximo de productos a retornar. Default 5, maximo 10.",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        },
    },
}


async def handle(user, phone: str, arguments: Dict[str, Any]) -> str:
    """Ejecuta la busqueda y serializa los resultados como JSON para el LLM."""
    query = (arguments.get("query") or "").strip()
    limit_raw = arguments.get("limit", 5)

    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, 10))

    if not query:
        return "Error: parametro 'query' vacio. Pide al usuario que precise que producto busca."

    try:
        resultados = await products_svc.search_products_hybrid(query, limit=limit)
    except RuntimeError as e:
        # Postgres no inicializado
        logger.error("[BUSCAR_PRODUCTOS] Error de infraestructura: %s", e)
        return (
            "Error: el catalogo de productos no esta disponible en este momento. "
            "Pide al usuario que intente nuevamente mas tarde."
        )
    except Exception:
        logger.exception("[BUSCAR_PRODUCTOS] Error inesperado para query='%s'", query)
        return "Error inesperado al buscar productos. Reporta al usuario que hubo un fallo."

    if not resultados:
        return json.dumps({
            "query": query,
            "total": 0,
            "productos": [],
            "mensaje": "No se encontraron productos relevantes para esta consulta.",
        }, ensure_ascii=False)

    ids = [r["id"] for r in resultados if r.get("id")]
    live_data: Dict[str, Dict[str, Any]] = {}
    try:
        live_data = await shopify_service.fetch_live_pricing_inventory(ids)
    except Exception:
        logger.exception("[BUSCAR_PRODUCTOS] Error consultando datos live de Shopify")
        live_data = {}

    productos_compactos = []
    for r in resultados:
        live = live_data.get(r["id"])
        if live is not None:
            price_range = live["price_range"]
            in_stock = live["in_stock"]
            total_inventory = live["total_inventory"]
            data_freshness = "live"
        else:
            price_range = r["price_range"]
            in_stock = r["in_stock"]
            total_inventory = None
            data_freshness = "cached"

        productos_compactos.append({
            "id": r["id"],
            "title": r["title"],
            "vendor": r["vendor"],
            "product_type": r["product_type"],
            "tags": r["tags"],
            "price_range": price_range,
            "in_stock": in_stock,
            "total_inventory": total_inventory,
            "status": r["status"],
            "shopify_url": r["shopify_url"],
            "data_freshness": data_freshness,
        })

    return json.dumps({
        "query": query,
        "total": len(productos_compactos),
        "productos": productos_compactos,
    }, ensure_ascii=False)
