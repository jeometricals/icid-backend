"""
A sidewalk's DOF tax-map PDF (Slice Y1.5): the current sheet of the NYC Digital Tax Map for its block, the official
lot lines and dimensions OE draws the sidewalk strip against. DOF says these maps are no substitute for a survey.

Two plain HTTP calls, no browser: DOF's public map library (an ArcGIS layer) names the block's current map
(current_map), and the Property Information Portal serves it as a PDF (fetch_map_pdf). A map covers a block, not a lot.
The PDF is kept in the private tax-maps bucket at {property id}/{DOF map id}.pdf (fetched) or
{property id}/upload-{random}.pdf (an admin's upload, staged at staging/{property id}/{random}.pdf), recorded on the
property, and handed out through signed URLs. A new PDF replaces the old one, whose file is removed (best-effort).
"""

import logging
import uuid
from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

import httpx

from api.core.config import (
    DOF_MAP_LIBRARY_URL,
    DOF_MAP_PDF_URL,
    DOF_TIMEOUT_SECONDS,
    TAX_MAP_BUCKET_NAME,
    TAX_MAP_MAX_BYTES,
    TAX_MAP_URL_EXPIRY_SECONDS,
)
from api.queries.properties import set_property_tax_map_pdf
from api.services.mappluto import BORO_CODES
from api.storage.client import (
    create_signed_upload_url,
    create_signed_url,
    download_file,
    object_exists,
    remove_files,
    upload_file,
)

logger = logging.getLogger(__name__)

PDF = "application/pdf"
UPLOAD_URL_LIFETIME = 7200  # what Supabase gives a signed upload URL, in seconds


class DofTaxMapUnavailable(Exception):
    """DOF's map library or portal couldn't be reached, or answered with an error or with something that isn't a PDF."""


class TaxMapPdfError(Exception):
    """A tax-map PDF that can't be stored: its status_code (400, 404, 413, 422, 502) and detail are the route's answer."""

    def __init__(self, status_code: int, detail: str) -> None:
        """
        Hold the refusal.
        Takes the HTTP status and the message.
        Returns nothing.
        """
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def current_map(boro: str, block: str) -> Optional[dict[str, Any]]:
    """
    Ask DOF's map library for a block's current tax map.
    Takes the borough spelled out and the block as stored.
    Returns {map_id, effective_date (a date, or None)}, the latest current one when DOF lists several, or None when DOF
    has none for the block; raises DofTaxMapUnavailable when the library can't be asked.
    """
    params = {"where": f"BOROUGH='{BORO_CODES[boro]}' AND BLOCK={int(block)} AND CURRENT_FLAG='Y'",
              "outFields": "ID,EFFECTIVE_DATE", "returnGeometry": "false", "f": "json"}
    try:
        response = httpx.get(DOF_MAP_LIBRARY_URL, params=params, timeout=DOF_TIMEOUT_SECONDS)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DofTaxMapUnavailable(f"DOF's map library call failed: {exc}") from exc
    if not isinstance(body, dict) or "error" in body or not isinstance(body.get("features"), list):
        raise DofTaxMapUnavailable(f"DOF's map library answered with an error: {str(body)[:200]}")
    maps = [feature.get("attributes") or {} for feature in body["features"]]
    maps = [m for m in maps if str(m.get("ID") or "").isdigit()]
    if not maps:
        logger.warning("DOF has no current tax map for %s block %s", boro, block)
        return None
    latest = max(maps, key=lambda m: str(m.get("EFFECTIVE_DATE") or ""))
    return {"map_id": str(latest["ID"]), "effective_date": _effective_date(latest.get("EFFECTIVE_DATE"))}


def _effective_date(value: Any) -> Optional[date]:
    """
    Read DOF's EFFECTIVE_DATE ("2013/07/25").
    Takes the value as DOF gives it.
    Returns the date, or None for one it can't read.
    """
    try:
        return datetime.strptime(str(value), "%Y/%m/%d").date()
    except ValueError:
        return None


def is_pdf(data: bytes) -> bool:
    """
    Tell a PDF by its first bytes.
    Takes the bytes.
    Returns True for a PDF.
    """
    return data[:5] == b"%PDF-"


def fetch_map_pdf(map_id: str) -> bytes:
    """
    Download one of DOF's tax maps from the Property Information Portal.
    Takes DOF's map id.
    Returns the PDF's bytes; raises DofTaxMapUnavailable when the portal can't be reached or answers with something
    that isn't a PDF of at most TAX_MAP_MAX_BYTES.
    """
    try:
        response = httpx.get(DOF_MAP_PDF_URL.format(map_id=map_id), timeout=DOF_TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise DofTaxMapUnavailable(f"DOF's portal call failed: {exc}") from exc
    data = response.content
    if not is_pdf(data) or len(data) > TAX_MAP_MAX_BYTES:
        raise DofTaxMapUnavailable(f"DOF's portal answered map {map_id} with something that isn't a PDF we keep")
    return data


def map_url(map_id: Optional[str]) -> Optional[str]:
    """
    The DOF portal's own address of a map, for a site's page to link to (it lists the block's inset maps too).
    Takes DOF's map id (None for an upload).
    Returns the URL, or None.
    """
    return DOF_MAP_PDF_URL.format(map_id=map_id) if map_id else None


def _record(prop: dict[str, Any], path: str, fields: dict[str, Any]) -> dict[str, Any]:
    """
    Record a stored PDF on its property and remove the file it replaced; on a refusal remove the new file instead.
    Takes the property, the stored file's path and the PDF's other columns.
    Returns the property as it now is; raises TaxMapPdfError 409 when the site moved to another block meanwhile.
    """
    row = set_property_tax_map_pdf(prop["id"], prop["boro"], prop["block"], {"tax_map_pdf_path": path, **fields})
    if row is None:
        _remove(path)
        raise TaxMapPdfError(409, "The site moved to another block meanwhile; nothing was changed")
    old = row.get("old_tax_map_pdf_path")
    if old and old != path:
        _remove(old)
    return row


def _remove(path: str) -> None:
    """
    Remove a file from the tax-maps bucket, best-effort.
    Takes the path.
    Returns nothing; a failure is logged.
    """
    try:
        remove_files([path], TAX_MAP_BUCKET_NAME)
    except Exception:  # noqa: BLE001 - Storage's errors vary; the file is merely left behind
        logger.exception("Could not remove tax-map file %s", path)


def store_current_map(prop: dict[str, Any]) -> dict[str, Any]:
    """
    Fetch a sidewalk's block's current tax map from DOF and keep it as the site's tax-map PDF.
    Takes the property (id, kind, boro, block).
    Returns the property as it now is; raises DofTaxMapUnavailable when DOF can't be asked, TaxMapPdfError 422 when DOF
    has no current map for the block, 502 when Storage fails and 409 when the site moved meanwhile.
    """
    found = current_map(prop["boro"], prop["block"])
    if found is None:
        raise TaxMapPdfError(422, f"DOF has no current tax map for {prop['boro']} block {prop['block']}")
    return store_map(prop, found, fetch_map_pdf(found["map_id"]))


def store_map(prop: dict[str, Any], found: dict[str, Any], data: bytes) -> dict[str, Any]:
    """
    Keep a map already fetched from DOF as a sidewalk's tax-map PDF (the backfill fetches a block's map once for all its
    sites).
    Takes the property (id, boro, block), the map as current_map found it ({map_id, effective_date}) and its PDF's bytes.
    Returns the property as it now is; raises TaxMapPdfError 502 when Storage fails and 409 when the site moved meanwhile.
    """
    path = f"{prop['id']}/{found['map_id']}.pdf"
    try:
        upload_file(TAX_MAP_BUCKET_NAME, path, data, PDF)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not store tax map %s: %s", path, exc)
        raise TaxMapPdfError(502, "Could not store the tax map") from exc
    return _record(prop, path, {"tax_map_pdf_source": "dof_map_library", "tax_map_pdf_map_id": found["map_id"],
                                "tax_map_pdf_effective_date": found["effective_date"]})


def cache_tax_map_pdf(property_id: UUID, boro: str, block: Optional[str]) -> None:
    """
    Fetch a new sidewalk's (or one moved to another block's) tax-map PDF after its response has gone (a background task).
    Nothing is raised: a block DOF has no map for, a portal that can't be reached and a write that fails are logged, and
    the site keeps no PDF (an admin's Refresh or upload is the way back).
    Takes the property's id and the borough and block it was saved at (a ped ramp's None, and then nothing is fetched).
    Returns nothing.
    """
    if block is None:
        return
    try:
        store_current_map({"id": property_id, "boro": boro, "block": block})
    except (DofTaxMapUnavailable, TaxMapPdfError) as exc:
        logger.warning("Tax map PDF of property %s not stored: %s", property_id, exc)
    except Exception:  # noqa: BLE001 - a background task must not raise
        logger.exception("Fetching the tax map PDF of property %s failed", property_id)


def _staging_folder(property_id: UUID) -> str:
    """
    The folder a site's tax-map uploads are staged in.
    Takes the property's id.
    Returns "staging/{property id}/".
    """
    return f"staging/{property_id}/"


def request_upload(property_id: UUID) -> dict[str, Any]:
    """
    Start an admin's upload of a site's tax-map PDF: a signed URL the client PUTs the PDF to, at a fresh staging path.
    Takes the property's id.
    Returns {upload_url, staging_path, expires_in}; raises TaxMapPdfError 502 when Storage can't sign one.
    """
    path = f"{_staging_folder(property_id)}{uuid.uuid4().hex}.pdf"
    try:
        url = create_signed_upload_url(path, TAX_MAP_BUCKET_NAME)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not sign a tax-map upload for %s: %s", path, exc)
        raise TaxMapPdfError(502, "Could not create the upload URL") from exc
    return {"upload_url": url, "staging_path": path, "expires_in": UPLOAD_URL_LIFETIME}


def complete_upload(prop: dict[str, Any], staging_path: str, uploaded_by: UUID) -> dict[str, Any]:
    """
    Finish an admin's upload: read the staged file back, check it is a PDF of at most TAX_MAP_MAX_BYTES, store it as the
    site's tax-map PDF and remove the staged file.
    Takes the property (id, boro, block), the staging path and the admin's uuid.
    Returns the property as it now is; raises TaxMapPdfError 400 for a path that isn't one of the site's uploads or a
    file that isn't a PDF, 404 for an upload that isn't there, 413 for one too large, 502 when Storage fails and 409
    when the site moved meanwhile.
    """
    folder = _staging_folder(prop["id"])
    name = staging_path[len(folder):] if staging_path.startswith(folder) else ""
    if not name or "/" in name or ".." in name:
        raise TaxMapPdfError(400, "staging_path is not an upload of this site")
    try:
        if not object_exists(TAX_MAP_BUCKET_NAME, staging_path):
            raise TaxMapPdfError(404, "No file at staging_path: upload it first")
        data = download_file(staging_path, TAX_MAP_BUCKET_NAME)
    except TaxMapPdfError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not read the tax-map upload %s: %s", staging_path, exc)
        raise TaxMapPdfError(502, "Could not read the upload") from exc
    if len(data) > TAX_MAP_MAX_BYTES:
        _remove(staging_path)
        raise TaxMapPdfError(413, "File too large")
    if not is_pdf(data):
        _remove(staging_path)
        raise TaxMapPdfError(400, "The file isn't a PDF")
    path = f"{prop['id']}/upload-{uuid.uuid4().hex}.pdf"
    try:
        upload_file(TAX_MAP_BUCKET_NAME, path, data, PDF)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not store tax map %s: %s", path, exc)
        raise TaxMapPdfError(502, "Could not store the tax map") from exc
    row = _record(prop, path, {"tax_map_pdf_source": "upload", "tax_map_pdf_uploaded_by": uploaded_by})
    _remove(staging_path)
    return row


def signed_pdf_url(prop: dict[str, Any]) -> str:
    """
    A time-limited URL for a site's tax-map PDF.
    Takes the property (tax_map_pdf_path, its block and borough for the file name).
    Returns the signed URL; raises TaxMapPdfError 502 when Storage can't sign one.
    """
    name = f"tax_map_{prop['boro'].replace(' ', '_')}_block_{prop['block']}.pdf"
    try:
        return create_signed_url(prop["tax_map_pdf_path"], TAX_MAP_URL_EXPIRY_SECONDS, name, TAX_MAP_BUCKET_NAME)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not sign the tax map %s: %s", prop["tax_map_pdf_path"], exc)
        raise TaxMapPdfError(502, "Could not create the download URL") from exc
