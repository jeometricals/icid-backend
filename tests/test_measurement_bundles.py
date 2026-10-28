from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from api.queries.features import create_feature, soft_delete_feature, update_feature
from api.queries.assessment_summary import list_assessment_flags
from api.queries.construction_report import list_report_flags
from api.queries.flag_measurements import (
    MIN_DEDUCTION_SF,
    form_bundle,
    get_measurement,
    list_remesh_neighbours,
    remesh_ctes,
    snapshot_intent,
    unbundle,
)
from api.queries.flags import create_flag, soft_delete_flag, undelete_flag, update_flag
from api.queries.non_assessable_areas import create_area, soft_delete_area, update_area
from api.queries.pir_import import import_pir_drawings
from api.queries.pir_pages import delete_pages_for_pir, replace_pages
from api.queries.sidewalk_strip import set_sidewalk_strip
from api.services.measurement_bundles import classification, join_target, unconnected
from tests.conftest import ADMIN_USER_ROW

# ---------------------------------------------------------------------------
# Slice Y3b-1: the remesh. A flag's measurement follows it through every writer of its classification, geometry,
# existence and covers, in the writer's own statement; the Python half picks the one bundle it may join.
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
PAGE_ID = UUID("8b9c0d1e-2f3a-4b4c-8d5e-6f7a8b9c0d1e")
PIR_ID = UUID("6f7a8b9c-0d1e-4f2a-8b3c-4d5e6f7a8b9c")
ADMIN_ID = ADMIN_USER_ROW["uuid"]
BUNDLE = UUID("b1000000-0000-4000-8000-000000000001")
OTHER_BUNDLE = UUID("b1000000-0000-4000-8000-000000000002")
PAY_ITEM = UUID("7a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4e")


def flag_id(n: int) -> UUID:
    """The nth test flag's id."""
    return UUID(f"f3000000-0000-4000-8000-00000000000{n}")


def neighbour(n: int, x: float, measurement: UUID, bundled: bool = True, frozen: bool = False, thickness: str = "4in",
              assessable: bool = True, pay_item=None, y: float = 300) -> dict:
    """A 5 x 5 ft flag on a 16 px/ft page centred at (x, y), as list_remesh_neighbours returns it."""
    return {"id": flag_id(n), "measurement_id": measurement, "shape": "rectangle", "dimensions": {"L": 5, "W": 5},
            "sketch_x": x, "sketch_y": y, "rotation": 0, "surveyed_thickness": thickness,
            "surveyed_is_assessable": assessable, "pay_item_id": pay_item, "scale_px_per_ft": Decimal("16"),
            "bundled": bundled, "frozen": frozen}


# The flag being reclassified: solo, at x 400; its neighbours to the right abut it (80 px = 5 ft apart)
ME = neighbour(1, 400, UUID("a1000000-0000-4000-8000-000000000001"), bundled=False, thickness="7in")


def me_as(**changes) -> dict:
    """The flag as it will be after its PATCH: 4in unless changed."""
    return {**ME, "surveyed_thickness": "4in", **changes}


class TestJoinTarget:
    def test_the_one_bundle_it_abuts_of_its_new_classification(self):
        page = [ME, neighbour(2, 480, BUNDLE)]
        assert join_target(me_as(), page) == BUNDLE

    def test_two_members_of_one_bundle_are_one_candidate(self):
        page = [ME, neighbour(2, 480, BUNDLE), neighbour(3, 400, BUNDLE, y=380)]
        assert join_target(me_as(), page) == BUNDLE

    def test_two_bundles_and_it_goes_solo(self):
        page = [ME, neighbour(2, 480, BUNDLE), neighbour(3, 320, OTHER_BUNDLE)]
        assert join_target(me_as(), page) is None

    def test_a_solo_neighbour_is_never_a_candidate_in_y3b_1(self):
        """The remesh forms no bundle of its own: only the bundle route does (Y3b-2)."""
        page = [ME, neighbour(2, 480, UUID("a1000000-0000-4000-8000-000000000002"), bundled=False)]
        assert join_target(me_as(), page) is None

    def test_a_frozen_bundle_is_never_a_candidate(self):
        page = [ME, neighbour(2, 480, BUNDLE, frozen=True)]
        assert join_target(me_as(), page) is None

    @pytest.mark.parametrize("change", [{"thickness": "7in"}, {"assessable": False}, {"pay_item": PAY_ITEM}])
    def test_another_classification_is_no_candidate(self, change):
        page = [ME, neighbour(2, 480, BUNDLE, **change)]
        assert join_target(me_as(), page) is None

    def test_the_pay_item_is_matched_whatever_its_type(self):
        page = [ME, neighbour(2, 480, BUNDLE, pay_item=PAY_ITEM)]
        assert join_target(me_as(pay_item_id=str(PAY_ITEM)), page) == BUNDLE

    def test_a_bundle_it_only_touches_at_a_corner_or_not_at_all_is_no_candidate(self):
        assert join_target(me_as(), [ME, neighbour(2, 480, BUNDLE, y=380)]) is None
        assert join_target(me_as(), [ME, neighbour(2, 600, BUNDLE)]) is None

    def test_its_own_bundle_is_no_candidate(self):
        mine = {**ME, "measurement_id": BUNDLE, "bundled": True}
        assert join_target({**mine, "surveyed_thickness": "4in"}, [mine, neighbour(2, 480, BUNDLE)]) is None

    def test_a_flag_moving_in_the_same_statement_doesnt_count(self):
        page = [ME, neighbour(2, 480, BUNDLE)]
        assert join_target(me_as(), page, frozenset({flag_id(2)})) is None

    def test_no_place_no_scale_or_not_on_the_page_and_it_goes_solo(self):
        page = [ME, neighbour(2, 480, BUNDLE)]
        assert join_target(me_as(sketch_x=None), page) is None
        assert join_target(me_as(), [{**ME, "scale_px_per_ft": None}, neighbour(2, 480, BUNDLE)]) is None
        assert join_target(me_as(), [neighbour(2, 480, BUNDLE)]) is None

    def test_classification_is_thickness_assessment_and_pay_item(self):
        assert classification({"surveyed_thickness": "4in", "surveyed_is_assessable": True, "pay_item_id": PAY_ITEM}) \
            == ("4in", True, str(PAY_ITEM))


def one_line(sql: str) -> str:
    """A statement on one line."""
    return " ".join(sql.split())


REMESH = one_line(remesh_ctes())


class TestRemeshRules:
    def test_what_each_flag_does(self):
        assert ("WHEN mv.event IN ('edit', 'flip') AND mv.reclassified AND mv.target_id IS NOT NULL THEN 'join' "
                "WHEN mv.event IN ('edit', 'flip') AND mv.reclassified AND mv.from_bundled THEN 'fresh' "
                "WHEN mv.event = 'delete' AND mv.from_bundled AND NOT mv.claimed THEN 'fresh' "
                "WHEN mv.event = 'undelete' AND mv.from_bundled THEN 'fresh' ELSE 'stay' END") in REMESH

    def test_the_pick_is_checked_again_in_the_statement(self):
        """Still a live bundle of the flag's new classification, no member claimed, a live member on its page."""
        assert "tgt.retired_at IS NULL AND tgt.surveyed_is_assessable IS NOT NULL" in REMESH
        assert ("(tgt.surveyed_thickness, tgt.surveyed_is_assessable, tgt.pay_item_id) IS NOT DISTINCT FROM "
                "(i.new_thickness, i.new_assessable, i.new_pay_item)") in REMESH
        assert ("NOT EXISTS (SELECT 1 FROM icid.flags c WHERE c.measurement_id = tgt.measurement_id "
                "AND c.claimed_by_idr_id IS NOT NULL)") in REMESH
        assert "n.deleted_at IS NULL AND n.pir_page_id = x.pir_page_id" in REMESH

    def test_a_frozen_bundle_keeps_its_figures(self):
        assert "CASE WHEN b.frozen THEN b.outer_area ELSE GREATEST(b.outer_area + d.delta, 0) END" in REMESH
        assert "CASE WHEN b.frozen THEN b.deductions ELSE dd.deductions END" in REMESH
        assert "JOIN rm_bundles b ON b.measurement_id = m.measurement_id AND NOT b.frozen" in REMESH  # no new shares

    def test_the_outer_area_takes_what_joins_and_gives_up_what_leaves(self):
        assert ("sum(CASE WHEN p.to_id = b.measurement_id AND p.action IN ('stay', 'join') THEN p.new_computed "
                "ELSE 0 END) - sum(CASE WHEN p.from_id = b.measurement_id THEN p.old_computed ELSE 0 END) AS delta") \
            in REMESH

    def test_shares_are_by_area_by_largest_remainder_summing_to_one(self):
        assert "(10000 - w.n) * CASE WHEN w.total > 0 THEN w.c / w.total ELSE 1.0 / w.n END AS raw" in REMESH
        assert ("<= (10000 - r.n) - sum(floor(r.raw)) OVER (PARTITION BY r.measurement_id) THEN 1 ELSE 0 END) "
                "/ 10000.0)::numeric(5,4) AS share") in REMESH

    def test_a_bundles_deductions_are_its_members_covers_of_1_sf_or_more(self):
        assert MIN_DEDUCTION_SF == 1
        assert "WHERE c.area_sf >= 1" in REMESH
        assert "COALESCE(ft.reported_quantity, ft.computed_quantity)" in REMESH  # as _NET subtracts a feature
        assert "ft.deleted_at IS NULL AND ft.area_impact = 'subtract'" in REMESH

    def test_an_emptied_bundle_is_retired_and_a_solo_one_left_for_a_bundle_too(self):
        assert ("CASE WHEN NOT EXISTS (SELECT 1 FROM rm_members m WHERE m.measurement_id = b.measurement_id) "
                "THEN now() ELSE b.retired_at END") in REMESH
        assert "retired_at = CASE WHEN s.action = 'join' THEN now() ELSE fm.retired_at END" in REMESH

    def test_a_flag_leaving_gets_a_fresh_solo_measurement_to_remeasure(self):
        assert ("INSERT INTO icid.flag_measurements (measurement_id, pir_id, outer_shape, outer_dimensions, outer_area, "
                "needs_remeasure, created_by) SELECT p.to_id, p.pir_id, p.new_shape, p.new_dims, p.new_computed, true")\
            in REMESH

    def test_a_solo_measurement_moves_with_its_flag_on_an_edit_only(self):
        assert "WHERE NOT (fm.surveyed_is_assessable IS NOT NULL) AND (p.action = 'join' OR (p.action = 'stay' AND " \
               "p.event = 'edit'))" in REMESH

    def test_every_change_is_a_system_edit_row(self):
        assert "INSERT INTO icid.measurement_edits (measurement_id, field_path, old_value, new_value, reason)" in REMESH
        for path in ("'member_flags[' || p.flag_id || ']'", "'area_share_fraction[' || s.flag_id || ']'",
                     "('outer_area',", "('deductions',", "('needs_remeasure',", "('retired_at',"):
            assert path in REMESH
        assert "WHERE e.old_value IS DISTINCT FROM e.new_value" in REMESH

    def test_the_editor_is_written_once(self):
        assert one_line(remesh_ctes("%s::uuid")).count("%s") == 1
        assert REMESH.count("%s") == 0

    def test_covers_the_statement_writes_replace_the_tables(self):
        sql = one_line(remesh_ctes(written="w"))
        assert "AND na.non_assessable_area_id NOT IN (SELECT source_id FROM w)" in sql
        assert "AND ft.feature_id NOT IN (SELECT source_id FROM w)" in sql
        assert "SELECT w.flag_id, w.kind, w.source_id, w.area_sf FROM w w WHERE w.live" in sql

    def test_a_snapshot_intent_keeps_the_flag_as_it_is(self):
        sql = one_line(snapshot_intent("delete", "flag deleted", "SELECT 1"))
        assert "'delete'::text AS event, NULL::uuid AS target_id, x.surveyed_thickness AS new_thickness" in sql
        assert "x.computed_quantity AS new_computed" in sql and "WHERE x.id IN (SELECT 1)" in sql

    def test_the_neighbours_read(self):
        with patch("api.queries.flag_measurements.run_query", return_value=[]) as run:
            list_remesh_neighbours(PAGE_ID)
        sql = one_line(run.call_args.args[0])
        assert "WHERE f.pir_page_id = %s AND f.deleted_at IS NULL" in sql and run.call_args.args[1] == (PAGE_ID,)
        assert "fm.surveyed_is_assessable IS NOT NULL AS bundled" in sql
        assert "c.claimed_by_idr_id IS NOT NULL) AS frozen" in sql


def statement(module: str, call) -> str:
    """The statement a query function runs, on one line."""
    with patch(f"api.queries.{module}.run_query", return_value=[]) as run:
        call()
    return one_line(run.call_args.args[0])


NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)
FLAG = {"display_order": 1, "shape": "rectangle", "dimensions": {"L": 5, "W": 5}, "surveyed_mark": "cross",
        "surveyed_is_assessable": True, "surveyed_thickness": "4in"}
AREA = {"display_order": 1, "shape": "rectangle", "dimensions": {"L": 1, "W": 1}, "sketch_x": 1, "sketch_y": 1,
        "rotation": 0, "reason": "grass"}
FEATURE = {"feature_type": "utility_cover", "area_impact": "subtract", "shape": "rectangle",
           "dimensions": {"L": 1, "W": 1}, "flag_id": flag_id(1)}
# Every writer the remesh rides on, and how its own UPDATE of flags takes the measurement (None: it updates none)
WRITERS = [
    ("a flag's PATCH", "flags", lambda: update_flag(flag_id(1), FLAG, Decimal("25"), ADMIN_ID), "flag"),
    ("a flag's delete", "flags", lambda: soft_delete_flag(flag_id(1), ADMIN_ID), "flag"),
    ("a flag's restore", "flags", lambda: undelete_flag(flag_id(1)), "flag"),
    ("the auto-flip", "sidewalk_strip", lambda: set_sidewalk_strip(PAGE_ID, [], ADMIN_ID, [flag_id(1)]), "f"),
    ("replacing the pages", "pir_pages", lambda: replace_pages(PIR_ID, [], ADMIN_ID), "flag"),
    ("deleting the pages", "pir_pages", lambda: delete_pages_for_pir(PIR_ID, ADMIN_ID), "flag"),
    ("an area's create", "non_assessable_areas", lambda: create_area(flag_id(1), ADMIN_ID, AREA, Decimal(1)), None),
    ("an area's edit", "non_assessable_areas", lambda: update_area(flag_id(1), AREA, Decimal(1)), None),
    ("an area's delete", "non_assessable_areas", lambda: soft_delete_area(flag_id(1), ADMIN_ID), None),
    ("a feature's create", "features", lambda: create_feature(PAGE_ID, ADMIN_ID, FEATURE, Decimal(1)), None),
    ("a feature's edit", "features", lambda: update_feature(flag_id(1), FEATURE, Decimal(1)), None),
    ("a feature's delete", "features", lambda: soft_delete_feature(flag_id(1), ADMIN_ID), None),
]


class TestEveryWriterCarriesTheRemesh:
    @pytest.mark.parametrize("name, module, call, alias", WRITERS, ids=[w[0] for w in WRITERS])
    def test_in_its_own_statement(self, name, module, call, alias):
        sql = statement(module, call)
        assert "intent AS (" in sql and "rm_plan AS MATERIALIZED (" in sql and "rm_edits AS (" in sql, name
        if alias:
            # its flags take their measurement and share from the remesh, in the same UPDATE
            assert "measurement_id = rf.measurement_id" in sql and "area_share_fraction = rf.share" in sql, name
            assert f"rf.flag_id = {alias}.id" in sql, name

    def test_a_flags_writers_read_it_back_through_the_measurements_as_left(self):
        for call in (lambda: update_flag(flag_id(1), FLAG, Decimal("25"), ADMIN_ID),
                     lambda: soft_delete_flag(flag_id(1), ADMIN_ID), lambda: undelete_flag(flag_id(1))):
            assert "JOIN rm_m fm ON fm.measurement_id = f.measurement_id" in statement("flags", call)

    def test_a_cover_writer_resyncs_the_covers_it_writes(self):
        area = statement("non_assessable_areas", lambda: update_area(flag_id(1), AREA, Decimal(1)))
        assert "'covers'::text AS event" in area and "WHERE x.id IN (SELECT flag_id FROM na)" in area
        assert "deleted_at IS NULL AS live FROM na" in area
        feature = statement("features", lambda: update_feature(flag_id(1), FEATURE, Decimal(1)))
        # the flag it was subtracted from, and the one it is now
        assert "UNION SELECT was.flag_id FROM icid.features was WHERE was.feature_id IN (SELECT feature_id FROM ft)" \
            in feature
        assert "deleted_at IS NULL AND area_impact = 'subtract' AND flag_id IS NOT NULL AS live FROM ft" in feature

    def test_the_pick_reaches_the_statement(self):
        with patch("api.queries.flags.run_query", return_value=[]) as run:
            update_flag(flag_id(1), FLAG, Decimal("25"), ADMIN_ID, BUNDLE)
        assert run.call_args.args[1][0] == BUNDLE
        with patch("api.queries.sidewalk_strip.run_query", return_value=[]) as run:
            set_sidewalk_strip(PAGE_ID, [], ADMIN_ID, [flag_id(1), flag_id(2)], {flag_id(2): BUNDLE})
        assert run.call_args.args[1][3:7] == ([flag_id(1), flag_id(2)], [None, BUNDLE], [flag_id(1), flag_id(2)],
                                               [flag_id(1), flag_id(2)])

    def test_a_new_flag_starts_solo_without_the_remesh(self):
        """A flag's create and the Smart PIR import give each new flag its own measurement (Y3a): nothing to remesh."""
        create = statement("flags", lambda: create_flag(PAGE_ID, ADMIN_ID, FLAG, Decimal("25")))
        assert "rm_plan" not in create
        with patch("api.queries.pir_import.run_query", return_value=[]) as run:
            import_pir_drawings(PIR_ID, ADMIN_ID, [], [], [], [], [], [], [])
        assert "rm_plan" not in run.call_args.args[0]


class TestMigration062:
    def test_the_schema_has_the_bundle_columns(self):
        schema = ROOT.joinpath("schema.sql").read_text(encoding="utf-8")
        table = schema[schema.index("CREATE TABLE icid.flag_measurements"):]
        table = table[:table.index("\n);\n")]
        for column in ("pay_item_id             UUID REFERENCES icid.contract_items(contract_item_id) ON DELETE SET NULL",
                       "surveyed_thickness      TEXT", "surveyed_is_assessable  BOOLEAN", "retired_at              TIMESTAMPTZ"):
            assert column in table
        assert "'component_rectangles'" in table and "jsonb_array_length(outer_dimensions -> 'rectangles') BETWEEN 1 AND 3" \
            in table
        assert "CHECK ((surveyed_thickness IS NULL) = (surveyed_is_assessable IS NULL)" in table

    def test_the_migration_matches_and_backfills_nothing(self):
        migration = ROOT.joinpath("migrations", "062_flag_measurement_bundles.sql").read_text(encoding="utf-8")
        assert "ADD COLUMN IF NOT EXISTS retired_at TIMESTAMPTZ" in migration
        assert "UPDATE" not in migration and "INSERT" not in migration


class TestBundleStatements:
    """Slice Y3b-2: forming a bundle, breaking one up and reading one, each in one statement."""

    def statement(self, call) -> tuple[str, tuple]:
        """The statement a measurement function runs, on one line, and its parameters."""
        with patch("api.queries.flag_measurements.run_query", return_value=[]) as run:
            call()
        return one_line(run.call_args.args[0]), run.call_args.args[1]

    def test_forming_checks_every_flag_again_and_writes_it_all_at_once(self):
        sql, params = self.statement(lambda: form_bundle(PAGE_ID, [flag_id(1), flag_id(2)], ("4in", True, None),
                                                         [{"L": 10, "W": 5}], Decimal("50.00"), ADMIN_ID))
        assert ("WHERE x.id = ANY(%s::uuid[]) AND x.pir_page_id = %s AND x.deleted_at IS NULL AND x.claimed_by_idr_id "
                "IS NULL AND src.surveyed_is_assessable IS NULL AND src.retired_at IS NULL") in sql
        assert "HAVING count(*) = cardinality(%s::uuid[])" in sql  # all of them, or nothing
        assert ("SELECT o.measurement_id, o.pir_id, 'component_rectangles', %s::jsonb, %s::numeric") in sql
        assert "c.area_sf >= 1" in sql and "ORDER BY c.flag_id::text, c.kind, c.source_id::text" in sql
        assert "SET measurement_id = s.measurement_id, area_share_fraction = s.share FROM rm_shares s" in sql
        assert "SET retired_at = now()" in sql and "'bundle formed'" in sql
        assert params[0] == ADMIN_ID and params[7].obj == {"rectangles": [{"L": 10, "W": 5}]}
        assert params[8] == Decimal("50.00") and params[9:] == (None, "4in", True)

    def test_forming_takes_no_needs_remeasure(self):
        sql, _ = self.statement(lambda: form_bundle(PAGE_ID, [flag_id(1)], ("4in", True, None), [], Decimal(0), ADMIN_ID))
        assert "needs_remeasure" not in sql  # the column's default, false: the OE just measured it

    def test_breaking_up_gives_each_member_its_own_and_retires_the_bundle_as_it_stood(self):
        sql, params = self.statement(lambda: unbundle(BUNDLE, ADMIN_ID))
        assert ("WHERE fm.measurement_id = %s AND fm.surveyed_is_assessable IS NOT NULL AND fm.retired_at IS NULL "
                "AND NOT EXISTS") in sql
        assert ("INSERT INTO icid.flag_measurements (measurement_id, pir_id, outer_shape, outer_dimensions, outer_area, "
                "created_by) SELECT u.to_id, u.pir_id, u.shape, u.dimensions, u.computed_quantity") in sql
        assert "SET measurement_id = u.to_id, area_share_fraction = 1" in sql
        assert "SET retired_at = now(), updated_at = now()" in sql and "outer_area =" not in sql.split("ub_retired")[1]
        assert "'bundle broken up'" in sql and params == (ADMIN_ID, BUNDLE)

    def test_reading_one_brings_its_members_and_whether_frozen(self):
        sql, params = self.statement(lambda: get_measurement(BUNDLE))
        assert "me.surveyed_is_assessable IS NOT NULL AS bundled" in sql and "AS frozen" in sql
        assert "'quantity', COALESCE(f.reported_quantity" in sql and "WHERE f.measurement_id = me.measurement_id" in sql
        assert params == (BUNDLE,)

    def test_a_chain_is_flags_sharing_edges_one_to_the_next(self):
        a, b, c = (neighbour(n, x, BUNDLE) for n, x in ((1, 400), (2, 480), (3, 560)))
        assert unconnected([a, c, b], 16) == []
        assert unconnected([a, c], 16) == [c]
        assert unconnected([a, b, neighbour(4, 480, BUNDLE, y=380)], 16) == []  # under b


class TestReadersCarryTheBundle:
    def test_the_construction_report_reads_each_flags_bundle(self):
        with patch("api.queries.construction_report.run_query", return_value=[]) as run:
            list_report_flags(PIR_ID, PIR_ID)
        sql = one_line(run.call_args.args[0])
        assert "CASE WHEN fm.surveyed_is_assessable IS NOT NULL THEN jsonb_build_object(" in sql and "AS bundle" in sql
        assert "'what', COALESCE(da.reason, df.feature_type)" in sql

    def test_the_assessment_summary_reads_needs_remeasure(self):
        with patch("api.queries.assessment_summary.run_query", return_value=[]) as run:
            list_assessment_flags("HWS0023")
        assert "fm.needs_remeasure" in run.call_args.args[0]
