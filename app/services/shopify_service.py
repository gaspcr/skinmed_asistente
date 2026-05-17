"""
Servicio para interactuar con la API Admin de Shopify.

Sólo expone las funciones necesarias para actualizar precios masivamente.
La consulta de productos se implementará por separado.
"""
import logging
from typing import Dict

from app.config import get_settings
from app.services import http as http_svc

logger = logging.getLogger(__name__)

_API_VERSION = "2025-01"


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
