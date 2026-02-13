"""
Configuracion centralizada con validacion de tipos via pydantic-settings.
Carga variables de entorno automaticamente, valida tipos y valores requeridos.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Configuracion de la aplicacion con validacion automatica."""

    # --- FileMaker ---
    FM_HOST: str = Field(default="fmsk.skinmed.cl", description="Host del servidor FileMaker")
    FM_DB: str = Field(default="Agenda%20v20b", description="Nombre de la base de datos FileMaker")
    FM_USER: str = Field(description="Usuario de FileMaker")
    FM_PASS: str = Field(description="Contraseña de FileMaker")

    # Layouts
    FM_AGENDA_LAYOUT: str = Field(default="ListadoDeHoras_dapi", description="Layout de agenda en FileMaker")
    FM_AUTH_LAYOUT: str = Field(default="AuthUsuarios_dapi", description="Layout de autenticacion en FileMaker")

    # --- WhatsApp ---
    WSP_TOKEN: str = Field(description="Token de WhatsApp Business API")
    WSP_PHONE_ID: str = Field(description="ID del telefono de WhatsApp")
    WSP_VERIFY_TOKEN: str = Field(description="Token de verificacion de webhook")
    WSP_APP_SECRET: str = Field(description="App Secret para firma HMAC-SHA256")
    META_API_VERSION: str = Field(default="v24.0", description="Version de la API de Meta Graph")

    # --- Redis ---
    REDIS_URL: str = Field(default="redis://localhost:6379/0", description="URL de conexion a Redis")

    # --- Logging ---
    LOG_LEVEL: str = Field(default="INFO", description="Nivel de logging (DEBUG, INFO, WARNING, ERROR)")

    # --- Entorno ---
    ENVIRONMENT: str = Field(default="development", description="Entorno de ejecucion (development, staging, production)")

    # --- Rate Limiting ---
    RATE_LIMIT_MAX: int = Field(default=30, description="Maximo de mensajes por ventana de rate limit")
    RATE_LIMIT_WINDOW: int = Field(default=60, description="Ventana de rate limit en segundos")

    # --- Message Limits ---
    MAX_MESSAGE_LENGTH: int = Field(default=500, description="Longitud maxima de mensaje de texto aceptado")

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


@lru_cache()
def get_settings() -> Settings:
    """
    Retorna la instancia de Settings cacheada.
    Se carga una sola vez y se reutiliza durante toda la vida de la app.
    Lanza ValidationError si faltan variables requeridas.
    """
    return Settings()


def validate():
    """
    Valida que todas las variables de entorno requeridas esten configuradas.
    Con pydantic-settings la validacion es automatica al instanciar Settings,
    pero mantenemos esta funcion para compatibilidad con main.py lifespan.
    """
    get_settings()  # Lanza ValidationError si faltan variables requeridas
