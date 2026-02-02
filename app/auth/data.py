from app.auth.models import User, Role

# Mapping phone -> User
# Replace with your actual phone numbers
USERS_DB = {
    "56948776414": User(phone="56948776414", name="Dr. Gubelin", role=Role.DOCTOR),
    # Add more users here:
    # "569..." : User(phone="...", name="Enfermera Jefe", role=Role.HEAD_NURSE),
}
