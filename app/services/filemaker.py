import logging
from datetime import datetime

import httpx
import pytz

from app.config import get_settings
from app.auth.models import User
from app.services import redis as redis_svc
from app.services import http as http_svc
from app.exceptions import ServicioNoDisponibleError
from app.utils.retry import con_reintentos
from app.utils.circuit_breaker import CircuitBreaker, CircuitBreakerAbierto

logger = logging.getLogger(__name__)

# Circuit breaker para proteger llamadas a FileMaker
_fm_circuit_breaker = CircuitBreaker(
    nombre="filemaker",
    umbral_fallos=5,
    timeout_recuperacion=30.0,
    excepciones_monitoreadas=(httpx.RequestError, httpx.HTTPStatusError, ServicioNoDisponibleError),
)


def _es_sin_registros(resp: httpx.Response) -> bool:
    """Verifica si la respuesta de FileMaker indica 'sin registros encontrados'."""
    try:
        body = resp.json()
        code = body.get('messages', [{}])[0].get('code', '')
        return code == '401'
    except Exception:
        return False


class FileMakerService:
    @classmethod
    async def get_token(cls, force_refresh: bool = False) -> str:
        if not force_refresh:
            cached = await redis_svc.get("fm:token")
            if cached:
                return cached

        async def _solicitar_token():
            settings = get_settings()
            client = http_svc.get_client()
            url = f"https://{settings.FM_HOST}/fmi/data/v1/databases/{settings.FM_DB}/sessions"
            resp = await client.post(url, auth=(settings.FM_USER, settings.FM_PASS), json={})
            resp.raise_for_status()
            return resp.json()['response']['token']

        token = await con_reintentos(
            _solicitar_token,
            max_intentos=3,
            backoff_base=1.0,
            nombre_operacion="FileMaker get_token",
        )

        await redis_svc.set("fm:token", token, ttl=840)  # 14 minutos
        return token

    @classmethod
    async def _fm_find(cls, layout: str, query: dict, intentar_reauth: bool = True) -> httpx.Response:
        """
        Ejecuta una busqueda en FileMaker con reintento automatico de token.
        Si recibe HTTP 401, refresca el token y reintenta una vez.
        """
        settings = get_settings()
        client = http_svc.get_client()
        token = await cls.get_token()
        url = f"https://{settings.FM_HOST}/fmi/data/v1/databases/{settings.FM_DB}/layouts/{layout}/_find"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

        async with _fm_circuit_breaker:
            resp = await client.post(url, json=query, headers=headers)

        if resp.status_code == 401 and intentar_reauth:
            logger.info("Token FM expirado, refrescando...")
            await cls.get_token(force_refresh=True)
            return await cls._fm_find(layout, query, intentar_reauth=False)

        return resp

    @staticmethod
    async def _parsear_respuesta_find(resp: httpx.Response, contexto: str) -> list:
        """Parsea respuesta de _fm_find, diferenciando datos vacios de errores."""
        if resp.status_code == 200:
            return resp.json()['response']['data']

        if resp.status_code == 500 and _es_sin_registros(resp):
            return []

        raise ServicioNoDisponibleError("FileMaker", f"{contexto}: HTTP {resp.status_code}")

    @staticmethod
    async def get_agenda_raw(id: str, date: str = None) -> list:
        """Obtiene datos crudos de agenda desde FileMaker."""
        settings = get_settings()
        tz = pytz.timezone("America/Santiago")
        today_str = date if date else datetime.now(tz).strftime("%m-%d-%Y")

        query = {
            "query": [
                {
                    "Fecha": today_str,
                    "Recurso Humano::Recurso Humano_pk": id,
                }
            ]
        }

        async def _buscar():
            resp = await FileMakerService._fm_find(settings.FM_AGENDA_LAYOUT, query)
            return await FileMakerService._parsear_respuesta_find(resp, "get_agenda_raw")

        try:
            return await con_reintentos(
                _buscar,
                max_intentos=2,
                backoff_base=1.0,
                nombre_operacion="FileMaker get_agenda_raw",
            )
        except ServicioNoDisponibleError:
            raise
        except CircuitBreakerAbierto as e:
            raise ServicioNoDisponibleError("FileMaker", str(e))
        except httpx.RequestError as e:
            raise ServicioNoDisponibleError("FileMaker", f"Error de conexion: {e}")
        except Exception as e:
            logger.error("Error inesperado al obtener agenda: %s", e)
            raise ServicioNoDisponibleError("FileMaker", f"Error inesperado: {e}")

    @staticmethod
    async def get_recados(doctor_id: str) -> list:
        """Obtiene recados de un doctor por su ID de FileMaker."""
        settings = get_settings()

        query = {
            "query": [
                {
                    "T500_RECADOS::_FK_IDRRHH": doctor_id,
                    "Estado": "Vigente"
                }
            ]
        }

        async def _buscar():
            resp = await FileMakerService._fm_find(settings.FM_RECADOS_LAYOUT, query)
            return await FileMakerService._parsear_respuesta_find(resp, "get_recados")

        try:
            return await con_reintentos(
                _buscar,
                max_intentos=2,
                backoff_base=1.0,
                nombre_operacion="FileMaker get_recados",
            )
        except ServicioNoDisponibleError:
            raise
        except CircuitBreakerAbierto as e:
            raise ServicioNoDisponibleError("FileMaker", str(e))
        except httpx.RequestError as e:
            raise ServicioNoDisponibleError("FileMaker", f"Error de conexion: {e}")
        except Exception as e:
            logger.error("Error inesperado al obtener recados: %s", e)
            raise ServicioNoDisponibleError("FileMaker", f"Error inesperado: {e}")

    @staticmethod
    async def get_user_by_phone(phone: str):
        """Busca usuario por telefono en FileMaker."""
        settings = get_settings()
        query = {
            "query": [
                {
                    "Telefono": phone,
                }
            ]
        }

        async def _buscar():
            resp = await FileMakerService._fm_find(settings.FM_AUTH_LAYOUT, query)

            if resp.status_code == 200:
                data = resp.json()['response']['data']
                if data:
                    user_data = data[0]['fieldData']
                    user_id = user_data.get('XUsuarioRRHH_Pk')
                    nombre = user_data.get('Nombre')
                    rol_str = user_data.get('ROL', '').lower().strip()
                    return User(phone=phone, id=user_id, name=nombre, role=rol_str)
                return None

            if resp.status_code == 500 and _es_sin_registros(resp):
                return None

            raise ServicioNoDisponibleError("FileMaker", f"get_user_by_phone: HTTP {resp.status_code}")

        try:
            return await con_reintentos(
                _buscar,
                max_intentos=2,
                backoff_base=1.0,
                nombre_operacion="FileMaker get_user_by_phone",
            )
        except ServicioNoDisponibleError:
            raise
        except CircuitBreakerAbierto as e:
            raise ServicioNoDisponibleError("FileMaker", str(e))
        except httpx.RequestError as e:
            raise ServicioNoDisponibleError("FileMaker", f"Error de conexion: {e}")
        except Exception as e:
            logger.error("Error inesperado al buscar usuario: %s", e)
            raise ServicioNoDisponibleError("FileMaker", f"Error inesperado: {e}")
