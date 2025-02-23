from fastapi import FastAPI, File, UploadFile
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

class PlateData(BaseModel):
    plate_number: str
    status: str

class UpdatePlateData(BaseModel):
    plate_number: str
    status: str

# Databasinställningar
DB_USER = "root"
DB_PASSWORD = "HG103961h"
DB_HOST = "localhost"
DB_PORT = 3306
DB_NAME = "license_plate_db"

# Skapa anslutning till MySQL
engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")
Session = sessionmaker(bind=engine)

app = FastAPI()

@app.on_event("startup")
async def log_endpoints():
    from fastapi.routing import APIRoute
    print("🚀 Server startar med följande endpoints:")
    for route in app.routes:
        print(f"🔍 {route.path} → {route.methods}")


# Initiera OCR
ocr = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)

@app.post("/AnalyzePicture")
async def analyze_picture(file: UploadFile = File(...)):
    # Läs in bilden från uppladdad fil
    image = Image.open(io.BytesIO(await file.read())).convert("L").resize((800, 600), Image.Resampling.LANCZOS)
    processed_image_path = "processed_image.jpg"
    image.save(processed_image_path)

    # Kör OCR på bilden
    ocr_result = ocr.ocr(processed_image_path, cls=False)
    print("OCR-resultat:", ocr_result)

    plate_number = None

    # Gå igenom OCR-resultatet för att hitta en giltig registreringsskylt
    for line in ocr_result:
        for word in line:
            if isinstance(word[1], tuple) and len(word[1]) == 2:
                text_value = word[1][0]
                confidence = word[1][1]
                print(f"Hittad text: {text_value}, Konfidens: {confidence}")

                # Kontrollera om texten matchar registreringsskyltformatet XXX 123A
                if re.match(r'^[A-Z]{3}\s?\d{2,3}[A-Z]?$', text_value) and confidence > 0.5:
                    plate_number = text_value.replace(" ", "")  # Ta bort mellanrum
                    break
        if plate_number:
            break

    # Kontrollera om vi hittade en registreringsskylt
    if plate_number:
        try:
            session = Session()
            # Kontrollera om registreringsskylten finns och hämta status
            check_query = sql_text("SELECT status FROM plates WHERE plate_number = :plate")
            result = session.execute(check_query, {"plate": plate_number}).fetchone()
            session.close()

            if result:
                status = result[0]
                # Kontrollera om statusen är "Godkänd"
                if status == "Godkänd":
                    # Anropa /ControlGate för att öppna grinden
                    async with httpx.AsyncClient() as client:
                        response = await client.post("http://127.0.0.1:8080/ControlGate", json={"action": "open"})
                        if response.status_code == 200:
                            return {
                                "plate_number": plate_number,
                                "status": status,
                                "action": "Grinden har öppnats."
                            }
                        else:
                            return {
                                "plate_number": plate_number,
                                "status": status,
                                "action": "Kunde inte öppna grinden."
                            }
                else:
                    return {"plate_number": plate_number, "status": status, "action": "Statusen tillåter inte grindöppning."}
            else:
                return {"message": "Registreringsnumret existerar inte i databasen."}

        except Exception as e:
            print(f"Databasfel: {e}")
            return {"error": str(e)}

    else:
        return {"message": "Ingen registreringsskylt kunde identifieras."}


@app.post("/AddPlate")
async def add_plate(plate: PlateData):
    try:
        session = Session()
        # Kontrollera om registreringsskylten redan finns
        check_query = sql_text("SELECT * FROM plates WHERE plate_number = :plate")
        existing = session.execute(check_query, {"plate": plate.plate_number}).fetchone()

        if existing:
            return {"message": "Registreringsnumret finns redan i databasen."}

        # Lägg till det nya registreringsnumret
        insert_query = sql_text("INSERT INTO plates (plate_number, status) VALUES (:plate, :status)")
        session.execute(insert_query, {"plate": plate.plate_number, "status": plate.status})
        session.commit()
        session.close()

        return {"message": "Registreringsnumret har lagts till."}

    except Exception as e:
        print(f"Databasfel: {e}")
        return {"error": str(e)}


@app.delete("/DeletePlate/{plate_number}")
async def delete_plate(plate_number: str):
    try:
        session = Session()
       
        delete_query = sql_text("DELETE FROM plates WHERE plate_number = :plate")
        session.execute(delete_query, {"plate": plate_number})
        session.commit()
        session.close()

        return {"message": "Registreringsnumret har tagits bort."}


    except Exception as e:
        print(f"Databasfel: {e}")
        return {"error": str(e)}


@app.put("/UpdatePlate")
async def update_plate(data: UpdatePlateData):
    try:
        session = Session()

        update_query = sql_text("UPDATE plates SET status = :status WHERE plate_number = :plate")
        result = session.execute(update_query, {"plate": data.plate_number, "status": data.status})
        session.commit()
        session.close()

        # 🛠 Kontrollera om skylten fanns i databasen
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Registreringsnumret hittades inte i databasen.")

        return {"message": f"Status för {data.plate_number} har uppdaterats till {data.status}."}

    except Exception as e:
        print(f"❌ Databasfel: {e}")
        raise HTTPException(status_code=500, detail=f"Databasfel: {str(e)}")

# 📲 Mobilappkommunikation
@app.post("/SendNotificationKnown")
# Skickar notis till appen vid känd bil.
async def send_notification():
    return {"message": "Notis-funktion inte implementerad än"}

@app.post("/SendNotificationUnknown")
# Skickar notis till appen vid okänd bil.
async def send_notification():
    return {"message": "Notis-funktion inte implementerad än"}

@app.post("/OpenGate")
# Öppnar grinden via mobilappen.
async def open_gate():
    return {"message": "Grinden är öppen!"}

@app.post("/CloseGate")
# Öppnar grinden via mobilappen.
async def open_gate():
    return {"message": "Grinden är stängd!"}

@app.post("/Login")
# Autentisering av användare i appen.
async def login():
    return {"message": "Inloggningsfunktion inte implementerad än"}


# 🗂️ Databashantering
@app.get("/GetRecentPlates")
# Hämtar senaste registreringsskyltarna.
async def get_recent_plates():
    return {"message": "Funktion för att hämta senaste skyltarna saknas"}

@app.get("/ListPlates")
async def list_plates():
    try:
        session = Session()
        query = sql_text("SELECT plate_number, status FROM plates")
        result = session.execute(query).fetchall()
        session.close()

        # Omvandla resultatet till en lista med dictionaries
        plates = [{"plate_number": row[0], "status": row[1]} for row in result]

        return {"plates": plates}  # Skickar tillbaka listan till frontend

    except Exception as e:
        print(f"Databasfel: {e}")
        return {"error": str(e)}


@app.put("/UpdatePlateStatus")
# Uppdaterar status på en registreringsskylt.
async def update_plate_status():
    return {"message": "Uppdateringsfunktion saknas"}

@app.delete("/DeletePlate")
# Tar bort en registreringsskylt från databasen.
async def delete_plate():
    return {"message": "Borttagningsfunktion saknas"}


# ⚙️ Systemstyrning
@app.post("/ControlGate")
# Styr grinden (öppna/stäng) från servern.
async def control_gate():
    # Kod för att öppna grinden och kollar så grinden faktiskt har öppnats
    return {"message": "Grindstyrningsfunktion saknas"}

@app.get("/GetSystemStatus")
# Hämtar aktuell systemstatus.
async def get_system_status():
    return {"message": "Systemstatusfunktion saknas"}





@app.post("/WebcamPicture")
async def webcam_picture():
    # IP-adressen för mobilens IP-webcam
    ip_webcam_url = "http://192.168.1.174:8080/shot.jpg"  # Ändra IP-adressen till din mobil

    # Hämta bilden från mobilkameran
    async with httpx.AsyncClient() as client:
        response = await client.get(ip_webcam_url)

    if response.status_code != 200:
        return {"error": "Kunde inte hämta bilden från mobilen"}

    # Läs in bilden för OCR
    image = Image.open(io.BytesIO(response.content)).convert("L").resize((800, 600), Image.Resampling.LANCZOS)
    processed_image_path = "processed_image.jpg"
    image.save(processed_image_path)

    # Kör OCR på bilden
    ocr_result = ocr.ocr(processed_image_path, cls=False)
    print("OCR-resultat:", ocr_result)

    plate_number = None

    # Analysera OCR-resultatet för registreringsskyltar
    if ocr_result:
        for line in ocr_result:
            if line:
                for word in line:
                    if isinstance(word[1], tuple) and len(word[1]) == 2:
                        text_value = word[1][0]
                        confidence = word[1][1]
                        print(f"Hittad text: {text_value}, Konfidens: {confidence}")

                        # Kontrollera om texten matchar formatet för en registreringsskylt
                        if re.match(r'^[A-Z]{3}\s?\d{2,3}[A-Z]?$', text_value) and confidence > 0.5:
                            plate_number = text_value.replace(" ", "")
                            break
                if plate_number:
                    break
    else:
        print("OCR-resultat var tomt – ingen text identifierades.")

    # Kontrollera om vi hittade en registreringsskylt
    if plate_number:
        try:
            session = Session()
            # Kontrollera om skylten finns i databasen
            check_query = sql_text("SELECT status FROM plates WHERE plate_number = :plate")
            result = session.execute(check_query, {"plate": plate_number}).fetchone()

            if result:
                status = result[0]
                if status == "Godkänd":
                    # Öppna grinden
                    async with httpx.AsyncClient() as client:
                        response = await client.post("http://127.0.0.1:8080/ControlGate", json={"action": "open"})
                        if response.status_code == 200:
                            return {
                                "plate_number": plate_number,
                                "status": status,
                                "action": "Grinden har öppnats."
                            }
                        else:
                            return {
                                "plate_number": plate_number,
                                "status": status,
                                "action": "Kunde inte öppna grinden."
                            }
                else:
                    # Skicka notis för Ej Godkänd registreringsskylt
                    async with httpx.AsyncClient() as client:
                        await client.post("http://127.0.0.1:8080/SendNotificationKnown", json={"plate_number": plate_number})
                    return {
                        "plate_number": plate_number,
                        "status": status,
                        "action": "Notis skickad för Ej Godkänd registreringsskylt."
                    }
            else:
                # Lägg till ny registreringsskylt som Ej Godkänd och skicka notis
                insert_query = sql_text("INSERT INTO plates (plate_number, status) VALUES (:plate, 'Ej Godkänd')")
                session.execute(insert_query, {"plate": plate_number})
                session.commit()
                session.close()

                async with httpx.AsyncClient() as client:
                    await client.post("http://127.0.0.1:8080/SendNotificationUnknown", json={"plate_number": plate_number})

                return {
                    "plate_number": plate_number,
                    "status": "Ej Godkänd",
                    "action": "Ny registreringsskylt tillagd och notis skickad."
                }

        except Exception as e:
            print(f"Databasfel: {e}")
            return {"error": str(e)}

    else:
        return {"message": "Ingen registreringsskylt kunde identifieras."}






if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
