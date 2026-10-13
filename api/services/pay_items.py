"""
What a project's pay item may hold: the rules for each of its fields, whether it comes from the admin's form or from
a row of an imported CSV.
"""

from decimal import Decimal
from typing import Any, Mapping

from api.services.pay_item_units import UnknownUnit, normalize_unit

# The text fields: the API's name, the column it is stored in and the longest value it takes
TEXT_FIELDS = (
    ("pay_item_ref", "item_no", 32),
    ("description", "description", 500),
    ("unit", "unit", 16),
    ("budget_code", "budget_code", 50),
)
MAX_SPEC_SECTION_LENGTH = 32
# The amounts: the API's name, the column it is stored in and the most decimal places it takes
AMOUNT_FIELDS = (("quantity", "bid_quantity", 10), ("unit_price", "bid_unit_price", 6))
# An amount is below this, so it goes out as a JSON number
AMOUNT_LIMIT = Decimal(10) ** 15


class PayItemValueError(ValueError):
    """A pay item's value that doesn't fit. Its message says which field and why."""


def _clean_text(value: Any, field: str, max_length: int, required: bool = True) -> Any:
    """
    Check one text value: trimmed, and refused when too long, or blank where it is required.
    Takes the value, the field's name (for the error), its longest length and whether it must be given.
    Returns the trimmed text, or None for an optional value left blank; raises PayItemValueError otherwise.
    """
    text = (value or "").strip()
    if not text:
        if required:
            raise PayItemValueError(f"{field} cannot be blank")
        return None
    if len(text) > max_length:
        raise PayItemValueError(f"{field} cannot be longer than {max_length} characters")
    return text


def _clean_amount(value: Decimal, field: str, max_places: int) -> Decimal:
    """
    Check a quantity or a price. Zero is allowed.
    Takes the value, the field's name (for the error) and the most decimal places it may have (trailing zeros don't count).
    Returns the value; raises PayItemValueError for one that isn't a finite number, is negative, is too large or has too many decimal places.
    """
    if not value.is_finite():
        raise PayItemValueError(f"{field} must be a number")
    if value < 0:
        raise PayItemValueError(f"{field} cannot be negative")
    if value >= AMOUNT_LIMIT:
        raise PayItemValueError(f"{field} is too large")
    if -value.normalize().as_tuple().exponent > max_places:
        raise PayItemValueError(f"{field} cannot have more than {max_places} decimal places")
    return value


def clean_pay_item_fields(values: Mapping[str, Any]) -> dict[str, Any]:
    """
    Check a pay item's values and ready them for icid.contract_items: text trimmed (the budget code otherwise as given), the unit in its stored form, amounts checked. A value of None is left out; spec_section, when the mapping has it, is optional, and a blank or None one is None.
    Takes the values by the API's names (pay_item_ref, description, unit, quantity, unit_price, budget_code, spec_section), the amounts as Decimals.
    Returns the values keyed by column, for the fields given; raises PayItemValueError for the first value that doesn't fit.
    """
    fields: dict[str, Any] = {}
    for name, column, max_length in TEXT_FIELDS:
        if values.get(name) is not None:
            fields[column] = _clean_text(values[name], name, max_length)
    if "unit" in fields:
        try:
            fields["unit"] = normalize_unit(fields["unit"])
        except UnknownUnit as exc:
            raise PayItemValueError(str(exc)) from exc
    for name, column, max_places in AMOUNT_FIELDS:
        if values.get(name) is not None:
            fields[column] = _clean_amount(values[name], name, max_places)
    if "spec_section" in values:
        fields["spec_section"] = _clean_text(values["spec_section"], "spec_section", MAX_SPEC_SECTION_LENGTH, required=False)
    return fields
