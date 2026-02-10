"""
Cliente HTTP compartido (httpx.AsyncClient singleton).
Reutiliza conexiones TCP+TLS para FileMaker y WhatsApp APIs.
"""
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None


async def init():
    """Inicializa el cliente HTTP con pool de conexiones."""
    global _client
    _client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    logger.info("Cliente HTTP inicializado")


async def close():
    """Cierra el cliente HTTP y libera conexiones."""
    global _client
    if _client:
        await _client.aclose()
        _client = None
        logger.info("Cliente HTTP cerrado")


def get_client() -> httpx.AsyncClient:
    """Retorna el cliente HTTP, lanzando error si no esta inicializado."""
    if _client is None:
        raise RuntimeError("Cliente HTTP no inicializado. Llamar a init() primero.")
    return _client
