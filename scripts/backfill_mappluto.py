"""
One-off, safe to repeat: fetch the MapPLUTO tax-map frontage of every live sidewalk that has none yet (Slice Y1).

A sidewalk created since Slice Y1 is fetched as it is created; this does the same for the ones created before. For each
live sidewalk without a frontage it asks MapPLUTO for its lot, then, with --commit, writes it as an admin's refresh does
(api.queries.properties.refresh_property_tax_map, one statement per sidewalk): onto the property, and onto the title
blocks of its live PIR's pages whose width wasn't typed by hand (page 1 getting a title block if it has none). Without
--commit nothing is written: each sidewalk's MapPLUTO frontage and address are printed beside the width its page 1
holds now, for review, and a lot whose MapPLUTO address has another house number than ours is marked CHECK (its BBL
may name the wrong lot). Running it again skips the sidewalks already fetched. --skip-bbl leaves a lot out (repeat it
for several): a site whose BBL is wrong is then neither asked about nor written, and keeps no frontage.

--depth (pilot sweep #2, after migration 065) does the same for the lot depth of the sidewalks that have a frontage but
no depth, writing only the depth (the frontage, its source and the title blocks are left as they are); a lot whose
MapPLUTO frontage is now another than ours is marked CHECK.

It works on the database DATABASE_URL names (.env). Run from the project root, after migration 058:
    python scripts/backfill_mappluto.py            # dry run
    python scripts/backfill_mappluto.py --commit --skip-bbl 3012340051
    python scripts/backfill_mappluto.py --depth    # dry run; --depth --commit writes
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.queries.properties import (  # noqa: E402
    list_sidewalks_without_tax_map,
    list_sidewalks_without_tax_map_depth,
    refresh_property_tax_map,
    set_property_tax_map_depth,
)
from api.services.mappluto import MapPlutoUnavailable, bbl, lookup_lot  # noqa: E402


def house_number(address: Optional[str]) -> str:
    """
    Read the house number an address starts with, as MapPLUTO and our sites write it ("138-22", "216A").
    Takes the address (None for none).
    Returns its first word in capitals, or "" for none.
    """
    words = (address or "").split()
    return words[0].upper().rstrip(",") if words else ""


def main(commit: bool, skip_bbls: frozenset[str] = frozenset()) -> int:
    """
    Fetch, and with commit write, the MapPLUTO frontage of every live sidewalk that has none, but those at a BBL skipped.
    Takes whether to write and the BBLs to leave out; reads (and writes) the configured database and asks MapPLUTO.
    Returns the exit code: 0 when every sidewalk was fetched (and written), 1 when the list couldn't be read or any failed.
    """
    sidewalks = list_sidewalks_without_tax_map()
    if sidewalks is None:
        print("backfill failed: the sidewalks couldn't be read")
        return 1
    print(f"{len(sidewalks)} live sidewalks without a MapPLUTO frontage ({'writing' if commit else 'dry run'})")
    found = pages = checks = skipped = 0
    missing, failed = [], []
    for site in sidewalks:
        lot = (site["boro"], site["block"], site["lot"])
        label = f"{site['project_id']} {site['address']} (BBL {bbl(*lot)})"
        page_1 = (f"page 1 {site['page_1_width_ft']} ft ({site['page_1_source']})"
                  if site["page_1_width_ft"] is not None else "page 1 has no width")
        if bbl(*lot) in skip_bbls:
            skipped += 1
            print(f"  {label}: skipped (--skip-bbl); nothing written")
            continue
        try:
            record = lookup_lot(*lot)
        except MapPlutoUnavailable as exc:
            failed.append(label)
            print(f"  {label}: MapPLUTO unavailable ({exc})")
            continue
        if record is None:
            missing.append(label)
            print(f"  {label}: no such lot in MapPLUTO; {page_1}")
            continue
        check = ""
        if house_number(record["address"]) != house_number(site["address"]):
            checks += 1
            check = f"  CHECK: MapPLUTO's lot is {record['address']}"
        if record["frontage_ft"] is None or record["source"] is None:
            missing.append(label)
            print(f"  {label}: no usable frontage in MapPLUTO ({record['address']}); {page_1}{check}")
            continue
        frontage, source = record["frontage_ft"], record["source"]
        found += 1
        if not commit:
            print(f"  {label}: {frontage} ft ({source}, {record['address']}); {page_1}{check}")
            continue
        row = refresh_property_tax_map(site["id"], *lot, frontage, source, record["depth_ft"])
        if row is None:
            failed.append(label)
            print(f"  {label}: not written (moved, deleted or the write failed)")
            continue
        pages += row["pages_updated"]
        print(f"  {label}: {frontage} ft ({source}); {row['pages_updated']} title block(s) updated; was {page_1}{check}")
    print(f"backfill {'complete' if commit else 'dry run complete'}: {found} with a frontage, {len(missing)} without"
          + (f", {pages} title blocks updated" if commit else "")
          + (f", {skipped} skipped" if skipped else "")
          + (f"; {checks} to check (MapPLUTO's address has another house number)" if checks else ""))
    if failed:
        print(f"{len(failed)} failed and were left as they were; run it again once the cause is fixed")
    return 1 if failed else 0


def main_depth(commit: bool, skip_bbls: frozenset[str] = frozenset()) -> int:
    """
    Fetch, and with commit write, the MapPLUTO lot depth of every live sidewalk that has a frontage but no depth (pilot
    sweep #2), but those at a BBL skipped. Only the depth is written (set_property_tax_map_depth): the frontage, its
    source and the title blocks stay as they are.
    Takes whether to write and the BBLs to leave out; reads (and writes) the configured database and asks MapPLUTO.
    Returns the exit code: 0 when every sidewalk was fetched (and written), 1 when the list couldn't be read or any failed.
    """
    sidewalks = list_sidewalks_without_tax_map_depth()
    if sidewalks is None:
        print("backfill failed: the sidewalks couldn't be read")
        return 1
    print(f"{len(sidewalks)} live sidewalks with a frontage and no depth ({'writing' if commit else 'dry run'})")
    found = skipped = 0
    missing, failed = [], []
    for site in sidewalks:
        lot = (site["boro"], site["block"], site["lot"])
        label = f"{site['project_id']} {site['address']} (BBL {bbl(*lot)})"
        if bbl(*lot) in skip_bbls:
            skipped += 1
            print(f"  {label}: skipped (--skip-bbl); nothing written")
            continue
        try:
            record = lookup_lot(*lot)
        except MapPlutoUnavailable as exc:
            failed.append(label)
            print(f"  {label}: MapPLUTO unavailable ({exc})")
            continue
        if record is None or record["depth_ft"] is None:
            missing.append(label)
            print(f"  {label}: no usable depth in MapPLUTO; frontage {site['frontage_ft']} ft kept")
            continue
        depth = record["depth_ft"]
        found += 1
        mismatch = (f"  CHECK: MapPLUTO's frontage is now {record['frontage_ft']} ft"
                    if record["frontage_ft"] != site["frontage_ft"] else "")
        if not commit:
            print(f"  {label}: {site['frontage_ft']} x {depth} ft ({record['source']}){mismatch}")
            continue
        if not set_property_tax_map_depth(site["id"], *lot, depth):
            failed.append(label)
            print(f"  {label}: not written (moved, deleted or the write failed)")
            continue
        print(f"  {label}: depth {depth} ft written beside {site['frontage_ft']} ft{mismatch}")
    print(f"depth backfill {'complete' if commit else 'dry run complete'}: {found} with a depth, {len(missing)} without"
          + (f", {skipped} skipped" if skipped else ""))
    if failed:
        print(f"{len(failed)} failed and were left as they were; run it again once the cause is fixed")
    return 1 if failed else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Fetch the MapPLUTO frontage of the sidewalks that have none.")
    parser.add_argument("--commit", action="store_true", help="write the frontages (a dry run without it)")
    parser.add_argument("--depth", action="store_true",
                        help="fetch the lot depth of the sidewalks that have a frontage but no depth, writing only it")
    parser.add_argument("--skip-bbl", action="append", default=[], metavar="BBL",
                        help="leave out the site at this 10-digit BBL (repeatable)")
    args = parser.parse_args()
    sys.exit((main_depth if args.depth else main)(args.commit, frozenset(args.skip_bbl)))
