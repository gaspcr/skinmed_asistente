from enum import Enum
from pydantic import BaseModel

class Role(str, Enum):
    DOCTOR = "doctor"
    HEAD_NURSE = "enfermera_jefe"
    MANAGER = "gerente"
    

class User(BaseModel):
    phone: str
    name: str
    role: Role
