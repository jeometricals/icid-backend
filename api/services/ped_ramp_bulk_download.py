"""
A project's ped ramp inspections in one ZIP (Pedramp Phase 3, S5): every approved inspection the listing's filters
leave, a folder per corner holding what its approval stored (one DOT PDF, or a ZIP of DOT copies and TIF PDFs, unpacked
into the folder). The ZIP is built in memory, stored in the pedramp-exports bucket at bulk/{project id}/{uuid}.zip and
handed out as a signed URL, never as bytes (Vercel's responses stop at 4.5 MB).
"""

import io
import logging
import re
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from api.core.config import PEDRAMP_EXPORT_BUCKET_NAME, PEDRAMP_EXPORT_URL_EXPIRY_SECONDS
from api.storage.client import create_signed_url, download_file, upload_file

logger = logging.getLogger(__name__)

# The most corners one download takes
MAX_BULK_CORNERS = 100
# Downloads from Storage run this many at a time, to stay inside the function's time limit
DOWNLOAD_WORKERS = 8
FORM_TIMEZONE = ZoneInfo("America/New_York")
ZIP_MIME = "application/zip"
# A corner's one DOT PDF is named as the approval's ZIP names its first copy
SINGLE_PDF_NAME = "inspection.pdf"


class BulkDownloadError(Exception):
    """Storage couldn't store or sign the ZIP: a 502."""


def folder_name(row: dict[str, Any]) -> str:
    """
    Name a corner's folder: "{N-S}_and_{E-W}_{direction}", anything but letters, digits, dots and hyphens turned to underscores.
    Takes the listing row (north_south_street, east_west_street, corner_direction).
    Returns the folder's name.
    """
    parts = [row["north_south_street"], "and", row["east_west_street"]]
    if row.get("corner_direction"):
        parts.append(row["corner_direction"])
    name = re.sub(r"[^A-Za-z0-9.-]+", "_", "_".join(parts)).strip("._")
    return name or "corner"


def _member_name(name: str) -> Optional[str]:
    """
    Keep only a ZIP member's own file name, so nothing in a stored ZIP can reach outside its corner's folder.
    Takes the member's name.
    Returns the file name, or None for a folder entry or a name with nothing left.
    """
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return base if base not in ("", ".", "..") else None


def _fetch(path: str) -> Optional[bytes]:
    """
    Download one stored export.
    Takes its path in the pedramp-exports bucket.
    Returns its bytes, or None when it can't be fetched (logged).
    """
    try:
        return download_file(path, PEDRAMP_EXPORT_BUCKET_NAME)
    except Exception as exc:
        logger.warning("Ped ramp export %s could not be fetched, left out of the bulk download: %s", path, exc)
        return None


def _add_corner(archive: zipfile.ZipFile, folder: str, path: str, data: bytes) -> bool:
    """
    Put one corner's stored export into its folder: a PDF as it is, a ZIP's members unpacked.
    Takes the archive being written, the folder's name, the export's path and its bytes.
    Returns True when something was added; False for a stored ZIP that can't be read (logged).
    """
    if not path.lower().endswith(".zip"):
        archive.writestr(f"{folder}/{SINGLE_PDF_NAME}", data)
        return True
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as stored:
            for member in stored.infolist():
                name = _member_name(member.filename)
                if name is not None and not member.is_dir():
                    archive.writestr(f"{folder}/{name}", stored.read(member))
    except zipfile.BadZipFile as exc:
        logger.warning("Ped ramp export %s is not a readable ZIP, left out: %s", path, exc)
        return False
    return True


def build_bulk_zip(rows: list[dict[str, Any]]) -> tuple[bytes, int]:
    """
    Build the ZIP of the inspections' stored exports, a folder per corner (a second corner of the same name gets "_2"); one that can't be fetched or read is logged and left out.
    Takes the listing rows (each with dot_pdf_path and the corner's streets and direction).
    Returns (the ZIP's bytes, how many corners are in it).
    """
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        files = list(pool.map(_fetch, [row["dot_pdf_path"] for row in rows]))
    buffer = io.BytesIO()
    used: dict[str, int] = {}
    added = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for row, data in zip(rows, files):
            if data is None:
                continue
            base = folder_name(row)
            used[base] = used.get(base, 0) + 1
            folder = base if used[base] == 1 else f"{base}_{used[base]}"
            if _add_corner(archive, folder, row["dot_pdf_path"], data):
                added += 1
    return buffer.getvalue(), added


def bulk_download(project_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Build a project's bulk ZIP, store it at bulk/{project id}/{uuid}.zip and sign a URL to it.
    Takes the project's id and the listing rows to include.
    Returns {url, expires_at, corner_count, file_name}; raises BulkDownloadError when Storage can't store or sign it.
    """
    data, count = build_bulk_zip(rows)
    path = f"bulk/{project_id}/{uuid.uuid4().hex}.zip"
    today = datetime.now(FORM_TIMEZONE).date().isoformat()
    file_name = f"{project_id}_pedramp_inspections_{today}.zip"
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=PEDRAMP_EXPORT_URL_EXPIRY_SECONDS)
    try:
        upload_file(PEDRAMP_EXPORT_BUCKET_NAME, path, data, ZIP_MIME)
        url = create_signed_url(path, PEDRAMP_EXPORT_URL_EXPIRY_SECONDS, file_name, PEDRAMP_EXPORT_BUCKET_NAME)
    except Exception as exc:
        logger.error("Storage could not store or sign the bulk download %s: %s", path, exc)
        raise BulkDownloadError("Could not store the download") from exc
    return {"url": url, "expires_at": expires_at, "corner_count": count, "file_name": file_name}
