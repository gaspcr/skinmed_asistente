"""
Servicio de PostgreSQL: pool async (asyncpg) con soporte para pgvector.

Mantiene un pool compartido a nivel de app, registra el codec de vector en cada
conexion adquirida, y expone helpers para ejecutar la migracion de esquema.
"""
import logging
from typing import Optional

import asyncpg
from pgvector.asyncpg import register_vector

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Callback ejecutado por asyncpg al adquirir cada conexion del pool.

    Registra el codec de pgvector para que los valores `vector(N)` se
    serialicen/deserialicen automaticamente como list[float].
    """
    await register_vector(conn)


async def init(url: str, min_size: int = 1, max_size: int = 5) -> None:
    """Crea el pool de conexiones a Postgres."""
    global _pool
    if not url:
        logger.warning("DATABASE_URL no configurada; Postgres deshabilitado")
        return

    _pool = await asyncpg.create_pool(
        dsn=url,
        min_size=min_size,
        max_size=max_size,
        init=_init_connection,
        command_timeout=30,
    )
    logger.info("Pool de Postgres inicializado (min=%d, max=%d)", min_size, max_size)


async def close() -> None:
    """Cierra el pool de Postgres."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Pool de Postgres cerrado")


def get_pool() -> asyncpg.Pool:
    """Retorna el pool, lanzando RuntimeError si no esta inicializado."""
    if _pool is None:
        raise RuntimeError("Postgres no inicializado. Llamar a init() primero.")
    return _pool


async def ping() -> bool:
    """Verifica conectividad ejecutando un SELECT 1."""
    if _pool is None:
        return False
    async with _pool.acquire() as conn:
        result = await conn.fetchval("SELECT 1")
        return result == 1


# ──────────────────────────────────────────────
# Migracion de esquema (productos)
# ──────────────────────────────────────────────

_PRODUCTS_SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS products (
    id            TEXT PRIMARY KEY,
    shopify_id    BIGINT UNIQUE,
    title         TEXT,
    vendor        TEXT,
    product_type  TEXT,
    tags          TEXT[],
    body_html     TEXT,
    status        TEXT,
    handle        TEXT,
    data          JSONB,
    search_text   TEXT,
    search_vector TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('spanish', coalesce(search_text, ''))
    ) STORED,
    embedding     vector(1536),
    updated_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS products_search_vector_idx
    ON products USING GIN(search_vector);

CREATE INDEX IF NOT EXISTS products_embedding_idx
    ON products USING hnsw(embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS products_title_trgm_idx
    ON products USING GIN(title gin_trgm_ops);
"""


async def ensure_products_schema() -> None:
    """Aplica la migracion de esquema para la tabla `products` (idempotente)."""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_PRODUCTS_SCHEMA_SQL)
    logger.info("Esquema de `products` aplicado correctamente")
