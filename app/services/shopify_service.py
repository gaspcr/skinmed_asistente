"""
Servicio para consultar la API Admin de Shopify.

Usa la API REST de Shopify para obtener información de productos,
inventario, precios y metadatos.  Se reutiliza el cliente httpx
compartido del proyecto.
"""
import html
import logging
import re
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.services import http as http_svc

logger = logging.getLogger(__name__)

# Versión de la API de Shopify Admin REST
_API_VERSION = "2025-01"


def _strip_html(text: str) -> str:
    """Elimina tags HTML y decodifica entidades."""
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = html.unescape(clean)
    return re.sub(r"\s+", " ", clean).strip()


def _format_clp(price_str: str) -> str:
    """Convierte string de precio Shopify a formato legible CLP."""
    try:
        price = int(float(price_str))
        return f"${price:,}".replace(",", ".")
    except (ValueError, TypeError):
        return price_str


# ──────────────────────────────────────────────
# API helpers
# ──────────────────────────────────────────────

def _base_url() -> str:
    settings = get_settings()
    domain = settings.SHOPIFY_STORE_DOMAIN.strip().rstrip("/")
    return f"https://{domain}/admin/api/{_API_VERSION}"


def _headers() -> Dict[str, str]:
    settings = get_settings()
    return {
        "X-Shopify-Access-Token": settings.SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json",
    }


# ──────────────────────────────────────────────
# Funciones públicas
# ──────────────────────────────────────────────

async def buscar_productos(query: str, limit: int = 5) -> str:
    """
    Busca productos en Shopify por título.
    Retorna un string formateado para el LLM.
    """
    settings = get_settings()
    if not settings.SHOPIFY_STORE_DOMAIN or not settings.SHOPIFY_ACCESS_TOKEN:
        return "La integración con Shopify no está configurada (faltan SHOPIFY_STORE_DOMAIN o SHOPIFY_ACCESS_TOKEN)."

    client = http_svc.get_client()
    url = f"{_base_url()}/products.json"

    params = {
        "title": query,
        "limit": limit,
        "fields": "id,title,vendor,body_html,tags,variants,handle,status,published_at",
    }

    try:
        resp = await client.get(url, params=params, headers=_headers(), timeout=15.0)

        if resp.status_code != 200:
            logger.error("Error Shopify API: HTTP %d — %s", resp.status_code, resp.text[:300])
            return f"Error al consultar Shopify: HTTP {resp.status_code}"

        data = resp.json()
        products = data.get("products", [])

        if not products:
            # Intentar búsqueda más amplia filtrando manualmente
            params_broad = {"limit": 50, "fields": "id,title,vendor,body_html,tags,variants,handle,status,published_at"}
            resp2 = await client.get(url, params=params_broad, headers=_headers(), timeout=15.0)
            if resp2.status_code == 200:
                all_products = resp2.json().get("products", [])
                query_lower = query.lower()
                products = [
                    p for p in all_products
                    if query_lower in p.get("title", "").lower()
                    or query_lower in p.get("vendor", "").lower()
                    or query_lower in p.get("tags", "").lower()
                    or any(query_lower in v.get("sku", "").lower() for v in p.get("variants", []))
                ][:limit]

        if not products:
            return f"No se encontraron productos con el término '{query}' en la tienda."

        return _format_products(products)

    except Exception as e:
        logger.error("Error consultando Shopify: %s", e)
        return f"Error al consultar la tienda Shopify: {e}"


async def obtener_producto_por_id(product_id: int) -> str:
    """Obtiene un producto específico por su ID de Shopify."""
    settings = get_settings()
    if not settings.SHOPIFY_STORE_DOMAIN or not settings.SHOPIFY_ACCESS_TOKEN:
        return "La integración con Shopify no está configurada."

    client = http_svc.get_client()
    url = f"{_base_url()}/products/{product_id}.json"

    try:
        resp = await client.get(url, headers=_headers(), timeout=15.0)
        if resp.status_code != 200:
            return f"No se encontró el producto con ID {product_id}."

        product = resp.json().get("product", {})
        return _format_products([product])

    except Exception as e:
        logger.error("Error obteniendo producto %s: %s", product_id, e)
        return f"Error al obtener el producto: {e}"


async def obtener_inventario(inventory_item_ids: List[int]) -> Dict[int, int]:
    """
    Obtiene los niveles de inventario para una lista de inventory_item_ids.
    Retorna un dict {inventory_item_id: available_quantity}.
    """
    settings = get_settings()
    if not settings.SHOPIFY_STORE_DOMAIN or not settings.SHOPIFY_ACCESS_TOKEN:
        return {}

    client = http_svc.get_client()
    url = f"{_base_url()}/inventory_levels.json"

    # Shopify acepta hasta 50 IDs por request
    ids_str = ",".join(str(i) for i in inventory_item_ids[:50])
    params = {"inventory_item_ids": ids_str}

    try:
        resp = await client.get(url, params=params, headers=_headers(), timeout=15.0)
        if resp.status_code != 200:
            logger.error("Error inventory_levels: HTTP %d", resp.status_code)
            return {}

        levels = resp.json().get("inventory_levels", [])
        result = {}
        for level in levels:
            item_id = level.get("inventory_item_id")
            available = level.get("available", 0)
            # Sumar si hay múltiples ubicaciones
            result[item_id] = result.get(item_id, 0) + (available or 0)
        return result

    except Exception as e:
        logger.error("Error obteniendo inventario: %s", e)
        return {}


# ──────────────────────────────────────────────
# Formateo
# ──────────────────────────────────────────────

def _format_products(products: List[Dict[str, Any]]) -> str:
    """Formatea una lista de productos para el LLM."""
    lines = [f"PRODUCTOS ENCONTRADOS EN LA TIENDA ({len(products)} resultado{'s' if len(products) > 1 else ''}):\n"]

    for p in products:
        title = p.get("title", "Sin nombre")
        vendor = p.get("vendor", "—")
        description = _strip_html(p.get("body_html", ""))
        tags = p.get("tags", "")
        handle = p.get("handle", "")
        status = p.get("status", "unknown")
        is_published = p.get("published_at") is not None

        lines.append(f"━━━ {title} ━━━")
        lines.append(f"  Marca: {vendor}")
        if description:
            # Truncar descripción larga
            desc_short = description[:300] + ("..." if len(description) > 300 else "")
            lines.append(f"  Descripción: {desc_short}")
        if tags:
            lines.append(f"  Tags: {tags}")
        lines.append(f"  Estado: {'Publicado' if is_published else 'No publicado'}")
        if handle:
            lines.append(f"  URL: https://tienda.skinmed.cl/products/{handle}")

        variants = p.get("variants", [])
        if variants:
            lines.append(f"  Variantes ({len(variants)}):")
            for v in variants:
                v_title = v.get("title", "")
                price = _format_clp(v.get("price", "0"))
                compare_price = v.get("compare_at_price")
                sku = v.get("sku", "—")
                inv_mgmt = v.get("inventory_management")
                inv_qty = v.get("inventory_quantity")

                label = f"    • {v_title}" if v_title and v_title != "Default Title" else "    •"
                label += f" | Precio: {price} CLP"
                if compare_price:
                    label += f" (antes: {_format_clp(compare_price)})"
                label += f" | SKU: {sku}"
                if inv_mgmt == "shopify" and inv_qty is not None:
                    label += f" | Stock: {inv_qty} unidades"
                lines.append(label)

        lines.append("")

    return "\n".join(lines)
