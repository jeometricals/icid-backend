"""
Ped ramp exports (Pedramp Phase 3): the DOT forms an approved inspection hands DOT, kept in the private pedramp-exports
bucket and handed out through signed URLs (a file runs 2-4 MB, past Vercel's 4.5 MB response limit with a few).

    inspections/{inspection id}/{random}.pdf   the DOT inspection form, when one copy holds the corner and it has no TIF
    inspections/{inspection id}/{random}.zip   otherwise: inspection.pdf (or inspection_1.pdf, inspection_2.pdf, ... for
                                               a corner of more ramps than one copy holds) and tif_{ramp}.pdf for each
                                               approved TIF
    tifs/{tif id}/{random}.pdf                 a TIF's form filled on demand (a draft's, to check it)

The package is filled and stored at approval, before the statement that approves the inspection, so a stored file
always has an approved inspection or none; one left behind by a lost race is logged.
"""

import io
import logging
import re
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from api.core.config import PEDRAMP_EXPORT_BUCKET_NAME, PEDRAMP_EXPORT_URL_EXPIRY_SECONDS
from api.queries.ped_ramp_attributes import list_corner_ramps
from api.queries.ped_ramp_corners import get_corner
from api.queries.ped_ramp_tifs import list_inspection_tifs
from api.services.dot_inspection_pdf import fill_inspection_forms
from api.services.dot_tif_pdf import fill_tif_form
from api.services.ped_ramp_photos import inspection_photo_bytes, tif_photo_bytes
from api.services.signatures import fetch_signature_file
from api.storage.client import create_signed_url, upload_file

logger = logging.getLogger(__name__)

CONTENT_TYPES = {"pdf": "application/pdf", "zip": "application/zip"}


class PedRampExportError(Exception):
    """The DOT forms couldn't be filled or stored; the endpoints turn it into a 502."""


def _safe(text: Optional[str]) -> str:
    """
    Make text safe for a file name: runs of anything but letters, digits, dots and dashes become one underscore.
    Takes the text (None for none).
    Returns it cleaned, without leading or trailing underscores.
    """
    return re.sub(r"[^A-Za-z0-9.-]+", "_", text or "").strip("_")


def corner_file_stem(corner: dict[str, Any]) -> str:
    """
    Name a corner's files by its streets and direction, as the inspection report is named.
    Takes the corner dict (its north_south_street, east_west_street and corner_direction).
    Returns the stem, as "3rd_Ave_and_38_Street_NE".
    """
    parts = [_safe(corner["north_south_street"]), "and", _safe(corner["east_west_street"]), _safe(corner.get("corner_direction"))]
    return "_".join(part for part in parts if part)


def tif_member_name(tif: dict[str, Any], index: int) -> str:
    """
    Name a TIF's PDF inside a package: by its ramp's label, else its place among the TIFs.
    Takes the TIF dict and its place (1 first).
    Returns the name, as "tif_CT-A.pdf" or "tif_ramp_2.pdf".
    """
    return f"tif_{_safe(tif.get('ramp_id_label')) or f'ramp_{index}'}.pdf"


def _signature(path: Optional[str]) -> Optional[bytes]:
    """
    Fetch a signature copy for a form, best-effort.
    Takes its path in the signatures bucket (None for none).
    Returns its bytes, or None when there is none or it can't be fetched (logged; the form prints without it).
    """
    if not path:
        return None
    try:
        return fetch_signature_file(path)
    except Exception as exc:
        logger.warning("Could not fetch the signature %s for a DOT form: %s", path, exc)
        return None


def fill_tif(tif: dict[str, Any], corner: dict[str, Any], ramps: list[dict[str, Any]]) -> bytes:
    """
    Fill DOT's TIF for one TIF, with its photos, its site layout and, once approved, its RE's signature.
    Takes the TIF dict, its corner and the corner's ramps (as list_corner_ramps returns them).
    Returns the PDF's bytes; raises PedRampExportError when its ramp isn't among the corner's or the form can't be filled.
    """
    ramp = next((row for row in ramps if str(row["property_id"]) == str(tif["property_id"])), None)
    if ramp is None:
        raise PedRampExportError("The TIF's ramp is no longer at its corner")
    photos, layout = tif_photo_bytes(tif["id"])
    try:
        return fill_tif_form(tif, corner, ramp, photos, layout, _signature(tif.get("signer_signature_path")))
    except Exception as exc:
        logger.error("Could not fill the DOT TIF for %s: %s", tif["id"], exc)
        raise PedRampExportError("Could not fill the DOT TIF") from exc


def dot_package(corner: dict[str, Any], ramps: list[dict[str, Any]], tifs: list[dict[str, Any]],
                photos: dict[str, list[tuple[int, Optional[str], bytes]]]) -> tuple[bytes, str]:
    """
    Fill an inspection's DOT forms: the inspection form (as many copies as its ramps need) and each approved TIF.
    Takes the corner, its ramps with their attributes, the inspection's TIFs and each ramp's photos by str(property_id).
    Returns (the bytes, 'pdf' for a single form or 'zip' for several); raises PedRampExportError when a form can't be filled.
    """
    try:
        forms = fill_inspection_forms(corner, ramps, photos).files
    except Exception as exc:
        logger.error("Could not fill the DOT inspection form for corner %s: %s", corner["corner_id"], exc)
        raise PedRampExportError("Could not fill the DOT inspection form") from exc
    members = [("inspection.pdf", forms[0])] if len(forms) == 1 else [
        (f"inspection_{number}.pdf", form) for number, form in enumerate(forms, start=1)
    ]
    approved = [tif for tif in tifs if tif["status"] == "approved"]
    members += [(tif_member_name(tif, index), fill_tif(tif, corner, ramps)) for index, tif in enumerate(approved, start=1)]
    if len(members) == 1:
        return members[0][1], "pdf"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        for name, data in members:
            package.writestr(name, data)
    return buffer.getvalue(), "zip"


def _store(path: str, data: bytes, kind: str) -> str:
    """
    Upload a file to the pedramp-exports bucket.
    Takes its path, its bytes and its kind ('pdf' or 'zip').
    Returns the path; raises PedRampExportError when Storage refuses it.
    """
    try:
        upload_file(PEDRAMP_EXPORT_BUCKET_NAME, path, data, CONTENT_TYPES[kind])
    except Exception as exc:
        logger.error("Could not store the ped ramp export %s: %s", path, exc)
        raise PedRampExportError("Could not store the DOT forms") from exc
    return path


def store_inspection_package(inspection: dict[str, Any]) -> str:
    """
    Fill an inspection's DOT forms as they stand and store them, ahead of the statement that approves it.
    Takes the inspection dict (its id and corner_id).
    Returns the stored file's path; raises PedRampExportError when they can't be read, filled or stored.
    """
    corner = get_corner(inspection["corner_id"])
    ramps = list_corner_ramps(inspection["corner_id"])
    tifs = list_inspection_tifs(inspection["id"])
    if corner is None or ramps is None or tifs is None:
        raise PedRampExportError("Could not read the inspection to fill its DOT forms")
    data, kind = dot_package(corner, ramps, tifs, inspection_photo_bytes(inspection["id"]))
    return _store(f"inspections/{inspection['id']}/{uuid4().hex}.{kind}", data, kind)


def store_tif(tif: dict[str, Any]) -> str:
    """
    Fill one TIF's DOT form as it stands and store it, to be downloaded.
    Takes the TIF dict (its id, corner_id and fields).
    Returns the stored file's path; raises PedRampExportError when it can't be read, filled or stored.
    """
    corner = get_corner(tif["corner_id"])
    ramps = list_corner_ramps(tif["corner_id"])
    if corner is None or ramps is None:
        raise PedRampExportError("Could not read the TIF's corner to fill its form")
    return _store(f"tifs/{tif['id']}/{uuid4().hex}.pdf", fill_tif(tif, corner, ramps), "pdf")


def signed_download(path: str, file_name: str) -> dict[str, Any]:
    """
    Sign a download URL for a stored ped ramp export.
    Takes its path in the pedramp-exports bucket and the name the browser saves it under.
    Returns {url, expires_at, file_name}; raises PedRampExportError when Storage can't sign one.
    """
    try:
        url = create_signed_url(path, PEDRAMP_EXPORT_URL_EXPIRY_SECONDS, file_name, PEDRAMP_EXPORT_BUCKET_NAME)
    except Exception as exc:
        logger.error("Could not sign a download of %s: %s", path, exc)
        raise PedRampExportError("Could not create a download link") from exc
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=PEDRAMP_EXPORT_URL_EXPIRY_SECONDS)
    return {"url": url, "expires_at": expires_at, "file_name": file_name}


def inspection_download_name(inspection: dict[str, Any], path: str) -> str:
    """
    Name an inspection's stored DOT forms as the browser saves them.
    Takes the inspection dict (its corner's streets and direction) and the stored path (its extension says pdf or zip).
    Returns the name, as "3rd_Ave_and_38_Street_NE_DOT_ped_ramp_inspection.zip".
    """
    return f"{corner_file_stem(inspection)}_DOT_ped_ramp_inspection.{path.rsplit('.', 1)[-1]}"


def tif_download_name(tif: dict[str, Any], corner: Optional[dict[str, Any]], tif_id: UUID) -> str:
    """
    Name one TIF's form as the browser saves it.
    Takes the TIF dict, its corner (None when unknown) and its id.
    Returns the name, as "3rd_Ave_and_38_Street_NE_TIF_CT-A.pdf".
    """
    stem = corner_file_stem(corner) if corner else str(tif_id)
    return f"{stem}_TIF_{_safe(tif.get('ramp_id_label')) or 'ramp'}.pdf"
