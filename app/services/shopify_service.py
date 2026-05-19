"""
Servicio para interactuar con la API Admin de Shopify.

Expone las funciones necesarias para actualizar precios masivamente y para
listar el catalogo de productos para sincronizacion con la base de datos
vectorial.
"""
import logging
import re
from typing import AsyncIterator, Dict, List

from app.config import get_settings
from app.services import http as http_svc

logger = logging.getLogger(__name__)

_API_VERSION = "2025-01"

# Campos minimos que necesitamos del producto para el catalogo vectorial.
_PRODUCT_FIELDS = (
    "id,title,body_html,vendor,product_type,tags,handle,status,variants"
)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


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


async def iter_all_products(page_size: int = 250) -> AsyncIterator[Dict]:
    """
    Itera todos los productos de la tienda usando paginacion `since_id`.

    Devuelve cada producto como dict crudo de la API REST de Shopify, con los
    campos definidos en `_PRODUCT_FIELDS` (incluyendo variants).
    """
    settings = get_settings()
    if not settings.SHOPIFY_STORE_DOMAIN or not settings.SHOPIFY_ACCESS_TOKEN:
        raise ValueError("Shopify no configurado (faltan SHOPIFY_STORE_DOMAIN o SHOPIFY_ACCESS_TOKEN)")

    client = http_svc.get_client()
    url = f"{_base_url()}/products.json"
    since_id = 0

    while True:
        params = {
            "limit": page_size,
            "fields": _PRODUCT_FIELDS,
            "since_id": since_id,
        }
        resp = await client.get(url, params=params, headers=_headers(), timeout=30.0)
        if resp.status_code != 200:
            logger.error("[SHOPIFY] Error paginando productos: HTTP %d — %s",
                         resp.status_code, resp.text[:200])
            return

        products = resp.json().get("products", [])
        if not products:
            return

        for product in products:
            yield product

        since_id = products[-1]["id"]
        if len(products) < page_size:
            return


def _strip_html(html: str) -> str:
    """Remueve etiquetas HTML y normaliza espacios."""
    if not html:
        return ""
    text = _HTML_TAG_RE.sub(" ", html)
    return _WHITESPACE_RE.sub(" ", text).strip()


def parse_tags(raw_tags) -> List[str]:
    """Normaliza tags a lista. La API REST devuelve tags como CSV string."""
    if isinstance(raw_tags, list):
        return [str(t).strip() for t in raw_tags if str(t).strip()]
    if isinstance(raw_tags, str):
        return [t.strip() for t in raw_tags.split(",") if t.strip()]
    return []


def build_product_text(product: Dict) -> str:
    """
    Construye el texto representativo del producto para FTS + embeddings.

    Concatena con ` | ` los campos no vacios:
      title | vendor | product_type | body_html(stripped) | tags | variant_titles
    """
    parts: List[str] = []

    title = (product.get("title") or "").strip()
    if title:
        parts.append(title)

    vendor = (product.get("vendor") or "").strip()
    if vendor:
        parts.append(vendor)

    product_type = (product.get("product_type") or "").strip()
    if product_type:
        parts.append(product_type)

    body = _strip_html(product.get("body_html") or "")
    if body:
        parts.append(body)

    tags = parse_tags(product.get("tags"))
    if tags:
        parts.append(" ".join(tags))

    variant_titles: List[str] = []
    for variant in product.get("variants") or []:
        vt = (variant.get("title") or "").strip()
        if vt and vt.lower() != "default title":
            variant_titles.append(vt)
    if variant_titles:
        parts.append(" ".join(variant_titles))

    return " | ".join(parts)


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
