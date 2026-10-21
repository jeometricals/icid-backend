"""
The sheets an IDR's imported sites bring into its export (W5), printed as unnumbered pages after their report's
attachments (export_attachments.PicturePage, one picture a page).

A sidewalk brings every live page of its property's live PIR, in page order. A ped ramp brings its corner's inspection,
once per export however many of the corner's ramps are imported: the DOT forms its latest approved inspection filed
(the inspection form only; a package's TIFs stay out), else ICID's own inspection report of the corner. PDFs are
rendered a page a picture (pir_image.render_pdf_pages), and every picture shrunk as a photo is (prepare_photo).

Everything here is best-effort: a file that can't be fetched, read or rendered is logged and left out, and the export
goes on without it.
"""

import io
import logging
import re
import zipfile
from typing import Any, Optional
from uuid import UUID

from api.core.config import PEDRAMP_EXPORT_BUCKET_NAME, PIR_IMAGE_BUCKET_NAME
from api.queries.idr_report_sites import idr_site_sheets
from api.queries.ped_ramp_corners import get_corner
from api.schemas.property import display_address_of
from api.services.export_attachments import Photo, PicturePage, prepare_photo
from api.services.ped_ramp_report import inspection_report, render_pdf
from api.services.pir_image import render_pdf_pages
from api.storage.client import download_file

logger = logging.getLogger(__name__)

# The inspection form's copies in a DOT package (ped_ramp_exports.dot_package): inspection.pdf, or inspection_1.pdf,
# inspection_2.pdf, ...; its TIFs (tif_*.pdf) don't match
INSPECTION_MEMBER = re.compile(r"inspection(?:_(\d+))?\.pdf")
DOT_TITLE = "DOT ped ramp inspection"
DOT_DESCRIPTION = "NYC DOT's Pedestrian Ramp Inspection Form, as filed when the corner's inspection was approved."
ICID_TITLE = "Ped ramp inspection (ICID)"
ICID_DESCRIPTION = "ICID's inspection report of the corner: it has no approved DOT form to print."


def _photo(data: bytes, what: str) -> Optional[Photo]:
    """
    Ready one picture for the workbook, best-effort.
    Takes the image's bytes and what it is (for the log).
    Returns the Photo, or None when it can't be read (logged).
    """
    try:
        return prepare_photo(data)
    except Exception as exc:  # noqa: BLE001 - one bad picture mustn't stop the export
        logger.warning("%s unreadable for the export: %s", what, exc)
        return None


def _rasterize(pdfs: list[bytes], what: str) -> list[Photo]:
    """
    Render PDFs a page a picture, each shrunk for the workbook.
    Takes the PDFs' bytes, in order, and what they are (for the log).
    Returns the pages' Photos, in order; a PDF that can't be rendered is logged and left out.
    """
    photos = []
    for pdf in pdfs:
        try:
            rendered = render_pdf_pages(pdf)
        except Exception as exc:  # noqa: BLE001 - a PDF that can't be rendered mustn't stop the export
            logger.warning("%s couldn't be rendered for the export: %s", what, exc)
            continue
        photos += [photo for png, _, _ in rendered if (photo := _photo(png, what)) is not None]
    return photos


def _pir_pages(site: dict[str, Any]) -> list[PicturePage]:
    """
    A sidewalk's sheets: each live page of its live PIR, fetched from the pir-images bucket.
    Takes the site row (idr_site_sheets).
    Returns a PicturePage per page that could be fetched and read, in page order (empty when the PIR has none).
    """
    address = display_address_of(site["kind"], site.get("address"), site.get("intersection_street_1"),
                                 site.get("intersection_street_2"), site.get("quadrant"), site.get("facing_street"))
    description = f"{site['boro']} Block {site['block']} Lot {site['lot']}" if site.get("block") else None
    pages = site.get("pir_pages") or []
    out = []
    for page in pages:
        what = f"PIR page {page['image_path']}"
        try:
            data = download_file(page["image_path"], PIR_IMAGE_BUCKET_NAME)
        except Exception as exc:  # noqa: BLE001 - a missing page mustn't stop the export
            logger.warning("%s unavailable for the export: %s", what, exc)
            continue
        photo = _photo(data, what)
        if photo is not None:
            title = f"PIR — {address} — page {page['page_number']} of {len(pages)}"
            out.append(PicturePage(title, description, photo))
    return out


def _dot_forms(path: str) -> list[bytes]:
    """
    Fetch the DOT inspection form an approved inspection filed: the one PDF, or a package's inspection copies in order
    (its TIFs left out).
    Takes the file's path in the pedramp-exports bucket.
    Returns the PDFs' bytes; empty when the file can't be fetched or read (logged).
    """
    try:
        data = download_file(path, PEDRAMP_EXPORT_BUCKET_NAME)
        if data.startswith(b"%PDF"):
            return [data]
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            members = [(m, INSPECTION_MEMBER.fullmatch(m)) for m in package.namelist()]
            copies = sorted((int(match.group(1) or 0), name) for name, match in members if match)
            return [package.read(name) for _, name in copies]
    except Exception as exc:  # noqa: BLE001 - the ICID report stands in
        logger.warning("DOT forms %s unavailable for the export: %s", path, exc)
        return []


def _icid_report(corner_id: UUID) -> list[bytes]:
    """
    Draw ICID's own inspection report of a corner (ped_ramp_report): the corner page and a page per ramp.
    Takes the corner's id.
    Returns the PDF's bytes in a list; empty when the corner or its ramps can't be read or drawn (logged).
    """
    try:
        corner = get_corner(corner_id)
        report = inspection_report(corner) if corner is not None else None
        if report is None:
            logger.warning("Corner %s couldn't be read for the export", corner_id)
            return []
        return [render_pdf(report)]
    except Exception as exc:  # noqa: BLE001 - a corner that can't be drawn mustn't stop the export
        logger.warning("Inspection report of corner %s couldn't be drawn for the export: %s", corner_id, exc)
        return []


def _corner_pages(site: dict[str, Any]) -> list[PicturePage]:
    """
    A ped ramp's sheets: its corner's DOT inspection form when an approved inspection filed one that can be read, else
    ICID's inspection report of the corner.
    Takes the site row (idr_site_sheets), its corner_id set.
    Returns a PicturePage per rendered page, in order (empty when neither can be had).
    """
    path = site.get("dot_pdf_path")
    title, description = DOT_TITLE, DOT_DESCRIPTION
    photos = _rasterize(_dot_forms(path), f"DOT forms {path}") if path else []
    if not photos:
        title, description = ICID_TITLE, ICID_DESCRIPTION
        photos = _rasterize(_icid_report(site["corner_id"]), f"Inspection report of corner {site['corner_id']}")
    corner = " ".join(part for part in (f"{site['north_south_street']} & {site['east_west_street']}",
                                        site.get("corner_direction")) if part)
    return [PicturePage(f"{title} — {corner} — page {number} of {len(photos)}", description, photo)
            for number, photo in enumerate(photos, start=1)]


def site_pages(idr_id: UUID, report_order: list[UUID]) -> dict[UUID, list[PicturePage]]:
    """
    Gather the sheets each report's imported sites bring, sites in the order they were added; a corner prints once,
    with the first report (in print order) and site that brings it.
    Takes the IDR's uuid and its reports' ids in print order.
    Returns {report id: its sites' PicturePages, in order}, reports without any left out; empty when the sites can't
    be read (logged).
    """
    rows = idr_site_sheets(idr_id)
    if rows is None:
        logger.error("The imported sites of IDR %s couldn't be read for its export", idr_id)
        return {}
    by_report: dict[UUID, list[dict[str, Any]]] = {}
    for row in rows:
        by_report.setdefault(row["report_id"], []).append(row)
    corners_done: set[UUID] = set()
    pages: dict[UUID, list[PicturePage]] = {}
    for report_id in report_order:
        for site in by_report.get(report_id, []):
            if site["kind"] == "sidewalk":
                sheets = _pir_pages(site)
            elif site.get("corner_id") is not None and site["corner_id"] not in corners_done:
                corners_done.add(site["corner_id"])
                sheets = _corner_pages(site)
            else:
                continue
            if sheets:
                pages.setdefault(report_id, []).extend(sheets)
    return pages
