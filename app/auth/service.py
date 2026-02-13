from typing import Optional

from app.auth.models import User
from app.services.filemaker import FileMakerService
from app.services import redis as redis_svc
from app.interaction_logger import log_auth


class AuthService:
    @classmethod
    async def get_user_by_phone(cls, phone: str) -> Optional[User]:
        """
        Obtiene usuario por numero de telefono con cache en Redis.
        Usuarios cacheados por 5 minutos para reducir llamadas a FileMaker.
        """
        cached = await redis_svc.get_json(f"auth:user:{phone}")
        if cached:
            user = User(**cached)
            log_auth(phone, user_name=user.name, role=user.role, status="cached")
            return user

        user = await FileMakerService.get_user_by_phone(phone)

        if user:
            await redis_svc.set_json(f"auth:user:{phone}", user.model_dump(), ttl=300)
            log_auth(phone, user_name=user.name, role=user.role, status="success")
        else:
            log_auth(phone, status="not_found")

        return user

    @classmethod
    async def clear_cache(cls, phone: Optional[str] = None):
        if phone:
            await redis_svc.delete(f"auth:user:{phone}")
