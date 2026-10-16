"""
The Construction Report's sketch geometry (R1h): where each flag, area, feature and segment sits on the sheet.

The outlines follow the frontend's lib/flagGeometry.js exactly, so the sheet shows what the sketch workspace shows: a
shape's outline in feet, centred on its bounding box, scaled by its page's px/ft, turned by its own rotation (degrees
clockwise) and moved to (sketch_x, sketch_y) in the page's native pixels. The page's own rotation is then applied,
as the workspace shows the page. The whole is fitted to the sketch pane; a sketch that would print too small to read,
or spreads over more than one page, is for R1h.1's multi-sheet support.
"""

import math
from dataclasses import dataclass
from typing import Any, Iterable, Optional

Point = tuple[float, float]

# Points sampled along an arc's curve, and around a circle (as the frontend samples them)
ARC_STEPS = 32
# The least a foot may print at, in points; below it the sketch needs more than one sheet
MIN_POINTS_PER_FOOT = 4.0
# The margin around the drawing, as a share of its size on each side
PADDING = 0.10
# A marker-only feature's glyph, in feet across, for the bounding box
GLYPH_FEET = 1.5

MULTI_SHEET_MESSAGE = ("This property's sketch is too large to render on a single sheet. "
                       "Multi-sheet support ships in R1h.1.")
NO_SCALE_MESSAGE = "Set the PIR page's scale first"
NOT_DRAWN_MESSAGE = "This property's flags have no place on their page yet"

# Which way North points, as degrees clockwise from straight up the sheet
COMPASS_DEGREES = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}


class SketchError(Exception):
    """A property's sketch can't be drawn on one sheet; the message says why (the route's 422)."""


def shape_outline(shape: Optional[str], dimensions: Optional[dict[str, Any]]) -> list[Point]:
    """
    A shape's outline in feet, in its own frame (x right, y down), before centring, as lib/flagGeometry.js draws it: rectangle {L, W}; trapezoid {a, b, w}, both parallel sides centred; triangle {a, b}, the right angle at the bottom left; polygon {vertices}; arc {r, theta}, the chord at the bottom; circle {r}.
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
    """Something to draw, its points already on the sheet's page in feet, the page turned as the workspace shows it."""

    kind: str  # flag, area, feature, segment
    points: list[Point]
    source: dict[str, Any]


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


def sketch_items(flags: list[dict[str, Any]], drawings: dict[str, Any]) -> list[SketchItem]:
    """
    Lay out a property's sketch: its claimed flags, their non-assessable areas, and the features and segments on their page, in feet, the page turned by its rotation. Raises SketchError when the flags are on more than one page, the page has no scale, or no flag has a place.
    Takes the flags (list_report_flags) and the page's drawings (list_page_drawings).
    Returns the items, flags first.
    """
    pages = {flag["pir_page_id"] for flag in flags}
    if len(pages) > 1:
        raise SketchError(MULTI_SHEET_MESSAGE)
    scale = flags[0]["scale_px_per_ft"] if flags else None
    if not scale:
        raise SketchError(NO_SCALE_MESSAGE)
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
                items.append(SketchItem("area", in_feet(area_outline), area))
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
    return items


def fit_to_pane(items: list[SketchItem], pane: tuple[float, float, float, float]) -> Fit:
    """
    Fit a sketch to the pane, keeping its proportions, with PADDING round it. Raises SketchError when a foot would print smaller than MIN_POINTS_PER_FOOT.
    Takes the items (sketch_items) and the pane (x, bottom y, width, height, in points).
    Returns the Fit, the drawing centred in the pane.
    """
    min_x, min_y, max_x, max_y = bounds_of(point for item in items for point in item.points)
    width_ft, height_ft = max(max_x - min_x, 1.0), max(max_y - min_y, 1.0)
    padded_w, padded_h = width_ft * (1 + 2 * PADDING), height_ft * (1 + 2 * PADDING)
    points_per_foot = min(pane[2] / padded_w, pane[3] / padded_h)
    if points_per_foot < MIN_POINTS_PER_FOOT:
        raise SketchError(MULTI_SHEET_MESSAGE)
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
