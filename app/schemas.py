from typing import List, Optional
from pydantic import BaseModel, Field

class Text(BaseModel):
    body: str

class Profile(BaseModel):
    name: str

class Contact(BaseModel):
    profile: Optional[Profile] = None
    wa_id: str

class Button(BaseModel):
    text: str
    payload: Optional[str] = None

class ButtonReply(BaseModel):
    id: str
    title: str

class Interactive(BaseModel):
    type: str
    button_reply: Optional[ButtonReply] = None

class Message(BaseModel):
    sender_phone: str = Field(alias="from") 
    id: str
    text: Optional[Text] = None
    interactive: Optional[Interactive] = None
    button: Optional[Button] = None
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
