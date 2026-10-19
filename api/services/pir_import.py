"""
A Smart PIR import: a sketch tool's file of what it drew on a PIR's pages, checked item by item and added to what the
pages have. A bad item is dropped, with its reason, and the rest go in; the writing is one statement
(api/queries/pir_import.py), so what is kept lands together or not at all.
"""

from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError

from api.queries.pir_import import import_pir_drawings
from api.schemas.non_assessable_area import NonAssessableAreaCreate
from api.schemas.pir_annotation import PirAnnotationIn
from api.schemas.pir_import import (
    DroppedItem,
    FeatureImportItem,
    FlagImportItem,
    ImportCounts,
    PirImportResult,
    SmartPirImport,
    SmartPirPageImport,
)
from api.schemas.match_line import MatchLineCreate
from api.schemas.segment import SegmentCreate
from api.services.area_containment import TOLERANCE_SF, area_outside_flag
from api.services.flag_area import compute_area
from api.services.pay_item_units import FLAG_UNITS

PAY_ITEM_ELSEWHERE = "pay item is not one of the project's"
FLAG_UNIT_REFUSED = f"a flag can't be measured in its pay item's unit (only {', '.join(FLAG_UNITS)})"
KINDS = {"flag": "flags", "non_assessable_area": "non_assessable_areas", "feature": "features",
         "segment": "segments", "annotations": "annotations", "match_line": "match_lines"}


@dataclass
class PreparedImport:
    """What an import will write, as rows keyed by column, and what it dropped."""

    pages: list[dict[str, Any]] = field(default_factory=list)
    flags: list[dict[str, Any]] = field(default_factory=list)
    areas: list[dict[str, Any]] = field(default_factory=list)
    features: list[dict[str, Any]] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)
    annotations: list[dict[str, Any]] = field(default_factory=list)
    match_lines: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[DroppedItem] = field(default_factory=list)

    def counts(self) -> ImportCounts:
        """
        Count what is to be written.
        Takes nothing.
        Returns the ImportCounts.
        """
        return ImportCounts(flags=len(self.flags), non_assessable_areas=len(self.areas), features=len(self.features),
                            segments=len(self.segments), annotations=len(self.annotations),
                            match_lines=len(self.match_lines))


def _reason(exc: ValidationError) -> str:
    """
    Say why an item failed its check: its first error.
    Takes the ValidationError.
    Returns "field: message" (just the message for a rule on the whole item).
    """
    error = exc.errors(include_url=False)[0]
    where = ".".join(str(part) for part in error["loc"])
    return f"{where}: {error['msg']}" if where else error["msg"]


def _checked(model: type[BaseModel], raw: Any) -> tuple[Optional[BaseModel], Optional[str]]:
    """
    Check one item.
    Takes the model it is checked as and the item as the file has it.
    Returns (the parsed item, None), or (None, why it failed).
    """
    if not isinstance(raw, dict):
        return None, "an item must be an object"
    try:
        return model.model_validate(raw), None
    except ValidationError as exc:
        return None, _reason(exc)


class _Importer:
    """Turns a file into a PreparedImport, item by item."""

    def __init__(self, page_ids: dict[int, UUID], pay_item_ids: set[UUID],
                 flag_pay_item_ids: Optional[set[UUID]] = None,
                 page_scales: Optional[dict[int, Any]] = None,
                 live_labels: Optional[dict[str, set[int]]] = None) -> None:
        """
        Start with nothing prepared.
        Takes the PIR's live pages' ids by page number, the ids of its project's pay items, those of them in a unit a flag can be measured in (all of them when None), the live pages' scales by page number (for checking each area lies within its flag), and the pages each of its live match lines' labels is on.
        Returns nothing.
        """
        self.page_ids, self.pay_item_ids = page_ids, pay_item_ids
        self.live_labels = live_labels or {}
        # The match lines that passed their own checks, before the pages-per-label check: (page number, index, row)
        self.line_candidates: list[tuple[int, int, dict[str, Any]]] = []
        self.page_scales = page_scales or {}
        self.flag_pay_item_ids = pay_item_ids if flag_pay_item_ids is None else flag_pay_item_ids
        self.out = PreparedImport()

    def drop(self, kind: str, page_number: Optional[int], index: Optional[int], reason: str,
             flag_index: Optional[int] = None) -> None:
        """
        Leave an item out, saying why.
        Takes its kind, its page entry's number, its place in its list, the reason and, for an area, its flag's place.
        Returns nothing.
        """
        self.out.dropped.append(
            DroppedItem(kind=kind, page_number=page_number, index=index, flag_index=flag_index, reason=reason)
        )

    def pay_item_ok(self, pay_item_id: Optional[UUID]) -> bool:
        """
        Whether a pay item, if any, is one of the project's.
        Takes the pay item's id (None for none).
        Returns True when there is none or it is the project's.
        """
        return pay_item_id is None or pay_item_id in self.pay_item_ids

    def drop_page(self, entry: SmartPirPageImport, reason: str) -> None:
        """
        Leave out everything a page entry brings.
        Takes the page entry and the reason.
        Returns nothing.
        """
        for kind, items in (("flag", entry.flags), ("feature", entry.features), ("segment", entry.segments),
                            ("match_line", entry.match_lines)):
            for index, _ in enumerate(items):
                self.drop(kind, entry.page_number, index, reason)
        if entry.annotations is not None:
            self.drop("annotations", entry.page_number, None, reason)

    def annotations(self, raw: Any, page_id: UUID, page_number: Optional[int]) -> None:
        """
        Take a page's title block.
        Takes the block as the file has it, the page's id, and the page entry's number (None for the file's own).
        Returns nothing.
        """
        block, reason = _checked(PirAnnotationIn, raw)
        if block is None:
            self.drop("annotations", page_number, None, reason)
            return
        self.out.annotations.append({"pir_page_id": page_id, **block.model_dump()})

    def flags(self, entry: SmartPirPageImport, page_id: UUID) -> dict[int, UUID]:
        """
        Take a page entry's flags and their non-assessable areas.
        Takes the page entry and its page's id.
        Returns the new flags' ids by their place in the entry's list (for features naming one).
        """
        kept: dict[int, UUID] = {}
        for index, raw in enumerate(entry.flags):
            flag, reason = _checked(FlagImportItem, raw)
            if flag is None:
                self.drop("flag", entry.page_number, index, reason)
                continue
            if not self.pay_item_ok(flag.pay_item_id):
                self.drop("flag", entry.page_number, index, PAY_ITEM_ELSEWHERE)
                continue
            if flag.pay_item_id is not None and flag.pay_item_id not in self.flag_pay_item_ids:
                self.drop("flag", entry.page_number, index, FLAG_UNIT_REFUSED)
                continue
            flag_id = uuid4()
            kept[index] = flag_id
            values = flag.model_dump(exclude={"non_assessable_areas"})
            self.out.flags.append({"id": flag_id, "pir_page_id": page_id, **values,
                                   "computed_quantity": compute_area(flag.shape, flag.dimensions)})
            scale = entry.scale_px_per_ft or self.page_scales.get(entry.page_number)
            self.areas(flag.non_assessable_areas, flag_id, entry.page_number, index, values, scale)
        return kept

    def areas(self, raws: list[Any], flag_id: UUID, page_number: int, flag_index: int,
              flag: Optional[dict[str, Any]] = None, scale: Optional[Any] = None) -> None:
        """
        Take a flag's non-assessable areas, each display order once, each within its flag (when that can be told).
        Takes the areas as the file has them, the new flag's id, its page entry's number, its place in the list, its values (shape and place) and its page's scale (the entry's, else the live page's).
        Returns nothing.
        """
        orders: set[int] = set()
        for index, raw in enumerate(raws):
            area, reason = _checked(NonAssessableAreaCreate, raw)
            if area is None:
                self.drop("non_assessable_area", page_number, index, reason, flag_index)
                continue
            if area.display_order in orders:
                self.drop("non_assessable_area", page_number, index,
                          f"display_order {area.display_order} is used by another area of this flag", flag_index)
                continue
            outside = None if flag is None else area_outside_flag(area.model_dump(), flag, scale)
            if outside is not None and outside > TOLERANCE_SF:
                self.drop("non_assessable_area", page_number, index,
                          f"extends past its flag's bounds by {outside:.2f} SF", flag_index)
                continue
            orders.add(area.display_order)
            self.out.areas.append({"flag_id": flag_id, **area.model_dump(),
                                   "computed_quantity": compute_area(area.shape, area.dimensions)})

    def features(self, entry: SmartPirPageImport, page_id: UUID, flag_ids: dict[int, UUID]) -> None:
        """
        Take a page entry's features, a flag_index resolved to the flag this import adds.
        Takes the page entry, its page's id and the new flags' ids by their place.
        Returns nothing.
        """
        for index, raw in enumerate(entry.features):
            if isinstance(raw, dict) and raw.get("flag_id") is not None:
                self.drop("feature", entry.page_number, index, "name the flag by flag_index, not flag_id")
                continue
            if isinstance(raw, dict) and raw.get("flag_index") is not None:
                flag_index = raw["flag_index"]
                if not isinstance(flag_index, int) or flag_index not in flag_ids:
                    self.drop("feature", entry.page_number, index,
                              f"flag_index {flag_index} is not a flag this page's import kept")
                    continue
                raw = {**raw, "flag_id": flag_ids[flag_index]}
            feature, reason = _checked(FeatureImportItem, raw)
            if feature is None:
                self.drop("feature", entry.page_number, index, reason)
                continue
            if not self.pay_item_ok(feature.pay_item_id):
                self.drop("feature", entry.page_number, index, PAY_ITEM_ELSEWHERE)
                continue
            area = None if feature.shape is None else compute_area(feature.shape, feature.dimensions)
            self.out.features.append({"pir_page_id": page_id, **feature.model_dump(exclude={"flag_index"}),
                                      "computed_quantity": area})

    def segments(self, entry: SmartPirPageImport, page_id: UUID) -> None:
        """
        Take a page entry's segments, each with the page's new scale (if the entry sets one) for its length.
        Takes the page entry and its page's id.
        Returns nothing.
        """
        for index, raw in enumerate(entry.segments):
            segment, reason = _checked(SegmentCreate, raw)
            if segment is None:
                self.drop("segment", entry.page_number, index, reason)
                continue
            if not self.pay_item_ok(segment.pay_item_id):
                self.drop("segment", entry.page_number, index, PAY_ITEM_ELSEWHERE)
                continue
            self.out.segments.append(
                {"pir_page_id": page_id, "scale_px_per_ft": entry.scale_px_per_ft, **segment.model_dump()}
            )

    def match_lines(self, entry: SmartPirPageImport, page_id: UUID) -> None:
        """
        Take a page entry's match lines as candidates: each checked as MatchLineCreate, a label repeated in the entry dropped (the second), and a label the page already has live dropped (the import only adds).
        Takes the page entry and its page's id.
        Returns nothing; the pages-per-label check runs once every page is read (match_lines_on_pages).
        """
        labels: set[str] = set()
        for index, raw in enumerate(entry.match_lines):
            line, reason = _checked(MatchLineCreate, raw)
            if line is None:
                self.drop("match_line", entry.page_number, index, reason)
                continue
            if line.label in labels:
                self.drop("match_line", entry.page_number, index, f'label "{line.label}" is listed twice on this page')
                continue
            if entry.page_number in self.live_labels.get(line.label, set()):
                self.drop("match_line", entry.page_number, index, f'label "{line.label}" is already on this page')
                continue
            labels.add(line.label)
            self.line_candidates.append((entry.page_number, index, {"pir_page_id": page_id, **line.columns()}))

    def match_lines_on_pages(self) -> None:
        """
        Keep the candidate match lines whose label ends up on at most two pages of the PIR (its live lines' pages and the file's together); every occurrence in the file of a label that would be on more is dropped, so none is picked by its place.
        Takes nothing.
        Returns nothing.
        """
        pages: dict[str, set[int]] = {label: set(numbers) for label, numbers in self.live_labels.items()}
        for page_number, _, row in self.line_candidates:
            pages.setdefault(row["label"], set()).add(page_number)
        for page_number, index, row in self.line_candidates:
            count = len(pages[row["label"]])
            if count > 2:
                self.drop("match_line", page_number, index, f'label "{row["label"]}" would be on {count} pages')
            else:
                self.out.match_lines.append(row)

    def page(self, entry: SmartPirPageImport, seen: set[int]) -> None:
        """
        Take one page entry: its scale and rotation, then its flags (and their areas), features, segments and title block.
        Takes the page entry and the page numbers already taken.
        Returns nothing.
        """
        if entry.page_number in seen:
            self.drop_page(entry, f"page {entry.page_number} is listed twice")
            return
        seen.add(entry.page_number)
        page_id = self.page_ids.get(entry.page_number)
        if page_id is None:
            self.drop_page(entry, f"the PIR has no live page {entry.page_number}")
            return
        if entry.scale_px_per_ft is not None or entry.rotation_deg is not None:
            self.out.pages.append({"pir_page_id": page_id, "scale_px_per_ft": entry.scale_px_per_ft,
                                   "rotation_deg": entry.rotation_deg})
        flag_ids = self.flags(entry, page_id)
        self.features(entry, page_id, flag_ids)
        self.segments(entry, page_id)
        self.match_lines(entry, page_id)
        if entry.annotations is not None:
            self.annotations(entry.annotations, page_id, entry.page_number)


def prepare_import(body: SmartPirImport, page_ids: dict[int, UUID], pay_item_ids: set[UUID],
                   flag_pay_item_ids: Optional[set[UUID]] = None,
                   page_scales: Optional[dict[int, Any]] = None,
                   live_labels: Optional[dict[str, set[int]]] = None) -> PreparedImport:
    """
    Check a Smart PIR import item by item and turn what passes into the rows the import statement writes. The file's own annotations go to page 1, unless page 1's entry brings its own.
    Takes the parsed file, the PIR's live pages' ids by page number, the ids of its project's pay items, those of them a flag can be measured in (all of them when None), the live pages' scales by page number and the pages each of the PIR's live match line labels is on.
    Returns the PreparedImport: the rows, and every item dropped with why.
    """
    importer = _Importer(page_ids, pay_item_ids, flag_pay_item_ids, page_scales, live_labels)
    seen: set[int] = set()
    for entry in body.pages:
        importer.page(entry, seen)
    importer.match_lines_on_pages()

    if body.annotations is not None:
        page_one = next((entry for entry in body.pages if entry.page_number == 1), None)
        if page_one is not None and page_one.annotations is not None:
            importer.drop("annotations", None, None, "page 1 brings its own annotations")
        elif 1 not in page_ids:
            importer.drop("annotations", None, None, "the PIR has no live page 1")
        else:
            importer.annotations(body.annotations, page_ids[1], None)

    return importer.out


def run_import(
    pir_id: UUID, created_by: UUID, body: SmartPirImport, page_ids: dict[int, UUID], pay_item_ids: set[UUID],
    flag_pay_item_ids: Optional[set[UUID]] = None, page_scales: Optional[dict[int, Any]] = None,
    live_labels: Optional[dict[str, set[int]]] = None,
) -> Optional[PirImportResult]:
    """
    Import a Smart PIR file: check it, then write what passed in one statement. An item the statement couldn't write (its page replaced or its pay item deleted meanwhile) is counted as dropped.
    Takes the PIR's id, the importing user's uuid, the parsed file, the PIR's live pages' ids by page number, the ids of its project's pay items, those of them a flag can be measured in (all of them when None), the live pages' scales by page number and the pages each of the PIR's live match line labels is on.
    Returns the PirImportResult, or None if the statement failed.
    """
    prepared = prepare_import(body, page_ids, pay_item_ids, flag_pay_item_ids, page_scales, live_labels)
    wanted = prepared.counts()

    if not (prepared.pages or prepared.flags or prepared.features or prepared.segments or prepared.annotations
            or prepared.match_lines):
        written: Optional[dict[str, int]] = {"pages_updated": 0, **ImportCounts().model_dump()}
    else:
        written = import_pir_drawings(pir_id, created_by, prepared.pages, prepared.flags, prepared.areas,
                                      prepared.features, prepared.segments, prepared.annotations,
                                      prepared.match_lines)
    if written is None:
        return None

    inserted = ImportCounts(**{key: written[key] for key in ImportCounts.model_fields})
    failed = {kind: 0 for kind in ImportCounts.model_fields}
    for item in prepared.dropped:
        failed[KINDS[item.kind]] += 1
    dropped = ImportCounts(**{
        key: failed[key] + getattr(wanted, key) - getattr(inserted, key) for key in ImportCounts.model_fields
    })
    return PirImportResult(inserted=inserted, dropped=dropped, dropped_items=prepared.dropped,
                           pages_updated=written["pages_updated"])
