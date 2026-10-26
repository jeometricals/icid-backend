"""
A sidewalk's tax-map frontage from DCP's MapPLUTO (Slice Y1): the lot's LotFront, in feet, asked for by its BBL, and
the release it came from as the source label (mappluto_26v2).

One call, no retry. A lot MapPLUTO doesn't have, or whose frontage is suspect (0 or less, 1000 or more), is None; a
service that can't be reached or answers with an error raises MapPlutoUnavailable.
"""

import logging
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional
from uuid import UUID

import httpx

from api.core.config import MAPPLUTO_QUERY_URL, MAPPLUTO_TIMEOUT_SECONDS
from api.queries.properties import set_property_tax_map

logger = logging.getLogger(__name__)

# DCP's borough codes, the BBL's first digit
BORO_CODES = {"Manhattan": "1", "Bronx": "2", "Brooklyn": "3", "Queens": "4", "Staten Island": "5"}
# A frontage outside these is taken as a bad record, not a lot (decision 12)
MIN_FRONTAGE = Decimal("0")
MAX_FRONTAGE = Decimal("1000")
CENT = Decimal("0.01")
# DCP's version names: 26v2, 25v1.1
VERSION = re.compile(r"^\d{2}v\d+(\.\d+)?$")


class MapPlutoUnavailable(Exception):
    """MapPLUTO couldn't be reached, or answered with an error or something that isn't its JSON."""


def bbl(boro: str, block: str, lot: str) -> str:
    """
    Build a lot's BBL as DCP numbers it: the borough's code, the block to 5 digits and the lot to 4.
    Takes the borough spelled out, and the block and lot as stored (no leading zeros).
    Returns the 10-digit BBL ("1008350041").
    """
    return f"{BORO_CODES[boro]}{int(block):05d}{int(lot):04d}"


def source_label(version: Any) -> Optional[str]:
    """
    Name where a frontage came from, as tax_map_source stores it.
    Takes MapPLUTO's Version ("26v2").
    Returns "mappluto_26v2", or None for a version that isn't DCP's form.
    """
    version = str(version or "").strip().lower()
    return f"mappluto_{version}" if VERSION.match(version) else None


def source_display(source: Optional[str]) -> Optional[str]:
    """
    Word a tax_map_source as the Construction Report and the site's page show it.
    Takes the source as stored.
    Returns "MapPLUTO 26v2", "Manual", "PIP", "R1k", or None for none.
    """
    if not source:
        return None
    if source.startswith("mappluto_"):
        return f"MapPLUTO {source.removeprefix('mappluto_')}"
    return {"manual": "Manual", "pip_scrape": "PIP", "r1k": "R1k"}.get(source, source)


def _query(lot_bbl: str) -> list[dict[str, Any]]:
    """
    Ask MapPLUTO for one lot's frontage and version.
    Takes the BBL.
    Returns the features' attributes (none, or one); raises MapPlutoUnavailable when the call fails.
    """
    params = {"where": f"BBL={lot_bbl}", "outFields": "BBL,LotFront,Version", "returnGeometry": "false", "f": "json"}
    try:
        response = httpx.get(MAPPLUTO_QUERY_URL, params=params, timeout=MAPPLUTO_TIMEOUT_SECONDS)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise MapPlutoUnavailable(f"MapPLUTO call failed: {exc}") from exc
    # ArcGIS reports a failed query as a 200 with an error object
    if not isinstance(body, dict) or "error" in body or not isinstance(body.get("features"), list):
        raise MapPlutoUnavailable(f"MapPLUTO answered with an error: {str(body)[:200]}")
    return [feature.get("attributes") or {} for feature in body["features"]]


def fetch_frontage_by_bbl(boro: str, block: str, lot: str) -> Optional[tuple[Decimal, str]]:
    """
    Look a lot's tax-map frontage up in MapPLUTO.
    Takes the borough spelled out, and the block and lot as stored.
    Returns (the frontage in feet to the cent, its source label), or None when MapPLUTO has no such lot or its record
    is suspect (logged); raises MapPlutoUnavailable when MapPLUTO can't be asked.
    """
    lot_bbl = bbl(boro, block, lot)
    features = _query(lot_bbl)
    if not features:
        logger.warning("MapPLUTO has no lot %s", lot_bbl)
        return None
    if len(features) > 1:
        logger.warning("MapPLUTO has %d records for lot %s; using the first", len(features), lot_bbl)
    attributes = features[0]
    try:
        frontage = Decimal(str(attributes.get("LotFront"))).quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        frontage = None
    if frontage is not None and not frontage.is_finite():
        frontage = None
    source = source_label(attributes.get("Version"))
    if frontage is None or not MIN_FRONTAGE < frontage < MAX_FRONTAGE or source is None:
        logger.warning("MapPLUTO's record for lot %s is suspect: LotFront=%r Version=%r", lot_bbl,
                       attributes.get("LotFront"), attributes.get("Version"))
        return None
    return frontage, source


def cache_frontage(property_id: UUID, boro: str, block: Optional[str], lot: Optional[str]) -> None:
    """
    Fetch a new or moved sidewalk's frontage and cache it on the property, after its response has gone (a background
    task). Nothing is raised: a lot MapPLUTO doesn't have, a suspect record, a service that can't be reached and a
    write that fails are logged, and the property keeps no frontage (an admin's refresh is the way back).
    Takes the property's id and the borough, block and lot it was saved at (a ped ramp's None, and then nothing is fetched).
    Returns nothing.
    """
    if block is None or lot is None:
        return
    try:
        found = fetch_frontage_by_bbl(boro, block, lot)
        if found is None:
            return
        if set_property_tax_map(property_id, boro, block, lot, *found) is None:
            logger.error("Failed to cache the MapPLUTO frontage of property %s", property_id)
    except Exception:
        logger.exception("Fetching the MapPLUTO frontage of property %s failed", property_id)
