from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta
from app.auth.models import User
from app.services.filemaker import FileMakerService

class AuthService:
    _user_cache: Dict[str, Tuple[User, datetime]] = {}
    _cache_duration = timedelta(minutes=5)
    
    @classmethod
    async def get_user_by_phone(cls, phone: str) -> Optional[User]:
        """
        Get user by phone number with caching.
        Users are cached for 5 minutes to reduce FileMaker calls.
        """
        now = datetime.now()
        
        if phone in cls._user_cache:
            user, expires_at = cls._user_cache[phone]
            if now < expires_at:
                return user
            else:
                del cls._user_cache[phone]
        
        user = await FileMakerService.get_user_by_phone(phone)
        
        if user:
            cls._user_cache[phone] = (user, now + cls._cache_duration)
        
        return user
    
    @classmethod
    def clear_cache(cls, phone: Optional[str] = None):
        if phone:
            cls._user_cache.pop(phone, None)
        else:
            cls._user_cache.clear()
