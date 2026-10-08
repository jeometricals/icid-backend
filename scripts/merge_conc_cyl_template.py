"""
One-off: add the DDC "Data Sheet for Concrete Test Cylinders" to the report-forms template as the Conc Cyl tab.

Reads templates/conc_cyl_source.xlsx (the DDC form, Rev. 10/31/03, converted from .xls; its one sheet is "Front
Side") and copies that sheet into templates/report_forms_source.xltx as the last tab, named Conc Cyl: the worksheet
with its merged cells, column widths, row heights and print setup, its drawing and the logo the drawing shows, the
cell styles it uses (fonts, fills, borders and number formats, renumbered into the template's own) and its texts
(added to the template's shared strings). It edits the package XML directly, as clean_report_template.py does:
openpyxl can't copy a sheet between workbooks and would drop the other forms' logos and shapes.

Run from the project root, once, then rebuild the export base from the template:
    python scripts/merge_conc_cyl_template.py
    python scripts/clean_report_template.py
"""

import posixpath
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORM = ROOT / "templates" / "conc_cyl_source.xlsx"
TEMPLATE = ROOT / "templates" / "report_forms_source.xltx"

FORM_SHEET = "Front Side"
SHEET_NAME = "Conc Cyl"

RELATIONSHIPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WORKSHEET_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
DRAWING_TYPE = "application/vnd.openxmlformats-officedocument.drawing+xml"
IMAGE_TYPES = {"jpeg": "image/jpeg", "jpg": "image/jpeg", "png": "image/png"}

# A style collection in styles.xml -> the element each of its entries is
STYLE_ENTRIES = {"fonts": "font", "fills": "fill", "borders": "border", "cellXfs": "xf"}
# The first number-format id a workbook defines for itself; the ids below it are built in
FIRST_CUSTOM_FORMAT = 164


def collection(styles: str, name: str) -> re.Match:
    """
    Find one collection (fonts, fills, borders, cellXfs, numFmts) in a styles.xml.
    Takes the styles XML and the collection's name.
    Returns the match: group 1 its start tag, group 2 its entries, group 3 its end tag; raises if it isn't there.
    """
    match = re.search(rf"(<{name}\b[^>]*>)(.*?)(</{name}>)", styles, re.DOTALL)
    if not match:
        raise ValueError(f"styles.xml has no {name}")
    return match


def entries(styles: str, name: str) -> list[str]:
    """
    List a style collection's entries, in index order.
    Takes the styles XML and the collection's name (a key of STYLE_ENTRIES).
    Returns each entry's XML.
    """
    tag = STYLE_ENTRIES[name]
    return re.findall(rf"<{tag}\b[^>]*?/>|<{tag}\b[^>]*>.*?</{tag}>", collection(styles, name).group(2), re.DOTALL)


def add_entries(styles: str, name: str, new: list[str]) -> tuple[str, list[int]]:
    """
    Add entries to a style collection, reusing an entry that is already there word for word.
    Takes the styles XML, the collection's name and the entries to add.
    Returns the styles XML with the collection and its count updated, and the index each entry now has.
    """
    current = entries(styles, name)
    indexes = []
    for entry in new:
        if entry not in current:
            current.append(entry)
        indexes.append(current.index(entry))
    match = collection(styles, name)
    start = re.sub(r'\bcount="\d+"', f'count="{len(current)}"', match.group(1), count=1)
    return styles[: match.start()] + start + "".join(current) + match.group(3) + styles[match.end():], indexes


def number_formats(styles: str) -> dict[int, str]:
    """
    Read the number formats a workbook defines for itself.
    Takes the styles XML.
    Returns {numFmtId: format code}; empty when the workbook defines none.
    """
    if "<numFmts" not in styles:
        return {}
    formats = re.findall(r'<numFmt numFmtId="(\d+)" formatCode="([^"]*)"/>', collection(styles, "numFmts").group(2))
    return {int(number): code for number, code in formats}


def add_number_formats(styles: str, form_styles: str) -> tuple[str, dict[int, int]]:
    """
    Give the template the number formats the form defines: "General" is the built-in format 0, a code the template
    already has keeps the template's id, and any other is added under the next free id.
    Takes the template's styles XML and the form's.
    Returns the template's styles XML, and {the form's numFmtId: the template's}.
    """
    known = {code: number for number, code in number_formats(styles).items()}
    mapping = {}
    for number, code in number_formats(form_styles).items():
        if code == "General":
            mapping[number] = 0
            continue
        if code not in known:
            known[code] = max([FIRST_CUSTOM_FORMAT - 1, *known.values()]) + 1
            match = collection(styles, "numFmts")
            start = re.sub(r'\bcount="\d+"', f'count="{len(known)}"', match.group(1), count=1)
            added = f'<numFmt numFmtId="{known[code]}" formatCode="{code}"/>'
            styles = styles[: match.start()] + start + match.group(2) + added + match.group(3) + styles[match.end():]
        mapping[number] = known[code]
    return styles, mapping


def merge_styles(styles: str, form_styles: str) -> tuple[str, list[int]]:
    """
    Add the form's cell styles to the template's: its fonts, fills, borders and number formats, then each cell format
    pointing at them, under the template's Normal style. The form's defaults become the template's.
    Takes the template's styles XML and the form's.
    Returns the template's styles XML, and the template index of each of the form's cell formats, in order; raises if
    the form's styles use palette or theme colours, which would read differently in the template, or if the two
    workbooks' default fonts differ.
    """
    parts = "".join(collection(form_styles, name).group(2) for name in ("fonts", "fills", "borders"))
    if re.search(r'\b(?:indexed|theme)="', parts):
        raise ValueError("the form's styles use palette or theme colours; convert them to rgb first")
    styles, formats = add_number_formats(styles, form_styles)
    default_font = lambda text: re.findall(r'<(?:sz|name) val="[^"]*"/>', entries(text, "fonts")[0])
    if default_font(styles) != default_font(form_styles):
        raise ValueError("the form's default font isn't the template's; its column widths would change")
    # Entry 0 of each collection is the workbook's default (its font, no fill, no border) and stays entry 0. Excel
    # takes a cell whose border isn't entry 0 as bordered, even when that border draws nothing, and the blank rows
    # under the form would then print as an empty second page.
    maps = {}
    for name, attribute in (("fonts", "fontId"), ("fills", "fillId"), ("borders", "borderId")):
        styles, indexes = add_entries(styles, name, entries(form_styles, name)[1:])
        maps[attribute] = [0] + indexes

    def renumbered(cell_format: str) -> str:
        start = re.match(r"<xf\b[^>]*>", cell_format).group(0)
        new_start = start
        for attribute, indexes in maps.items():
            new_start = re.sub(rf'\b{attribute}="(\d+)"', lambda m: f'{attribute}="{indexes[int(m.group(1))]}"',
                               new_start)
        new_start = re.sub(r'\bnumFmtId="(\d+)"',
                           lambda m: f'numFmtId="{formats.get(int(m.group(1)), int(m.group(1)))}"', new_start)
        new_start = re.sub(r'\bxfId="\d+"', 'xfId="0"', new_start)
        return new_start + cell_format[len(start):]

    # So does cell format 0
    cell_formats = entries(form_styles, "cellXfs")
    styles, indexes = add_entries(styles, "cellXfs", [renumbered(cell_format) for cell_format in cell_formats[1:]])
    return styles, [0] + indexes


def merge_strings(shared_strings: str, form_strings: str, references: int) -> tuple[str, int]:
    """
    Add the form's shared strings after the template's.
    Takes the template's sharedStrings XML, the form's, and how many of the form's cells hold a string.
    Returns the template's sharedStrings XML with its counts updated, and the index the form's first string now has.
    """
    added = re.findall(r"<si>.*?</si>|<si/>", form_strings, re.DOTALL)
    start = re.search(r"<sst\b[^>]*>", shared_strings).group(0)
    first = int(re.search(r'uniqueCount="(\d+)"', start).group(1))
    total = int(re.search(r'\bcount="(\d+)"', start).group(1)) + references
    new_start = re.sub(r'uniqueCount="\d+"', f'uniqueCount="{first + len(added)}"', start)
    new_start = re.sub(r'\bcount="\d+"', f'count="{total}"', new_start)
    shared_strings = shared_strings.replace(start, new_start, 1)
    return shared_strings.replace("</sst>", "".join(added) + "</sst>"), first


STRING_CELL = re.compile(r'(<c\b[^>]*?\st="s"[^>]*>\s*<v>)(\d+)(</v>)')


def renumber_sheet(sheet_xml: str, cell_formats: list[int], first_string: int) -> str:
    """
    Point the form's worksheet at the template's styles and shared strings, and unselect its tab (the form's one
    sheet is saved selected; a second selected tab would open the workbook with its sheets grouped).
    Takes the worksheet XML, the template index of each of the form's cell formats, and the template index of the
    form's first shared string.
    Returns the worksheet XML.
    """
    restyle = lambda match: f'{match.group(1)}{cell_formats[int(match.group(2))]}"'
    sheet_xml = re.sub(r'(<(?:c|row)\b[^>]*?\ss=")(\d+)"', restyle, sheet_xml)
    sheet_xml = re.sub(r'(<col\b[^>]*?\sstyle=")(\d+)"', restyle, sheet_xml)
    sheet_xml = STRING_CELL.sub(lambda m: f"{m.group(1)}{first_string + int(m.group(2))}{m.group(3)}", sheet_xml)
    return re.sub(r'(<sheetView\b[^>]*?)\stabSelected="[^"]*"', r"\1", sheet_xml)


def rels_part(part: str) -> str:
    """
    Name the relationships part that belongs to a part.
    Takes the part name, e.g. 'xl/worksheets/sheet8.xml'.
    Returns e.g. 'xl/worksheets/_rels/sheet8.xml.rels'.
    """
    folder, name = posixpath.split(part)
    return f"{folder}/_rels/{name}.rels"


def next_part(names: list[str], folder: str, stem: str, extension: str) -> str:
    """
    Pick the next free numbered part name, e.g. xl/drawings/drawing23.xml after drawing22.xml.
    Takes the package's part names, the folder, the name's stem and its extension.
    Returns the part name one above the highest number in use.
    """
    pattern = re.compile(rf"{re.escape(folder)}/{re.escape(stem)}(\d+)\.\w+")
    numbers = [int(match.group(1)) for name in names if (match := pattern.fullmatch(name))]
    return f"{folder}/{stem}{max(numbers, default=0) + 1}.{extension}"


def only_relationship(package: zipfile.ZipFile, part: str, kind: str) -> tuple[str, str]:
    """
    Read a part's one relationship, which must be of the kind expected (a worksheet's drawing, a drawing's image).
    Takes the open package, the part name and the relationship kind.
    Returns the part's relationships XML and the part it points at; raises if it has any other relationship.
    """
    rels = package.read(rels_part(part)).decode("utf-8")
    relationships = re.findall(r"<Relationship [^>]*/>", rels)
    if len(relationships) != 1 or f'Type="{RELATIONSHIPS}/{kind}"' not in relationships[0]:
        raise ValueError(f"expected {part} to have one {kind} relationship, found {relationships}")
    target = re.search(r'Target="([^"]+)"', relationships[0]).group(1)
    return rels, posixpath.normpath(posixpath.join(posixpath.dirname(part), target))


def form_sheet_part(package: zipfile.ZipFile) -> str:
    """
    Find the worksheet part of the form's sheet.
    Takes the open form package.
    Returns its part name, e.g. 'xl/worksheets/sheet1.xml'; raises if the form has no sheet named FORM_SHEET.
    """
    workbook = package.read("xl/workbook.xml").decode("utf-8")
    sheet = re.search(rf'<sheet\b[^>]*?name="{FORM_SHEET}"[^>]*/>', workbook)
    if not sheet:
        raise ValueError(f"{FORM.name} has no sheet named {FORM_SHEET!r}")
    rel_id = re.search(r'r:id="([^"]+)"', sheet.group(0)).group(1)
    rels = package.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    target = re.search(rf'<Relationship\b[^>]*?Id="{rel_id}"[^>]*/>', rels).group(0)
    return "xl/" + re.search(r'Target="([^"]+)"', target).group(1).lstrip("/").removeprefix("xl/")


def add_sheet_to_workbook(workbook: str, workbook_rels: str, sheet_part: str) -> tuple[str, str]:
    """
    List the new worksheet in the workbook, as its last tab.
    Takes the workbook XML, the workbook's relationships XML and the new worksheet's part name.
    Returns both, updated.
    """
    rel_id = f"rId{max(int(n) for n in re.findall(r'Id=.rId(\d+).', workbook_rels)) + 1}"
    relationship = (f'<Relationship Id="{rel_id}" Type="{RELATIONSHIPS}/worksheet" '
                    f'Target="{sheet_part.removeprefix("xl/")}"/>')
    workbook_rels = workbook_rels.replace("</Relationships>", relationship + "</Relationships>")
    sheet_id = max(int(n) for n in re.findall(r'<sheet [^>]*?sheetId="(\d+)"', workbook)) + 1
    sheet = f'<sheet name="{SHEET_NAME}" sheetId="{sheet_id}" r:id="{rel_id}"/>'
    return workbook.replace("</sheets>", sheet + "</sheets>"), workbook_rels


def add_content_types(content_types: str, sheet_part: str, drawing_part: str, image_part: str) -> str:
    """
    Register the new worksheet and drawing, and the image's extension when the template has no picture of that kind.
    Takes the [Content_Types].xml text and the three new part names.
    Returns it updated.
    """
    extension = image_part.rsplit(".", 1)[1]
    added = ""
    if f'<Default Extension="{extension}"' not in content_types:
        added += f'<Default Extension="{extension}" ContentType="{IMAGE_TYPES[extension]}"/>'
    added += f'<Override PartName="/{sheet_part}" ContentType="{WORKSHEET_TYPE}"/>'
    added += f'<Override PartName="/{drawing_part}" ContentType="{DRAWING_TYPE}"/>'
    return content_types.replace("</Types>", added + "</Types>")


def main() -> None:
    """
    Copy the form's sheet into the template, in place.
    Takes nothing; reads FORM and rewrites TEMPLATE.
    Returns nothing; raises if the template already has a Conc Cyl sheet.
    """
    with zipfile.ZipFile(FORM) as form, zipfile.ZipFile(TEMPLATE) as template:
        workbook = template.read("xl/workbook.xml").decode("utf-8")
        if f'<sheet name="{SHEET_NAME}"' in workbook:
            raise ValueError(f"{TEMPLATE.name} already has a {SHEET_NAME} sheet")
        names = template.namelist()
        form_sheet = form_sheet_part(form)
        sheet_rels, form_drawing = only_relationship(form, form_sheet, "drawing")
        drawing_rels, form_image = only_relationship(form, form_drawing, "image")

        sheet_part = next_part(names, "xl/worksheets", "sheet", "xml")
        drawing_part = next_part(names, "xl/drawings", "drawing", "xml")
        image_part = next_part(names, "xl/media", "image", form_image.rsplit(".", 1)[1])

        styles, cell_formats = merge_styles(template.read("xl/styles.xml").decode("utf-8"),
                                            form.read("xl/styles.xml").decode("utf-8"))
        sheet_xml = form.read(form_sheet).decode("utf-8")
        shared_strings, first_string = merge_strings(template.read("xl/sharedStrings.xml").decode("utf-8"),
                                                     form.read("xl/sharedStrings.xml").decode("utf-8"),
                                                     len(STRING_CELL.findall(sheet_xml)))
        workbook, workbook_rels = add_sheet_to_workbook(
            workbook, template.read("xl/_rels/workbook.xml.rels").decode("utf-8"), sheet_part)
        content_types = add_content_types(template.read("[Content_Types].xml").decode("utf-8"), sheet_part,
                                          drawing_part, image_part)
        retarget = lambda rels, part, folder: re.sub(r'Target="[^"]+"',
                                                     f'Target="{posixpath.relpath(part, folder)}"', rels)
        edits = {"xl/workbook.xml": workbook, "xl/_rels/workbook.xml.rels": workbook_rels, "xl/styles.xml": styles,
                 "xl/sharedStrings.xml": shared_strings, "[Content_Types].xml": content_types}
        added = {
            sheet_part: renumber_sheet(sheet_xml, cell_formats, first_string).encode("utf-8"),
            rels_part(sheet_part): retarget(sheet_rels, drawing_part, "xl/worksheets").encode("utf-8"),
            drawing_part: form.read(form_drawing),
            rels_part(drawing_part): retarget(drawing_rels, image_part, "xl/drawings").encode("utf-8"),
            image_part: form.read(form_image),
        }

        merged = TEMPLATE.with_suffix(".merged")
        with zipfile.ZipFile(merged, "w", zipfile.ZIP_DEFLATED) as target:
            for item in template.infolist():
                content = edits[item.filename].encode("utf-8") if item.filename in edits else template.read(item)
                target.writestr(item, content)
            for part, content in added.items():
                target.writestr(part, content)
    merged.replace(TEMPLATE)
    print(f"added {SHEET_NAME} to {TEMPLATE.relative_to(ROOT)} as {sheet_part} ({len(cell_formats)} cell formats, "
          f"drawing {drawing_part}, logo {image_part})")


if __name__ == "__main__":
    main()
