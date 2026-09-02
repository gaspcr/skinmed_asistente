from typing import List, Optional
from pydantic import BaseModel, Field

class Text(BaseModel):
    body: str

class Profile(BaseModel):
    name: str
    username: Optional[str] = None

class Contact(BaseModel):
    profile: Optional[Profile] = None
    wa_id: Optional[str] = None
    user_id: Optional[str] = None
    parent_user_id: Optional[str] = None

class Button(BaseModel):
    text: str
    payload: Optional[str] = None

class ButtonReply(BaseModel):
    id: str
    title: str

class Interactive(BaseModel):
    type: str
    button_reply: Optional[ButtonReply] = None

class DocumentoWsp(BaseModel):
    id: str
    mime_type: Optional[str] = None
    filename: Optional[str] = None
    caption: Optional[str] = None

class ImagenWsp(BaseModel):
    id: str
    mime_type: Optional[str] = None
    sha256: Optional[str] = None
    caption: Optional[str] = None

class Message(BaseModel):
    sender_phone: Optional[str] = Field(default=None, alias="from")
    from_user_id: Optional[str] = None
    from_parent_user_id: Optional[str] = None
    id: str
    text: Optional[Text] = None
    interactive: Optional[Interactive] = None
    button: Optional[Button] = None
    document: Optional[DocumentoWsp] = None
    image: Optional[ImagenWsp] = None
    type: str

    model_config = {"populate_by_name": True}

class Value(BaseModel):
    messaging_product: str
    messages: Optional[List[Message]] = None
    contacts: Optional[List[Contact]] = None

class Change(BaseModel):
    value: Value
    field: str

class Entry(BaseModel):
    id: str
    changes: List[Change]

class WSPPayload(BaseModel):
    object: str
    entry: List[Entry]

class SolicitudFotoRequest(BaseModel):
    """Body del endpoint interno que FileMaker llama para disparar la solicitud de foto al TENS.
    Nunca debe incluir datos identificables del paciente (nombre/RUT)."""
    token: str
    telefono: str
