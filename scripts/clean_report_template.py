"""
One-off: turn the DDC report-forms template into the export base workbook.

Reads templates/report_forms_source.xltx, blanks the sample project values the template shipped
with (Contract Info C2:C7, and the hardcoded borough on Sketch Cont and Report Cont), drops the
sample results cached on every formula that reads Contract Info, sets the workbook to recalculate
on open, and writes templates/report_forms.xlsx as a regular workbook. It edits the package XML
directly instead of round-tripping through openpyxl, which would drop the forms' logos, checkbox
rectangles and lines.

Run from the project root:
    python scripts/clean_report_template.py
"""

import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "templates" / "report_forms_source.xltx"
TARGET = ROOT / "templates" / "report_forms.xlsx"

# Sheet name -> cells holding sample project data to blank
CELLS_TO_BLANK = {
    "Contract Info": ["C2", "C3", "C4", "C5", "C6", "C7"],
    "Sketch Cont": ["F15"],
    "Report Cont": ["F17"],
}

# The sample values themselves, also left behind as unreferenced shared strings
SAMPLE_VALUES = ["SER200220", "STORM/SANITARY SEWERS IN Jewett Ave", "Staten Island", "STATEN ISLAND",
                 "Inter LaPeruta JV", "Jay Patel"]

TEMPLATE_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml"
WORKBOOK_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"


def sheet_paths(package: zipfile.ZipFile) -> dict[str, str]:
    """
    Map each sheet name to its worksheet part inside the package.
    Takes the open template package.
    Returns {sheet name: 'xl/worksheets/sheetN.xml'}.
    """
    workbook = package.read("xl/workbook.xml").decode("utf-8")
    rels = package.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    targets = {}
    for rel in re.findall(r"<Relationship [^>]*/>", rels):
        rel_id = re.search(r'Id="([^"]+)"', rel).group(1)
        targets[rel_id] = re.search(r'Target="([^"]+)"', rel).group(1)
    paths = {}
    for sheet in re.findall(r"<sheet [^>]*/>", workbook):
        name = re.search(r'name="([^"]+)"', sheet).group(1)
        rel_id = re.search(r'r:id="([^"]+)"', sheet).group(1)
        paths[name] = "xl/" + targets[rel_id].lstrip("/").removeprefix("xl/")
    return paths


def blank_cell(sheet_xml: str, coordinate: str) -> str:
    """
    Empty one cell in a worksheet's XML, keeping its style.
    Takes the worksheet XML and a cell reference like 'C2'.
    Returns the XML with that cell's value and type removed; raises if the cell isn't there.
    """
    pattern = re.compile(rf'<c r="{coordinate}"([^>]*?)(?:/>|>.*?</c>)', re.DOTALL)
    match = pattern.search(sheet_xml)
    if not match:
        raise ValueError(f"cell {coordinate} not found")
    style = re.search(r'\ss="(\d+)"', match.group(1))
    replacement = f'<c r="{coordinate}" s="{style.group(1)}"/>' if style else f'<c r="{coordinate}"/>'
    return sheet_xml[: match.start()] + replacement + sheet_xml[match.end():]


# A formula cell reading Contract Info, with its cached result (and the cached result's type)
CACHED_CONTRACT_INFO_FORMULA = re.compile(
    r'(<c r="\w+"[^>]*?)(?: t="str")?([^>]*>)(<f>[^<]*Contract Info[^<]*</f>)<v>[^<]*</v>'
)


def drop_cached_contract_info(sheet_xml: str) -> tuple[str, int]:
    """
    Remove the cached results of formulas that read Contract Info, so no viewer shows stale sample values.
    Takes a worksheet's XML.
    Returns the XML without those cached values, and how many cells were changed.
    """
    return CACHED_CONTRACT_INFO_FORMULA.subn(r"\1\2\3", sheet_xml)


def blank_sample_shared_strings(shared_strings_xml: str) -> str:
    """
    Empty the shared-string entries holding the sample values, in place so other strings keep their indexes.
    Takes the sharedStrings.xml text.
    Returns it with those entries' text blanked.
    """
    for value in SAMPLE_VALUES:
        shared_strings_xml = re.sub(rf"(<t[^>]*>){re.escape(value)}(</t>)", r"\1\2", shared_strings_xml)
    return shared_strings_xml


def main() -> None:
    """
    Write the cleaned workbook next to the source template.
    Takes nothing; reads SOURCE and writes TARGET.
    Returns nothing.
    """
    with zipfile.ZipFile(SOURCE) as source:
        paths = sheet_paths(source)
        edits = {}
        dropped = 0
        for sheet, path in paths.items():
            xml = source.read(path).decode("utf-8")
            for cell in CELLS_TO_BLANK.get(sheet, []):
                xml = blank_cell(xml, cell)
            xml, count = drop_cached_contract_info(xml)
            dropped += count
            if sheet in CELLS_TO_BLANK or count:
                edits[path] = xml.encode("utf-8")
        workbook = source.read("xl/workbook.xml").decode("utf-8")
        edits["xl/workbook.xml"] = re.sub(r"<calcPr ", '<calcPr fullCalcOnLoad="1" ', workbook, count=1).encode("utf-8")
        shared_strings = source.read("xl/sharedStrings.xml").decode("utf-8")
        edits["xl/sharedStrings.xml"] = blank_sample_shared_strings(shared_strings).encode("utf-8")
        content_types = source.read("[Content_Types].xml").decode("utf-8")
        edits["[Content_Types].xml"] = content_types.replace(TEMPLATE_TYPE, WORKBOOK_TYPE).encode("utf-8")

        with zipfile.ZipFile(TARGET, "w", zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                target.writestr(item, edits.get(item.filename, source.read(item.filename)))
    print(f"wrote {TARGET.relative_to(ROOT)} (dropped {dropped} cached formula results)")


if __name__ == "__main__":
    main()
