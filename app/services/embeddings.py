"""
Servicio de embeddings: wrapper para OpenAI text-embedding-3-small.

Usa el cliente httpx compartido del proyecto (mismo patron que llm_service.py)
para evitar agregar el SDK de openai como dependencia del runtime.

Modelo: text-embedding-3-small (1536 dimensiones).
Batch maximo recomendado por OpenAI: 100 textos por request.
"""
import logging
from typing import List

from app.config import get_settings
from app.services import http as http_svc
from app.exceptions import ServicioNoDisponibleError

logger = logging.getLogger(__name__)

OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536
_MAX_BATCH_SIZE = 100


def _headers() -> dict:
    settings = get_settings()
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
    }


async def embed(text: str) -> List[float]:
    """Genera el embedding de un unico texto."""
    if not text or not text.strip():
        raise ValueError("No se puede generar embedding de texto vacio")

    results = await embed_batch([text])
    return results[0]


async def embed_batch(texts: List[str]) -> List[List[float]]:
    """
    Genera embeddings para una lista de textos.

    Divide automaticamente en batches de hasta 100 textos por request.
    Preserva el orden de entrada en la salida.
    """
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        raise ServicioNoDisponibleError("OpenAI", "OPENAI_API_KEY no configurada")
    if not texts:
        return []

    client = http_svc.get_client()
    all_embeddings: List[List[float]] = []

    for start in range(0, len(texts), _MAX_BATCH_SIZE):
        chunk = texts[start:start + _MAX_BATCH_SIZE]
        payload = {"model": EMBEDDING_MODEL, "input": chunk}

        try:
            resp = await client.post(
                OPENAI_EMBEDDINGS_URL,
                json=payload,
                headers=_headers(),
                timeout=60.0,
            )
        except Exception as e:
            logger.error("Error de conexion con OpenAI embeddings: %s", e)
            raise ServicioNoDisponibleError("OpenAI", f"Error de conexion: {e}")

        if resp.status_code == 429:
            logger.warning("OpenAI embeddings rate limit alcanzado")
            raise ServicioNoDisponibleError("OpenAI", "Rate limit alcanzado")
        if resp.status_code >= 500:
            logger.error("Error servidor OpenAI embeddings: HTTP %d", resp.status_code)
            raise ServicioNoDisponibleError("OpenAI", f"HTTP {resp.status_code}")
        if resp.status_code != 200:
            logger.error("Error embeddings: HTTP %d — %s", resp.status_code, resp.text[:200])
            raise ServicioNoDisponibleError("OpenAI", f"HTTP {resp.status_code}")

        data = resp.json().get("data", [])
        # OpenAI garantiza que data viene ordenado por `index`, pero lo verificamos
        data.sort(key=lambda d: d.get("index", 0))
        all_embeddings.extend([d["embedding"] for d in data])

    return all_embeddings
