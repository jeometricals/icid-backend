"""
Prints an IDR's report attachments on copies of the template's Sketch Cont page ("Attachments 1", "Attachments 2", ...).

Each attachment gets a page of its own under the continuation header: its name as a title and its description beneath,
then, for a photo, the photo fitted to the sketch grid. A PDF can't be drawn on a page, so its page names the file; a
photo that can't be fetched or read gets a page saying so. At most MAX_PHOTOS photos print per export; any past that
are counted on one closing page.

Photos are fetched from Storage on a few threads at once, then shrunk (and HEIC / WebP converted) with Pillow so the
workbook stays a reasonable size.
"""

import io
import logging
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from time import monotonic
from typing import Any, Optional
from uuid import UUID

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from api.services.export_common import (
    ContinuationHeader, TextArea, fill_lines, mark_truncated, paragraphs, stamp_continuation_header, text_value,
    write_lines,
)
from api.services.xlsx_template import WorkbookTemplate
from api.storage.client import download_file

logger = logging.getLogger(__name__)

register_heif_opener()  # lets Pillow open HEIC / HEIF photos

SKETCH_CONT = "Sketch Cont"
ATTACHMENTS = "Attachments"
PDF = "application/pdf"

MAX_PHOTOS = 50
FETCH_WORKERS = 4
# All of one export's fetches together; each Storage request also times out on its own (storage.client)
FETCH_BUDGET_SECONDS = 45

# Photos are stored at most this many pixels on their long side: about 150 dpi across the 646 px grid, printed
MAX_LONG_SIDE_PX = 1600
JPEG_QUALITY = 85
LOSSLESS_FORMATS = frozenset({"PNG", "GIF", "WEBP"})  # kept as PNG; photos (JPEG, HEIC) are saved as JPEG

# Sketch Cont's header sits a few rows below Report Cont's, in the same cells otherwise
SKETCH_CONT_HEADER = ContinuationHeader(
    project_cells={"G12": "project_id", "P12": "registration_code", "I13": "project_description", "F15": "borough"},
    date="I19", day_of_week=("I20", "J20", "K20", "L20", "M20", "N20", "O20"), ir_no="U19", sheet_no="AA19",
    inspector="H17",
)

# The page below the header: row 21 (empty, above the grid) holds the title, the grid's first four rows the description
# (10 pt across B:AI, 85 characters a line, as on Report Cont), and the rest of the grid the photo. Every column is
# 19 px and every grid row 17 px, so the photo area B26:AI58 is 34 x 19 = 646 px wide and 33 x 17 = 561 px tall.
TITLE_CELL = "B21"
TITLE_CHARS = 85
CAPTION = TextArea(rows=range(22, 26), column="B", line_chars=85)
PHOTO_COLUMN, PHOTO_ROW = "B", 26
COLUMN_PX, ROW_PX = 19, 17
PHOTO_AREA_PX = (34 * COLUMN_PX, 33 * ROW_PX)
NOTE_CELL = "B26"  # a PDF's or a missing photo's file name, where the photo would go


@dataclass
class Photo:
    """A photo ready to place: its bytes, file extension and size in pixels."""

    data: bytes
    extension: str
    width: int
    height: int


def prepare_photo(data: bytes) -> Photo:
    """
    Read a photo and shrink it for the workbook: upright (per its EXIF orientation), at most MAX_LONG_SIDE_PX on its
    long side, as JPEG (photos, HEIC included) or PNG (PNG, GIF's first frame, WebP).
    Takes the file's bytes.
    Returns the Photo; raises Pillow's error for a file it can't read.
    """
    with Image.open(io.BytesIO(data)) as source:
        lossless = source.format in LOSSLESS_FORMATS
        image = ImageOps.exif_transpose(source)
        image.thumbnail((MAX_LONG_SIDE_PX, MAX_LONG_SIDE_PX))
        output = io.BytesIO()
        if lossless:
            image.convert("RGBA" if "A" in image.getbands() else "RGB").save(output, "PNG", optimize=True)
        else:
            image.convert("RGB").save(output, "JPEG", quality=JPEG_QUALITY)
        return Photo(output.getvalue(), "png" if lossless else "jpeg", image.width, image.height)


def _fetch_photo(attachment: dict[str, Any]) -> Optional[Photo]:
    """
    Fetch and prepare one photo, treating any failure (missing file, timeout, unreadable image) as unavailable.
    Takes the attachment row.
    Returns the Photo, or None when it can't be had.
    """
    try:
        return prepare_photo(download_file(attachment["storage_path"]))
    except Exception as exc:  # noqa: BLE001 - one bad photo mustn't stop the export
        logger.warning("Attachment %s unavailable for export: %s", attachment["attachment_id"], exc)
        return None


def fetch_photos(candidates: list[dict[str, Any]]) -> dict[UUID, Optional[Photo]]:
    """
    Fetch photos for the export, FETCH_WORKERS at a time, until MAX_PHOTOS of them have arrived. A photo that can't
    be fetched doesn't count, so the next ones are tried in its place; once FETCH_BUDGET_SECONDS have gone, whatever
    hasn't arrived counts as unavailable.
    Takes the photo attachments, in print order.
    Returns each attempted attachment's Photo (None when unavailable); ones never attempted are absent.
    """
    results: dict[UUID, Optional[Photo]] = {}
    deadline = monotonic() + FETCH_BUDGET_SECONDS
    pool = ThreadPoolExecutor(max_workers=FETCH_WORKERS)
    try:
        remaining = list(candidates)
        arrived = 0
        while remaining and arrived < MAX_PHOTOS:
            batch, remaining = remaining[: MAX_PHOTOS - arrived], remaining[MAX_PHOTOS - arrived:]
            futures: dict[Future, dict[str, Any]] = {pool.submit(_fetch_photo, a): a for a in batch}
            done, _ = wait(futures, timeout=max(0.0, deadline - monotonic()))
            for future, attachment in futures.items():
                results[attachment["attachment_id"]] = future.result() if future in done else None
            arrived = sum(photo is not None for photo in results.values())
            if monotonic() >= deadline:
                results.update({a["attachment_id"]: None for a in remaining})
                break
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # don't hold the export for a fetch that ran past the budget
    return results


def _place_photo(workbook: WorkbookTemplate, sheet: str, photo: Photo, description: str) -> None:
    """
    Draw a photo as large as fits the photo area, keeping its proportions, centred in it.
    Takes the workbook, the sheet, the Photo and its alt text.
    Returns nothing.
    """
    area_width, area_height = PHOTO_AREA_PX
    scale = min(area_width / photo.width, area_height / photo.height)
    width, height = max(1, round(photo.width * scale)), max(1, round(photo.height * scale))
    left, top = (area_width - width) // 2, (area_height - height) // 2
    first_column = ord(PHOTO_COLUMN) - ord("A")
    cell = f"{chr(ord('A') + first_column + left // COLUMN_PX)}{PHOTO_ROW + top // ROW_PX}"
    workbook.add_picture(sheet, photo.data, photo.extension, cell, width, height,
                         offset_x_px=left % COLUMN_PX, offset_y_px=top % ROW_PX, description=description)


def _new_page(workbook: WorkbookTemplate, number: int, idr: dict[str, Any], project: dict[str, Any],
              inspector: Optional[str], title: str, description: Any) -> str:
    """
    Make the next attachment page: a copy of the blank Sketch Cont with its header, a bold title and the description.
    Takes the workbook, the page's number (Attachments <number>), the IDR row, the project row, the inspector's
    name, the title and the description (cut with "continued in ICID" past four lines).
    Returns the page's sheet name.
    """
    sheet = f"{ATTACHMENTS} {number}"
    workbook.clone_sheet(SKETCH_CONT, sheet)
    stamp_continuation_header(workbook, sheet, SKETCH_CONT_HEADER, idr, project, inspector)
    shown = title if len(title) <= TITLE_CHARS else title[: TITLE_CHARS - 3].rstrip() + "..."
    workbook.set_cell(sheet, TITLE_CELL, shown)
    workbook.set_style(sheet, TITLE_CELL, workbook.font_style(workbook.cell_style(sheet, TITLE_CELL), bold=True))
    workbook.align_left(sheet, TITLE_CELL)
    queue = paragraphs(description)
    lines = fill_lines(queue, len(CAPTION.rows), CAPTION.line_chars)
    if queue:
        lines = mark_truncated(lines, CAPTION.line_chars)
    write_lines(workbook, sheet, CAPTION.rows, lines, CAPTION.column)
    return sheet


def _write_note(workbook: WorkbookTemplate, sheet: str, note: str) -> None:
    """
    Write a one-line note where the photo would go (a PDF's file name, or why a photo is missing).
    Takes the workbook, the sheet and the note.
    Returns nothing.
    """
    workbook.set_cell(sheet, NOTE_CELL, note)
    workbook.align_left(sheet, NOTE_CELL)


def more_attachments_note(count: int) -> str:
    """
    Word the closing page's count of photos left out.
    Takes how many.
    Returns e.g. "1 more attachment in ICID" or "3 more attachments in ICID".
    """
    return f"{count} more attachment{'' if count == 1 else 's'} in ICID"


def render(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any], inspector: Optional[str],
           attachments: list[tuple[UUID, list[dict[str, Any]]]]) -> tuple[dict[UUID, list[str]], list[str]]:
    """
    Print the IDR's attachments, one page each, numbered Attachments 1, 2, ... in the order given: photos fitted to
    the page, PDFs and unavailable photos as notes naming the file, and, when more than MAX_PHOTOS photos arrived,
    a closing page counting the ones left out.
    Takes the workbook, the IDR row, the project row, the inspector's name and each report's uploaded attachments,
    reports in print order (each report's in upload order).
    Returns ({report id: its attachment pages, in order}, the closing page as a one-item list, or empty); each page
    is set to print on one Letter page, and the caller places and shows them.
    """
    ordered = [attachment for _, rows in attachments for attachment in rows]
    photos = fetch_photos([a for a in ordered if a["file_type"] != PDF])
    pages: dict[UUID, list[str]] = {}
    number = 0
    for report_id, rows in attachments:
        for attachment in rows:
            is_pdf = attachment["file_type"] == PDF
            if not is_pdf and attachment["attachment_id"] not in photos:
                continue  # past the photo cap: counted on the closing page
            photo = None if is_pdf else photos[attachment["attachment_id"]]
            name = text_value(attachment["attachment_name"]) or attachment["file_name"]
            title = f"PDF: {name}" if is_pdf else name if photo else f"Attachment unavailable: {name}"
            number += 1
            sheet = _new_page(workbook, number, idr, project, inspector, title, attachment["attachment_description"])
            if photo is not None:
                _place_photo(workbook, sheet, photo, name)
            elif is_pdf:
                _write_note(workbook, sheet, f"File: {attachment['file_name']} (a PDF: open it in ICID)")
            else:
                _write_note(workbook, sheet, f"File: {attachment['file_name']} (couldn't be fetched for this export)")
            workbook.fit_to_letter_page(sheet)
            pages.setdefault(report_id, []).append(sheet)

    left_out = sum(1 for a in ordered if a["file_type"] != PDF and a["attachment_id"] not in photos)
    closing = []
    if left_out:
        sheet = _new_page(workbook, number + 1, idr, project, inspector, more_attachments_note(left_out), None)
        workbook.fit_to_letter_page(sheet)
        closing.append(sheet)
    return pages, closing
