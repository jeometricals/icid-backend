"""
CSV downloads: how every CSV the API hands out is written (the Assessment Summary's, Slice ML1c; Progress's, ML2).
UTF-8 with a byte-order mark, so Excel reads its characters, and CRLF line ends.
"""

import csv
import io
import re
from datetime import datetime, timezone
from typing import Optional

from api.services.export_common import signed_date

CSV = "text/csv; charset=utf-8"


def csv_bytes(header: list[str], lines: list[list[str]]) -> bytes:
    """
    Write a CSV: UTF-8 with a byte-order mark, CRLF line ends.
    Takes the header and the lines (each a list of text).
    Returns the file's bytes.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(lines)
    return buffer.getvalue().encode("utf-8-sig")


def file_token(text: str) -> str:
    """
    A piece of text made safe for a file name: letters, digits, hyphens and underscores kept, anything else an underscore.
    Takes the text.
    Returns the token.
    """
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_") or "_"


def today_in_new_york(now: Optional[datetime] = None) -> str:
    """
    Today's date in New York, for a download's file name.
    Takes, for tests, the moment it is (now, UTC, by default).
    Returns YYYY-MM-DD.
    """
    return signed_date(now or datetime.now(timezone.utc)).isoformat()
