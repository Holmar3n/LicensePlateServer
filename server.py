from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect, HTTPException
from typing import List
import asyncio
from PIL import Image
from paddleocr import PaddleOCR
import io
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import text as sql_text
import pymysql
from pydantic import BaseModel
import re
import httpx

# 📌 Databasmodell
class PlateData(BaseModel):
    plate_number: str
    status: str

class UpdatePlateData(BaseModel):
    plate_number: str
    status: str

# 📂 Databasinställningar
DB_USER = "root"
DB_PASSWORD = "HG103961h"
DB_HOST = "localhost"
DB_PORT = 3306
DB_NAME = "license_plate_db"

# 📂 Skapa databasanslutning
engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")
Session = sessionmaker(bind=engine)

app = FastAPI()

# 🔗 WebSocket-klienter
active_connections: List[WebSocket] = []

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            await websocket.receive_text()  # Väntar på klientmeddelanden (kan vara tomt)
    except WebSocketDisconnect:
        active_connections.remove(websocket)  # Ta bort från aktiva anslutningar vid frånkoppling

# 📡 Skicka notis till WebSocket-klienter
async def send_notification_to_clients(message: str):
    for connection in active_connections:
        try:
            await connection.send_text(message)
        except:
            active_connections.remove(connection)

# 🔍 OCR-inställning
ocr = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)

# 📸 Analys av registreringsskylt
@app.post("/AnalyzePicture")
async def analyze_picture(file: UploadFile = File(...)):
    image = Image.open(io.BytesIO(await file.read())).convert("L").resize((800, 600), Image.Resampling.LANCZOS)
    processed_image_path = "processed_image.jpg"
    image.save(processed_image_path)

    ocr_result = ocr.ocr(processed_image_path, cls=False)

    plate_number = None
    for line in ocr_result:
        for word in line:
            if isinstance(word[1], tuple) and len(word[1]) == 2:
                text_value = word[1][0]
                confidence = word[1][1]

                if re.match(r'^[A-Z]{3}\s?\d{2,3}[A-Z]?$', text_value) and confidence > 0.5:
                    plate_number = text_value.replace(" ", "")
                    break
        if plate_number:
            break

    if plate_number:
        session = Session()
        try:
            check_query = sql_text("SELECT status FROM plates WHERE plate_number = :plate")
            result = session.execute(check_query, {"plate": plate_number}).fetchone()

            if result:
                status = result[0]
                if status == "Godkänd":
                    async with httpx.AsyncClient() as client:
                        response = await client.post("http://127.0.0.1:8080/ControlGate", json={"action": "open"})
                        if response.status_code == 200:
                            return {"plate_number": plate_number, "status": status, "action": "Grinden har öppnats."}
                        else:
                            return {"plate_number": plate_number, "status": status, "action": "Kunde inte öppna grinden."}
                else:
                    return {"plate_number": plate_number, "status": status, "action": "Statusen tillåter inte grindöppning."}
            else:
                return {"message": "Registreringsnumret existerar inte i databasen."}
        except Exception as e:
            return {"error": str(e)}
        finally:
            session.close()
    else:
        return {"message": "Ingen registreringsskylt kunde identifieras."}

# 🛠️ Lägg till en ny registreringsskylt
@app.post("/AddPlate")
async def add_plate(plate: PlateData):
    session = Session()
    try:
        check_query = sql_text("SELECT * FROM plates WHERE plate_number = :plate")
        existing = session.execute(check_query, {"plate": plate.plate_number}).fetchone()

        if existing:
            return {"message": "Registreringsnumret finns redan i databasen."}

        insert_query = sql_text("INSERT INTO plates (plate_number, status) VALUES (:plate, :status)")
        session.execute(insert_query, {"plate": plate.plate_number, "status": plate.status})
        session.commit()
        return {"message": "Registreringsnumret har lagts till."}
    except Exception as e:
        return {"error": str(e)}
    finally:
        session.close()

# 🔍 Hämta alla registreringsskyltar
@app.get("/ListPlates")
async def list_plates():
    session = Session()
    try:
        query = sql_text("SELECT plate_number, status FROM plates")
        result = session.execute(query).fetchall()

        plates = [{"plate_number": row[0], "status": row[1]} for row in result]
        return {"plates": plates}
    except Exception as e:
        return {"error": str(e)}
    finally:
        session.close()

# 🔔 Skicka notis vid känd bil
@app.post("/SendNotificationKnown")
async def send_notification_known(plate_number: str):
    message = f"Känd bil identifierad: {plate_number}"
    await send_notification_to_clients(message)
    return {"message": "Notis skickad till appen."}

# 🔔 Skicka notis vid okänd bil
@app.post("/SendNotificationUnknown")
async def send_notification_unknown(plate_number: str):
    message = f"Okänd bil identifierad: {plate_number}"
    await send_notification_to_clients(message)
    return {"message": "Notis skickad till appen."}

# 🚪 Styr grinden
@app.post("/ControlGate")
async def control_gate(action: str):
    return {"message": f"Grinden {action}!"}

# 📡 Hämta systemstatus
@app.get("/GetSystemStatus")
async def get_system_status():
    return {"message": "Systemstatus: Aktiv"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
