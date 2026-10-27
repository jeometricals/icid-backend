"""
One-off, safe to repeat: fetch the DOF tax-map PDF of every live sidewalk that has none yet (Slice Y1.5).

A sidewalk created since Slice Y1.5 gets its block's map as it is created; this does the same for the ones created
before. A tax map covers a block, so the sites are grouped by borough and block: each block's current map is looked up
in DOF's map library once and, with --commit, downloaded once and stored for each of its sites
(api.services.dof_tax_map.store_map: {property id}/{DOF map id}.pdf in tax-maps, recorded on the property). Without
--commit nothing is written or downloaded: each block's map (its DOF id and effective date) is printed with the sites
it would go to, for review. --skip-bbl leaves a site out (repeat it for several). DOF publishes no rate limit, so the
script pauses between its calls (--pause, 1 s). Running it again skips the sites already done.

It works on the database DATABASE_URL names (.env). Run from the project root, after migrations 059 and 059b:
    python scripts/backfill_tax_map_pdfs.py            # dry run
    python scripts/backfill_tax_map_pdfs.py --commit --skip-bbl 3012340051
"""

import argparse
import logging
import sys
import time
from itertools import groupby
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.queries.properties import list_sidewalks_without_tax_map_pdf  # noqa: E402
from api.services.dof_tax_map import (  # noqa: E402
    DofTaxMapUnavailable,
    TaxMapPdfError,
    current_map,
    fetch_map_pdf,
    map_url,
    store_map,
)
from api.services.mappluto import bbl  # noqa: E402


def main(commit: bool, skip_bbls: frozenset[str] = frozenset(), pause: float = 1.0) -> int:
    """
    Look up, and with commit fetch and store, the DOF tax-map PDF of every live sidewalk that has none, block by block.
    Takes whether to write, the BBLs to leave out and the seconds to wait between DOF calls; reads (and writes) the
    configured database and Storage, and asks DOF.
    Returns the exit code: 0 when every block was looked up (and stored), 1 when the list couldn't be read or any failed.
    """
    sidewalks = list_sidewalks_without_tax_map_pdf()
    if sidewalks is None:
        print("backfill failed: the sidewalks couldn't be read")
        return 1
    print(f"{len(sidewalks)} live sidewalks without a tax map PDF ({'writing' if commit else 'dry run'})")
    stored = skipped = blocks = 0
    no_map, failed = [], []
    first_call = True
    for (boro, block), group in groupby(sidewalks, key=lambda site: (site["boro"], site["block"])):
        sites = []
        for site in group:
            label = f"{site['project_id']} {site['address']} (BBL {bbl(boro, block, site['lot'])})"
            if bbl(boro, block, site["lot"]) in skip_bbls:
                skipped += 1
                print(f"  {label}: skipped (--skip-bbl); nothing written")
            else:
                sites.append((site, label))
        if not sites:
            continue
        blocks += 1
        if not first_call:
            time.sleep(pause)
        first_call = False
        try:
            found = current_map(boro, block)
        except DofTaxMapUnavailable as exc:
            failed.extend(label for _, label in sites)
            print(f"{boro} block {block}: DOF unavailable ({exc})")
            continue
        if found is None:
            no_map.extend(label for _, label in sites)
            print(f"{boro} block {block}: no current map in DOF's library")
            for _, label in sites:
                print(f"  {label}: nothing to store")
            continue
        print(f"{boro} block {block}: map {found['map_id']}, effective {found['effective_date']} ({map_url(found['map_id'])})")
        if not commit:
            for _, label in sites:
                print(f"  {label}: would get it")
            continue
        time.sleep(pause)
        try:
            data = fetch_map_pdf(found["map_id"])
        except DofTaxMapUnavailable as exc:
            failed.extend(label for _, label in sites)
            print(f"  download failed ({exc}); its {len(sites)} site(s) left as they were")
            continue
        for site, label in sites:
            try:
                store_map(site, found, data)
            except TaxMapPdfError as exc:
                failed.append(label)
                print(f"  {label}: not stored ({exc.detail})")
                continue
            stored += 1
            print(f"  {label}: stored ({len(data):,} bytes)")
    print(f"backfill {'complete' if commit else 'dry run complete'}: {blocks} block(s) looked up"
          + (f", {stored} site(s) stored" if commit else "")
          + f", {len(no_map)} site(s) on a block DOF has no map for"
          + (f", {skipped} skipped" if skipped else ""))
    if failed:
        print(f"{len(failed)} site(s) failed and were left as they were; run it again once the cause is fixed")
    return 1 if failed else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Fetch the DOF tax-map PDF of the sidewalks that have none.")
    parser.add_argument("--commit", action="store_true", help="download and store the maps (a dry run without it)")
    parser.add_argument("--skip-bbl", action="append", default=[], metavar="BBL",
                        help="leave out the site at this 10-digit BBL (repeatable)")
    parser.add_argument("--pause", type=float, default=1.0, help="seconds between DOF calls (default 1)")
    args = parser.parse_args()
    sys.exit(main(args.commit, frozenset(args.skip_bbl), args.pause))
