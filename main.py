from fastapi import FastAPI, Request, Response, BackgroundTasks, HTTPException
from app.config import VERIFY_TOKEN
from app.schemas import WSPPayload
from app.auth.service import AuthService
from app.auth.models import Role
from app.workflows.doctor import DoctorWorkflow
from app.workflows.manager import ManagerWorkflow
from app.workflows.nurse import NurseWorkflow

app = FastAPI(title="Bot Clínica SkinMed")

# Workflow dispatcher
WORKFLOW_HANDLERS = {
    Role.DOCTOR: DoctorWorkflow(),
    Role.MANAGER: ManagerWorkflow(),
    Role.HEAD_NURSE: NurseWorkflow(),
}

def extract_button_title(msg) -> str:
    """Extract button title from interactive or button message"""
    if msg.type == "interactive":
        return msg.interactive.button_reply.title
    elif msg.type == "button":
        return msg.button.text
    return ""

@app.get("/webhook")
async def verify(request: Request):
    params = request.query_params
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(status_code=403, detail="Token de verificación inválido")

@app.post("/webhook")
async def webhook(payload: WSPPayload, background_tasks: BackgroundTasks):
    try:
        change = payload.entry[0].changes[0].value
        
        if change.messages:
            msg = change.messages[0]
            sender_phone = msg.sender_phone
            
            user = await AuthService.get_user_by_phone(sender_phone)
            if not user:
                return {"status": "ignored", "reason": "unauthorized"}

            handler = WORKFLOW_HANDLERS.get(user.role)
            if not handler:
                return {"status": "error", "reason": "no_handler_for_role"}

            if msg.type == "text":
                await handler.handle_text(user, sender_phone)
            
            elif msg.type in ["interactive", "button"]:
                btn_title = extract_button_title(msg)
                await handler.handle_button(user, sender_phone, btn_title, background_tasks)
            
    except Exception as e:
        print(f"❌ Error en webhook: {e}")
        import traceback
        traceback.print_exc()
        
    return {"status": "ok"}