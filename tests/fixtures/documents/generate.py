from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).parent


def scan():
    image = Image.new("RGB", (1200, 420), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=36)
    for index, text in enumerate(
        [
            "Service handbook",
            "Returns are accepted for thirty days.",
            "Standard delivery takes three days.",
            "Keep the original receipt for each order.",
        ]
    ):
        draw.text((50, 50 + index * 75), text, fill="black", font=font)
    return image


for name in ("scanned", "mixed", "native", "blank"):
    pdf = Canvas(str(ROOT / f"{name}.pdf"), pagesize=(612, 792), invariant=True)
    if name in ("mixed", "native"):
        pdf.setFont("Helvetica", 16)
        pdf.drawString(60, 700, "Invoice total: 1234.56 USD")
        pdf.showPage()
    if name in ("scanned", "mixed"):
        pdf.drawImage(ImageReader(scan()), 36, 480, width=540, height=189)
        pdf.showPage()
    if name == "mixed":
        pdf.setFont("Helvetica", 16)
        pdf.drawString(60, 720, "Shipping policy revision 7")
        pdf.drawImage(ImageReader(scan()), 36, 480, width=540, height=189)
        pdf.showPage()
    if name == "blank":
        pdf.showPage()
    pdf.save()
