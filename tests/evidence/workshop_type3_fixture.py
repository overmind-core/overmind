from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)


def main():
    writer = PdfWriter()
    text = "AI Security: Model Processing 123\x02"
    chars = sorted(set(text))
    procedures = DictionaryObject()
    mapping = []
    for code, char in enumerate(chars, 1):
        stream = DecodedStreamObject()
        stream.set_data(b"600 0 0 0 600 800 d1\n")
        procedures[NameObject(f"/C{code}")] = writer._add_object(stream)
        mapping.append(f"<{code:02x}> <{ord(char):04x}>")
    unicode = DecodedStreamObject()
    unicode.set_data(
        (
            "/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            "/CMapName /Evidence def /CMapType 2 def\n"
            "1 begincodespacerange <01> <ff> endcodespacerange\n"
            f"{len(chars)} beginbfchar\n" + "\n".join(mapping) + "\nendbfchar\n"
            "endcmap CMapName currentdict /CMap defineresource pop end end\n"
        ).encode()
    )
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type3"),
            NameObject("/FontBBox"): ArrayObject([NumberObject(0)] * 4),
            NameObject("/FontMatrix"): ArrayObject([NumberObject(n) for n in (1, 0, 0, 1, 0, 0)]),
            NameObject("/FirstChar"): NumberObject(1),
            NameObject("/LastChar"): NumberObject(len(chars)),
            NameObject("/Widths"): ArrayObject([NumberObject(600)] * len(chars)),
            NameObject("/CharProcs"): procedures,
            NameObject("/ToUnicode"): writer._add_object(unicode),
            NameObject("/Encoding"): DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Encoding"),
                    NameObject("/Differences"): ArrayObject([NumberObject(1), *procedures]),
                }
            ),
            NameObject("/Resources"): DictionaryObject(),
        }
    )
    font_id = writer._add_object(font)
    image = Image.new("RGB", (1600, 80), "white")
    ImageDraw.Draw(image).text(
        (0, 0), text, font=ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 64), fill="black"
    )
    bitmap = DecodedStreamObject()
    bitmap.set_data(image.tobytes())
    bitmap.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(image.width),
            NameObject("/Height"): NumberObject(image.height),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    bitmap_id = writer._add_object(bitmap.flate_encode())
    for rotation in (0, 90, 180, 270):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_id}),
                NameObject("/XObject"): DictionaryObject({NameObject("/I1"): bitmap_id}),
            }
        )
        content = DecodedStreamObject()
        codes = bytes(chars.index(char) + 1 for char in text).hex()
        content.set_data(
            f"q 500 0 0 25 50 700 cm /I1 Do Q\nBT /F1 0.02 Tf 50 700 Td <{codes}> Tj ET\n".encode()
        )
        page[NameObject("/Contents")] = writer._add_object(content)
        page.rotate(rotation)
    destination = Path(__file__).parents[1] / "fixtures/documents/type3.pdf"
    writer.write(destination)
    print(destination)


if __name__ == "__main__":
    main()
