"""
The Construction Report's slabs (R1h.4): flags that abut and are of the same thickness and assessment are drawn as one
piece of sidewalk, as DOT's sheets draw them: one outer border, and light division lines where the panels meet.

Two flags abut when an edge of one runs along an edge of the other (within SLAB_TOLERANCE_FT, for more than that
length). A group whose flags overlap each other by more than OVERLAP_FALLBACK_SF isn't a clean slab, and its flags are
drawn on their own, as before. Grouping is drawing only: the flags keep their own numbers and figures.
"""

import math
from dataclasses import dataclass

from api.services.area_containment import overlap_area
from api.services.construction_sketch import Point, SketchItem, bounds_of

# How close two edges must lie to count as one, in feet
SLAB_TOLERANCE_FT = 0.1
# How much two flags of a group may overlap before the group is drawn flag by flag, in square feet
OVERLAP_FALLBACK_SF = 0.5


@dataclass
class EdgeRun:
    """A stretch of a slab's edge, in feet: outer (the slab's border) or inner (where two of its flags meet)."""

    start: Point
    end: Point
    inner: bool


def _edges(points: list[Point]) -> list[tuple[Point, Point]]:
    """
    A polygon's edges.
    Takes its points.
    Returns each edge as (from, to), the last closing the polygon.
    """
    return list(zip(points, points[1:] + points[:1]))


def _along(edge: tuple[Point, Point], other: tuple[Point, Point]) -> tuple[float, float] | None:
    """
    Where another edge runs along an edge: both its ends within SLAB_TOLERANCE_FT of the edge's line.
    Takes the edge and the other edge.
    Returns the stretch of the edge it covers, as distances from the edge's start (clamped to the edge), or None when it doesn't lie along it or covers no more than the tolerance.
    """
    (ax, ay), (bx, by) = edge
    length = math.hypot(bx - ax, by - ay)
    if length < 1e-9:
        return None
    ux, uy = (bx - ax) / length, (by - ay) / length
    if any(abs((qx - ax) * uy - (qy - ay) * ux) > SLAB_TOLERANCE_FT for qx, qy in other):
        return None
    t0, t1 = sorted((qx - ax) * ux + (qy - ay) * uy for qx, qy in other)
    t0, t1 = max(t0, 0.0), min(t1, length)
    return (t0, t1) if t1 - t0 > SLAB_TOLERANCE_FT else None


def abut(a: list[Point], b: list[Point]) -> bool:
    """
    Whether two shapes share an edge: some edge of one runs along an edge of the other.
    Takes the two shapes' points, in feet.
    Returns True when they do.
    """
    return any(_along(edge, other) for edge in _edges(a) for other in _edges(b))


def slab_groups(items: list[SketchItem]) -> list[list[SketchItem]]:
    """
    Group a sketch's flags into slabs: flags that abut (directly or through others) and share their surveyed thickness and assessment. A group whose flags overlap one another by more than OVERLAP_FALLBACK_SF is split back into single flags.
    Takes the sketch's items (only flags are grouped).
    Returns the groups, each one or more flag items, in the order the flags were given.
    """
    flags = [item for item in items if item.kind == "flag"]
    key = lambda item: (item.source["surveyed_thickness"], item.source["surveyed_is_assessable"])  # noqa: E731
    parent = list(range(len(flags)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for i, a in enumerate(flags):
        for j in range(i + 1, len(flags)):
            if key(a) == key(flags[j]) and abut(a.points, flags[j].points):
                parent[root(j)] = root(i)
    groups: dict[int, list[SketchItem]] = {}
    for index, item in enumerate(flags):
        groups.setdefault(root(index), []).append(item)
    result = []
    for group in groups.values():
        overlapping = any(overlap_area(a.points, b.points) > OVERLAP_FALLBACK_SF
                          for i, a in enumerate(group) for b in group[i + 1:])
        result += [[item] for item in group] if overlapping else [group]
    return result


def edge_runs(group: list[SketchItem]) -> list[EdgeRun]:
    """
    A slab's edges as they are drawn: each flag's edges cut where another flag of the slab runs along them, the shared stretches inner, the rest outer (the slab's border).
    Takes the group's flag items.
    Returns the runs, in feet.
    """
    runs = []
    for item in group:
        others = [edge for other in group if other is not item for edge in _edges(other.points)]
        for start, end in _edges(item.points):
            length = math.hypot(end[0] - start[0], end[1] - start[1])
            if length < 1e-9:
                continue
            covered = sorted(span for other in others if (span := _along((start, end), other)))
            merged: list[list[float]] = []
            for t0, t1 in covered:
                if merged and t0 <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], t1)
                else:
                    merged.append([t0, t1])
            at = lambda t: (start[0] + (end[0] - start[0]) * t / length,  # noqa: E731
                            start[1] + (end[1] - start[1]) * t / length)
            cursor = 0.0
            for t0, t1 in merged:
                if t0 > cursor:
                    runs.append(EdgeRun(at(cursor), at(t0), False))
                runs.append(EdgeRun(at(t0), at(t1), True))
                cursor = t1
            if cursor < length:
                runs.append(EdgeRun(at(cursor), at(length), False))
    return runs


def group_bounds(group: list[SketchItem]) -> tuple[float, float, float, float]:
    """
    A slab's bounding box, in feet.
    Takes the group's flag items.
    Returns (min x, min y, max x, max y).
    """
    return bounds_of(point for item in group for point in item.points)
