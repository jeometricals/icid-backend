"""
One-off, safe to repeat: fill icid.quantities for the IDRs that are already approved.

Final approval writes an IDR's pay-item quantities as it approves it; this does the same for every IDR approved
before that existed. It reads each approved IDR that isn't deleted, oldest work date first, readies its rows from its
reports as they are stored now (api.services.quantities.extract_rows) and replaces whatever rows the IDR has with
them, one statement per IDR. Running it again changes nothing that is already right.

It works on the database DATABASE_URL names (.env). Run from the project root, after migration 025:
    python scripts/backfill_quantities.py
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.queries.idrs import list_approved_idrs  # noqa: E402
from api.services.quantities import quantity_rows_for_idr, write_quantities  # noqa: E402

PROGRESS_EVERY = 10


def main() -> int:
    """
    Replace the quantity rows of every approved IDR with the ones its reports give now.
    Takes nothing; reads and writes the configured database.
    Returns the exit code: 0 when every IDR was written, 1 when the IDRs couldn't be listed or any IDR failed.
    """
    idrs = list_approved_idrs()
    if idrs is None:
        print("backfill failed: the approved IDRs couldn't be read")
        return 1
    done = written = 0
    failed = []
    for idr in idrs:
        rows = quantity_rows_for_idr(idr)
        count = write_quantities(idr["idr_id"], rows) if rows is not None else None
        if count is None:
            failed.append(idr["idr_id"])
            print(f"IDR {idr['idr_id']} failed: its {'reports' if rows is None else 'quantities'} couldn't be "
                  f"{'read' if rows is None else 'written'}")
            continue
        done += 1
        written += count
        if done % PROGRESS_EVERY == 0:
            print(f"processed {done}/{len(idrs)}, wrote {written:,} rows across {done} IDRs")
    print(f"backfill complete: {done:,} IDRs, {written:,} quantity rows")
    if failed:
        print(f"{len(failed)} IDRs failed and were left as they were; run it again once the cause is fixed")
    return 1 if failed else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    sys.exit(main())
