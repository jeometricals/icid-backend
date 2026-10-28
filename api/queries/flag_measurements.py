"""
SQL for measurement bundles (Slice Y3b-1): the remesh, a set of CTEs every statement that changes a flag's classification,
geometry, existence or covers carries, so a flag's measurement follows it in the same statement; and the read of a
page's flags the remesh's Python picks a bundle to join from.

A measurement is a bundle when it carries a classification (surveyed_is_assessable set, migration 062); a solo one
(every flag's since Y3a) doesn't, and reads its flag's. A bundle with a claimed member is frozen: no flag joins it and no
writer changes its figures, so an approved IDR's quantities stand. Bundles are formed and broken up by their own routes
(Slice Y3b-2: form_bundle, unbundle) and read with get_measurement.
"""

from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from psycopg.types.json import Jsonb

from api.db.runner import run_query

# What changed, as the measurement_edits rows say (and the intent CTEs name them)
REASON_EDIT = "flag edited"
REASON_FLIP = "auto-flip outside the sidewalk strip"
REASON_DELETE = "flag deleted"
REASON_RETIRE = "page replaced or deleted"
REASON_UNDELETE = "flag restored"
REASON_COVERS = "non-assessable area or subtracted feature changed"
# A cover (a non-assessable area, or a feature subtracted from the flag) counts on a bundle from this many square feet
MIN_DEDUCTION_SF = 1
# Whether a measurement (fm) is a bundle, and whether one (by id) has a claimed member
BUNDLED = "fm.surveyed_is_assessable IS NOT NULL"
_HAS_CLAIMED = "EXISTS (SELECT 1 FROM icid.flags c WHERE c.measurement_id = {id} AND c.claimed_by_idr_id IS NOT NULL)"


# A deduction as a measurement keeps it, from a cover row named c (_covers_sql)
_DEDUCTION = """jsonb_build_object('type', c.kind, 'area_sf', c.area_sf, 'source_flag_id', c.flag_id,
                                          CASE c.kind WHEN 'non_ass_area' THEN 'source_area_id'
                                                      ELSE 'source_feature_id' END, c.source_id)"""
# The order a measurement keeps its deductions in, the same however they were written, so two lists compare
_DEDUCTION_ORDER = "c.flag_id::text, c.kind, c.source_id::text"


def _shares_ctes(members: str) -> str:
    """
    The CTEs that share measurements among their members by area: each member's computed_quantity over the members'
    total (equally when that is 0), to 4 places by largest remainder (every member at least 0.0001), so a
    measurement's shares sum to exactly 1.0000.
    Takes the SELECT of the members (flag_id, measurement_id, old_share, computed); written here, never from a request.
    Returns the CTEs rm_weights, rm_raw and rm_shares (flag_id, measurement_id, old_share, share), with no leading
    comma.
    """
    return f"""
        rm_weights AS (
            SELECT m.flag_id, m.measurement_id, m.old_share, GREATEST(m.computed, 0) AS c,
                   count(*) OVER w AS n, sum(GREATEST(m.computed, 0)) OVER w AS total
            FROM ({members}) m
            WINDOW w AS (PARTITION BY m.measurement_id)
        ),
        rm_raw AS (
            SELECT w.*, (10000 - w.n) * CASE WHEN w.total > 0 THEN w.c / w.total ELSE 1.0 / w.n END AS raw
            FROM rm_weights w
        ),
        rm_shares AS MATERIALIZED (
            SELECT r.flag_id, r.measurement_id, r.old_share,
                   ((1 + floor(r.raw)
                     + CASE WHEN row_number() OVER (PARTITION BY r.measurement_id
                                                    ORDER BY r.raw - floor(r.raw) DESC, r.flag_id)
                                 <= (10000 - r.n) - sum(floor(r.raw)) OVER (PARTITION BY r.measurement_id)
                            THEN 1 ELSE 0 END) / 10000.0)::numeric(5,4) AS share
            FROM rm_raw r
        )"""


def _covers_sql(flags: str, written_ids: str = "", written_rows: str = "") -> str:
    """
    The covers of some flags, as a bundle deducts them: their live non-assessable areas and the live features
    subtracted from them, each with its area (a feature's override, else its area, as _NET takes it off a solo flag).
    Takes the SELECT of the flags' ids, and, for covers the statement writes, the filter leaving the table's rows for
    them out ({column} for the id) and the rows standing in for them (both '' for none).
    Returns the SELECT (flag_id, kind, source_id, area_sf).
    """
    return f"""SELECT na.flag_id, 'non_ass_area'::text AS kind, na.non_assessable_area_id AS source_id,
                   na.computed_quantity AS area_sf
            FROM icid.non_assessable_areas na
            WHERE na.deleted_at IS NULL AND na.flag_id IN ({flags})
              {written_ids.format(column="na.non_assessable_area_id")}
            UNION ALL
            SELECT ft.flag_id, 'subtract_feature', ft.feature_id, COALESCE(ft.reported_quantity, ft.computed_quantity)
            FROM icid.features ft
            WHERE ft.deleted_at IS NULL AND ft.area_impact = 'subtract' AND ft.flag_id IN ({flags})
              {written_ids.format(column="ft.feature_id")}{written_rows}"""


def list_remesh_neighbours(pir_page_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch a page's live flags as the remesh weighs them: where each lies, its classification, its measurement, whether
    that is a bundle and whether a member of it is claimed.
    Takes the page's id.
    Returns [{id, display_order, measurement_id, shape, dimensions, sketch_x, sketch_y, rotation, surveyed_thickness,
    surveyed_is_assessable, pay_item_id, claimed_by_idr_id, claimed_by_idr_number, scale_px_per_ft, bundled, frozen}],
    or None on failure.
    """
    sql = f"""
        SELECT f.id, f.display_order, f.measurement_id, f.shape, f.dimensions, f.sketch_x, f.sketch_y, f.rotation,
               f.surveyed_thickness, f.surveyed_is_assessable, f.pay_item_id, f.claimed_by_idr_id,
               (SELECT i.idr_number FROM icid.idrs i WHERE i.idr_id = f.claimed_by_idr_id) AS claimed_by_idr_number,
               pg.scale_px_per_ft, {BUNDLED} AS bundled, {_HAS_CLAIMED.format(id="f.measurement_id")} AS frozen
        FROM icid.flags f
        JOIN icid.pir_pages pg ON pg.pir_page_id = f.pir_page_id
        JOIN icid.flag_measurements fm ON fm.measurement_id = f.measurement_id
        WHERE f.pir_page_id = %s AND f.deleted_at IS NULL;
    """
    return run_query(sql, (pir_page_id,))


def snapshot_intent(event: str, reason: str, flags: str) -> str:
    """
    An intent (see remesh_ctes) for flags whose classification, shape and area the statement leaves as they are: a
    delete, a restore, a page retired or a cover changed.
    Takes the event (delete, undelete or covers), the reason (one of this module's REASON_ constants) and the SQL
    listing the flags' ids (written here, never from a request).
    Returns the SELECT, for a CTE named intent; no parameters.
    """
    return f"""SELECT x.id AS flag_id, '{event}'::text AS event, NULL::uuid AS target_id,
                   x.surveyed_thickness AS new_thickness, x.surveyed_is_assessable AS new_assessable,
                   x.pay_item_id AS new_pay_item, x.computed_quantity AS new_computed, x.shape AS new_shape,
                   x.dimensions AS new_dims, '{reason}'::text AS reason
            FROM icid.flags x
            WHERE x.id IN ({flags})"""


def remesh_ctes(editor: str = "NULL::uuid", written: Optional[str] = None) -> str:
    """
    The remesh, as CTEs to follow a CTE named intent in the statement's WITH list. intent has a row per flag the
    statement changes (each once): flag_id, event (edit, flip, delete, undelete or covers), target_id (the bundle the
    remesh's Python picked for it to join, or NULL), the flag's classification, area, shape and dimensions after the
    statement (new_thickness, new_assessable, new_pay_item, new_computed, new_shape, new_dims) and reason. It must
    list exactly the flags the statement's own write changes, by the same conditions, so both read one snapshot.

    What each flag does (rm_plan.action):
      * join: reclassified (edit, flip) with a target still a live, unfrozen bundle of its new classification with a
        live member on its page. It leaves its measurement (a solo one is retired) and takes a share of the target.
      * fresh: reclassified without one, while in a bundle; or deleted (unclaimed) or restored while in a bundle. It
        leaves for a new solo measurement of its own, needs_remeasure set.
      * stay: anything else. On a solo measurement an edit moves the measurement's shape, dimensions and area with the
        flag, as Y3a did; in a bundle its area change moves the outer area.
    A bundle a flag leaves, joins or resizes in (not frozen) takes the outer area of its members' changes, its
    members' shares again (by computed_quantity, largest remainder, summing to 1.0000) and their deductions again (each
    member's covers of MIN_DEDUCTION_SF or more), and needs_remeasure; emptied, it is retired. A frozen one keeps its
    figures and only takes needs_remeasure. Every change of a measurement is a measurement_edits row (no editor: the
    system's).

    The statement's own UPDATE of its flags sets measurement_id and area_share_fraction from rm_flags (flag_id,
    measurement_id, share); other members' shares are written here. rm_m is every measurement as the statement leaves
    it, for quantity_laterals.
    Takes the SQL of the editing user's uuid (one expression, or a placeholder: it is written once, in rm_editor) and
    the name of a CTE of covers the statement writes (flag_id, kind non_ass_area or subtract_feature, source_id,
    area_sf, live), read in place of the table's rows for them; None when it writes none.
    Returns the CTEs, each preceded by a comma, to append after intent.
    """
    if written is None:
        written_ids = written_rows = ""
    else:
        written_ids = "AND {column} NOT IN (SELECT source_id FROM " + written + ")"
        written_rows = f"""
            UNION ALL
            SELECT w.flag_id, w.kind, w.source_id, w.area_sf FROM {written} w
            WHERE w.live AND w.flag_id IN (SELECT flag_id FROM rm_plan)"""
    return f""",
        rm_editor AS (
            SELECT {editor} AS uuid
        ),
        rm_mv AS MATERIALIZED (
            SELECT i.flag_id, i.event, i.reason, i.new_thickness, i.new_assessable, i.new_pay_item, i.new_computed,
                   i.new_shape, i.new_dims, x.measurement_id AS from_id, x.computed_quantity AS old_computed,
                   x.area_share_fraction AS old_share, x.pir_id, x.claimed_by_idr_id IS NOT NULL AS claimed,
                   src.surveyed_is_assessable IS NOT NULL AS from_bundled,
                   (x.surveyed_thickness, x.surveyed_is_assessable, x.pay_item_id)
                       IS DISTINCT FROM (i.new_thickness, i.new_assessable, i.new_pay_item) AS reclassified,
                   tgt.measurement_id AS target_id
            FROM intent i
            JOIN icid.flags x ON x.id = i.flag_id
            JOIN icid.flag_measurements src ON src.measurement_id = x.measurement_id
            LEFT JOIN icid.flag_measurements tgt
                   ON tgt.measurement_id = i.target_id AND tgt.measurement_id <> x.measurement_id
                  AND tgt.retired_at IS NULL AND tgt.surveyed_is_assessable IS NOT NULL
                  AND (tgt.surveyed_thickness, tgt.surveyed_is_assessable, tgt.pay_item_id)
                      IS NOT DISTINCT FROM (i.new_thickness, i.new_assessable, i.new_pay_item)
                  AND NOT {_HAS_CLAIMED.format(id="tgt.measurement_id")}
                  AND EXISTS (SELECT 1 FROM icid.flags n WHERE n.measurement_id = tgt.measurement_id
                              AND n.deleted_at IS NULL AND n.pir_page_id = x.pir_page_id)
        ),
        rm_plan AS MATERIALIZED (
            SELECT mv.*, a.action,
                   CASE a.action WHEN 'join' THEN mv.target_id WHEN 'fresh' THEN gen_random_uuid()
                        ELSE mv.from_id END AS to_id
            FROM rm_mv mv
            CROSS JOIN LATERAL (SELECT CASE
                WHEN mv.event IN ('edit', 'flip') AND mv.reclassified AND mv.target_id IS NOT NULL THEN 'join'
                WHEN mv.event IN ('edit', 'flip') AND mv.reclassified AND mv.from_bundled THEN 'fresh'
                WHEN mv.event = 'delete' AND mv.from_bundled AND NOT mv.claimed THEN 'fresh'
                WHEN mv.event = 'undelete' AND mv.from_bundled THEN 'fresh'
                ELSE 'stay' END AS action) a
        ),
        rm_reason AS (
            SELECT min(reason) AS reason FROM rm_plan
        ),
        rm_bundles AS MATERIALIZED (
            SELECT fm.*, {_HAS_CLAIMED.format(id="fm.measurement_id")} AS frozen
            FROM icid.flag_measurements fm
            WHERE {BUNDLED}
              AND fm.measurement_id IN (SELECT from_id FROM rm_plan UNION SELECT to_id FROM rm_plan WHERE action = 'join')
        ),
        rm_members AS MATERIALIZED (
            SELECT x.id AS flag_id, x.measurement_id, x.computed_quantity AS computed, x.area_share_fraction AS old_share
            FROM icid.flags x
            WHERE x.measurement_id IN (SELECT measurement_id FROM rm_bundles) AND x.id NOT IN (SELECT flag_id FROM rm_plan)
            UNION ALL
            SELECT p.flag_id, p.to_id, p.new_computed, p.old_share
            FROM rm_plan p
            WHERE p.action IN ('stay', 'join') AND p.to_id IN (SELECT measurement_id FROM rm_bundles)
        ),
        rm_bundle_delta AS (
            SELECT b.measurement_id,
                   sum(CASE WHEN p.to_id = b.measurement_id AND p.action IN ('stay', 'join') THEN p.new_computed
                            ELSE 0 END)
                   - sum(CASE WHEN p.from_id = b.measurement_id THEN p.old_computed ELSE 0 END) AS delta,
                   bool_or(p.action <> 'stay' OR p.new_computed IS DISTINCT FROM p.old_computed) AS reshaped
            FROM rm_bundles b
            JOIN rm_plan p ON p.from_id = b.measurement_id OR p.to_id = b.measurement_id
            GROUP BY b.measurement_id
        ),
{_shares_ctes('''SELECT m.* FROM rm_members m
                  JOIN rm_bundles b ON b.measurement_id = m.measurement_id AND NOT b.frozen
                  JOIN rm_bundle_delta d ON d.measurement_id = m.measurement_id AND d.reshaped''')},
        rm_covers AS (
            {_covers_sql("SELECT flag_id FROM rm_plan", written_ids, written_rows)}
        ),
        rm_deductions AS (
            SELECT b.measurement_id,
                   COALESCE(jsonb_agg(d.entry ORDER BY d.entry ->> 'source_flag_id', d.entry ->> 'type', d.source_id)
                                FILTER (WHERE d.entry IS NOT NULL), '[]'::jsonb) AS deductions
            FROM rm_bundles b
            LEFT JOIN LATERAL (
                SELECT e AS entry, COALESCE(e ->> 'source_area_id', e ->> 'source_feature_id') AS source_id
                FROM jsonb_array_elements(b.deductions) e
                WHERE (e ->> 'source_flag_id')::uuid NOT IN (
                    SELECT flag_id FROM rm_plan WHERE from_id = b.measurement_id OR to_id = b.measurement_id)
                UNION ALL
                SELECT {_DEDUCTION}, c.source_id::text
                FROM rm_covers c
                JOIN rm_plan p ON p.flag_id = c.flag_id AND p.to_id = b.measurement_id AND p.action IN ('stay', 'join')
                WHERE c.area_sf >= {MIN_DEDUCTION_SF}
            ) d ON true
            WHERE NOT b.frozen
            GROUP BY b.measurement_id
        ),
        rm_bundle_after AS (
            SELECT b.measurement_id,
                   CASE WHEN b.frozen THEN b.outer_area ELSE GREATEST(b.outer_area + d.delta, 0) END AS outer_area,
                   CASE WHEN b.frozen THEN b.deductions ELSE dd.deductions END AS deductions,
                   b.needs_remeasure OR d.reshaped AS needs_remeasure,
                   CASE WHEN NOT EXISTS (SELECT 1 FROM rm_members m WHERE m.measurement_id = b.measurement_id)
                        THEN now() ELSE b.retired_at END AS retired_at
            FROM rm_bundles b
            JOIN rm_bundle_delta d ON d.measurement_id = b.measurement_id
            LEFT JOIN rm_deductions dd ON dd.measurement_id = b.measurement_id
        ),
        rm_solo_after AS (
            SELECT p.from_id AS measurement_id, p.action, p.new_shape, p.new_dims, p.new_computed
            FROM rm_plan p
            JOIN icid.flag_measurements fm ON fm.measurement_id = p.from_id
            WHERE NOT ({BUNDLED}) AND (p.action = 'join' OR (p.action = 'stay' AND p.event = 'edit'))
        ),
        rm_solo AS (
            UPDATE icid.flag_measurements fm
            SET outer_shape = CASE WHEN s.action = 'stay' THEN s.new_shape ELSE fm.outer_shape END,
                outer_dimensions = CASE WHEN s.action = 'stay' THEN s.new_dims ELSE fm.outer_dimensions END,
                outer_area = CASE WHEN s.action = 'stay' THEN s.new_computed ELSE fm.outer_area END,
                retired_at = CASE WHEN s.action = 'join' THEN now() ELSE fm.retired_at END,
                updated_at = CASE WHEN s.action = 'join' OR (fm.outer_shape, fm.outer_dimensions, fm.outer_area)
                                       IS DISTINCT FROM (s.new_shape, s.new_dims, s.new_computed)
                                  THEN now() ELSE fm.updated_at END,
                updated_by = CASE WHEN s.action = 'join' OR (fm.outer_shape, fm.outer_dimensions, fm.outer_area)
                                       IS DISTINCT FROM (s.new_shape, s.new_dims, s.new_computed)
                                  THEN (SELECT uuid FROM rm_editor) ELSE fm.updated_by END
            FROM rm_solo_after s
            WHERE fm.measurement_id = s.measurement_id
            RETURNING fm.*
        ),
        rm_bundled AS (
            UPDATE icid.flag_measurements fm
            SET outer_area = a.outer_area, deductions = a.deductions, needs_remeasure = a.needs_remeasure,
                retired_at = a.retired_at, updated_at = now(), updated_by = (SELECT uuid FROM rm_editor)
            FROM rm_bundle_after a
            WHERE fm.measurement_id = a.measurement_id
              AND (fm.outer_area, fm.deductions, fm.needs_remeasure, fm.retired_at)
                  IS DISTINCT FROM (a.outer_area, a.deductions, a.needs_remeasure, a.retired_at)
            RETURNING fm.*
        ),
        rm_fresh AS (
            INSERT INTO icid.flag_measurements (measurement_id, pir_id, outer_shape, outer_dimensions, outer_area,
                                                needs_remeasure, created_by)
            SELECT p.to_id, p.pir_id, p.new_shape, p.new_dims, p.new_computed, true,
                   COALESCE((SELECT uuid FROM rm_editor), x.created_by)
            FROM rm_plan p
            JOIN icid.flags x ON x.id = p.flag_id
            WHERE p.action = 'fresh'
            RETURNING *
        ),
        rm_flags AS (
            SELECT p.flag_id, p.to_id AS measurement_id,
                   (CASE WHEN p.action = 'fresh' THEN 1 ELSE COALESCE(s.share, p.old_share) END)::numeric(5,4) AS share
            FROM rm_plan p
            LEFT JOIN rm_shares s ON s.flag_id = p.flag_id
        ),
        rm_member_shares AS (
            UPDATE icid.flags x
            SET area_share_fraction = s.share
            FROM rm_shares s
            WHERE x.id = s.flag_id AND s.flag_id NOT IN (SELECT flag_id FROM rm_plan)
              AND x.area_share_fraction <> s.share
            RETURNING x.id
        ),
        rm_m AS (
            SELECT * FROM rm_fresh
            UNION ALL SELECT * FROM rm_solo
            UNION ALL SELECT * FROM rm_bundled
            UNION ALL
            SELECT fm.* FROM icid.flag_measurements fm
            WHERE fm.measurement_id NOT IN (SELECT measurement_id FROM rm_fresh UNION ALL SELECT measurement_id FROM rm_solo
                                            UNION ALL SELECT measurement_id FROM rm_bundled)
        ),
        rm_edits AS (
            INSERT INTO icid.measurement_edits (measurement_id, field_path, old_value, new_value, reason)
            SELECT e.measurement_id, e.field_path, e.old_value, e.new_value, e.reason
            FROM (
                SELECT side.measurement_id, 'member_flags[' || p.flag_id || ']' AS field_path,
                       jsonb_build_object('measurement_id', p.from_id) AS old_value,
                       jsonb_build_object('measurement_id', p.to_id) AS new_value, p.reason
                FROM rm_plan p
                CROSS JOIN LATERAL (VALUES (p.from_id), (p.to_id)) AS side(measurement_id)
                WHERE p.action <> 'stay'
                UNION ALL
                SELECT s.measurement_id, 'area_share_fraction[' || s.flag_id || ']', to_jsonb(s.old_share),
                       to_jsonb(s.share), r.reason
                FROM rm_shares s CROSS JOIN rm_reason r
                UNION ALL
                SELECT b.measurement_id, v.field_path, v.old_value, v.new_value, r.reason
                FROM rm_bundles b
                JOIN rm_bundle_after a ON a.measurement_id = b.measurement_id
                CROSS JOIN rm_reason r
                CROSS JOIN LATERAL (VALUES
                    ('outer_area', to_jsonb(b.outer_area), to_jsonb(a.outer_area)),
                    ('deductions', b.deductions, a.deductions),
                    ('needs_remeasure', to_jsonb(b.needs_remeasure), to_jsonb(a.needs_remeasure)),
                    ('retired_at', COALESCE(to_jsonb(b.retired_at), 'null'::jsonb),
                     COALESCE(to_jsonb(a.retired_at), 'null'::jsonb))
                ) AS v(field_path, old_value, new_value)
                UNION ALL
                SELECT s.measurement_id, 'retired_at', 'null'::jsonb, to_jsonb(now()), r.reason
                FROM rm_solo_after s CROSS JOIN rm_reason r
                WHERE s.action = 'join'
            ) e
            WHERE e.old_value IS DISTINCT FROM e.new_value
            RETURNING edit_id
        )"""


# What a bundle's own writes say in measurement_edits
REASON_BUNDLE = "bundle formed"
REASON_UNBUNDLE = "bundle broken up"


def form_bundle(pir_page_id: UUID, flag_ids: list[UUID], classification: tuple[Any, Any, Any],
                rectangles: list[dict[str, Any]], outer_area: Decimal, editor_uuid: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Bundle flags into one measurement, in one statement (Slice Y3b-2): a new measurement of the outer dimensions measured
    (component_rectangles, outer_area their sum, needs_remeasure false), carrying the flags' classification and as
    deductions their covers of MIN_DEDUCTION_SF or more; the flags take it, shared by area (_shares_ctes); their solo
    measurements are retired; every move is a measurement_edits row by the editor. Writes nothing unless every flag is
    still live, on the page, unclaimed, in a live solo measurement and of the classification given.
    Takes the page's id, the flags' ids (each once), their classification (thickness, assessable, pay item id), the
    rectangles ([{L, W}], 1 to 3), their total area and the editing user's uuid.
    Returns [{measurement_id}] for the bundle formed, [] when the flags changed meanwhile, or None on failure.
    """
    thickness, assessable, pay_item = classification
    sql = f"""
        WITH bd_editor AS (
            SELECT %s::uuid AS uuid
        ),
        bd_members AS MATERIALIZED (
            SELECT x.id AS flag_id, x.measurement_id AS from_id, x.computed_quantity AS computed,
                   x.area_share_fraction AS old_share, x.pir_id
            FROM icid.flags x
            JOIN icid.flag_measurements src ON src.measurement_id = x.measurement_id
            WHERE x.id = ANY(%s::uuid[]) AND x.pir_page_id = %s AND x.deleted_at IS NULL
              AND x.claimed_by_idr_id IS NULL AND src.surveyed_is_assessable IS NULL AND src.retired_at IS NULL
              AND (x.surveyed_thickness, x.surveyed_is_assessable, x.pay_item_id)
                  IS NOT DISTINCT FROM (%s::text, %s::boolean, %s::uuid)
        ),
        bd_ok AS MATERIALIZED (
            SELECT gen_random_uuid() AS measurement_id, (array_agg(pir_id))[1] AS pir_id
            FROM bd_members
            HAVING count(*) = cardinality(%s::uuid[])
        ),
        bd_flags AS MATERIALIZED (
            SELECT m.flag_id, o.measurement_id, m.old_share, m.computed, m.from_id
            FROM bd_members m CROSS JOIN bd_ok o
        ),{_shares_ctes("SELECT flag_id, measurement_id, old_share, computed FROM bd_flags")},
        bd_covers AS (
            {_covers_sql("SELECT flag_id FROM bd_flags")}
        ),
        bd_new AS (
            INSERT INTO icid.flag_measurements (measurement_id, pir_id, outer_shape, outer_dimensions, outer_area,
                                                deductions, pay_item_id, surveyed_thickness, surveyed_is_assessable,
                                                created_by)
            SELECT o.measurement_id, o.pir_id, 'component_rectangles', %s::jsonb, %s::numeric,
                   COALESCE((SELECT jsonb_agg({_DEDUCTION} ORDER BY {_DEDUCTION_ORDER})
                             FROM bd_covers c WHERE c.area_sf >= {MIN_DEDUCTION_SF}), '[]'::jsonb),
                   %s::uuid, %s::text, %s::boolean, (SELECT uuid FROM bd_editor)
            FROM bd_ok o
            RETURNING measurement_id
        ),
        bd_moved AS (
            UPDATE icid.flags x
            SET measurement_id = s.measurement_id, area_share_fraction = s.share
            FROM rm_shares s
            WHERE x.id = s.flag_id
            RETURNING x.id
        ),
        bd_retired AS (
            UPDATE icid.flag_measurements fm
            SET retired_at = now(), updated_at = now(), updated_by = (SELECT uuid FROM bd_editor)
            FROM bd_flags b
            WHERE fm.measurement_id = b.from_id
            RETURNING fm.measurement_id
        ),
        bd_edits AS (
            INSERT INTO icid.measurement_edits (measurement_id, field_path, old_value, new_value, reason, editor_uuid)
            SELECT side.measurement_id, 'member_flags[' || b.flag_id || ']', jsonb_build_object('measurement_id', b.from_id),
                   jsonb_build_object('measurement_id', b.measurement_id), '{REASON_BUNDLE}', (SELECT uuid FROM bd_editor)
            FROM bd_flags b
            CROSS JOIN LATERAL (VALUES (b.from_id), (b.measurement_id)) AS side(measurement_id)
            UNION ALL
            SELECT b.from_id, 'retired_at', 'null'::jsonb, to_jsonb(now()), '{REASON_BUNDLE}', (SELECT uuid FROM bd_editor)
            FROM bd_flags b
            RETURNING edit_id
        )
        SELECT n.measurement_id
        FROM bd_new n
        CROSS JOIN (SELECT count(*) FROM bd_moved) AS moved
        CROSS JOIN (SELECT count(*) FROM bd_retired) AS retired
        CROSS JOIN (SELECT count(*) FROM bd_edits) AS edits;
    """
    ids = list(flag_ids)
    params = (editor_uuid, ids, pir_page_id, thickness, assessable, pay_item, ids, Jsonb({"rectangles": rectangles}),
              outer_area, pay_item, thickness, assessable)
    return run_query(sql, params)


def unbundle(measurement_id: UUID, editor_uuid: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Break a bundle up, in one statement (Slice Y3b-2): each member takes a new solo measurement of its own shape,
    dimensions and area (needs_remeasure false, share 1.0000), and the bundle is retired as it stood (its figures kept
    for the record); every move is a measurement_edits row by the editor. Writes nothing unless it is still a live
    bundle with no member claimed.
    Takes the bundle's id and the editing user's uuid.
    Returns [{retired, members}] (retired 0 when nothing was written), or None on failure.
    """
    sql = f"""
        WITH ub_editor AS (
            SELECT %s::uuid AS uuid
        ),
        ub AS MATERIALIZED (
            SELECT fm.measurement_id
            FROM icid.flag_measurements fm
            WHERE fm.measurement_id = %s AND {BUNDLED} AND fm.retired_at IS NULL
              AND NOT {_HAS_CLAIMED.format(id="fm.measurement_id")}
        ),
        ub_flags AS MATERIALIZED (
            SELECT x.id AS flag_id, x.pir_id, x.shape, x.dimensions, x.computed_quantity, x.created_by,
                   ub.measurement_id AS from_id, gen_random_uuid() AS to_id
            FROM icid.flags x
            JOIN ub ON ub.measurement_id = x.measurement_id
        ),
        ub_fresh AS (
            INSERT INTO icid.flag_measurements (measurement_id, pir_id, outer_shape, outer_dimensions, outer_area,
                                                created_by)
            SELECT u.to_id, u.pir_id, u.shape, u.dimensions, u.computed_quantity,
                   COALESCE((SELECT uuid FROM ub_editor), u.created_by)
            FROM ub_flags u
            RETURNING measurement_id
        ),
        ub_moved AS (
            UPDATE icid.flags x
            SET measurement_id = u.to_id, area_share_fraction = 1
            FROM ub_flags u
            WHERE x.id = u.flag_id
            RETURNING x.id
        ),
        ub_retired AS (
            UPDATE icid.flag_measurements fm
            SET retired_at = now(), updated_at = now(), updated_by = (SELECT uuid FROM ub_editor)
            FROM ub
            WHERE fm.measurement_id = ub.measurement_id
            RETURNING fm.measurement_id
        ),
        ub_edits AS (
            INSERT INTO icid.measurement_edits (measurement_id, field_path, old_value, new_value, reason, editor_uuid)
            SELECT side.measurement_id, 'member_flags[' || u.flag_id || ']', jsonb_build_object('measurement_id', u.from_id),
                   jsonb_build_object('measurement_id', u.to_id), '{REASON_UNBUNDLE}', (SELECT uuid FROM ub_editor)
            FROM ub_flags u
            CROSS JOIN LATERAL (VALUES (u.from_id), (u.to_id)) AS side(measurement_id)
            UNION ALL
            SELECT ub.measurement_id, 'retired_at', 'null'::jsonb, to_jsonb(now()), '{REASON_UNBUNDLE}',
                   (SELECT uuid FROM ub_editor)
            FROM ub
            RETURNING edit_id
        )
        SELECT (SELECT count(*) FROM ub_retired) AS retired, (SELECT count(*) FROM ub_moved) AS members,
               (SELECT count(*) FROM ub_fresh) AS fresh, (SELECT count(*) FROM ub_edits) AS edits;
    """
    return run_query(sql, (editor_uuid, measurement_id))


def get_measurement(measurement_id: UUID) -> Optional[dict[str, Any]]:
    """
    Fetch one measurement as the measurement routes show it (Slice Y3b-2): its own columns, its PIR's property and
    project, whether it is a bundle and frozen (a member claimed, and by which IDR), and its members with their shares
    and quantities (FLAG_QUANTITY), live or deleted.
    Takes the measurement's id.
    Returns the measurement dict (members a list of {flag_id, display_order, pir_page_id, area_share_fraction,
    computed_quantity, quantity, claimed_by_idr_id, deleted_at}), or None if none matches.
    """
    # api.queries.flags reads this module's remesh, so its quantity SQL is imported when this runs
    from api.queries.flags import FLAG_QUANTITY, QUANTITY_LATERALS

    sql = f"""
        SELECT me.*, pir.property_id, p.project_id, me.surveyed_is_assessable IS NOT NULL AS bundled,
               (SELECT NULLIF(btrim(concat_ws(' ', u.first_name, u.last_name)), '') FROM icid.users u
                WHERE u.uuid = me.created_by) AS created_by_name,
               {_HAS_CLAIMED.format(id="me.measurement_id")} AS frozen,
               (SELECT i.idr_number FROM icid.flags c JOIN icid.idrs i ON i.idr_id = c.claimed_by_idr_id
                WHERE c.measurement_id = me.measurement_id ORDER BY i.idr_number LIMIT 1) AS claimed_by_idr_number,
               COALESCE((
                   SELECT jsonb_agg(jsonb_build_object(
                       'flag_id', f.id, 'display_order', f.display_order, 'pir_page_id', f.pir_page_id,
                       'area_share_fraction', f.area_share_fraction, 'computed_quantity', f.computed_quantity,
                       'quantity', {FLAG_QUANTITY}, 'claimed_by_idr_id', f.claimed_by_idr_id,
                       'deleted_at', f.deleted_at) ORDER BY f.display_order, f.created_at)
                   FROM icid.flags f{QUANTITY_LATERALS}
                   WHERE f.measurement_id = me.measurement_id
               ), '[]'::jsonb) AS members
        FROM icid.flag_measurements me
        JOIN icid.pirs pir ON pir.id = me.pir_id
        JOIN icid.properties p ON p.id = pir.property_id
        WHERE me.measurement_id = %s;
    """
    rows = run_query(sql, (measurement_id,))
    return rows[0] if rows else None


REASON_REMEASURED = "re-measured"


def update_measurement(measurement_id: UUID, rectangles: Optional[list[dict[str, Any]]], outer_area: Optional[Decimal],
                       editor_uuid: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Mark a bundle re-measured, in one statement (Slice Y4a): its outline, when given, replaced by the rectangles
    measured (outer_area their sum), and needs_remeasure cleared either way; each field it changes a measurement_edits
    row by the editor. Writes nothing unless it is still a live bundle, and, for a new outline, has no member claimed
    (a frozen bundle's figures never change).
    Takes the bundle's id, the rectangles ([{L, W}], 1 to 3; None to keep the outline), their total area (None with
    them) and the editing user's uuid.
    Returns [{measurement_id}] when written, [] when it no longer may be, or None on failure.
    """
    sql = f"""
        WITH me_editor AS (
            SELECT %s::uuid AS uuid
        ),
        me AS MATERIALIZED (
            SELECT fm.*
            FROM icid.flag_measurements fm
            WHERE fm.measurement_id = %s AND {BUNDLED} AND fm.retired_at IS NULL
              AND (%s::jsonb IS NULL OR NOT {_HAS_CLAIMED.format(id="fm.measurement_id")})
        ),
        me_new AS (
            UPDATE icid.flag_measurements fm
            SET outer_shape = CASE WHEN %s::jsonb IS NULL THEN fm.outer_shape ELSE 'component_rectangles' END,
                outer_dimensions = COALESCE(%s::jsonb, fm.outer_dimensions),
                outer_area = COALESCE(%s::numeric, fm.outer_area),
                needs_remeasure = false, updated_at = now(), updated_by = (SELECT uuid FROM me_editor)
            FROM me
            WHERE fm.measurement_id = me.measurement_id
            RETURNING fm.*
        ),
        me_edits AS (
            INSERT INTO icid.measurement_edits (measurement_id, field_path, old_value, new_value, reason, editor_uuid)
            SELECT me.measurement_id, v.field_path, v.old_value, v.new_value, '{REASON_REMEASURED}',
                   (SELECT uuid FROM me_editor)
            FROM me
            JOIN me_new n ON n.measurement_id = me.measurement_id
            CROSS JOIN LATERAL (VALUES
                ('outer_dimensions', me.outer_dimensions, n.outer_dimensions),
                ('outer_area', to_jsonb(me.outer_area), to_jsonb(n.outer_area)),
                ('needs_remeasure', to_jsonb(me.needs_remeasure), to_jsonb(n.needs_remeasure))
            ) AS v(field_path, old_value, new_value)
            WHERE v.old_value IS DISTINCT FROM v.new_value
            RETURNING edit_id
        )
        SELECT n.measurement_id FROM me_new n CROSS JOIN (SELECT count(*) FROM me_edits) AS edits;
    """
    outline = None if rectangles is None else Jsonb({"rectangles": rectangles})
    return run_query(sql, (editor_uuid, measurement_id, outline, outline, outline, outer_area))
