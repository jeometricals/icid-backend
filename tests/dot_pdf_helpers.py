"""Helpers the DOT PDF tests share: images made in memory, and reading a filled PDF back."""

import io
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
from PIL import Image
from pypdf import PdfReader


def jpeg(width: int, height: int, color: Any = (90, 120, 160), exif: bytes | None = None) -> bytes:
    """
    Make a plain JPEG.
    Takes its size, colour and optional EXIF block; returns the file's bytes.
    """
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "JPEG", quality=90, **({"exif": exif} if exif else {}))
    return out.getvalue()


def busy_jpeg(width: int, height: int) -> bytes:
    """
    Make a JPEG with photo-like detail (a fractal under noise), so its size after shrinking means something.
    Takes its size; returns the file's bytes.
    """
    fractal = Image.effect_mandelbrot((width, height), (-2.2, -1.2, 1.0, 1.2), 100).convert("RGB")
    noise = Image.effect_noise((width, height), 25).convert("RGB")
    out = io.BytesIO()
    Image.blend(fractal, noise, 0.3).save(out, "JPEG", quality=92)
    return out.getvalue()


def field_values(pdf: bytes) -> dict[str, Any]:
    """
    Read every field's value back.
    Takes the PDF; returns each field's /V (None when unset) by name.
    """
    return {name: field.get("/V") for name, field in (PdfReader(io.BytesIO(pdf)).get_fields() or {}).items()}


def widgets(pdf: bytes, name: str) -> list[tuple[int, Any]]:
    """
    Find a field's widgets.
    Takes the PDF and the field's name; returns each widget's page number (1 first) and dictionary.
    """
    found = []
    for number, page in enumerate(PdfReader(io.BytesIO(pdf)).pages, start=1):
        for ref in page.annotations or []:
            widget = ref.get_object()
            field = widget if "/T" in widget else widget.get("/Parent", {}).get_object() if "/Parent" in widget else {}
            if field.get("/T") == name:
                found.append((number, widget))
    return found


def page_image_xobjects(widget: Any) -> list[Any]:
    """
    Find the images an image button's normal appearance draws.
    Takes the widget; returns the image XObjects.
    """
    appearance = widget["/AP"]["/N"].get_object()
    images = appearance["/Resources"]["/XObject"]
    return [images[key].get_object() for key in images]


def flattened_text(pdf: bytes, page_number: int) -> str:
    """
    Read a page's text as printed, its fields' appearances included: the page is flattened with PDFium (a copy),
    saved, reopened and read. Takes the PDF and the page number (1 first); returns the text.
    """
    document = pdfium.PdfDocument(pdf)
    page = document[page_number - 1]
    pdfium_raw.FPDFPage_Flatten(page, pdfium_raw.FLAT_PRINT)
    page.close()
    out = io.BytesIO()
    document.save(out)
    return pdfium.PdfDocument(out.getvalue())[page_number - 1].get_textpage().get_text_bounded()


def dark_pixels(pdf: bytes, page_number: int, rect: tuple[float, float, float, float]) -> int:
    """
    Count the dark pixels a rendered page (forms drawn) has inside a rectangle.
    Takes the PDF, the page number and the rectangle in PDF points (x1, y1, x2, y2); returns the count.
    """
    document = pdfium.PdfDocument(pdf)
    document.init_forms()
    page = document[page_number - 1]
    height = page.get_height()
    image = page.render(scale=1, may_draw_forms=True).to_pil().convert("L")
    x1, y1, x2, y2 = rect
    crop = image.crop((int(x1), int(height - y2), int(x2), int(height - y1)))
    return sum(1 for value in crop.getdata() if value < 100)
