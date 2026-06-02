"""
Servicio para interactuar con la API Admin de Shopify.

Expone las funciones necesarias para actualizar precios masivamente y para
listar el catalogo de productos para sincronizacion con la base de datos
vectorial.
"""
import json
import logging
import re
from typing import Any, AsyncIterator, Dict, List, Optional

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
      | metafields (clave + valor, si product["_metafields"] esta presente).
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

    metafields = product.get("_metafields") or {}
    if isinstance(metafields, dict):
        for full_key, value in metafields.items():
            if not value:
                continue
            label = full_key.split(".", 1)[-1].replace("_", " ").replace("-", " ").strip()
            parts.append(f"{label}: {value}" if label else str(value))

    return " | ".join(parts)


# Tipos de metafield cuyo `value` es texto humano (lo que queremos indexar en RAG).
# Excluye explicitamente: file_reference, *_reference, json, number_*, boolean,
# date, url, dimension, weight, volume, rating, metaobject_reference, etc.
_TEXT_METAFIELD_TYPES = {
    "single_line_text_field",
    "multi_line_text_field",
    "rich_text_field",
    "list.single_line_text_field",
    "list.multi_line_text_field",
}


def _extract_rich_text(raw: Any) -> str:
    """Convierte rich_text_field (JSON con nodos type/children/value) a texto plano."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw.strip()
    else:
        data = raw

    parts: List[str] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                v = node.get("value")
                if isinstance(v, str) and v.strip():
                    parts.append(v.strip())
            for child in node.get("children") or []:
                walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return " ".join(parts).strip()


def _extract_list_text(raw: Any) -> str:
    """Convierte list.*_text_field (JSON array de strings) a texto unido por ' | '."""
    if raw is None:
        return ""
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        try:
            items = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw.strip()
    else:
        return str(raw).strip()
    if not isinstance(items, list):
        return ""
    return " | ".join(str(i).strip() for i in items if str(i).strip())


def _normalize_metafield_value(mtype: str, raw_value: Any) -> str:
    """Devuelve la representacion en texto plano de un metafield indexable."""
    if raw_value is None:
        return ""
    if mtype == "rich_text_field":
        return _extract_rich_text(raw_value)
    if mtype.startswith("list."):
        return _extract_list_text(raw_value)
    if isinstance(raw_value, str):
        return raw_value.strip()
    return str(raw_value).strip()


_METAFIELDS_QUERY = """
query GetMetafields($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on Product {
      id
      metafields(first: 100) {
        edges {
          node {
            namespace
            key
            value
            type
          }
        }
      }
    }
  }
}
""".strip()


async def fetch_metafields_for_products(
    product_ids: List[str],
    batch_size: int = 10,
    timeout: float = 15.0,
) -> Dict[str, Dict[str, str]]:
    """
    Consulta a Shopify (GraphQL) los metafields de tipo texto para los productos
    indicados. Devuelve un dict {product_id_str: {"namespace.key": "valor", ...}}.

    - Solo se incluyen tipos en `_TEXT_METAFIELD_TYPES` (texto humano legible).
    - Tipos `rich_text_field` y `list.*` se aplanan a texto plano.
    - Productos sin metafields (o que fallen) aparecen con dict vacio.
    - Si la llamada a Shopify falla por completo retorna {} (no propaga).
    """
    if not product_ids:
        return {}

    settings = get_settings()
    if not settings.SHOPIFY_STORE_DOMAIN or not settings.SHOPIFY_ACCESS_TOKEN:
        logger.warning("[SHOPIFY] Metafields fetch saltado: credenciales no configuradas")
        return {}

    client = http_svc.get_client()
    url = f"{_base_url()}/graphql.json"
    result: Dict[str, Dict[str, str]] = {}

    for i in range(0, len(product_ids), batch_size):
        batch = [str(pid) for pid in product_ids[i:i + batch_size] if pid]
        if not batch:
            continue
        gids = [f"gid://shopify/Product/{pid}" for pid in batch]

        try:
            resp = await client.post(
                url,
                json={"query": _METAFIELDS_QUERY, "variables": {"ids": gids}},
                headers=_headers(),
                timeout=timeout,
            )
        except Exception as e:
            logger.warning("[SHOPIFY] Metafields fetch fallo en batch %d: %s", i, e)
            continue

        if resp.status_code != 200:
            logger.warning(
                "[SHOPIFY] Metafields HTTP %d en batch %d — %s",
                resp.status_code, i, resp.text[:200],
            )
            continue

        try:
            body = resp.json()
        except ValueError:
            logger.warning("[SHOPIFY] Metafields: respuesta no es JSON en batch %d", i)
            continue

        if body.get("errors"):
            logger.warning("[SHOPIFY] Metafields GraphQL errors en batch %d: %s",
                           i, body["errors"])
            continue

        nodes = (body.get("data") or {}).get("nodes") or []
        for node in nodes:
            if not node:
                continue
            gid = node.get("id") or ""
            numeric_id = gid.rsplit("/", 1)[-1]
            if not numeric_id:
                continue

            extracted: Dict[str, str] = {}
            for edge in (node.get("metafields") or {}).get("edges") or []:
                mf = edge.get("node") or {}
                mtype = mf.get("type") or ""
                if mtype not in _TEXT_METAFIELD_TYPES:
                    continue
                ns = (mf.get("namespace") or "").strip()
                key = (mf.get("key") or "").strip()
                if not key:
                    continue
                full_key = f"{ns}.{key}" if ns else key
                value = _normalize_metafield_value(mtype, mf.get("value"))
                if value:
                    extracted[full_key] = value

            result[numeric_id] = extracted

    return result


_LIVE_INVENTORY_QUERY = """
query getProductsLive($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on Product {
      id
      totalInventory
      variants(first: 50) {
        edges {
          node {
            id
            price
            inventoryQuantity
            availableForSale
          }
        }
      }
    }
  }
}
""".strip()


async def fetch_live_pricing_inventory(
    product_ids: List[str],
    timeout: float = 5.0,
) -> Dict[str, Dict]:
    """
    Consulta a Shopify (GraphQL) precio + inventario en vivo para los productos
    indicados. Devuelve un dict {product_id_str: {...}} con el shape:

        {
          "total_inventory": int | None,
          "in_stock": bool,
          "price_range": {"min": float, "max": float} | None,
          "variants": [{"id": str, "price": float, "inventory_quantity": int,
                        "available_for_sale": bool}, ...]
        }

    Productos no encontrados o que fallen no aparecen en el dict. Si la llamada
    a Shopify falla completa retorna {}.
    """
    if not product_ids:
        return {}

    settings = get_settings()
    if not settings.SHOPIFY_STORE_DOMAIN or not settings.SHOPIFY_ACCESS_TOKEN:
        logger.warning("[SHOPIFY] Live fetch saltado: credenciales no configuradas")
        return {}

    gids = [f"gid://shopify/Product/{pid}" for pid in product_ids if pid]
    if not gids:
        return {}

    client = http_svc.get_client()
    url = f"{_base_url()}/graphql.json"
    payload = {"query": _LIVE_INVENTORY_QUERY, "variables": {"ids": gids}}

    try:
        resp = await client.post(url, json=payload, headers=_headers(), timeout=timeout)
    except Exception as e:
        logger.warning("[SHOPIFY] Live fetch fallo (%s) — usando datos cacheados", e)
        return {}

    if resp.status_code != 200:
        logger.warning(
            "[SHOPIFY] Live fetch HTTP %d — %s",
            resp.status_code, resp.text[:200],
        )
        return {}

    try:
        body = resp.json()
    except ValueError:
        logger.warning("[SHOPIFY] Live fetch: respuesta no es JSON")
        return {}

    if body.get("errors"):
        logger.warning("[SHOPIFY] Live fetch GraphQL errors: %s", body["errors"])
        return {}

    nodes = (body.get("data") or {}).get("nodes") or []
    result: Dict[str, Dict] = {}

    for node in nodes:
        if not node:
            continue
        gid = node.get("id") or ""
        numeric_id = gid.rsplit("/", 1)[-1] if gid else ""
        if not numeric_id:
            continue

        variants_out: List[Dict] = []
        prices: List[float] = []
        in_stock = False
        for edge in (node.get("variants") or {}).get("edges") or []:
            v = edge.get("node") or {}
            price_val: Optional[float] = None
            try:
                price_val = float(v["price"]) if v.get("price") is not None else None
            except (TypeError, ValueError):
                price_val = None
            inv = v.get("inventoryQuantity")
            if isinstance(inv, int) and inv > 0:
                in_stock = True
            if price_val is not None:
                prices.append(price_val)
            variants_out.append({
                "id": (v.get("id") or "").rsplit("/", 1)[-1],
                "price": price_val,
                "inventory_quantity": inv if isinstance(inv, int) else None,
                "available_for_sale": bool(v.get("availableForSale")),
            })

        total_inv = node.get("totalInventory")
        if isinstance(total_inv, int) and total_inv > 0:
            in_stock = True

        price_range: Optional[Dict[str, float]] = None
        if prices:
            price_range = {"min": min(prices), "max": max(prices)}

        result[numeric_id] = {
            "total_inventory": total_inv if isinstance(total_inv, int) else None,
            "in_stock": in_stock,
            "price_range": price_range,
            "variants": variants_out,
        }

    return result


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
