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
from app.services import redis as redis_svc

logger = logging.getLogger(__name__)

# Versión de la API de Shopify Admin REST
_API_VERSION = "2025-01"

_CATALOG_CACHE_KEY = "shopify:catalog"
_CATALOG_TTL = 1200  # 20 minutos


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

async def _obtener_catalogo() -> List[Dict[str, Any]]:
    """
    Retorna el catálogo completo de Shopify. Usa Redis como caché (TTL 20 min).
    En caso de cache miss, pagina toda la tienda y guarda el resultado.
    """
    cached = await redis_svc.get_json(_CATALOG_CACHE_KEY)
    if cached is not None:
        logger.debug("[SHOPIFY] Catálogo desde caché Redis (%d productos)", len(cached))
        return cached

    client = http_svc.get_client()
    url = f"{_base_url()}/products.json"
    fields = "id,title,vendor,body_html,tags,variants,handle,status,published_at"
    all_products: List[Dict[str, Any]] = []
    since_id = 0

    logger.info("[SHOPIFY] Paginando catálogo completo...")
    while True:
        resp = await client.get(url, params={
            "limit": 250, "fields": fields, "since_id": since_id,
        }, headers=_headers(), timeout=30.0)
        if resp.status_code != 200:
            logger.error("[SHOPIFY] Error paginando catálogo: HTTP %d", resp.status_code)
            break
        page = resp.json().get("products", [])
        if not page:
            break
        all_products.extend(page)
        if len(page) < 250:
            break
        since_id = page[-1]["id"]

    logger.info("[SHOPIFY] Catálogo cargado: %d productos. Guardando en Redis.", len(all_products))
    await redis_svc.set_json(_CATALOG_CACHE_KEY, all_products, ttl=_CATALOG_TTL)
    return all_products


async def buscar_productos(query: str) -> str:
    """
    Busca productos en el catálogo Shopify (cacheado en Redis).
    Sin query vacío retorna todo el catálogo. Filtra in-memory por título,
    vendor, tags y SKU. Sin límite artificial de resultados.
    Retorna un string formateado para el LLM.
    """
    settings = get_settings()
    if not settings.SHOPIFY_STORE_DOMAIN or not settings.SHOPIFY_ACCESS_TOKEN:
        return "La integración con Shopify no está configurada (faltan SHOPIFY_STORE_DOMAIN o SHOPIFY_ACCESS_TOKEN)."

    try:
        catalogo = await _obtener_catalogo()
    except Exception as e:
        logger.error("Error cargando catálogo Shopify: %s", e)
        return f"Error al consultar la tienda Shopify: {e}"

    if not catalogo:
        return "No se encontraron productos en la tienda."

    query_stripped = (query or "").strip()

    if not query_stripped:
        products = catalogo
    else:
        q = query_stripped.lower()
        products = [
            p for p in catalogo
            if q in p.get("title", "").lower()
            or q in p.get("vendor", "").lower()
            or q in p.get("tags", "").lower()
            or any(q in v.get("sku", "").lower() for v in p.get("variants", []))
        ]

    if not products:
        return f"No se encontraron productos con el término '{query_stripped}' en la tienda."

    return _format_products(products)


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
# Actualización masiva de precios
# ──────────────────────────────────────────────

async def construir_mapa_sku_a_variant_id() -> Dict[str, str]:
    """
    Pagina todos los productos de Shopify y construye un mapa {sku: variant_id}.
    Usa paginación por since_id para evitar cursores de Link header.
    """
    settings = get_settings()
    if not settings.SHOPIFY_STORE_DOMAIN or not settings.SHOPIFY_ACCESS_TOKEN:
        raise ValueError("Shopify no está configurado (faltan SHOPIFY_STORE_DOMAIN o SHOPIFY_ACCESS_TOKEN)")

    client = http_svc.get_client()
    url = f"{_base_url()}/products.json"
    mapa: Dict[str, str] = {}
    since_id = 0

    while True:
        params = {
            "limit": 250,
            "fields": "id,variants",
            "since_id": since_id,
        }
        resp = await client.get(url, params=params, headers=_headers(), timeout=30.0)
        if resp.status_code != 200:
            logger.error("[SHOPIFY] Error paginando productos: HTTP %d", resp.status_code)
            break

        products = resp.json().get("products", [])
        if not products:
            break

        for product in products:
            for variant in product.get("variants", []):
                sku = str(variant.get("sku", "")).strip()
                variant_id = str(variant.get("id", ""))
                if sku and variant_id:
                    mapa[sku] = variant_id

        since_id = products[-1]["id"]
        if len(products) < 250:
            break

    logger.info("[SHOPIFY] Mapa SKU→variant_id construido: %d variantes", len(mapa))
    return mapa


async def actualizar_precio_variante(variant_id: str, price: str) -> bool:
    """Actualiza el precio de una variante via PUT /variants/{id}.json. Retorna True si éxito."""
    settings = get_settings()
    if not settings.SHOPIFY_STORE_DOMAIN or not settings.SHOPIFY_ACCESS_TOKEN:
        return False

    client = http_svc.get_client()
    url = f"{_base_url()}/variants/{variant_id}.json"
    body = {"variant": {"id": int(variant_id), "price": price}}

    try:
        resp = await client.put(url, json=body, headers=_headers(), timeout=15.0)
        if resp.status_code == 200:
            return True
        logger.error("[SHOPIFY] Error actualizando variante %s: HTTP %d — %s",
                     variant_id, resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        logger.error("[SHOPIFY] Excepción actualizando variante %s: %s", variant_id, e)
        return False


# ──────────────────────────────────────────────
# Formateo
# ──────────────────────────────────────────────

_COMPACT_THRESHOLD = 15  # > N productos → formato compacto


def _format_products(products: List[Dict[str, Any]]) -> str:
    """
    Formatea lista de productos para el LLM.
    Compacto (>15 resultados): una línea por variante.
    Detallado (≤15): incluye descripción, tags, URL.
    """
    n = len(products)
    plural = "s" if n > 1 else ""
    lines = [f"PRODUCTOS ENCONTRADOS EN LA TIENDA ({n} resultado{plural}):\n"]

    if n > _COMPACT_THRESHOLD:
        # Formato compacto: nombre | marca | variante | precio | stock
        for p in products:
            title = p.get("title", "Sin nombre")
            vendor = p.get("vendor", "—")
            is_published = p.get("published_at") is not None
            pub_tag = "" if is_published else " [no publicado]"

            variants = p.get("variants", [])
            if not variants:
                lines.append(f"• {title} ({vendor}){pub_tag} — sin variantes")
                continue

            for v in variants:
                v_title = v.get("title", "")
                price = _format_clp(v.get("price", "0"))
                sku = v.get("sku", "—")
                inv_mgmt = v.get("inventory_management")
                inv_qty = v.get("inventory_quantity")

                row = f"• {title}"
                if v_title and v_title != "Default Title":
                    row += f" — {v_title}"
                row += f" | {vendor} | {price} CLP | SKU: {sku}"
                if inv_mgmt == "shopify" and inv_qty is not None:
                    row += f" | Stock: {inv_qty}"
                row += pub_tag
                lines.append(row)

        return "\n".join(lines)

    # Formato detallado para conjuntos pequeños
    for p in products:
        title = p.get("title", "Sin nombre")
        vendor = p.get("vendor", "—")
        description = _strip_html(p.get("body_html", ""))
        tags = p.get("tags", "")
        handle = p.get("handle", "")
        is_published = p.get("published_at") is not None

        lines.append(f"━━━ {title} ━━━")
        lines.append(f"  Marca: {vendor}")
        if description:
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
