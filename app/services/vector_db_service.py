import logging
from typing import List, Dict, Any
import psycopg2
from pgvector.psycopg2 import register_vector
from openai import AsyncOpenAI

from app.config import get_settings
from app.exceptions import ServicioNoDisponibleError

logger = logging.getLogger(__name__)

async def _get_embedding(query: str, openai_key: str) -> List[float]:
    """Obtiene el embedding de la consulta usando OpenAI API directamente."""
    try:
        client = AsyncOpenAI(api_key=openai_key)
        response = await client.embeddings.create(
            input=query,
            model="text-embedding-3-small"
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Error obteniendo embedding para consulta: {e}")
        raise ServicioNoDisponibleError("OpenAI", "Error generando embedding de busqueda")


async def search_knowledge_base(query: str, top_k: int = 4) -> str:
    """
    Busca fragmentos relevantes en la base de datos de conocimiento de la clínica.
    Retorna un string formateado con los fragmentos encontrados.
    """
    settings = get_settings()
    
    if not settings.DATABASE_URL:
        logger.warning("DATABASE_URL no configurada. RAG no disponible.")
        return "La base de conocimientos de la clínica no está disponible en este momento (Falta DATABASE_URL)."
        
    if not settings.OPENAI_API_KEY:
        return "Servicio de búsqueda no disponible (Falta OPENAI_API_KEY)."

    try:
        # 1. Obtener embedding de la pregunta
        query_embedding = await _get_embedding(query, settings.OPENAI_API_KEY)
        
        # 2. Buscar en PostgreSQL (pgvector)
        conn = psycopg2.connect(settings.DATABASE_URL)
        register_vector(conn)
        cursor = conn.cursor()
        
        # Búsqueda de similitud por coseno (<=>)
        sql = """
        SELECT nombre, categoria, content, 1 - (embedding <=> %s::vector) AS similarity
        FROM knowledge_base
        ORDER BY embedding <=> %s::vector
        LIMIT %s;
        """
        
        cursor.execute(sql, (query_embedding, query_embedding, top_k))
        results = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        if not results:
            return "No encontré información relevante en los documentos de la clínica sobre tu consulta."
            
        # 3. Formatear la respuesta
        formatted_results = "INFORMACIÓN DE LA CLÍNICA ENCONTRADA (Úsala para responder al doctor):\n\n"
        for idx, row in enumerate(results):
            nombre, categoria, content, similarity = row
            # Solo incluimos resultados con un mínimo de relevancia (ej. > 0.3)
            if similarity < 0.3:
                continue
                
            formatted_results += f"--- FRAGMENTO {idx+1} [Relevancia: {similarity:.2f}] ---\n"
            formatted_results += f"Documento: {nombre} | Categoría: {categoria}\n"
            formatted_results += f"Contenido:\n{content}\n\n"
            
        if formatted_results == "INFORMACIÓN DE LA CLÍNICA ENCONTRADA (Úsala para responder al doctor):\n\n":
             return "Se encontraron documentos, pero no fueron lo suficientemente relevantes para responder tu consulta."
             
        return formatted_results

    except Exception as e:
        logger.error(f"Error buscando en knowledge_base: {e}")
        return f"Ocurrió un error al consultar la base de documentos: {e}"
