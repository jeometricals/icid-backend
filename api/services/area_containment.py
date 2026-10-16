"""
Whether a non-assessable area lies within its flag (R1h.4): an area is part of one flag, so it may not reach past that
flag's outline. Both are placed as the sketch places them (construction_sketch.placed_outline: native page pixels at
the page's scale), turned into feet, and the area is clipped against the flag. The flag is cut into triangles (ear
clipping), so a concave polygon flag is measured as exactly as a rectangle; the area is clipped against each triangle
(Sutherland-Hodgman) and the pieces' areas summed.
"""

from typing import Any, Optional

from api.services.construction_sketch import Point, placed_outline

# How much of an area may lie past its flag before it is refused, in square feet: rounding, not drawing
TOLERANCE_SF = 0.01


def _signed_area(points: list[Point]) -> float:
    """
    A polygon's signed area (shoelace): positive when its points run anticlockwise with y up.
    Takes the points.
    Returns the area.
    """
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])) / 2


def _anticlockwise(points: list[Point]) -> list[Point]:
    """
    A polygon's points, anticlockwise.
    Takes the points.
    Returns them, reversed when they ran clockwise.
    """
    return points if _signed_area(points) >= 0 else points[::-1]


def _cross(o: Point, a: Point, b: Point) -> float:
    """
    The turn from o to a to b: positive anticlockwise.
    Takes the three points.
    Returns the cross product of (a - o) and (b - o).
    """
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _inside_triangle(p: Point, a: Point, b: Point, c: Point) -> bool:
    """
    Whether a point lies in an anticlockwise triangle, its edges included.
    Takes the point and the triangle's corners.
    Returns True when it does.
    """
    return _cross(a, b, p) >= 0 and _cross(b, c, p) >= 0 and _cross(c, a, p) >= 0


def triangles(points: list[Point]) -> list[tuple[Point, Point, Point]]:
    """
    Cut a simple polygon into triangles by ear clipping; collinear corners are dropped.
    Takes the polygon's points (three or more, either way round).
    Returns the triangles, each anticlockwise; [] for a polygon with no area.
    """
    left = _anticlockwise(list(points))
    found: list[tuple[Point, Point, Point]] = []
    while len(left) > 3:
        for index in range(len(left)):
            a, b, c = left[index - 1], left[index], left[(index + 1) % len(left)]
            turn = _cross(a, b, c)
            if abs(turn) < 1e-12:
                del left[index]  # a corner on a straight line
                break
            if turn < 0:
                continue  # a reflex corner is not an ear
            if any(_inside_triangle(p, a, b, c) for p in left if p not in (a, b, c)):
                continue
            found.append((a, b, c))
            del left[index]
            break
        else:
            return found  # nothing more to cut (a degenerate remainder)
    if len(left) == 3 and abs(_signed_area(left)) > 1e-12:
        found.append((left[0], left[1], left[2]))
    return found


def _clip(subject: list[Point], triangle: tuple[Point, Point, Point]) -> list[Point]:
    """
    The part of a polygon inside an anticlockwise triangle (Sutherland-Hodgman, one edge at a time).
    Takes the polygon and the triangle.
    Returns the clipped polygon's points ([] when nothing is inside).
    """
    output = subject
    for a, b in zip(triangle, triangle[1:] + triangle[:1]):
        if not output:
            break
        source, output = output, []
        for p, q in zip(source, source[1:] + source[:1]):
            p_in, q_in = _cross(a, b, p) >= 0, _cross(a, b, q) >= 0
            if p_in != q_in:
                dx, dy = q[0] - p[0], q[1] - p[1]
                ex, ey = b[0] - a[0], b[1] - a[1]
                t = ((a[0] - p[0]) * ey - (a[1] - p[1]) * ex) / (dx * ey - dy * ex)
                output.append((p[0] + t * dx, p[1] + t * dy))
            if q_in:
                output.append(q)
    return output


def overlap_area(subject: list[Point], container: list[Point]) -> float:
    """
    How much of one polygon lies inside another, in its own units squared.
    Takes the polygon and the one it should lie in (simple, either way round, convex or not).
    Returns the area of their overlap.
    """
    subject = _anticlockwise(list(subject))
    return sum(abs(_signed_area(piece)) for triangle in triangles(container)
               if len(piece := _clip(subject, triangle)) >= 3)


def area_outside_flag(area: dict[str, Any], flag: dict[str, Any], scale: Optional[float]) -> Optional[float]:
    """
    How much of a non-assessable area lies outside its flag, as each is placed on their page.
    Takes the area and the flag (shape, dimensions, sketch_x, sketch_y, rotation) and their page's px/ft.
    Returns square feet outside; None when it can't be told (no scale, or either has no place or no shape to draw).
    """
    if not scale:
        return None
    scale = float(scale)
    area_points, flag_points = placed_outline(area, scale), placed_outline(flag, scale)
    if len(area_points) < 3 or len(flag_points) < 3:
        return None
    in_feet = lambda points: [(x / scale, y / scale) for x, y in points]  # noqa: E731
    area_feet = in_feet(area_points)
    return max(abs(_signed_area(area_feet)) - overlap_area(area_feet, in_feet(flag_points)), 0.0)


def oversize_message(display_order: int, flag_id: Any, outside_sf: float) -> str:
    """
    The refusal for an area that reaches past its flag.
    Takes the area's display order, its flag's id and how much lies outside, in square feet.
    Returns the message.
    """
    return (f"Non-assessable area {display_order} extends past flag {flag_id}'s bounds by {outside_sf:.2f} SF; "
            "please redraw or split across adjacent flags.")
