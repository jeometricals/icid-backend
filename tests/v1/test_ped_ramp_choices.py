import re
from pathlib import Path
from typing import Literal, Optional, Union, get_args, get_origin

import pytest

from api.schemas.ped_ramp import CHOICE_LABELS, CornerFields, RampAttributesIn

# ---------------------------------------------------------------------------
# A ped ramp's choice lists (Slice V): every Literal on a corner's or a ramp's fields takes exactly the values its
# column's CHECK in schema.sql allows, so a value the database takes is never a 422 (Reza's dws_material
# "preformed_plastic_embedded", refused against the lists from before migration 048).
# ---------------------------------------------------------------------------

SCHEMA = Path(__file__).resolve().parents[2].joinpath("schema.sql").read_text(encoding="utf-8")


def table_body(table: str) -> str:
    """
    Read one table's CREATE TABLE body out of schema.sql.
    Takes the table's name without its schema.
    Returns the text between its opening line and the closing ");".
    """
    match = re.search(rf"CREATE TABLE icid\.{table} \((.*?)\n\);", SCHEMA, re.S)
    assert match, table
    return match.group(1)


def check_values(table: str, column: str) -> set[str]:
    """
    Read the values a column's CHECK (column IN (...)) allows.
    Takes the table and the column.
    Returns the set of quoted values.
    """
    match = re.search(rf"\n\s+{column}\s+TEXT\b[^\n]*?CHECK \({column} IN \((.*?)\)\)", table_body(table), re.S)
    assert match, (table, column)
    return set(re.findall(r"'([^']+)'", match.group(1)))


def literal_values(model: type, field: str) -> set[str]:
    """
    Read the values a model's Optional[Literal[...]] field takes.
    Takes the Pydantic model and the field's name.
    Returns the set of values.
    """
    annotation = model.model_fields[field].annotation
    if get_origin(annotation) in (Union, Optional):
        annotation = next(arg for arg in get_args(annotation) if arg is not type(None))
    assert get_origin(annotation) is Literal, (model, field)
    return set(get_args(annotation))


def literal_fields(model: type) -> list[str]:
    """
    List a model's fields typed as an Optional Literal.
    Takes the Pydantic model.
    Returns the field names, but boro.
    """
    out = []
    for name, info in model.model_fields.items():
        if name == "boro":  # a Postgres enum type (icid.boro), not a CHECK
            continue
        args = get_args(info.annotation)
        if get_origin(info.annotation) is Literal or any(get_origin(arg) is Literal for arg in args):
            out.append(name)
    return out


CASES = [("ped_ramp_corners", CornerFields, field) for field in literal_fields(CornerFields)] + \
        [("ped_ramp_attributes", RampAttributesIn, field) for field in literal_fields(RampAttributesIn)]


def test_the_four_lists_048_widened_are_among_the_cases():
    fields = {field for _, _, field in CASES}
    assert {"entity", "curb_material", "dws_color", "dws_material"} <= fields


@pytest.mark.parametrize("table,model,field", CASES, ids=[field for _, _, field in CASES])
def test_a_literal_takes_exactly_what_its_columns_check_allows(table, model, field):
    assert literal_values(model, field) == check_values(table, field)


@pytest.mark.parametrize("table,model,field", CASES, ids=[field for _, _, field in CASES])
def test_every_choice_has_its_words_for_the_report(table, model, field):
    assert literal_values(model, field) - set(CHOICE_LABELS) <= {"SW", "NW", "NE", "SE", "N", "S", "E", "W"}


def test_reza_s_dws_material_is_taken():
    assert RampAttributesIn.model_validate({"dws_material": "preformed_plastic_embedded"}).dws_material == \
        "preformed_plastic_embedded"
