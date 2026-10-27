import copy
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from api.schemas.pir_import import SmartPirImport
from api.services.pir_import import prepare_import, run_import

# ---------------------------------------------------------------------------
# api/services/pir_import.py: a Smart PIR file checked item by item, and the counts of what was written
# ---------------------------------------------------------------------------

SAMPLE = json.loads(Path(__file__).parent.joinpath("fixtures", "smart_pir_sample.json").read_text(encoding="utf-8"))
PIR_ID = UUID("6f7a8b9c-0d1e-4f2a-8b3c-4d5e6f7a8b9c")
USER_ID = UUID("a0000000-0000-4000-8000-000000000001")
PAGE_1 = UUID("8b9c0d1e-2f3a-4b4c-8d5e-6f7a8b9c0d1e")
PAGE_2 = UUID("8b9c0d1e-2f3a-4b4c-8d5e-6f7a8b9c0d2e")
PAGE_IDS = {1: PAGE_1, 2: PAGE_2}
PAY_ITEM = UUID("7a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4e")
ELSEWHERE = "00000000-0000-4000-8000-0000000000ff"
WRITE = "api.services.pir_import.import_pir_drawings"


def sample(**changes) -> dict:
    """
    A fresh copy of the sample file, with the PIR's id set.
    Takes top-level keys to replace.
    Returns the file as a dict.
    """
    return {**copy.deepcopy(SAMPLE), "pir_id": str(PIR_ID), **changes}


def prepared(raw: dict, pay_item_ids: frozenset = frozenset({PAY_ITEM})):
    """
    Check a file as the route does.
    Takes the file as a dict and the project's pay item ids.
    Returns the PreparedImport.
    """
    return prepare_import(SmartPirImport.model_validate(raw), PAGE_IDS, set(pay_item_ids))


def reasons(result) -> list[tuple]:
    """
    The dropped items as (kind, page_number, index, reason).
    Takes the PreparedImport.
    Returns the tuples, in order.
    """
    return [(item.kind, item.page_number, item.index, item.reason) for item in result.dropped]


def page_one(raw: dict) -> dict:
    """
    The first page entry of a file.
    Takes the file as a dict.
    Returns the entry (to change in place).
    """
    return raw["pages"][0]


class TestTheSample:
    def test_everything_is_kept(self):
        result = prepared(sample())
        assert result.dropped == []
        assert (len(result.flags), len(result.areas), len(result.features), len(result.segments),
                len(result.annotations)) == (5, 2, 3, 2, 1)

    def test_flags_get_fresh_ids_on_their_page_with_their_areas(self):
        result = prepared(sample())
        ids = [flag["id"] for flag in result.flags]
        assert len(set(ids)) == 5 and all(isinstance(flag_id, UUID) for flag_id in ids)
        assert all(flag["pir_page_id"] == PAGE_1 for flag in result.flags)
        assert [flag["computed_quantity"] for flag in result.flags] == [
            Decimal("20.00"), Decimal("12.00"), Decimal("6.28"), Decimal("16.00"), Decimal("6.00")]
        assert result.flags[0]["defect_codes"] == ["1", "6A"] and "non_assessable_areas" not in result.flags[0]
        assert [area["flag_id"] for area in result.areas] == [ids[0], ids[0]]
        assert [area["computed_quantity"] for area in result.areas] == [Decimal("2.00"), Decimal("1.00")]

    def test_a_features_flag_index_is_that_flags_new_id(self):
        result = prepared(sample())
        subtract, hydrant, door = result.features
        assert subtract["flag_id"] == result.flags[0]["id"] and "flag_index" not in subtract
        assert subtract["computed_quantity"] == Decimal("3.14")
        assert hydrant["flag_id"] is None and hydrant["computed_quantity"] is None and hydrant["area_impact"] == "marker_only"
        assert door["computed_quantity"] == Decimal("6.00")

    def test_segments_carry_their_page_entrys_scale(self):
        first, second = prepared(sample()).segments
        assert first["pir_page_id"] == PAGE_1 and first["scale_px_per_ft"] == Decimal("15")
        assert second["pir_page_id"] == PAGE_2 and second["scale_px_per_ft"] is None
        assert second["points"] == [{"x": 0, "y": 0}, {"x": 30, "y": 40}]

    def test_page_settings_are_only_for_a_page_that_sets_one(self):
        assert prepared(sample()).pages == [{"pir_page_id": PAGE_1, "scale_px_per_ft": Decimal("15"), "rotation_deg": 90}]

    def test_the_annotations_go_on_their_page(self):
        (block,) = prepared(sample()).annotations
        assert block["pir_page_id"] == PAGE_1 and block["boro"] == "Queens" and block["violation_not_removed_due_to"] == ["tree"]


class TestDrops:
    def test_an_invalid_shape(self):
        raw = sample()
        page_one(raw)["flags"][1]["shape"] = "hexagon"
        result = prepared(raw)
        assert len(result.flags) == 4
        ((kind, page, index, reason),) = reasons(result)
        assert (kind, page, index) == ("flag", 1, 1) and reason.startswith("shape:")

    def test_a_defect_code_outside_the_list(self):
        raw = sample()
        page_one(raw)["flags"][4]["defect_codes"] = ["7"]
        ((kind, page, index, reason),) = reasons(prepared(raw))
        assert (kind, index) == ("flag", 4) and reason.startswith("defect_codes.0:")

    @pytest.mark.parametrize("kind,key,index", [("flag", "flags", 1), ("feature", "features", 1),
                                                ("segment", "segments", 0)])
    def test_a_pay_item_of_another_project(self, kind, key, index):
        raw = sample()
        page_one(raw)[key][index]["pay_item_id"] = ELSEWHERE
        result = prepared(raw)
        assert reasons(result) == [(kind, 1, index, "pay item is not one of the project's")]

    def test_a_pay_item_of_the_project_is_kept(self):
        raw = sample()
        page_one(raw)["flags"][1]["pay_item_id"] = str(PAY_ITEM)
        result = prepared(raw)
        assert result.dropped == [] and result.flags[1]["pay_item_id"] == PAY_ITEM

    def test_a_display_order_twice_in_one_flag(self):
        raw = sample()
        page_one(raw)["flags"][0]["non_assessable_areas"][1]["display_order"] = 1
        result = prepared(raw)
        assert len(result.areas) == 1
        (item,) = result.dropped
        assert (item.kind, item.index, item.flag_index) == ("non_assessable_area", 1, 0)
        assert item.reason == "display_order 1 is used by another area of this flag"

    def test_a_bad_area_drops_only_itself(self):
        raw = sample()
        page_one(raw)["flags"][0]["non_assessable_areas"][0]["reason"] = "moon"
        result = prepared(raw)
        assert len(result.flags) == 5 and len(result.areas) == 1
        assert [(item.kind, item.flag_index) for item in result.dropped] == [("non_assessable_area", 0)]

    def test_an_area_reaching_past_its_flag_is_dropped_saying_by_how_much(self):
        raw = sample()
        # Flag 0 is 5 x 4 ft at 15 px/ft, its middle at (100, 120): its right edge is at x 137.5. The 1 x 2 ft area,
        # its middle moved onto that edge, has half of it (1 SF) outside
        page_one(raw)["flags"][0]["non_assessable_areas"][0]["sketch_x"] = 137.5
        result = prepared(raw)
        assert len(result.flags) == 5 and len(result.areas) == 1
        assert [(item.kind, item.flag_index, item.reason) for item in result.dropped] == [
            ("non_assessable_area", 0, "extends past its flag's bounds by 1.00 SF")]

    def test_without_the_files_scale_the_live_pages_is_used(self):
        raw = sample()
        page_one(raw)["scale_px_per_ft"] = None
        page_one(raw)["flags"][0]["non_assessable_areas"][0]["sketch_x"] = 137.5
        assert len(prepared(raw).areas) == 2  # no scale anywhere: it can't be told, so it is kept
        result = prepare_import(SmartPirImport.model_validate(raw), PAGE_IDS, {PAY_ITEM}, page_scales={1: 15})
        assert len(result.areas) == 1 and "extends past its flag's bounds" in result.dropped[0].reason

    def test_a_flag_index_at_a_dropped_flag(self):
        raw = sample()
        page_one(raw)["flags"][0]["shape"] = "hexagon"
        result = prepared(raw)
        assert reasons(result)[1] == ("feature", 1, 0, "flag_index 0 is not a flag this page's import kept")
        assert len(result.features) == 2 and result.areas == []

    def test_a_raw_flag_id_on_a_feature(self):
        raw = sample()
        page_one(raw)["features"][1]["flag_id"] = ELSEWHERE
        assert reasons(prepared(raw)) == [("feature", 1, 1, "name the flag by flag_index, not flag_id")]

    def test_a_page_the_pir_doesnt_have(self):
        raw = sample()
        page_one(raw)["page_number"] = 9
        result = prepared(raw)
        dropped = reasons(result)
        assert len(dropped) == 5 + 3 + 1 + 1 and all(reason == "the PIR has no live page 9" for *_, reason in dropped)
        assert {kind for kind, *_ in dropped} == {"flag", "feature", "segment", "annotations"}
        assert result.flags == [] and result.pages == [] and len(result.segments) == 1

    def test_a_page_listed_twice(self):
        raw = sample()
        raw["pages"].append({"page_number": 2, "segments": [{"segment_type": "other",
                                                            "points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]}]})
        result = prepared(raw)
        assert reasons(result) == [("segment", 2, 0, "page 2 is listed twice")] and len(result.segments) == 2

    def test_the_files_annotations_when_page_one_brings_its_own(self):
        result = prepared(sample(annotations={"boro": "Bronx"}))
        assert reasons(result) == [("annotations", None, None, "page 1 brings its own annotations")]
        assert result.annotations[0]["boro"] == "Queens"

    def test_the_files_annotations_go_to_page_one(self):
        raw = sample(annotations={"boro": "Bronx"})
        del page_one(raw)["annotations"]
        result = prepared(raw)
        assert result.dropped == [] and result.annotations[0]["pir_page_id"] == PAGE_1
        assert result.annotations[0]["boro"] == "Bronx"

    def test_bad_annotations(self):
        raw = sample()
        page_one(raw)["annotations"]["boro"] = "Mars"
        result = prepared(raw)
        ((kind, page, index, reason),) = reasons(result)
        assert (kind, page, index) == ("annotations", 1, None) and reason.startswith("boro:")
        assert result.annotations == []


WRITTEN = {"pages_updated": 1, "segments_rescaled": 0, "flags": 5, "non_assessable_areas": 2, "features": 3,
           "segments": 2, "annotations": 1, "match_lines": 0}


class TestRunImport:
    def test_the_counts_are_what_the_statement_wrote(self):
        with patch(WRITE, return_value=WRITTEN) as write:
            result = run_import(PIR_ID, USER_ID, SmartPirImport.model_validate(sample()), PAGE_IDS, {PAY_ITEM})
        assert result.inserted.model_dump() == {"flags": 5, "non_assessable_areas": 2, "features": 3, "segments": 2,
                                                "annotations": 1, "match_lines": 0}
        assert sum(result.dropped.model_dump().values()) == 0 and result.pages_updated == 1
        args = write.call_args.args
        assert args[0] == PIR_ID and args[1] == USER_ID and len(args[3]) == 5 and len(args[4]) == 2

    def test_dropped_counts_failed_checks_and_rows_the_statement_couldnt_write(self):
        raw = sample()
        page_one(raw)["flags"][1]["shape"] = "hexagon"
        with patch(WRITE, return_value={**WRITTEN, "flags": 3, "segments": 1}):
            result = run_import(PIR_ID, USER_ID, SmartPirImport.model_validate(raw), PAGE_IDS, {PAY_ITEM})
        # 1 failed its check, and 1 of the 4 prepared wasn't written; 1 of the 2 segments wasn't either
        assert result.dropped.flags == 2 and result.dropped.segments == 1
        assert len(result.dropped_items) == 1

    def test_nothing_to_write_runs_no_statement(self):
        raw = {"schema_version": "1.0", "pir_id": str(PIR_ID), "pages": [{"page_number": 9, "flags": [{}]}]}
        with patch(WRITE) as write:
            result = run_import(PIR_ID, USER_ID, SmartPirImport.model_validate(raw), PAGE_IDS, set())
        write.assert_not_called()
        assert result.inserted.model_dump() == dict.fromkeys(result.inserted.model_dump(), 0)
        assert result.dropped.flags == 1 and result.pages_updated == 0

    def test_a_failed_statement_is_none(self):
        with patch(WRITE, return_value=None):
            assert run_import(PIR_ID, USER_ID, SmartPirImport.model_validate(sample()), PAGE_IDS, set()) is None


class TestFlagUnits:
    """R1f: a flag's pay item must be in a unit an IDR's rollup can measure a flag in."""

    def test_a_flag_whose_pay_item_a_flag_cant_be_measured_in_is_dropped(self):
        from api.services.pir_import import FLAG_UNIT_REFUSED
        raw = sample()
        page_one(raw)["flags"][1]["pay_item_id"] = str(PAY_ITEM)
        result = prepare_import(SmartPirImport.model_validate(raw), PAGE_IDS, {PAY_ITEM}, set())
        assert [(kind, index, reason) for kind, _, index, reason in reasons(result) if kind == "flag"] == [
            ("flag", 1, FLAG_UNIT_REFUSED)]
        assert FLAG_UNIT_REFUSED == "a flag can't be measured in its pay item's unit (only SF, SY, CY, EA)"

    def test_a_flag_whose_pay_item_a_flag_measures_is_kept(self):
        raw = sample()
        page_one(raw)["flags"][1]["pay_item_id"] = str(PAY_ITEM)
        result = prepare_import(SmartPirImport.model_validate(raw), PAGE_IDS, {PAY_ITEM}, {PAY_ITEM})
        assert result.dropped == [] and result.flags[1]["pay_item_id"] == PAY_ITEM


# ---------------------------------------------------------------------------
# Match lines (R1h.1a): a page entry's match_lines, each checked on its own, a label on at most two pages
# ---------------------------------------------------------------------------

def line(label: str = "A", x: float = 1562.0) -> dict:
    """
    A match line as the file has one: its label and two endpoints, a vertical line at x.
    Takes the label and the x of the line.
    Returns the item.
    """
    return {"label": label, "points": [{"x": x, "y": 640}, {"x": x, "y": 1010}]}


def two_pages(lines_1: list, lines_2: list) -> dict:
    """
    A file of two empty page entries carrying the given match lines.
    Takes page 1's and page 2's lines.
    Returns the file as a dict.
    """
    return {"schema_version": "1.0", "pir_id": str(PIR_ID),
            "pages": [{"page_number": 1, "match_lines": lines_1}, {"page_number": 2, "match_lines": lines_2}]}


def lines_prepared(raw: dict, live_labels: dict = None):
    """
    Check a file as the route does, with the PIR's live match line labels.
    Takes the file and {label: page numbers it is live on}.
    Returns the PreparedImport.
    """
    return prepare_import(SmartPirImport.model_validate(raw), PAGE_IDS, {PAY_ITEM}, None, None, live_labels or {})


class TestMatchLines:
    def test_a_label_on_two_pages_pairs_them(self):
        out = lines_prepared(two_pages([line("a ")], [line("A", 140)]))
        assert out.dropped == []
        assert out.match_lines == [
            {"pir_page_id": PAGE_1, "label": "A", "x1": 1562.0, "y1": 640.0, "x2": 1562.0, "y2": 1010.0},
            {"pir_page_id": PAGE_2, "label": "A", "x1": 140.0, "y1": 640.0, "x2": 140.0, "y2": 1010.0}]

    def test_a_middle_sheet_carries_two_lines(self):
        out = lines_prepared(two_pages([line("A"), line("B", 10)], []))
        assert [row["label"] for row in out.match_lines] == ["A", "B"]

    @pytest.mark.parametrize("bad, why", [
        ({"label": "A1", "points": [{"x": 1, "y": 1}, {"x": 2, "y": 2}]}, "label"),
        ({"label": "A", "points": [{"x": 1, "y": 1}]}, "points"),
        ({"label": "A", "points": [{"x": 1, "y": 1}, {"x": 1, "y": 1}]}, "must differ"),
    ])
    def test_a_line_that_fails_its_check_is_dropped_and_the_rest_kept(self, bad, why):
        out = lines_prepared(two_pages([bad, line("B")], []))
        assert [row["label"] for row in out.match_lines] == ["B"]
        assert [(item.kind, item.page_number, item.index) for item in out.dropped] == [("match_line", 1, 0)]
        assert why in out.dropped[0].reason

    def test_a_label_listed_twice_on_a_page_keeps_the_first(self):
        out = lines_prepared(two_pages([line("A"), line("A", 20)], []))
        assert len(out.match_lines) == 1 and out.dropped[0].reason == 'label "A" is listed twice on this page'

    def test_a_label_the_page_already_has_is_dropped(self):
        out = lines_prepared(two_pages([line("A")], []), {"A": {1}})
        assert out.match_lines == [] and out.dropped[0].reason == 'label "A" is already on this page'

    def test_a_label_that_would_be_on_three_pages_is_dropped_everywhere(self):
        out = lines_prepared(two_pages([line("C")], [line("C")]), {"C": {3}})
        assert out.match_lines == []
        assert [(item.page_number, item.reason) for item in out.dropped] == [
            (1, 'label "C" would be on 3 pages'), (2, 'label "C" would be on 3 pages')]

    def test_a_dropped_page_takes_its_lines(self):
        raw = {"schema_version": "1.0", "pir_id": str(PIR_ID), "pages": [{"page_number": 9, "match_lines": [line()]}]}
        out = lines_prepared(raw)
        assert out.match_lines == [] and out.dropped[0].kind == "match_line"

    def test_the_counts_include_the_lines_written(self):
        written = {**WRITTEN, "flags": 0, "non_assessable_areas": 0, "features": 0, "segments": 0, "annotations": 0,
                   "pages_updated": 0, "match_lines": 2}
        with patch(WRITE, return_value=written) as write:
            result = run_import(PIR_ID, USER_ID, SmartPirImport.model_validate(two_pages([line()], [line()])),
                                PAGE_IDS, {PAY_ITEM}, None, None, {})
        assert result.inserted.match_lines == 2 and result.dropped.match_lines == 0
        assert len(write.call_args.args[-1]) == 2


def numbered(numbers_1: list, numbers_2: list = None) -> dict:
    """
    A file of the sample's first flag repeated, carrying the given PIR flag numbers (Slice Y3a; a key left out for None).
    Takes page 1's numbers and, optionally, page 2's.
    Returns the file as a dict.
    """
    flag = {key: value for key, value in SAMPLE["pages"][0]["flags"][0].items() if key != "non_assessable_areas"}

    def flags(numbers: list) -> list:
        return [{**flag, **({} if number is None else {"pir_flag_no": number})} for number in numbers]

    pages = [{"page_number": 1, "flags": flags(numbers_1)}]
    if numbers_2 is not None:
        pages.append({"page_number": 2, "flags": flags(numbers_2)})
    return {"schema_version": "1.0", "pir_id": str(PIR_ID), "pages": pages}


def numbers_prepared(raw: dict, live_flag_nos: dict = None):
    """
    Check a file as the route does, with the PIR flag numbers its live flags have.
    Takes the file and {page number: numbers live there}.
    Returns the PreparedImport.
    """
    return prepare_import(SmartPirImport.model_validate(raw), PAGE_IDS, {PAY_ITEM}, None, None, {},
                          live_flag_nos or {})


class TestPirFlagNumbers:
    """Slice Y3a: a flag can bring the signed PIR's own number for it, once per page."""

    def test_numbers_are_kept_trimmed_and_a_blank_one_is_none(self):
        out = numbers_prepared(numbered([" 12 ", "", None]))
        assert [row["pir_flag_no"] for row in out.flags] == ["12", None, None] and out.dropped == []

    def test_a_number_listed_twice_on_a_page_drops_the_second(self):
        out = numbers_prepared(numbered(["7", "7", "8"]))
        assert [row["pir_flag_no"] for row in out.flags] == ["7", "8"]
        assert reasons(out) == [("flag", 1, 1, 'pir_flag_no "7" is listed twice on this page')]

    def test_a_number_the_page_has_live_is_dropped(self):
        out = numbers_prepared(numbered(["7", "9"]), {1: {"9"}})
        assert [row["pir_flag_no"] for row in out.flags] == ["7"]
        assert reasons(out) == [("flag", 1, 1, 'pir_flag_no "9" is already on this page')]

    def test_the_same_number_on_two_pages_is_fine(self):
        out = numbers_prepared(numbered(["7"], ["7"]), {2: {"8"}})
        assert [row["pir_flag_no"] for row in out.flags] == ["7", "7"] and out.dropped == []

    def test_a_number_past_16_characters_is_dropped(self):
        out = numbers_prepared(numbered(["x" * 17]))
        assert out.flags == [] and reasons(out)[0][3].startswith("pir_flag_no: String should have at most 16")

    def test_a_dropped_flag_takes_no_number(self):
        """A flag dropped for its number is no flag: a feature naming it by flag_index is dropped too."""
        raw = numbered(["7", "7"])
        raw["pages"][0]["features"] = [{"feature_type": "manhole", "sketch_x": 1, "sketch_y": 1, "flag_index": 1}]
        out = numbers_prepared(raw)
        assert out.features == [] and reasons(out)[-1][3] == "flag_index 1 is not a flag this page's import kept"
