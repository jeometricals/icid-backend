"""
One-off: turn the DDC report-forms template into the export base workbook.

Reads templates/report_forms_source.xltx, blanks the sample project values the template shipped
with (Contract Info C2:C7, and the hardcoded borough on Sketch Cont and Report Cont), drops the
sample results cached on every formula that reads Contract Info, sets the workbook to recalculate
on open, makes the white-filled checkbox rectangles on Conc Fr / Conc Bk / AC Bk transparent (keeping
their outlines) so an "X" stamped in the cell beneath shows through, deletes the collapsed zero-height
rectangles left in AC Fr's top row, and writes templates/report_forms.xlsx
as a regular workbook. It edits the package XML directly instead of round-tripping through openpyxl,
which would drop the forms' logos, checkbox rectangles and lines.

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

# Sheet name -> cells under a drawn checkbox rectangle. The rectangles are filled white with a black outline, which
# hides anything in the cell beneath; their fill is removed so a stamped "X" shows inside the outline.
CHECKBOXES_TO_OPEN = {
    "Conc Fr": ["E29", "K29", "S29", "Z29"],  # Curb, Sidewalk, Concrete Base, Structural
    "Conc Bk": ["Z37", "C52"],  # "See attached Concrete Truck and Mixing Information", "Attached pages for remarks"
    "AC Bk": ["C48"],  # "Attached Pages for Additional Remarks and / or Sketches"
}

# Sheet name -> cells with a collapsed rectangle anchored on them (zero height, a few pixels wide at most), deleted.
# AC Fr's five sit in row 1, the strip the export writes "DRAFT - Not for Submission" across; they draw nothing.
SHAPES_TO_DELETE = {
    "AC Fr": ["B1", "C1", "D1", "F1", "J1"],
}

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


def drawing_path(package: zipfile.ZipFile, sheet_path: str) -> str:
    """
    Find the drawing part a worksheet shows (its logos and shapes).
    Takes the open template package and the worksheet's part name.
    Returns the drawing's part name, e.g. 'xl/drawings/drawing10.xml'; raises if the sheet has none.
    """
    folder, name = sheet_path.rsplit("/", 1)
    rels = package.read(f"{folder}/_rels/{name}.rels").decode("utf-8")
    target = re.search(r'Target="\.\./drawings/([^"]+)"', rels)
    if not target:
        raise ValueError(f"{sheet_path} has no drawing")
    return f"xl/drawings/{target.group(1)}"


def cell_position(coordinate: str) -> tuple[int, int]:
    """
    Convert a cell reference to the zero-based column and row a drawing anchor uses.
    Takes a reference like 'E29'.
    Returns (4, 28).
    """
    letters = re.match(r"[A-Z]+", coordinate).group(0)
    column = 0
    for letter in letters:
        column = column * 26 + ord(letter) - 64
    return column - 1, int(coordinate[len(letters):]) - 1


def _anchors_on(drawing_xml: str, cell: str) -> list[str]:
    """
    Find the shape anchors (not pictures) whose top-left corner sits in a cell.
    Takes the drawing XML and the cell.
    Returns each matching twoCellAnchor element's XML.
    """
    column, row = cell_position(cell)
    anchor = re.compile(
        rf"<xdr:twoCellAnchor\b[^>]*><xdr:from><xdr:col>{column}</xdr:col><xdr:colOff>\d+</xdr:colOff>"
        rf"<xdr:row>{row}</xdr:row>.*?</xdr:twoCellAnchor>",
        re.DOTALL,
    )
    return [m.group(0) for m in anchor.finditer(drawing_xml) if "<xdr:sp>" in m.group(0) or "<xdr:sp " in m.group(0)]


def delete_collapsed_shapes(drawing_xml: str, cells: list[str]) -> str:
    """
    Delete the collapsed shapes anchored on the given cells.
    Takes the drawing XML and the cells (each must have exactly one shape anchored on it, of zero height).
    Returns the drawing XML without them; raises if a cell doesn't match exactly once or its shape has a height.
    """
    for cell in cells:
        matches = _anchors_on(drawing_xml, cell)
        if len(matches) != 1:
            raise ValueError(f"expected one shape anchored on {cell}, found {len(matches)}")
        if not re.search(r'<a:ext cx="\d+" cy="0"/>', matches[0]):
            raise ValueError(f"the shape on {cell} isn't collapsed (it has a height); not deleting it")
        drawing_xml = drawing_xml.replace(matches[0], "", 1)
    return drawing_xml


def open_checkboxes(drawing_xml: str, cells: list[str]) -> str:
    """
    Remove the fill from the checkbox rectangles anchored on the given cells, keeping their outlines.
    Takes the drawing XML and the cells (each must have exactly one filled shape anchored on it).
    Returns the drawing XML with those shapes' fill set to <a:noFill/>; raises if a cell doesn't match exactly once.
    """
    for cell in cells:
        matches = _anchors_on(drawing_xml, cell)
        if len(matches) != 1:
            raise ValueError(f"expected one shape anchored on {cell}, found {len(matches)}")
        shape = matches[0]
        properties = re.search(r"<xdr:spPr\b[^>]*>(.*?)</xdr:spPr>", shape, re.DOTALL)
        fill_area = properties.group(1).split("<a:ln", 1)[0]  # the shape's own fill comes before its outline
        if "<a:solidFill>" not in fill_area:
            raise ValueError(f"the shape on {cell} has no solid fill to remove")
        new_fill_area = re.sub(r"<a:solidFill>.*?</a:solidFill>", "<a:noFill/>", fill_area, count=1, flags=re.DOTALL)
        new_properties = properties.group(0).replace(fill_area, new_fill_area, 1)
        drawing_xml = drawing_xml.replace(shape, shape.replace(properties.group(0), new_properties, 1), 1)
    return drawing_xml


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
        opened = deleted = 0
        for sheet, cells in CHECKBOXES_TO_OPEN.items():
            drawing = drawing_path(source, paths[sheet])
            edits[drawing] = open_checkboxes(source.read(drawing).decode("utf-8"), cells).encode("utf-8")
            opened += len(cells)
        for sheet, cells in SHAPES_TO_DELETE.items():
            drawing = drawing_path(source, paths[sheet])
            current = edits.get(drawing, source.read(drawing)).decode("utf-8")
            edits[drawing] = delete_collapsed_shapes(current, cells).encode("utf-8")
            deleted += len(cells)
        workbook = source.read("xl/workbook.xml").decode("utf-8")
        edits["xl/workbook.xml"] = re.sub(r"<calcPr ", '<calcPr fullCalcOnLoad="1" ', workbook, count=1).encode("utf-8")
        shared_strings = source.read("xl/sharedStrings.xml").decode("utf-8")
        edits["xl/sharedStrings.xml"] = blank_sample_shared_strings(shared_strings).encode("utf-8")
        content_types = source.read("[Content_Types].xml").decode("utf-8")
        edits["[Content_Types].xml"] = content_types.replace(TEMPLATE_TYPE, WORKBOOK_TYPE).encode("utf-8")

        with zipfile.ZipFile(TARGET, "w", zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                target.writestr(item, edits.get(item.filename, source.read(item.filename)))
    print(f"wrote {TARGET.relative_to(ROOT)} (dropped {dropped} cached formula results, opened {opened} checkboxes, "
          f"deleted {deleted} collapsed shapes)")


if __name__ == "__main__":
    main()
