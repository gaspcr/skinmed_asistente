from typing import Optional
from app.auth.models import User
from app.auth.data import USERS_DB

class AuthService:
    @staticmethod
    def get_user_by_phone(phone: str) -> Optional[User]:
        return USERS_DB.get(phone)
