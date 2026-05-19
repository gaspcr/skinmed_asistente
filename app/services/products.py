"""
Repositorio de productos: persistencia en Postgres y busqueda hibrida.

La busqueda hibrida combina:
  - Semantica via pgvector (distancia coseno sobre embedding 1536d).
  - Full-text Search via tsvector espanol (GIN sobre `search_vector`).

Resultados se fusionan con Reciprocal Rank Fusion (k=60).
"""
import json
import logging
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.services import postgres as pg_svc
from app.services import embeddings as emb_svc
from app.services import shopify_service

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Upsert
# ──────────────────────────────────────────────

_UPSERT_SQL = """
INSERT INTO products (
    id, shopify_id, title, vendor, product_type, tags, body_html,
    status, handle, data, search_text, embedding, updated_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, now())
ON CONFLICT (id) DO UPDATE SET
    shopify_id   = EXCLUDED.shopify_id,
    title        = EXCLUDED.title,
    vendor       = EXCLUDED.vendor,
    product_type = EXCLUDED.product_type,
    tags         = EXCLUDED.tags,
    body_html    = EXCLUDED.body_html,
    status       = EXCLUDED.status,
    handle       = EXCLUDED.handle,
    data         = EXCLUDED.data,
    search_text  = EXCLUDED.search_text,
    embedding    = EXCLUDED.embedding,
    updated_at   = now();
"""


async def upsert_product(product: Dict[str, Any], embedding: List[float]) -> None:
    """Inserta o actualiza un producto con su embedding precomputado."""
    pool = pg_svc.get_pool()
    shopify_id = product.get("id")
    if shopify_id is None:
        raise ValueError("Producto sin id de Shopify")

    text = shopify_service.build_product_text(product)
    tags = shopify_service.parse_tags(product.get("tags"))

    async with pool.acquire() as conn:
        await conn.execute(
            _UPSERT_SQL,
            str(shopify_id),                # id (TEXT)
            int(shopify_id),                # shopify_id (BIGINT)
            product.get("title"),
            product.get("vendor"),
            product.get("product_type"),
            tags,
            product.get("body_html"),
            product.get("status"),
            product.get("handle"),
            json.dumps(product, default=str),
            text,
            embedding,
        )


async def delete_product(shopify_id: int) -> None:
    """Elimina un producto por su shopify_id."""
    pool = pg_svc.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM products WHERE id = $1", str(shopify_id))


async def count_products() -> int:
    """Retorna el numero total de productos almacenados."""
    pool = pg_svc.get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT count(*) FROM products") or 0


# ──────────────────────────────────────────────
# Hybrid search (RRF)
# ──────────────────────────────────────────────

_HYBRID_SEARCH_SQL = """
WITH semantic AS (
    SELECT id,
           RANK() OVER (ORDER BY embedding <=> $1) AS rank
    FROM products
    WHERE embedding IS NOT NULL
    ORDER BY embedding <=> $1
    LIMIT $3
),
fulltext AS (
    SELECT id,
           RANK() OVER (ORDER BY ts_rank(search_vector, query) DESC) AS rank
    FROM products,
         plainto_tsquery('spanish', $2) query
    WHERE search_vector @@ query
    LIMIT $3
)
SELECT
    p.id,
    p.shopify_id,
    p.title,
    p.vendor,
    p.product_type,
    p.tags,
    p.status,
    p.handle,
    p.data,
    (COALESCE(1.0 / (60 + s.rank), 0) +
     COALESCE(1.0 / (60 + f.rank), 0)) AS rrf_score
FROM products p
LEFT JOIN semantic s ON p.id = s.id
LEFT JOIN fulltext  f ON p.id = f.id
WHERE s.id IS NOT NULL OR f.id IS NOT NULL
ORDER BY rrf_score DESC
LIMIT $4;
"""

# Cuantos resultados por rama (semantic / fulltext) consideramos antes de fusionar
_CANDIDATE_POOL = 20


async def search_products_hybrid(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Busca productos combinando similitud semantica y full-text con RRF.

    Args:
        query: Texto en lenguaje natural (espanol).
        limit: Numero maximo de resultados a retornar.

    Returns:
        Lista de productos formateados (ver `_format_row`).
    """
    if not query or not query.strip():
        return []

    embedding = await emb_svc.embed(query)

    pool = pg_svc.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _HYBRID_SEARCH_SQL,
            embedding,
            query,
            _CANDIDATE_POOL,
            limit,
        )

    return [_format_row(row) for row in rows]


# ──────────────────────────────────────────────
# Filter-based listing (sin relevancia semantica)
# ──────────────────────────────────────────────

async def list_products_by_filter(
    vendor: Optional[str] = None,
    product_type: Optional[str] = None,
    tag: Optional[str] = None,
    status: Optional[str] = "active",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Lista productos aplicando filtros exactos (no relevancia semantica).

    - `vendor` y `product_type`: comparacion ILIKE (case-insensitive exacto).
    - `tag`: match case-insensitive contra cualquier elemento del array `tags`.
    - `status`: comparacion exacta (default 'active'). Pasa None para no filtrar.
    - Resultados ordenados por titulo, formateados con `_format_row`.
    """
    where: List[str] = []
    params: List[Any] = []

    if vendor:
        params.append(vendor)
        where.append(f"vendor ILIKE ${len(params)}")
    if product_type:
        params.append(product_type)
        where.append(f"product_type ILIKE ${len(params)}")
    if tag:
        params.append(tag.lower())
        where.append(
            f"EXISTS (SELECT 1 FROM unnest(tags) t WHERE LOWER(t) = ${len(params)})"
        )
    if status:
        params.append(status)
        where.append(f"status = ${len(params)}")

    where_clause = " AND ".join(where) if where else "TRUE"
    params.append(limit)
    limit_param = f"${len(params)}"

    sql = f"""
    SELECT id, shopify_id, title, vendor, product_type, tags, status, handle, data,
           NULL::float AS rrf_score
    FROM products
    WHERE {where_clause}
    ORDER BY title
    LIMIT {limit_param};
    """

    pool = pg_svc.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [_format_row(row) for row in rows]


# ──────────────────────────────────────────────
# Formatter
# ──────────────────────────────────────────────

def _format_row(row) -> Dict[str, Any]:
    """Formatea una fila de la BD al shape esperado por la tool LLM."""
    data = row["data"]
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = {}
    data = data or {}

    variants = data.get("variants") or []
    prices: List[float] = []
    in_stock = False
    for v in variants:
        price_str = v.get("price")
        if price_str is not None:
            try:
                prices.append(float(price_str))
            except (TypeError, ValueError):
                pass
        inv = v.get("inventory_quantity")
        if isinstance(inv, (int, float)) and inv > 0:
            in_stock = True

    price_range: Optional[Dict[str, float]] = None
    if prices:
        price_range = {"min": min(prices), "max": max(prices)}

    settings = get_settings()
    handle = row["handle"] or data.get("handle") or ""
    shopify_url = ""
    if handle and settings.SHOPIFY_STORE_DOMAIN:
        shopify_url = f"https://{settings.SHOPIFY_STORE_DOMAIN.rstrip('/')}/products/{handle}"

    metafields = data.get("_metafields") or {}
    if not isinstance(metafields, dict):
        metafields = {}

    return {
        "id": row["id"],
        "shopify_id": row["shopify_id"],
        "title": row["title"],
        "vendor": row["vendor"],
        "product_type": row["product_type"],
        "tags": list(row["tags"] or []),
        "status": row["status"],
        "price_range": price_range,
        "in_stock": in_stock,
        "shopify_url": shopify_url,
        "metafields": metafields,
        "rrf_score": float(row["rrf_score"]) if row["rrf_score"] is not None else 0.0,
    }
