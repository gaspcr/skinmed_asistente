# DEPRECATED

from app.auth.models import User, Role

# Mapping phone -> User
# Replace with your actual phone numbers
USERS_DB = {
    "56948776414": User(phone="56948776414", name="Rodrigo", role=Role.DOCTOR),
    "56939129139": User(phone="56939129139", name="Gerente", role=Role.MANAGER),
    "56944250961": User(phone="56944250961", name="Enfermera Jefe", role=Role.HEAD_NURSE),
    # Add more users here:
    # "569..." : User(phone="...", name="Enfermera Jefe", role=Role.HEAD_NURSE),
}
