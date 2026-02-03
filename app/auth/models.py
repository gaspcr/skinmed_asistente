from pydantic import BaseModel

class User(BaseModel):
    phone: str
    name: str
    role: str  # Dynamic role from FileMaker, validated by workflow registry

