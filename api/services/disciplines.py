"""
Maps a report type to the disciplines it covers, and the reverse.

A quantity row keeps the type of the report its pay item is on; its disciplines are read off that type here. The
quantities reader resolves a discipline filter to report types the other way round, and the archive filter lists the
disciplines an IDR has. Concrete Truck & Mix Info and Concrete Cylinder Data carry no pay items, so they never tag a
quantity; they are here for the lists. PIR_FLAGS is no report's type: it tags the quantity rows an IDR's flags give
(migration 037), which are sidewalk work, so it maps a quantity row's type, never a report's.
"""

from api.schemas.idr_report import ReportType

REPORT_TYPE_TO_DISCIPLINES: dict[str, tuple[str, ...]] = {
    ReportType.GEN.value: ("General",),
    ReportType.SWCB.value: ("Sidewalk", "Curb"),
    ReportType.CONC_MIX.value: ("Concrete (delivery)",),
    ReportType.AC.value: ("AC Pavement",),
    ReportType.CONC_CYL.value: ("Concrete (testing)",),
}
# A quantity row's types: its report's, or PIR_FLAGS for a row an IDR's flags gave
QUANTITY_TYPE_TO_DISCIPLINES: dict[str, tuple[str, ...]] = {**REPORT_TYPE_TO_DISCIPLINES, "PIR_FLAGS": ("Sidewalk",)}


def disciplines_for_report_type(report_type: str) -> tuple[str, ...]:
    """
    List the disciplines a report type covers (or a quantity row's PIR_FLAGS).
    Takes the stored report_type.
    Returns its disciplines, in order; empty for a type that has none here.
    """
    return QUANTITY_TYPE_TO_DISCIPLINES.get(report_type, ())


def disciplines_for_report_types(report_types: list[str]) -> list[str]:
    """
    List the disciplines a set of report types covers between them.
    Takes the stored report types, in any order and repeated or not.
    Returns the disciplines, each once, in alphabetical order; empty for no report types.
    """
    return sorted({discipline for report_type in report_types
                   for discipline in disciplines_for_report_type(report_type)})


def report_types_for_discipline(discipline: str, quantities: bool = False) -> tuple[str, ...]:
    """
    List the report types that cover a discipline: a report's, or, for quantity rows, PIR_FLAGS too.
    Takes the discipline's name, as REPORT_TYPE_TO_DISCIPLINES spells it, and whether the types are a quantity row's.
    Returns the report types, in the mapping's order; empty for a discipline nothing covers.
    """
    mapping = QUANTITY_TYPE_TO_DISCIPLINES if quantities else REPORT_TYPE_TO_DISCIPLINES
    return tuple(report_type for report_type, disciplines in mapping.items() if discipline in disciplines)
