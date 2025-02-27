from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, Response
from typing import List
import asyncio
from PIL import Image, ImageEnhance, ImageFilter
from paddleocr import PaddleOCR
import io
from sqlalchemy import create_engine, LargeBinary
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import text as sql_text
import pymysql
from pydantic import BaseModel
from typing import Optional
import re
import httpx


# 📌 Databasmodell
class PlateData(BaseModel):
    plate_number: str
    status: str
    image: Optional[bytes] = None

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

# 🔍 OCR-inställning
ocr = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)



async def async_ocr(image_path):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, ocr.ocr, image_path, False)



def process_image(image_bytes: bytes) -> str:
    """Tar en bild som bytes, optimerar för OCR och sparar den."""
    
    # 🖼️ Läs in bilden och konvertera till gråskala
    image = Image.open(io.BytesIO(image_bytes)).convert("L")

    # 🎭 Brusreducering – Gaussisk blur för att jämna ut bakgrund
    image = image.filter(ImageFilter.GaussianBlur(radius=1))

    # 🔍 Förbättra kontrast och skärpa
    enhancer = ImageEnhance.Contrast(image)
    image = enhancer.enhance(2.0)  # Öka kontrasten
    enhancer = ImageEnhance.Sharpness(image)
    image = enhancer.enhance(2.0)  # Öka skärpan

    # 🗜️ Optimera storlek och komprimering
    target_size = (800, 600)
    image = image.resize(target_size, Image.Resampling.LANCZOS)

    # 💾 Spara optimerad bild med bättre komprimering
    processed_image_path = "processed_image.jpg"
    image.save(processed_image_path, format="JPEG", quality=85, optimize=True)

    return processed_image_path




app = FastAPI()


@app.post("/SavePlateImage")
async def save_plate_image(plate_number: str, file: UploadFile = File(...)):
    session = Session()
    try:
        image_data = await file.read()  # Läs in bilden som binär data
        
        # Spara bilden i databasen
        insert_query = sql_text("UPDATE plates SET image = :image WHERE plate_number = :plate")
        session.execute(insert_query, {"image": image_data, "plate": plate_number})
        session.commit()

        return {"message": "Bilden har sparats i databasen"}
    except Exception as e:
        session.rollback()
        return {"error": str(e)}
    finally:
        session.close()


@app.get("/GetPlateImage/{plate_number}")
async def get_plate_image(plate_number: str):
    session = Session()
    try:
        query = sql_text("SELECT image FROM plates WHERE plate_number = :plate")
        result = session.execute(query, {"plate": plate_number}).fetchone()

        if result and result[0]:  # Om en bild hittas i databasen
            return Response(content=result[0], media_type="image/jpeg")
        else:
            return {"message": "Ingen bild hittades för detta registreringsnummer"}
    finally:
        session.close()


@app.post("/WebcamPicture")
async def webcam_picture():
    ip_webcam_url = "http://192.168.1.174:8080/shot.jpg"  # Ändra IP-adressen till din mobil

    async with httpx.AsyncClient() as client:
        response = await client.get(ip_webcam_url)

    if response.status_code != 200:
        return {"error": "Kunde inte hämta bilden från mobilen"}

    # 🔧 Använd process_image-funktionen för att optimera bilden
    processed_image_path = process_image(response.content)

    # 📸 Kör OCR på den optimerade bilden
    ocr_result = await async_ocr(processed_image_path)
    print("OCR-resultat:", ocr_result)

    plate_number = None
    if ocr_result:
        for line in ocr_result:
            for word in line:
                if isinstance(word[1], tuple) and len(word[1]) == 2:
                    text_value, confidence = word[1]
                    if re.match(r'^[A-Z]{3}\s?\d{2,3}[A-Z]?$', text_value) and confidence > 0.5:
                        plate_number = text_value.replace(" ", "")
                        break
            if plate_number:
                break

    if not plate_number:
        return {"message": "Ingen registreringsskylt kunde identifieras."}

    # 🔍 Kontrollera registreringsnumret i databasen
    try:
        session = Session()
        check_query = sql_text("SELECT status FROM plates WHERE plate_number = :plate")
        result = session.execute(check_query, {"plate": plate_number}).fetchone()

        if not result:
            # 🆕 Lägg till ny registreringsskylt som "Ej Godkänd"
            insert_query = sql_text("INSERT INTO plates (plate_number, status) VALUES (:plate, 'Ej Godkänd')")
            session.execute(insert_query, {"plate": plate_number})
            session.commit()

        # 📸 🔄 Skicka bilden till `/SavePlateImage`
        async with httpx.AsyncClient() as client:
            with open(processed_image_path, "rb") as image_file:
                response = await client.post(
                    "http://127.0.0.1:8080/SavePlateImage",
                    files={"file": image_file},
                    params={"plate_number": plate_number}
                )

        if response.status_code != 200:
            print(f"Fel vid bildsparning: {response.text}")

        # 🚪 Om skylten är godkänd → öppna grinden
        if result and result[0] == "Godkänd":
            async with httpx.AsyncClient() as client:
                response = await client.post("http://127.0.0.1:8080/OpenGate")
                if response.status_code == 200:
                    return {"plate_number": plate_number, "status": "Godkänd", "action": "Grinden har öppnats!"}
                else:
                    return {"plate_number": plate_number, "status": "Godkänd", "action": "Kunde inte öppna grinden."}

        return {"plate_number": plate_number, "status": "Ej Godkänd", "action": "Registreringsskylt sparad, notis skickad."}

    except Exception as e:
        print(f"Databasfel: {e}")
        return {"error": str(e)}
    finally:
        session.close()




# 📸 Analys av registreringsskylt

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
import io
import re
from PIL import Image, ImageEnhance, ImageFilter
from paddleocr import PaddleOCR
from sqlalchemy.sql import text as sql_text
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

app = FastAPI()

# 🔍 OCR-inställning
ocr = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)

# 📂 Databasinställningar
DB_USER = "root"
DB_PASSWORD = "HG103961h"
DB_HOST = "localhost"
DB_PORT = 3306
DB_NAME = "license_plate_db"

# 📂 Skapa databasanslutning
engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")
Session = sessionmaker(bind=engine)


def process_image(image_bytes: bytes) -> str:
    """Bearbetar bilden för bättre OCR igenkänning."""
    image = Image.open(io.BytesIO(image_bytes)).convert("L")

    # Förbättra kontrast och skärpa
    image = image.filter(ImageFilter.GaussianBlur(radius=1))
    enhancer = ImageEnhance.Contrast(image)
    image = enhancer.enhance(2.0)
    enhancer = ImageEnhance.Sharpness(image)
    image = enhancer.enhance(2.0)

    # Ändra storlek och optimera
    image = image.resize((800, 600), Image.Resampling.LANCZOS)

    processed_image_path = "processed_image.jpg"
    image.save(processed_image_path, format="JPEG", quality=85, optimize=True)

    return processed_image_path


@app.post("/AnalyzePicture")
async def analyze_picture(file: UploadFile = File(...)):
    try:
        # 🖼️ Läs in bilden
        image_data = await file.read()
        if not image_data or len(image_data) == 0:
            return JSONResponse(content={"error": "Ingen bild mottagen."}, status_code=400)

        processed_image_path = process_image(image_data)

        # 🔍 Kör OCR
        try:
            ocr_result = ocr.ocr(processed_image_path, cls=False)
            if ocr_result is None or ocr_result == [None]:  
                return JSONResponse(content={"status": "invalid", "message": "Kunde inte identifiera ett registreringsnummer."}, status_code=200)
        except Exception as e:
            return JSONResponse(content={"error": f"OCR-fel: {str(e)}"}, status_code=500)

        print("OCR-resultat:", ocr_result)

        # 🚗 Extrahera registreringsnummer
        plate_number = None
        if ocr_result:
            for line in ocr_result:
                if line is None:
                    continue
                for word in line:
                    if isinstance(word[1], tuple) and len(word[1]) == 2:
                        text_value, confidence = word[1]
                        if re.match(r'^[A-Z]{3}\s?\d{2,3}[A-Z]?$', text_value) and confidence > 0.5:
                            plate_number = text_value.replace(" ", "")
                            break
                if plate_number:
                    break

        # 🚨 Om ingen skylt hittas, returnera tydligt meddelande
        if not plate_number:
            return JSONResponse(content={"status": "invalid", "message": "Kunde inte identifiera ett registreringsnummer."}, status_code=200)

        # 🔍 Kontrollera registreringsnumret i databasen
        try:
            session = Session()
            check_query = sql_text("SELECT status FROM plates WHERE plate_number = :plate")
            result = session.execute(check_query, {"plate": plate_number}).fetchone()
            session.close()
        except Exception as e:
            return JSONResponse(content={"error": f"Databasfel: {str(e)}"}, status_code=500)

        # 🏷️ Hantering av databassvar
        if result:
            if result[0] == "Godkänd":
                return JSONResponse(content={"status": "valid", "plate_number": plate_number}, status_code=200)
            else:
                return JSONResponse(content={"status": "invalid", "message": "Registreringsnumret är ej tillåtet.", "plate_number": plate_number}, status_code=200)
        else:
            return JSONResponse(content={"status": "invalid", "message": "Kunde inte hitta registreringsnumret i databasen."}, status_code=200)

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)





# 🛠️ Lägg till en ny registreringsskylt
@app.post("/AddPlate")
async def add_plate(plate: PlateData):
    session = Session()
    try:
        check_query = sql_text("SELECT * FROM plates WHERE plate_number = :plate")
        existing = session.execute(check_query, {"plate": plate.plate_number}).fetchone()

        if existing:
            return {"message": "Registreringsnumret finns redan i databasen."}

        # Lägg till i databasen utan att inkludera image om den inte behövs
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

@app.put("/UpdatePlate")
async def update_plate_status(data: UpdatePlateData):
    session = Session()
    try:
        # Kontrollera om skylten finns i databasen
        check_query = sql_text("SELECT * FROM plates WHERE plate_number = :plate")
        existing = session.execute(check_query, {"plate": data.plate_number}).fetchone()

        if not existing:
            raise HTTPException(status_code=404, detail="Registreringsnumret finns inte i databasen.")

        # Uppdatera statusen
        update_query = sql_text("UPDATE plates SET status = :status WHERE plate_number = :plate")
        session.execute(update_query, {"plate": data.plate_number, "status": data.status})
        session.commit()

        return {"message": f"Status för {data.plate_number} har uppdaterats till {data.status}"}
    
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Ett fel uppstod: {str(e)}")

    finally:
        session.close()

@app.delete("/DeletePlate/{plate_number}")
async def delete_plate(plate_number: str):
    session = Session()
    try:
        # Kontrollera om skylten finns i databasen
        check_query = sql_text("SELECT * FROM plates WHERE plate_number = :plate")
        existing = session.execute(check_query, {"plate": plate_number}).fetchone()

        if not existing:
            raise HTTPException(status_code=404, detail="Registreringsnumret finns inte i databasen.")

        # Ta bort registreringsnumret
        delete_query = sql_text("DELETE FROM plates WHERE plate_number = :plate")
        session.execute(delete_query, {"plate": plate_number})
        session.commit()

        return {"message": f"Registreringsnumret {plate_number} har tagits bort"}
    
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Ett fel uppstod: {str(e)}")

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

# 📡 Hämta systemstatus
@app.get("/GetSystemStatus")
async def get_system_status():
    return {"message": "Systemstatus: Aktiv"}

@app.post("/OpenGate")
async def open_gate():
    return {"message": f"Grinden har öppnats!"}

@app.post("/CloseGate")
async def close_gate():
    return {"message": f"Grinden har stängts!"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
