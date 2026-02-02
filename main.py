from fastapi import FastAPI, Request, Response, BackgroundTasks, HTTPException
from app.config import VERIFY_TOKEN
from app.schemas import WSPPayload
from app.services.filemaker import FileMakerService
from app.services.whatsapp import WhatsAppService
from app.auth.service import AuthService
from app.auth.models import Role

app = FastAPI(title="Bot Clínica SkinMed")

async def process_doctor_request(name: str, phone: str):
    print(f"🚀 Iniciando procesamiento para el doctor: {name}")
    agenda_msg = await FileMakerService.get_agenda(name)
    print(f"📤 Intentando enviar mensaje por WSP a {name} - {phone}...")
    await WhatsAppService.send_message(phone, agenda_msg)
    print(f"✅ Proceso finalizado con éxito")

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
                print(f"⚠️ Usuario no autorizado o no registrado: {sender_phone}")
                return {"status": "ignored", "reason": "unauthorized"}

            print(f"✅ Usuario autenticado: {user.name} ({user.role})")

            if msg.type == "text":
                if user.role == Role.DOCTOR:
                    await WhatsAppService.send_template(sender_phone, user.name, "respuesta_inicial_doctores")
                elif user.role == Role.MANAGER:
                    await WhatsAppService.send_message(sender_phone, f"Hola Gerente {user.name}. Panel en construcción.")
                else:
                    await WhatsAppService.send_message(sender_phone, f"Hola {user.name}. Tu rol ({user.role}) no tiene flujo definido.")

            elif msg.type in ["interactive", "button"]:
                btn_title = ""
                if msg.type == "interactive":
                    btn_title = msg.interactive.button_reply.title
                elif msg.type == "button":
                    btn_title = msg.button.text
                
                print(f"🔘 Botón presionado: {btn_title} por {user.role}")

                if user.role == Role.DOCTOR:
                    if btn_title == "Revisar mi agenda del día":
                        background_tasks.add_task(process_doctor_request, user.name, sender_phone)
                    elif btn_title in ["Consultar cita paciente", "Consultar mis boxes"]:
                        await WhatsAppService.send_message(sender_phone, "Estamos trabajando en esta opción 🚧")
                    else:
                        await WhatsAppService.send_message(sender_phone, "Opción no reconocida")
                
                # Add handle for other roles here if needed
            
    except Exception as e:
        print(f"❌ Error en webhook: {e}")
        import traceback
        traceback.print_exc()
        
    return {"status": "ok"}