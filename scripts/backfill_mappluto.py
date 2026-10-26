"""
One-off, safe to repeat: fetch the MapPLUTO tax-map frontage of every live sidewalk that has none yet (Slice Y1).

A sidewalk created since Slice Y1 is fetched as it is created; this does the same for the ones created before. For each
live sidewalk without a frontage it asks MapPLUTO for its lot, then, with --commit, writes it as an admin's refresh does
(api.queries.properties.refresh_property_tax_map, one statement per sidewalk): onto the property, and onto the title
blocks of its live PIR's pages whose width wasn't typed by hand (page 1 getting a title block if it has none). Without
--commit nothing is written: each sidewalk's MapPLUTO frontage is printed beside the width its page 1 holds now, for
review. Running it again skips the sidewalks already fetched.

It works on the database DATABASE_URL names (.env). Run from the project root, after migration 058:
    python scripts/backfill_mappluto.py            # dry run
    python scripts/backfill_mappluto.py --commit
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.queries.properties import list_sidewalks_without_tax_map, refresh_property_tax_map  # noqa: E402
from api.services.mappluto import MapPlutoUnavailable, bbl, fetch_frontage_by_bbl  # noqa: E402


def main(commit: bool) -> int:
    """
    Fetch, and with commit write, the MapPLUTO frontage of every live sidewalk that has none.
    Takes whether to write; reads (and writes) the configured database and asks MapPLUTO.
    Returns the exit code: 0 when every sidewalk was fetched (and written), 1 when the list couldn't be read or any failed.
    """
    sidewalks = list_sidewalks_without_tax_map()
    if sidewalks is None:
        print("backfill failed: the sidewalks couldn't be read")
        return 1
    print(f"{len(sidewalks)} live sidewalks without a MapPLUTO frontage ({'writing' if commit else 'dry run'})")
    found = pages = 0
    missing, failed = [], []
    for site in sidewalks:
        lot = (site["boro"], site["block"], site["lot"])
        label = f"{site['project_id']} {site['address']} (BBL {bbl(*lot)})"
        page_1 = (f"page 1 {site['page_1_width_ft']} ft ({site['page_1_source']})"
                  if site["page_1_width_ft"] is not None else "page 1 has no width")
        try:
            result = fetch_frontage_by_bbl(*lot)
        except MapPlutoUnavailable as exc:
            failed.append(label)
            print(f"  {label}: MapPLUTO unavailable ({exc})")
            continue
        if result is None:
            missing.append(label)
            print(f"  {label}: no usable frontage in MapPLUTO; {page_1}")
            continue
        frontage, source = result
        found += 1
        if not commit:
            print(f"  {label}: {frontage} ft ({source}); {page_1}")
            continue
        row = refresh_property_tax_map(site["id"], *lot, frontage, source)
        if row is None:
            failed.append(label)
            print(f"  {label}: not written (moved, deleted or the write failed)")
            continue
        pages += row["pages_updated"]
        print(f"  {label}: {frontage} ft ({source}); {row['pages_updated']} title block(s) updated; was {page_1}")
    print(f"backfill {'complete' if commit else 'dry run complete'}: {found} with a frontage, {len(missing)} without"
          + (f", {pages} title blocks updated" if commit else ""))
    if failed:
        print(f"{len(failed)} failed and were left as they were; run it again once the cause is fixed")
    return 1 if failed else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Fetch the MapPLUTO frontage of the sidewalks that have none.")
    parser.add_argument("--commit", action="store_true", help="write the frontages (a dry run without it)")
    sys.exit(main(parser.parse_args().commit))
