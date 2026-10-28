import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from api.queries.assessment_clip_audits import list_clip_inputs, list_property_clip_inputs
from api.queries.assessment_summary import list_assessment_flags
from api.queries.construction_report import list_report_flags
from api.queries.flags import FLAG_QUANTITY, list_flags
from api.queries.idr_flags import list_idr_flags
from api.queries.progress import list_flag_progress
from api.queries.property_pay_items import property_pay_item_lines
from api.queries.quantities import insert_flag_quantities_cte

# ---------------------------------------------------------------------------
# Slice Y3a: every reader of a flag's quantity reads it through the flag's measurement
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
ID = UUID("6f7a8b9c-0d1e-4f2a-8b3c-4d5e6f7a8b9c")
MEASURED = "JOIN icid.flag_measurements fm ON fm.measurement_id = f.measurement_id"
# Slice Y3b-1: less the measurement's deductions (a bundle's), and a solo measurement's flag's own covers only
NET = ("GREATEST(round((fm.outer_area - ded.quantity) * f.area_share_fraction, 2) - CASE WHEN fm.surveyed_is_assessable "
       "IS NULL THEN naa.quantity + sub.quantity ELSE 0 END, 0)")

# Each reader, the module whose run_query it calls, and how to call it
READERS = [
    ("flags", "api.queries.flags", lambda: list_flags(ID)),
    ("an IDR's flags (rollup, export lines, site amounts, SMART sheets)", "api.queries.idr_flags",
     lambda: list_idr_flags(ID, False)),
    ("Pay Items Reported", "api.queries.property_pay_items", lambda: property_pay_item_lines(ID)),
    ("Progress", "api.queries.progress", lambda: list_flag_progress("HWS0023", None)),
    ("the Construction Report", "api.queries.construction_report", lambda: list_report_flags(ID, ID)),
    ("the Assessment Summary", "api.queries.assessment_summary", lambda: list_assessment_flags("HWS0023")),
    ("the clip at approval", "api.queries.assessment_clip_audits", lambda: list_clip_inputs([ID])),
    ("the re-clip", "api.queries.assessment_clip_audits", lambda: list_property_clip_inputs(ID)),
]


def one_line(sql: str) -> str:
    """A statement on one line, its whitespace collapsed."""
    return " ".join(sql.split())


@pytest.mark.parametrize("name, module, call", READERS, ids=[reader[0] for reader in READERS])
def test_every_reader_measures_through_the_measurement(name, module, call):
    with patch(f"{module}.run_query", return_value=[]) as run:
        call()
    sql = one_line(run.call_args.args[0])
    assert MEASURED in sql and NET in sql, name


def test_approvals_from_flags_rows_measure_through_the_measurement():
    sql = one_line(insert_flag_quantities_cte("moved"))
    assert MEASURED in sql and NET in sql


def test_the_quantity_is_the_override_else_the_measured_net():
    assert FLAG_QUANTITY == f"COALESCE(f.reported_quantity, {NET})"


def test_no_statement_works_a_flag_net_from_its_computed_quantity_any_more():
    """The old path, the shape's area less its areas and features, is gone: a reader that brings it back skips the
    measurement."""
    for path in ROOT.joinpath("api", "queries").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"f\.computed_quantity\s*-", text), path.name


@pytest.mark.parametrize("area, areas, features", [
    ("25.00", "0", "0"), ("25.00", "1.00", "0"), ("12.57", "3.14", "2.5"), ("8.73", "0", "9.00"), ("0.01", "0", "0"),
])
def test_a_solo_measurement_gives_the_net_of_before(area, areas, features):
    """For a solo measurement (outer_area the flag's computed_quantity, share 1.0000, no deductions) the formula is the
    old net, decimal places included, as the statement works it: round((outer - 0) x share, 2), less the areas and
    features together, at least 0."""
    outer, share, deducted = Decimal(area), Decimal("1.0000"), Decimal(0)
    new = max(((outer - deducted) * share).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
              - (Decimal(areas) + Decimal(features)), Decimal(0))
    old = max(Decimal(area) - Decimal(areas) - Decimal(features), Decimal(0))
    assert new == old and str(new) == str(old)


def test_the_schema_has_the_measurement_and_its_columns():
    schema = ROOT.joinpath("schema.sql").read_text(encoding="utf-8")
    flags = schema[schema.index("CREATE TABLE icid.flags ("):]
    flags = flags[:flags.index("\n);\n")]
    assert schema.index("CREATE TABLE icid.flag_measurements") < schema.index("CREATE TABLE icid.flags (")
    assert "measurement_id                  UUID NOT NULL REFERENCES icid.flag_measurements(measurement_id)" in flags
    assert "area_share_fraction             NUMERIC(5,4) NOT NULL DEFAULT 1.0000" in flags
    assert "outer_area        NUMERIC(10,2) NOT NULL" in schema
    assert ("CREATE UNIQUE INDEX uq_flags_page_pir_flag_no\n    ON icid.flags (pir_page_id, pir_flag_no) "
            "WHERE pir_flag_no IS NOT NULL AND deleted_at IS NULL;") in schema
    assert "CREATE TABLE icid.measurement_edits" in schema
