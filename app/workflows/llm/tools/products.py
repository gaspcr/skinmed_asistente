"""
Tools LLM para el catalogo Shopify:

- `buscar_productos`: busqueda hibrida (semantica + FTS) por intencion.
- `listar_productos`: listado por filtros exactos (vendor/tipo/tag/stock).

Ambas devuelven productos en formato compacto con precio/stock en vivo
y metafields editoriales truncados.
"""
import json
import logging
from typing import Any, Dict, List, Optional

from app.services import products as products_svc
from app.services import shopify_service

logger = logging.getLogger(__name__)


_METAFIELD_VALUE_MAX_CHARS = 600


def _truncate_metafields(metafields: Dict[str, Any]) -> Dict[str, str]:
    """Recorta cada valor de metafield a `_METAFIELD_VALUE_MAX_CHARS` para
    mantener el payload del tool result acotado."""
    out: Dict[str, str] = {}
    for k, v in metafields.items():
        if v is None:
            continue
        s = str(v)
        if len(s) > _METAFIELD_VALUE_MAX_CHARS:
            s = s[:_METAFIELD_VALUE_MAX_CHARS].rstrip() + "…"
        out[k] = s
    return out


async def _enrich_and_compact(
    resultados: List[Dict[str, Any]],
    in_stock_only: bool = False,
) -> List[Dict[str, Any]]:
    """Consulta datos live a Shopify y arma el dict compacto por producto.

    Si `in_stock_only=True`, filtra el resultado a productos cuyo stock
    (live cuando esta disponible) es positivo.
    """
    ids = [r["id"] for r in resultados if r.get("id")]
    live_data: Dict[str, Dict[str, Any]] = {}
    if ids:
        try:
            live_data = await shopify_service.fetch_live_pricing_inventory(ids)
        except Exception:
            logger.exception("[PRODUCTS_TOOL] Error consultando datos live de Shopify")
            live_data = {}

    compactos: List[Dict[str, Any]] = []
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

        if in_stock_only and not in_stock:
            continue

        compactos.append({
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
            "metafields": _truncate_metafields(r.get("metafields") or {}),
        })
    return compactos


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
            "buscando algo especifico de la tienda. Devuelve los productos "
            "ordenados por relevancia, con precio y stock consultados en vivo "
            "a Shopify (campo `total_inventory` con unidades disponibles y "
            "`in_stock` booleano). Si `data_freshness` es 'cached' los datos "
            "de precio/stock pueden estar desactualizados; si es 'live' son "
            "actuales. Nunca asumas falta de stock si `data_freshness` es 'cached'. "
            "Cada producto trae un dict `metafields` con contenido editorial de "
            "Shopify (claves tipicas: 'custom.beneficios', 'custom.modo_de_uso', "
            "'custom.ingredientes', 'custom.ingrediente_clave_1' + 'descripcion_*', "
            "'shopify.skin-care-effect', etc.). Cuando el usuario pregunte por "
            "modo de uso, beneficios, ingredientes, indicaciones, tipo de piel "
            "u otra info editorial, lee `metafields` antes de responder. NO "
            "inventes contenido editorial: si la clave no esta presente, dilo."
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
                    "description": (
                        "Maximo de productos a retornar tras la fusion RRF "
                        "(semantica + FTS). Default 5. Maximo 20: el pool de "
                        "candidatos k-NN de cada rama es 20, asi que pedir mas "
                        "no devuelve mas relevancia, solo ruido. Si necesitas "
                        "TODOS los productos de una marca/tipo/tag, usa "
                        "listar_productos — ese tool no tiene tope."
                    ),
                    "minimum": 1,
                    "maximum": 20,
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
    # Tope de 20: coincide con el _CANDIDATE_POOL de cada rama (semantica y FTS).
    # Pedir mas no devuelve mas relevancia porque el pool de RRF tiene <= 40
    # ids unicos y el scoring decae rapido tras la posicion 20.
    limit = max(1, min(limit, 20))

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

    productos_compactos = await _enrich_and_compact(resultados)

    return json.dumps({
        "query": query,
        "total": len(productos_compactos),
        "productos": productos_compactos,
    }, ensure_ascii=False)


# ──────────────────────────────────────────────
# listar_productos: filtros exactos, sin relevancia semantica
# ──────────────────────────────────────────────

LISTAR_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "listar_productos",
        "description": (
            "Lista productos del catalogo Shopify aplicando filtros exactos "
            "(marca/vendor, tipo, tag, stock). NO usa relevancia semantica: "
            "devuelve TODOS los que coinciden con los filtros, hasta `limit`. "
            "Usala cuando el usuario pida 'todos los X', '¿que productos hay "
            "de la marca Y?', 'productos con tag Z', 'lista de cremas con "
            "stock', etc. — preguntas de listado/inventario, no de busqueda. "
            "Para preguntas vagas o por intencion ('algo para acne', 'que me "
            "recomiendas para piel seca') usa buscar_productos en su lugar. "
            "Requiere AL MENOS uno de: vendor, product_type, tag. Devuelve "
            "precio y stock en vivo y metafields editoriales (mismo shape que "
            "buscar_productos)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "vendor": {
                    "type": "string",
                    "description": (
                        "Marca exacta, case-insensitive. Ej: 'SkinCeuticals', "
                        "'La Roche-Posay'."
                    ),
                },
                "product_type": {
                    "type": "string",
                    "description": "Tipo de producto exacto, case-insensitive.",
                },
                "tag": {
                    "type": "string",
                    "description": (
                        "Una etiqueta/tag, case-insensitive. Ej: 'Antiedad', "
                        "'Piel Grasa', 'Vitamina C'."
                    ),
                },
                "in_stock_only": {
                    "type": "boolean",
                    "description": (
                        "Si true, filtra solo productos con stock > 0 (segun "
                        "consulta en vivo a Shopify). Default false."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "Maximo de productos a retornar. Default 1000 "
                        "(efectivamente sin limite para el catalogo actual). "
                        "Pasa un numero menor SOLO si el usuario pide "
                        "explicitamente pocos resultados ('dame 5', 'top 10'). "
                        "Por defecto, devuelve TODOS los que matchean."
                    ),
                    "minimum": 1,
                },
            },
        },
    },
}


async def handle_listar(user, phone: str, arguments: Dict[str, Any]) -> str:
    """Lista productos por filtros exactos, enriquece con datos live y serializa."""
    vendor: Optional[str] = (arguments.get("vendor") or "").strip() or None
    product_type: Optional[str] = (arguments.get("product_type") or "").strip() or None
    tag: Optional[str] = (arguments.get("tag") or "").strip() or None
    in_stock_only = bool(arguments.get("in_stock_only", False))

    try:
        limit = int(arguments.get("limit", 1000))
    except (TypeError, ValueError):
        limit = 1000
    # Sin tope artificial: 1000 cubre el catalogo completo en la practica.
    limit = max(1, min(limit, 1000))

    if not any([vendor, product_type, tag]):
        return (
            "Error: listar_productos requiere al menos un filtro "
            "(vendor, product_type o tag). Si la consulta es vaga o por "
            "intencion, usa buscar_productos en su lugar."
        )

    try:
        resultados = await products_svc.list_products_by_filter(
            vendor=vendor,
            product_type=product_type,
            tag=tag,
            limit=limit,
        )
    except RuntimeError as e:
        logger.error("[LISTAR_PRODUCTOS] Error de infraestructura: %s", e)
        return (
            "Error: el catalogo de productos no esta disponible en este momento. "
            "Pide al usuario que intente nuevamente mas tarde."
        )
    except Exception:
        logger.exception(
            "[LISTAR_PRODUCTOS] Error inesperado vendor=%s type=%s tag=%s",
            vendor, product_type, tag,
        )
        return "Error inesperado al listar productos. Reporta al usuario que hubo un fallo."

    filtros = {
        "vendor": vendor,
        "product_type": product_type,
        "tag": tag,
        "in_stock_only": in_stock_only,
    }

    if not resultados:
        return json.dumps({
            "filtros": filtros,
            "total": 0,
            "productos": [],
            "mensaje": "No hay productos que cumplan esos filtros.",
        }, ensure_ascii=False)

    productos_compactos = await _enrich_and_compact(resultados, in_stock_only=in_stock_only)

    return json.dumps({
        "filtros": filtros,
        "total": len(productos_compactos),
        "productos": productos_compactos,
    }, ensure_ascii=False)
