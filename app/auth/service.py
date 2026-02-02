from typing import Optional
from app.auth.models import User
from app.services.filemaker import FileMakerService

class AuthService:
    @staticmethod
    async def get_user_by_phone(phone: str) -> Optional[User]:
        """Look up user in FileMaker by phone number."""
        return await FileMakerService.get_user_by_phone(phone)
