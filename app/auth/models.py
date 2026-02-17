from pydantic import BaseModel

class User(BaseModel):
    phone: str
    id: str  # FileMaker primary key (XUsuarioRRHH_Pk)
    name: str
    role: str  # Dynamic role from FileMaker, validated by workflow registry

