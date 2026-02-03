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
                print(f"DEBUG: Using cached user data for {phone} (expires in {(expires_at - now).seconds}s)")
                return user
            else:
                del cls._user_cache[phone]
        
        print(f"DEBUG: Fetching user from FileMaker for {phone}")
        user = await FileMakerService.get_user_by_phone(phone)
        
        if user:
            cls._user_cache[phone] = (user, now + cls._cache_duration)
            print(f"DEBUG: User cached for {phone}, expires at {(now + cls._cache_duration).strftime('%H:%M:%S')}")
        
        return user
    
    @classmethod
    def clear_cache(cls, phone: Optional[str] = None):
        """Clear user cache. If phone is provided, clear only that user."""
        if phone:
            cls._user_cache.pop(phone, None)
            print(f"DEBUG: Cleared cache for {phone}")
        else:
            cls._user_cache.clear()
            print("DEBUG: Cleared all user cache")
