"""
The Construction Report's sketch geometry (R1h): where each flag, area, feature and segment sits on the sheet.

The outlines follow the frontend's lib/flagGeometry.js exactly, so the sheet shows what the sketch workspace shows: a
shape's outline in feet, centred on its bounding box, scaled by its page's px/ft, turned by its own rotation (degrees
clockwise) and moved to (sketch_x, sketch_y) in the page's native pixels. The page's own rotation is then applied,
as the workspace shows the page. Each PIR page with claimed flags is its own sheet (R1h.1a), fitted to its own sketch
pane in its own page's transform; a page whose sketch would print too small to read is refused, to be split with a
match line onto another page. A match line (two endpoints in its page's native pixels) is placed like everything else
on its page and drawn at those coordinates.

Numbering (R1h.3), as DOT's sheets number them: every flag 1..n, top to bottom then left to right as the sheet shows
it, page after page (R1h.1a); and A1..An for what isn't assessed, ordered the same way: a flag surveyed non-assessable (which also keeps its
number) and a non-assessable area on an assessable flag. The sketch and the calculations panel both read them.
"""

import math
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from api.services.corner_quadrant import corner_quadrant_outline

Point = tuple[float, float]

# Points sampled along an arc's curve, and around a circle (as the frontend samples them)
ARC_STEPS = 32
# The least a foot may print at, in points; below it the sketch needs more than one sheet
MIN_POINTS_PER_FOOT = 4.0
# The margin around the drawing, as a share of its size on each side (the streets, PL, TAX MAP and CC have room
# of their own round the pane)
PADDING = 0.04
# A marker-only feature's glyph, in feet across, for the bounding box
GLYPH_FEET = 1.5

MULTI_SHEET_MESSAGE = ("This property's sketch is too large to render on one sheet; split it with a match line onto "
                       "another PIR page.")
NO_SCALE_MESSAGE = "Set the PIR page's scale first"
NOT_DRAWN_MESSAGE = "This property's flags have no place on their page yet"

# Which way North points, as degrees clockwise from straight up the sheet
COMPASS_DEGREES = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}


def too_large_message(page_number: Optional[int]) -> str:
    """
    Say a sheet's sketch is too large to read, naming its PIR page when the report has more than one.
    Takes the page's number (None for a one-sheet report).
    Returns the message.
    """
    if page_number is None:
        return MULTI_SHEET_MESSAGE
    return f"PIR page {page_number}'s sketch is too large for one sheet; split it with a match line onto another page."


def no_scale_message(page_number: Optional[int]) -> str:
    """
    Say a sheet's page has no scale, naming it when the report has more than one.
    Takes the page's number (None for a one-sheet report).
    Returns the message.
    """
    return NO_SCALE_MESSAGE if page_number is None else f"Set the scale of PIR page {page_number} first"


class SketchError(Exception):
    """A property's sketch can't be drawn on one sheet; the message says why (the route's 422)."""


def shape_outline(shape: Optional[str], dimensions: Optional[dict[str, Any]]) -> list[Point]:
    """
    A shape's outline in feet, in its own frame (x right, y down), before centring, as lib/flagGeometry.js draws it: rectangle {L, W}; trapezoid {a, b, w}, both parallel sides centred; triangle {a, b}, the right angle at the bottom left; polygon {vertices}; arc {r, theta}, the chord at the bottom; circle {r}; corner quadrant {H, C, R1, R2, A, B} (corner_quadrant_outline).
    Takes the shape and its dimensions.
    Returns the points; [] for an unknown shape or no dimensions.
    """
    if not dimensions:
        return []
    if shape == "rectangle":
        length, width = dimensions["L"], dimensions["W"]
        return [(0, 0), (length, 0), (length, width), (0, width)]
    if shape == "trapezoid":
        a, b, w = dimensions["a"], dimensions["b"], dimensions["w"]
        half = max(a, b) / 2
        return [(half - a / 2, 0), (half + a / 2, 0), (half + b / 2, w), (half - b / 2, w)]
    if shape == "triangle":
        a, b = dimensions["a"], dimensions["b"]
        return [(0, 0), (a, b), (0, b)]
    if shape == "polygon":
        return [(vertex["x"], vertex["y"]) for vertex in dimensions.get("vertices") or []]
    if shape == "arc":
        r, theta = dimensions["r"], dimensions["theta"]
        angles = (-theta / 2 + theta * step / ARC_STEPS for step in range(ARC_STEPS + 1))
        return [(r * math.sin(angle), -r * math.cos(angle)) for angle in angles]
    if shape == "circle":
        r = dimensions["r"]
        angles = (2 * math.pi * step / ARC_STEPS for step in range(ARC_STEPS))
        return [(r * math.cos(angle), r * math.sin(angle)) for angle in angles]
    if shape == "corner_quadrant":
        return corner_quadrant_outline(dimensions)
    return []


def bounds_of(points: Iterable[Point]) -> tuple[float, float, float, float]:
    """
    The bounding box of some points.
    Takes the points (at least one).
    Returns (min x, min y, max x, max y).
    """
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def turn(point: Point, degrees: float, about: Point = (0.0, 0.0)) -> Point:
    """
    Turn a point clockwise (y down) about another.
    Takes the point, the angle in degrees and the point to turn about.
    Returns the turned point.
    """
    angle = math.radians(degrees)
    dx, dy = point[0] - about[0], point[1] - about[1]
    return (about[0] + dx * math.cos(angle) - dy * math.sin(angle),
            about[1] + dx * math.sin(angle) + dy * math.cos(angle))


def placed_outline(item: dict[str, Any], scale: float) -> list[Point]:
    """
    An item's outline on its page, in native page pixels, as flagOutline in lib/flagGeometry.js: centred on its bounding box, scaled, turned by its rotation and moved to (sketch_x, sketch_y).
    Takes the item (shape, dimensions, sketch_x, sketch_y, rotation) and its page's px/ft.
    Returns the points; [] without a place or a drawable shape.
    """
    if item.get("sketch_x") is None or item.get("sketch_y") is None:
        return []
    outline = shape_outline(item.get("shape"), item.get("dimensions"))
    if not outline:
        return []
    min_x, min_y, max_x, max_y = bounds_of(outline)
    centre_x, centre_y = (min_x + max_x) / 2, (min_y + max_y) / 2
    x, y = float(item["sketch_x"]), float(item["sketch_y"])
    return [turn((x + (px - centre_x) * scale, y + (py - centre_y) * scale), item.get("rotation") or 0, (x, y))
            for px, py in outline]


@dataclass
class SketchItem:
    """Something to draw, its points already on the sheet's page in feet, the page turned as the workspace shows it; an area knows its flag."""

    kind: str  # flag, area, feature, segment, match_line, strip
    points: list[Point]
    source: dict[str, Any]
    parent: Optional[dict[str, Any]] = None


def material(item: SketchItem) -> Optional[str]:
    """
    What a flag or an area is made of, as the sketch fills and labels it.
    Takes the item.
    Returns '4in' (4", stippled), '7in' (7" or 7" assessed as 4", diagonal lines), 'driveway' (an area, or a flag surveyed non-assessable, for a driveway: DW's stripes) or 'non_assessable' (any other such flag or area); None for a feature or a segment.
    """
    if item.kind == "area":
        return "driveway" if item.source.get("reason") == "driveway" else "non_assessable"
    if item.kind != "flag":
        return None
    if not item.source["surveyed_is_assessable"]:
        return "driveway" if item.source.get("surveyed_non_assessable_reason") == "driveway" else "non_assessable"
    return "4in" if item.source["surveyed_thickness"] == "4in" else "7in"


def reading_order(boxes: dict[Any, tuple[float, float, float, float]]) -> list[Any]:
    """
    Put shapes in reading order, as a viewer scans a sheet: in rows, top to bottom, then left to right along each row. A row starts at its topmost shape not yet placed and takes every shape whose middle lies within that shape's height, so hand-drawn rows that aren't quite level read as rows.
    Takes each shape's bounding box (min x, min y, max x, max y; feet, y down), keyed.
    Returns the keys in order.
    """
    middles = {key: ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) for key, box in boxes.items()}
    left = sorted(boxes, key=lambda key: (middles[key][1], middles[key][0]))
    ordered: list[Any] = []
    while left:
        first = boxes[left[0]]
        row = [key for key in left if first[1] - 1e-9 <= middles[key][1] <= first[3] + 1e-9]
        if left[0] not in row:
            row.insert(0, left[0])
        ordered += sorted(row, key=lambda key: (middles[key][0], middles[key][1]))
        left = [key for key in left if key not in row]
    return ordered


def number_items(flags: list[dict[str, Any]], items: list[SketchItem]) -> dict[str, str]:
    """
    Number a report's flags and what isn't assessed, as the sketch and the calculations panel show them: flags 1..n and non-assessable flags and areas A1..An, each in reading order (reading_order), page after page (each page's own coordinates); flags and areas not drawn come after, in drawing order. An area on a flag surveyed non-assessable gets no A-number: the whole flag is already one. On a sheet where every flag has the signed PIR's own number (pir_flag_no, Slice Y3a), the flags show those; A-numbers are unchanged.
    Takes the flags (list_report_flags) and the sketch's items (sketch_items, every page's).
    Returns the labels keyed 'flag:<id>' (its number), 'a:flag:<id>' and 'a:area:<id>' (A-numbers).
    """
    boxes = {(item.kind, str(item.source["id"])): bounds_of(item.points)
             for item in items if item.kind in ("flag", "area")}
    # The page a flag or an area is on: its flag's
    page_of: dict[tuple[str, str], int] = {}
    for flag in flags:
        page = flag.get("page_number") or 0
        page_of[("flag", str(flag["id"]))] = page
        for area in flag["areas"]:
            page_of[("area", str(area["id"]))] = page

    def in_order(keys: list[tuple[str, str]]) -> list[tuple[str, str]]:
        drawn = []
        for page in sorted({page_of.get(key, 0) for key in keys}):
            drawn += reading_order({key: boxes[key] for key in keys if key in boxes and page_of.get(key, 0) == page})
        return drawn + [key for key in keys if key not in boxes]

    labels = {f"flag:{key[1]}": str(number)
              for number, key in enumerate(in_order([("flag", str(flag["id"])) for flag in flags]), start=1)}
    not_assessed = []
    for flag in flags:
        if not flag["surveyed_is_assessable"]:
            not_assessed.append(("flag", str(flag["id"])))
        else:
            not_assessed += [("area", str(area["id"])) for area in flag["areas"]]
    for number, (kind, key) in enumerate(in_order(not_assessed), start=1):
        labels[f"a:{kind}:{key}"] = f"A{number}"
    # A sheet whose every flag carries the signed PIR's own number (Slice Y3a) shows those instead; the other sheets
    # keep the numbers above, all or nothing per sheet
    sheets: dict[int, list[dict[str, Any]]] = {}
    for flag in flags:
        sheets.setdefault(flag.get("page_number") or 0, []).append(flag)
    for sheet in sheets.values():
        if all(flag.get("pir_flag_no") for flag in sheet):
            labels.update({f"flag:{flag['id']}": str(flag["pir_flag_no"]) for flag in sheet})
    return labels


@dataclass
class Fit:
    """How the sketch's feet map onto the pane: points per foot, and the feet at the pane's top-left corner."""

    points_per_foot: float
    origin: Point
    pane: tuple[float, float, float, float]  # x, y (bottom), width, height, in points

    def place(self, point: Point) -> Point:
        """
        Turn a point in feet (y down) into sheet points (y up).
        Takes the point.
        Returns (x, y) on the sheet.
        """
        x, y, _, height = self.pane
        return (x + (point[0] - self.origin[0]) * self.points_per_foot,
                y + height - (point[1] - self.origin[1]) * self.points_per_foot)


def sketch_items(flags: list[dict[str, Any]], drawings: dict[str, Any], match_lines: Optional[list[dict[str, Any]]] = None,
                 page_number: Optional[int] = None) -> list[SketchItem]:
    """
    Lay out one sheet's sketch: the claimed flags on one page, their non-assessable areas, the features and segments on that page, its match lines, and its sidewalk strip (Slice Y2), in feet, the page turned by its rotation. Raises SketchError when the page has no scale or no flag has a place.
    Takes the page's flags (list_report_flags), its drawings (list_page_drawings; strips optional), its match lines (each with label, x1, y1, x2, y2 and a caption) and the page's number when the report has more than one sheet (named in a message), else None.
    Returns the items, flags first, then match lines and the strip, its source carrying the page's scale_px_per_ft.
    """
    scale = flags[0]["scale_px_per_ft"] if flags else None
    if not scale:
        raise SketchError(no_scale_message(page_number))
    scale = float(scale)
    rotation = flags[0]["rotation_deg"] or 0

    def in_feet(points: list[Point]) -> list[Point]:
        return [turn((px / scale, py / scale), rotation) for px, py in points]

    items = []
    for flag in flags:
        outline = placed_outline(flag, scale)
        if outline:
            items.append(SketchItem("flag", in_feet(outline), flag))
        for area in flag["areas"]:
            area_outline = placed_outline(area, scale)
            if area_outline:
                items.append(SketchItem("area", in_feet(area_outline), area, flag))
    if not items:
        raise SketchError(NOT_DRAWN_MESSAGE)
    for feature in drawings["features"]:
        outline = placed_outline(feature, scale) if feature.get("shape") else []
        if not outline:
            centre = (float(feature["sketch_x"]), float(feature["sketch_y"]))
            half = GLYPH_FEET * scale / 2
            outline = [(centre[0] - half, centre[1] - half), (centre[0] + half, centre[1] + half)]
        items.append(SketchItem("feature", in_feet(outline), feature))
    for segment in drawings["segments"]:
        points = [(float(point["x"]), float(point["y"])) for point in segment["points"]]
        if len(points) >= 2:
            items.append(SketchItem("segment", in_feet(points), segment))
    for line in match_lines or []:
        points = [(float(line["x1"]), float(line["y1"])), (float(line["x2"]), float(line["y2"]))]
        items.append(SketchItem("match_line", in_feet(points), line))
    for strip in drawings.get("strips", []):
        points = [(float(point["x"]), float(point["y"])) for point in strip["points"]]
        if len(points) >= 3:
            items.append(SketchItem("strip", in_feet(points), {**strip, "scale_px_per_ft": scale}))
    return items


def fit_to_pane(items: list[SketchItem], pane: tuple[float, float, float, float], min_width_ft: float = 0.0,
                page_number: Optional[int] = None) -> Fit:
    """
    Fit a sketch to the pane, keeping its proportions, with PADDING round it. Raises SketchError when a foot would print smaller than MIN_POINTS_PER_FOOT.
    Takes the items (sketch_items, its match lines among them; a sidewalk strip is drawn but never fitted, so it can't shrink the sheet), the pane (x, bottom y, width, height, in points), the least width the drawing must make room for, in feet (a dimension drawn at scale beside it: the tax map's, the curb-to-curb), and the page's number when the report has more than one sheet (named in the message).
    Returns the Fit, the drawing centred in the pane.
    """
    min_x, min_y, max_x, max_y = bounds_of(point for item in items if item.kind != "strip" for point in item.points)
    width_ft, height_ft = max(max_x - min_x, 1.0, min_width_ft), max(max_y - min_y, 1.0)
    min_x = (min_x + max_x) / 2 - width_ft / 2
    padded_w, padded_h = width_ft * (1 + 2 * PADDING), height_ft * (1 + 2 * PADDING)
    points_per_foot = min(pane[2] / padded_w, pane[3] / padded_h)
    if points_per_foot < MIN_POINTS_PER_FOOT:
        raise SketchError(too_large_message(page_number))
    # Centre the drawing: the feet at the pane's top-left corner
    origin = (min_x - (pane[2] / points_per_foot - width_ft) / 2, min_y - (pane[3] / points_per_foot - height_ft) / 2)
    return Fit(points_per_foot, origin, pane)


def north_degrees(bearing: Optional[str]) -> Optional[float]:
    """
    Which way the north arrow points on the sheet: the PIR's bearing, read on the page as shown (upright), as the sketch is drawn, so not turned again by the page's rotation.
    Takes the north bearing (a compass point, or None).
    Returns degrees clockwise from straight up, or None without a bearing (no arrow).
    """
    if bearing not in COMPASS_DEGREES:
        return None
    return float(COMPASS_DEGREES[bearing])
