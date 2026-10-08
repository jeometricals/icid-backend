"""
The report-type and discipline mapping (api/services/disciplines.py).
"""

from api.schemas.idr_report import ReportType
from api.services.disciplines import (
    REPORT_TYPE_TO_DISCIPLINES, disciplines_for_report_type, report_types_for_discipline,
)


class TestDisciplines:
    def test_each_report_type_gives_its_disciplines(self):
        assert disciplines_for_report_type("GEN") == ("General",)
        assert disciplines_for_report_type("AC") == ("AC Pavement",)
        assert disciplines_for_report_type("CONC_MIX") == ("Concrete (delivery)",)
        assert disciplines_for_report_type("CONC_CYL") == ("Concrete (testing)",)

    def test_swcb_covers_sidewalk_and_curb(self):
        assert disciplines_for_report_type("SWCB") == ("Sidewalk", "Curb")

    def test_an_unknown_report_type_has_none(self):
        assert disciplines_for_report_type("SWR") == ()
        assert disciplines_for_report_type("") == ()

    def test_each_discipline_gives_its_report_types(self):
        assert report_types_for_discipline("General") == ("GEN",)
        assert report_types_for_discipline("Sidewalk") == ("SWCB",)
        assert report_types_for_discipline("Curb") == ("SWCB",)
        assert report_types_for_discipline("AC Pavement") == ("AC",)
        assert report_types_for_discipline("Concrete (delivery)") == ("CONC_MIX",)
        assert report_types_for_discipline("Concrete (testing)") == ("CONC_CYL",)

    def test_an_unknown_discipline_has_no_report_types(self):
        assert report_types_for_discipline("Sewer") == ()
        assert report_types_for_discipline("sidewalk") == ()  # spelled as the mapping spells it, or nothing

    def test_every_mapped_type_is_a_report_type(self):
        assert set(REPORT_TYPE_TO_DISCIPLINES) <= {report_type.value for report_type in ReportType}
        assert all(disciplines for disciplines in REPORT_TYPE_TO_DISCIPLINES.values())
