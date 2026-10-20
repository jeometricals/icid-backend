"""
Filling NYC DOT's fillable PDFs (the Pedestrian Ramp Inspection Form and the Technical Infeasibility Form) with pypdf,
keeping them fillable: values go into the AcroForm's fields with appearances generated for them, never flattened.

`DotForm` is one copy of a template being filled: text, a dropdown's export value, radio and check boxes (a group's
widgets ticked one by one, so several boxes of one group can show ticked), a photo in an image button's appearance,
and text or images drawn on a page as its content (a ReportLab overlay merged into the page). The forms' own modules
hold their field maps; this module knows nothing of ICID's data.
"""

import io
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    StreamObject,
    TextStringObject,
)
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

logger = logging.getLogger(__name__)

register_heif_opener()  # lets Pillow open HEIC / HEIF photos

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
# A photo is shrunk to this long edge and saved as a JPEG of this quality before it goes into a form
PHOTO_MAX_EDGE_PX = 1600
PHOTO_JPEG_QUALITY = 80
CAPTION_FONT = "Helvetica"
CAPTION_FONT_PT = 7.0
OFF = "/Off"


@dataclass(frozen=True)
class PreparedImage:
    """A photo ready for a PDF: a baseline RGB JPEG and its size in pixels."""

    data: bytes
    width: int
    height: int


@dataclass
class _Field:
    """A form field: its dictionary (where /V lives) and its widgets, each with the index of its page."""

    field: DictionaryObject
    widgets: list[tuple[int, DictionaryObject]]


def load_template(file_name: str) -> bytes:
    """
    Read a DOT template from `templates/`.
    Takes the file's name; returns its bytes.
    """
    return (TEMPLATES_DIR / file_name).read_bytes()


def template_field_names(template: bytes) -> set[str]:
    """
    List every field a template's pages have a widget for.
    Takes the template's bytes; returns the fields' full names.
    """
    return set(_index_fields(_template_reader(template).pages))


def warn_unmapped(template_name: str, template_fields: set[str], mapped: set[str]) -> None:
    """
    Log one warning naming a template's fields that its field map never fills (and any mapped name it lacks).
    Takes the template's name, its fields and the names the map fills; returns nothing.
    """
    unmapped = sorted(template_fields - mapped)
    missing = sorted(mapped - template_fields)
    if unmapped:
        logger.warning("Unmapped DOT fields in %s (%d): %s", template_name, len(unmapped), ", ".join(unmapped))
    if missing:
        logger.warning("DOT field map names fields %s lacks: %s", template_name, ", ".join(missing))


def format_number(value: Any) -> str:
    """
    Print a measurement as the form wants it: the number alone, no trailing zeros (8.30 is 8.3, 48.0 is 48).
    Takes a Decimal, int, float, numeric text or None; returns the text, blank for None.
    """
    if value is None or value == "":
        return ""
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    if number == number.to_integral_value():
        return str(number.quantize(Decimal(1)))
    return format(number.normalize(), "f")


def as_date(value: Any) -> Optional[date]:
    """
    Read a day from what a row gives: a date, a datetime (its own day) or ISO text.
    Takes the value; returns the date, or None for None or blank.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def prepare_image(data: bytes) -> PreparedImage:
    """
    Make a photo small enough for a form: turned upright by its EXIF, long edge at most PHOTO_MAX_EDGE_PX, RGB on
    white, JPEG at PHOTO_JPEG_QUALITY. Takes the file's bytes (JPEG, PNG, HEIC, ...); returns the prepared image.
    """
    with Image.open(io.BytesIO(data)) as source:
        width, height = source.size
        long_edge = max(width, height)
        if long_edge > PHOTO_MAX_EDGE_PX:
            # A JPEG decodes straight to a smaller scale, far faster than decoding it whole
            source.draft("RGB", (-(-width * PHOTO_MAX_EDGE_PX // long_edge), -(-height * PHOTO_MAX_EDGE_PX // long_edge)))
        image = ImageOps.exif_transpose(source)
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            flat = Image.new("RGB", image.size, (255, 255, 255))
            flat.paste(image, mask=image.getchannel("A"))
            image = flat
        elif image.mode != "RGB":
            image = image.convert("RGB")
        image.thumbnail((PHOTO_MAX_EDGE_PX, PHOTO_MAX_EDGE_PX), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, "JPEG", quality=PHOTO_JPEG_QUALITY)
        return PreparedImage(out.getvalue(), image.width, image.height)


def fit_text(text: str, width: float, font: str = CAPTION_FONT, size: float = CAPTION_FONT_PT) -> str:
    """
    Cut a line of text to a width, ending it with an ellipsis when it had to be cut.
    Takes the text, the width in points and the font and size; returns the text that fits.
    """
    if stringWidth(text, font, size) <= width:
        return text
    while text and stringWidth(text + "...", font, size) > width:
        text = text[:-1]
    return text.rstrip() + "..."


def letterbox(box: tuple[float, float, float, float], width: float, height: float) -> tuple[float, float, float, float]:
    """
    Fit a picture into a box, its proportions kept, centred.
    Takes the box (x, y, width, height) and the picture's width and height; returns where it goes (x, y, w, h).
    """
    x, y, box_w, box_h = box
    scale = min(box_w / width, box_h / height)
    w, h = width * scale, height * scale
    return x + (box_w - w) / 2, y + (box_h - h) / 2, w, h


@lru_cache(maxsize=4)
def _template_reader(template: bytes) -> PdfReader:
    """
    Parse a template once per process; every copy is cloned from this reader, whose parsed objects are kept (cloning
    copies them, so the reader itself is never changed). Takes the template's bytes; returns its reader.
    """
    return PdfReader(io.BytesIO(template))


def _full_name(node: DictionaryObject) -> str:
    """
    Work out a field's full name from its /T and its parents'.
    Takes the field's dictionary; returns its dotted name.
    """
    parts: list[str] = []
    current: Optional[DictionaryObject] = node
    while current is not None:
        if "/T" in current:
            parts.insert(0, str(current["/T"]))
        parent = current.get("/Parent")
        current = parent.get_object() if parent is not None else None
    return ".".join(parts)


def _index_fields(pages: Iterable[Any]) -> dict[str, _Field]:
    """
    Find every field through its widgets, page by page.
    Takes the document's pages; returns the fields by full name.
    """
    fields: dict[str, _Field] = {}
    for page_index, page in enumerate(pages):
        annotations = page.get("/Annots")
        for ref in annotations.get_object() if annotations is not None else []:
            widget = ref.get_object()
            if widget.get("/Subtype") != "/Widget":
                continue
            field = widget if "/T" in widget else widget["/Parent"].get_object()
            name = _full_name(field)
            fields.setdefault(name, _Field(field, [])).widgets.append((page_index, widget))
    return fields


def _on_state(widget: DictionaryObject) -> Optional[str]:
    """
    Read the name of the state a check box or radio widget shows when ticked.
    Takes the widget; returns the state's name (e.g. '/TRUE'), or None when it has no such appearance.
    """
    normal = widget.get("/AP", DictionaryObject()).get("/N")
    if not isinstance(normal, DictionaryObject):
        return None
    states = [str(key) for key in normal.keys() if key != OFF]
    return states[0] if states else None


class DotForm:
    """One copy of a DOT template being filled; `output()` gives the filled PDF's bytes."""

    def __init__(self, template: bytes) -> None:
        """
        Open a fresh copy of a template.
        Takes the template's bytes.
        """
        self.writer = PdfWriter(clone_from=_template_reader(template))
        self.fields = _index_fields(self.writer.pages)
        self._text_by_page: dict[int, dict[str, str]] = {}
        self._choice_values: dict[str, str] = {}
        self._overlays: dict[int, list[Callable[[Canvas], None]]] = {}

    def _field(self, name: str) -> _Field:
        """
        Look a field up by name.
        Takes its full name; returns it, raising KeyError for a name the template lacks.
        """
        if name not in self.fields:
            raise KeyError(f"DOT form has no field {name!r}")
        return self.fields[name]

    def text(self, name: str, value: Optional[str]) -> None:
        """
        Set a text field (every widget of it). Blank or None leaves it empty.
        Takes the field's name and the text; returns nothing.
        """
        if value is None or value == "":
            return
        for page_index, _ in self._field(name).widgets:
            self._text_by_page.setdefault(page_index, {})[name] = value

    def choice(self, name: str, export_value: Optional[str]) -> None:
        """
        Set a dropdown to one of its options by export value; it shows the option's display text.
        Takes the field's name and the export value (None leaves it blank); returns nothing.
        """
        if export_value is None:
            return
        display = self.options(name).get(export_value)
        if display is None:
            raise ValueError(f"DOT field {name!r} has no option {export_value!r}")
        self._choice_values[name] = export_value
        for page_index, _ in self._field(name).widgets:
            self._text_by_page.setdefault(page_index, {})[name] = display

    def options(self, name: str) -> dict[str, str]:
        """
        Read a dropdown's options.
        Takes the field's name; returns its options' display text by export value.
        """
        options: dict[str, str] = {}
        for option in self._field(name).field.get("/Opt", []):
            option = option.get_object()
            if isinstance(option, list):
                options[str(option[0])] = str(option[1])
            else:
                options[str(option)] = str(option)
        return options

    def states(self, name: str) -> set[str]:
        """
        List the states a check box or radio group's widgets can show ticked.
        Takes the field's name; returns the states' names (e.g. {'/TRUE', '/FALSE'}).
        """
        return {state for _, widget in self._field(name).widgets if (state := _on_state(widget))}

    def tick(self, name: str, states: Iterable[str]) -> None:
        """
        Tick the widgets of a check box or radio group showing the given states, the rest off; several states tick
        several boxes of one group. Takes the field's name and the states (e.g. ['/TRUE']); returns nothing.
        """
        wanted = list(dict.fromkeys(states))
        unknown = set(wanted) - self.states(name)
        if unknown:
            raise ValueError(f"DOT field {name!r} has no state {sorted(unknown)}")
        if not wanted:
            return
        field = self._field(name)
        field.field[NameObject("/V")] = NameObject(wanted[0])
        for _, widget in field.widgets:
            state = _on_state(widget)
            widget[NameObject("/AS")] = NameObject(state if state in wanted else OFF)

    def check(self, name: str, value: Optional[bool]) -> None:
        """
        Tick a lone check box for True, clear it for False, leave it for None.
        Takes the field's name and the value; returns nothing.
        """
        if value is None:
            return
        field = self._field(name)
        if value:
            self.tick(name, self.states(name))
            return
        field.field[NameObject("/V")] = NameObject(OFF)
        for _, widget in field.widgets:
            widget[NameObject("/AS")] = NameObject(OFF)

    def image(self, name: str, image: PreparedImage, caption_band_pt: float = 0.0) -> None:
        """
        Put a photo into an image button: in its normal appearance and its icon (so Acrobat keeps it), letterboxed
        into the widget above a band left free for a caption. Takes the field's name, the image and the band's
        height in points; returns nothing.
        """
        stream = StreamObject()
        stream._data = image.data  # already DCT-encoded
        stream.update({
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(image.width),
            NameObject("/Height"): NumberObject(image.height),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
            NameObject("/Filter"): NameObject("/DCTDecode"),
        })
        image_ref = self.writer._add_object(stream)
        for _, widget in self._field(name).widgets:
            x1, y1, x2, y2 = (float(v) for v in widget["/Rect"])
            box_w, box_h = abs(x2 - x1), abs(y2 - y1)
            x, y, w, h = letterbox((1.0, caption_band_pt + 1.0, box_w - 2.0, box_h - caption_band_pt - 2.0),
                                   image.width, image.height)
            appearance = DecodedStreamObject()
            appearance.set_data(f"q {w:.3f} 0 0 {h:.3f} {x:.3f} {y:.3f} cm /Im0 Do Q".encode("ascii"))
            appearance.update({
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Form"),
                NameObject("/BBox"): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(box_w),
                                                  FloatObject(box_h)]),
                NameObject("/Resources"): DictionaryObject({
                    NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): image_ref}),
                }),
            })
            appearance_ref = self.writer._add_object(appearance)
            widget[NameObject("/AP")] = DictionaryObject({NameObject("/N"): appearance_ref})
            characteristics = DictionaryObject(widget.get("/MK", DictionaryObject()))
            characteristics.pop(NameObject("/BG"), None)  # a regenerated appearance must not paint over the caption
            characteristics[NameObject("/I")] = appearance_ref
            characteristics[NameObject("/TP")] = NumberObject(1)
            widget[NameObject("/MK")] = characteristics

    def widget_rects(self, name: str) -> list[tuple[int, tuple[float, float, float, float]]]:
        """
        Read where a field's widgets sit.
        Takes the field's name; returns each widget's page index and rectangle (x1, y1, x2, y2), normalised.
        """
        rects = []
        for page_index, widget in self._field(name).widgets:
            x1, y1, x2, y2 = (float(v) for v in widget["/Rect"])
            rects.append((page_index, (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))))
        return rects

    def draw(self, page_index: int, painter: Callable[[Canvas], None]) -> None:
        """
        Draw on a page as its content (under the form's widgets), merged at output.
        Takes the page's index and a function drawing on a ReportLab canvas the page's size; returns nothing.
        """
        self._overlays.setdefault(page_index, []).append(painter)

    def output(self) -> bytes:
        """
        Write the values and appearances, merge the drawings and save; the form stays fillable.
        Takes nothing; returns the PDF's bytes.
        """
        for page_index, values in self._text_by_page.items():
            self.writer.update_page_form_field_values(self.writer.pages[page_index], values, auto_regenerate=None)
        for name, export_value in self._choice_values.items():
            # The appearance shows the option's display text; the value is its export value
            self._field(name).field[NameObject("/V")] = TextStringObject(export_value)
        self.writer.set_need_appearances_writer(True)
        for page_index, painters in self._overlays.items():
            page = self.writer.pages[page_index]
            box = page.mediabox
            buffer = io.BytesIO()
            canvas = Canvas(buffer, pagesize=(float(box.width), float(box.height)))
            for painter in painters:
                painter(canvas)
            canvas.save()
            page.merge_page(PdfReader(io.BytesIO(buffer.getvalue())).pages[0])
        out = io.BytesIO()
        self.writer.write(out)
        return out.getvalue()


def draw_caption(form: DotForm, name: str, caption: str, band_pt: float) -> None:
    """
    Print a caption inside the band at the foot of an image button's (first) widget, on the page, cut to its width.
    Takes the form, the field's name, the caption and the band's height; returns nothing.
    """
    for page_index, (x1, y1, x2, _) in form.widget_rects(name)[:1]:  # a duplicated widget sits on the first
        text = fit_text(caption, x2 - x1 - 4.0)

        def paint(canvas: Canvas, x: float = x1 + 2.0, y: float = y1 + (band_pt - CAPTION_FONT_PT) / 2 + 1.0,
                  line: str = text) -> None:
            canvas.setFont(CAPTION_FONT, CAPTION_FONT_PT)
            canvas.drawString(x, y, line)

        form.draw(page_index, paint)

