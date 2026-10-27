"""
SQL for a flag's edit history, icid.flag_edits (Slice Y1.5): one row per change of a flag's surveyed_is_assessable or
surveyed_non_assessable_reason, written in the same statement as the change. Rows are only ever added.
"""

# The two fields whose changes are kept
EDITED_FIELDS = ("surveyed_is_assessable", "surveyed_non_assessable_reason")


def flag_edit_rows_cte(changed: str) -> str:
    """
    The INSERT, for a data-modifying CTE, that adds a flag_edits row for each audited field a write changed. The CTE it
    reads has a row per flag written: id, the two fields as they now are, and as they were (old_assessable, old_reason).
    Values are kept as JSON (a cleared reason as JSON null). It takes two parameters, in order: the reason (the words,
    or None) and the editor's uuid (None for a change the system makes).
    Takes the name of the CTE of flags written.
    Returns the INSERT ... RETURNING edit_id.
    """
    return f"""INSERT INTO icid.flag_edits (flag_id, field_path, old_value, new_value, reason, editor_uuid)
            SELECT c.id, v.field_path, v.old_value, v.new_value, %s, %s::uuid
            FROM {changed} c
            CROSS JOIN LATERAL (VALUES
                ('surveyed_is_assessable', to_jsonb(c.old_assessable), to_jsonb(c.surveyed_is_assessable)),
                ('surveyed_non_assessable_reason', COALESCE(to_jsonb(c.old_reason), 'null'::jsonb),
                 COALESCE(to_jsonb(c.surveyed_non_assessable_reason), 'null'::jsonb))
            ) AS v(field_path, old_value, new_value)
            WHERE v.old_value IS DISTINCT FROM v.new_value
            RETURNING edit_id"""
