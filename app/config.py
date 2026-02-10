import os

# FileMaker Config
FM_HOST = os.getenv("FM_HOST", "fmsk.skinmed.cl")
FM_DB = os.getenv("FM_DB", "Agenda%20v20b")
FM_USER = os.getenv("FM_USER")
FM_PASS = os.getenv("FM_PASS")

# Layouts
AGENDA_LAYOUT = os.getenv("FM_AGENDA_LAYOUT", "ListadoDeHoras_dapi")
AUTH_LAYOUT = os.getenv("FM_AUTH_LAYOUT", "AuthUsuarios_dapi")

# WhatsApp Config
WSP_TOKEN = os.getenv("WSP_TOKEN")
WSP_PHONE_ID = os.getenv("WSP_PHONE_ID")
VERIFY_TOKEN = os.getenv("WSP_VERIFY_TOKEN")
WSP_APP_SECRET = os.getenv("WSP_APP_SECRET")
META_API_VERSION = os.getenv("META_API_VERSION", "v24.0")

# Redis Config
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


def validate():
    """
    Valida que todas las variables de entorno requeridas esten configuradas.
    Lanza RuntimeError con la lista de variables faltantes.
    """
    requeridas = {
        "FM_USER": FM_USER,
        "FM_PASS": FM_PASS,
        "WSP_TOKEN": WSP_TOKEN,
        "WSP_PHONE_ID": WSP_PHONE_ID,
        "WSP_VERIFY_TOKEN": VERIFY_TOKEN,
        "WSP_APP_SECRET": WSP_APP_SECRET,
    }
    faltantes = [nombre for nombre, valor in requeridas.items() if not valor]
    if faltantes:
        raise RuntimeError(
            f"Variables de entorno requeridas no configuradas: {', '.join(faltantes)}"
        )
