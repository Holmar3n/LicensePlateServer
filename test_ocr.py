from PIL import Image
from paddleocr import PaddleOCR
import os

image_path = "routes/regskylt.jpg"

if not os.path.isfile(image_path):
    raise FileNotFoundError(f"Filen '{image_path}' hittades inte. Kontrollera sökvägen.")

img = Image.open(image_path).convert("L").resize((800, 600), Image.Resampling.LANCZOS)
processed_image_path = "processed_image.jpg"
img.save(processed_image_path)

ocr = PaddleOCR(
    use_angle_cls=False,
    lang="en",
    det_db_thresh=0.5,
    det_limit_side_len=512,
    cpu_threads=16,
    use_gpu=False,
    use_pdserving=False,
    show_log=False,
    savefile=False,
    layout=False,
    table=False,
    max_text_length=6,
    rec_image_shape="3, 48, 320",
)

result = ocr.ocr(processed_image_path, cls=False)

if result and result[0]:
    plate_number = result[0][0][1][0]
    print("✅ Identifierad registreringsskylt:", plate_number)

    if plate_number == "HET 69A":
        print("✅ Godkänd")
    else:
        print("❌ Ej Godkänd")
else:
    print("❌ Ingen registreringsskylt kunde identifieras.")
