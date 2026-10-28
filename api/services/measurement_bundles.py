"""
Which bundle a reclassified flag joins (Slice Y3b-1): the remesh's Python half. The statement that changes the flag
re-checks the pick (api/queries/flag_measurements.py, remesh_ctes) and falls back when it no longer holds.

A flag joins a bundle only when exactly one is open to it: a live, unfrozen bundle on its page, of its new
classification, with a member sharing an edge with it (within the Construction Report's slab tolerance). None, or more
than one, and it goes solo. In Y3b-1 only bundles are candidates, never a neighbour's solo measurement, so the remesh
forms no bundle of its own: bundles come from the bundle route (Y3b-2).
"""

from typing import Any, Optional
from uuid import UUID

from api.services.construction_sketch import Point, placed_outline
from api.services.construction_slabs import abut


def classification(flag: dict[str, Any]) -> tuple[Any, Any, Optional[str]]:
    """
    A flag's classification, as a bundle's members share it.
    Takes the flag (surveyed_thickness, surveyed_is_assessable, pay_item_id).
    Returns (thickness, assessable, pay item id as text or None).
    """
    pay_item = flag.get("pay_item_id")
    return flag.get("surveyed_thickness"), flag.get("surveyed_is_assessable"), None if pay_item is None else str(pay_item)


def _outline_ft(flag: dict[str, Any], scale: float) -> list[Point]:
    """
    A flag's outline on its page, in feet.
    Takes the flag (shape, dimensions, sketch_x, sketch_y, rotation) and the page's px/ft.
    Returns the points; [] without a place or a drawable shape.
    """
    return [(x / scale, y / scale) for x, y in placed_outline(flag, scale)]


def unconnected(flags: list[dict[str, Any]], scale: float) -> list[dict[str, Any]]:
    """
    The flags a bundle's chain doesn't reach (Slice Y3b-2): starting from the first, each flag sharing an edge with one
    already reached (within the slab tolerance) is reached in turn.
    Takes the flags (shape, dimensions, sketch_x, sketch_y, rotation; each placed) and their page's px/ft.
    Returns the flags not reached, in the order given; [] when they form one chain.
    """
    outlines = [_outline_ft(flag, scale) for flag in flags]
    reached, frontier = {0}, [0]
    while frontier:
        current = frontier.pop()
        for index, outline in enumerate(outlines):
            if index not in reached and abut(outlines[current], outline):
                reached.add(index)
                frontier.append(index)
    return [flag for index, flag in enumerate(flags) if index not in reached]


def join_target(flag: dict[str, Any], neighbours: list[dict[str, Any]],
                moving: frozenset[UUID] = frozenset()) -> Optional[UUID]:
    """
    The bundle a flag joins after a change of classification, if exactly one is open to it.
    Takes the flag as it will be (id, its shape, dimensions and place, and its new classification), its page's live
    flags (list_remesh_neighbours, the flag itself among them) and the ids of other flags the same statement moves
    (their bundles aren't settled, so they don't count).
    Returns the bundle's measurement id, or None: no place, no page scale, or no single bundle.
    """
    me = next((row for row in neighbours if row["id"] == flag["id"]), None)
    if me is None or not me.get("scale_px_per_ft"):
        return None
    scale = float(me["scale_px_per_ft"])
    outline = _outline_ft(flag, scale)
    if not outline:
        return None
    wanted = classification(flag)
    found = set()
    for row in neighbours:
        if (row["id"] == flag["id"] or row["id"] in moving or not row["bundled"] or row["frozen"]
                or row["measurement_id"] == me["measurement_id"] or classification(row) != wanted):
            continue
        other = _outline_ft(row, scale)
        if other and abut(outline, other):
            found.add(row["measurement_id"])
    return found.pop() if len(found) == 1 else None
