import os

# FileMaker Config
FM_HOST = "fmsk.skinmed.cl"
FM_DB = "Agenda%20v20b"
FM_USER = os.getenv("FM_USER")
FM_PASS = os.getenv("FM_PASS")

# Layouts
AGENDA_LAYOUT = "ListadoDeHoras_dapi"
AUTH_LAYOUT = "AuthUsuarios_dapi"

# WhatsApp Config
WSP_TOKEN = os.getenv("WSP_TOKEN")
WSP_PHONE_ID = os.getenv("WSP_PHONE_ID")
VERIFY_TOKEN = os.getenv("WSP_VERIFY_TOKEN")
META_API_VERSION = 'v24.0'
