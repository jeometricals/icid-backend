"""
Fills cells in an .xlsx template by editing the package XML directly.

openpyxl rewrites a workbook from its own model on save and drops what it doesn't model, which on
the DDC report forms means their logos, checkbox rectangles and ruled lines. Editing the sheet XML
in place keeps everything else in the package byte-for-byte.
"""

import re
import zipfile
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional, Union
from xml.sax.saxutils import escape

CellValue = Union[str, int, float, Decimal, date, None]

# Excel's day zero for date serial numbers (1900 date system, including its leap-year quirk)
EXCEL_EPOCH = date(1899, 12, 30)

LETTER_PAPER = "1"

_COLUMN = re.compile(r"[A-Z]+")

# Characters XML 1.0 doesn't allow in text (control characters other than tab, newline, carriage return)
_INVALID_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _column_number(coordinate: str) -> int:
    """
    Convert a cell reference's column letters to a 1-based number.
    Takes a reference like 'AI4'.
    Returns its column number (AI → 35).
    """
    number = 0
    for letter in _COLUMN.match(coordinate).group(0):
        number = number * 26 + ord(letter) - 64
    return number


def _row_number(coordinate: str) -> int:
    """
    Read the row number from a cell reference.
    Takes a reference like 'AI4'.
    Returns 4.
    """
    return int(coordinate[_COLUMN.match(coordinate).end():])


def _set_attribute(tag: str, name: str, value: str) -> str:
    """
    Set one attribute on an XML start tag, replacing it if present.
    Takes the tag text (e.g. '<pageSetup scale="80"/>'), the attribute name and its value.
    Returns the tag text with the attribute set.
    """
    if re.search(rf"\s{name}=\"[^\"]*\"", tag):
        return re.sub(rf"(\s{name}=)\"[^\"]*\"", rf'\g<1>"{value}"', tag, count=1)
    closing = "/>" if tag.endswith("/>") else ">"
    return f'{tag[: -len(closing)]} {name}="{value}"{closing}'


def _cell_xml(coordinate: str, style: Optional[str], value: CellValue) -> str:
    """
    Build one <c> element for a cell value, keeping the cell's style.
    Takes the reference, the existing style index (or None) and the value; text is written inline,
    numbers as numbers, dates as Excel serial numbers, and None as an empty cell.
    Returns the element's XML.
    """
    style_attr = f' s="{style}"' if style is not None else ""
    if value is None or value == "":
        return f'<c r="{coordinate}"{style_attr}/>'
    if isinstance(value, date):
        return f'<c r="{coordinate}"{style_attr}><v>{(value - EXCEL_EPOCH).days}</v></c>'
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return f'<c r="{coordinate}"{style_attr}><v>{value}</v></c>'
    text = escape(_INVALID_XML_CHARS.sub("", str(value)))
    return f'<c r="{coordinate}"{style_attr} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


class WorkbookTemplate:
    """An .xlsx package held in memory, whose cells, styles, print setup and sheet visibility can be edited."""

    def __init__(self, path: Path) -> None:
        """
        Load every part of the package at path.
        Takes the .xlsx file path.
        Returns nothing.
        """
        with zipfile.ZipFile(path) as package:
            self._infos = package.infolist()
            self._parts = {info.filename: package.read(info.filename) for info in self._infos}
        self._sheet_paths, self._sheet_order = self._read_sheets()
        # Derived styles already added, keyed by (kind, source style), so repeated cells share one new style
        self._style_cache: dict[tuple[str, Optional[str]], str] = {}

    def _text(self, part: str) -> str:
        """
        Read one package part as text.
        Takes the part name (e.g. 'xl/workbook.xml').
        Returns its decoded XML.
        """
        return self._parts[part].decode("utf-8")

    def _write(self, part: str, xml: str) -> None:
        """
        Replace one package part.
        Takes the part name and its new XML.
        Returns nothing.
        """
        self._parts[part] = xml.encode("utf-8")

    def _read_sheets(self) -> tuple[dict[str, str], list[str]]:
        """
        Map sheet names to their worksheet parts.
        Takes nothing; reads the workbook part and its relationships.
        Returns ({sheet name: part name}, sheet names in tab order).
        """
        rels = self._text("xl/_rels/workbook.xml.rels")
        targets = {}
        for rel in re.findall(r"<Relationship [^>]*/>", rels):
            rel_id = re.search(r'Id="([^"]+)"', rel).group(1)
            targets[rel_id] = re.search(r'Target="([^"]+)"', rel).group(1)
        paths, order = {}, []
        for sheet in re.findall(r"<sheet [^>]*/>", self._text("xl/workbook.xml")):
            name = re.search(r'name="([^"]+)"', sheet).group(1)
            rel_id = re.search(r'r:id="([^"]+)"', sheet).group(1)
            paths[name] = "xl/" + targets[rel_id].lstrip("/").removeprefix("xl/")
            order.append(name)
        return paths, order

    def _sheet(self, sheet: str) -> str:
        """
        Read a worksheet's XML by sheet name.
        Takes the sheet name.
        Returns the worksheet XML; raises KeyError for an unknown sheet.
        """
        return self._text(self._sheet_paths[sheet])

    def cell_style(self, sheet: str, coordinate: str) -> Optional[str]:
        """
        Read a cell's style index.
        Takes the sheet name and cell reference.
        Returns the style index as a string, or None when the cell has none or doesn't exist.
        """
        match = re.search(rf'<c r="{coordinate}"(?=[\s/>])([^>]*?)(?:/>|>)', self._sheet(sheet))
        style = re.search(r'\ss="(\d+)"', match.group(1)) if match else None
        return style.group(1) if style else None

    def set_cell(self, sheet: str, coordinate: str, value: CellValue, style: Optional[str] = None) -> None:
        """
        Write a value into a cell, keeping its style unless one is given.
        Takes the sheet name, cell reference, value (text, number, date or None to empty it) and an optional style index.
        Returns nothing; adds the cell (and its row) when the template doesn't have it.
        """
        xml = self._sheet(sheet)
        style = style if style is not None else self.cell_style(sheet, coordinate)
        new_cell = _cell_xml(coordinate, style, value)
        existing = re.compile(rf'<c r="{coordinate}"(?=[\s/>])[^>]*?(?:/>|>.*?</c>)', re.DOTALL)
        if existing.search(xml):
            xml = existing.sub(lambda _: new_cell, xml, count=1)
        else:
            xml = self._insert_cell(xml, coordinate, new_cell)
        self._write(self._sheet_paths[sheet], xml)

    def set_style(self, sheet: str, coordinate: str, style: str) -> None:
        """
        Change a cell's style, keeping its value.
        Takes the sheet name, cell reference and the new style index.
        Returns nothing; raises KeyError if the cell isn't in the sheet.
        """
        xml = self._sheet(sheet)
        start_tag = re.compile(rf'<c r="{coordinate}"(?=[\s/>])[^>]*?/?>')
        match = start_tag.search(xml)
        if match is None:
            raise KeyError(f"{sheet}!{coordinate} not in the template")
        xml = xml[: match.start()] + _set_attribute(match.group(0), "s", style) + xml[match.end():]
        self._write(self._sheet_paths[sheet], xml)

    def _insert_cell(self, xml: str, coordinate: str, new_cell: str) -> str:
        """
        Insert a cell the sheet doesn't have yet, in column order, creating its row if needed.
        Takes the worksheet XML, the cell reference and the cell's XML.
        Returns the updated worksheet XML.
        """
        row_number = _row_number(coordinate)
        row = re.search(rf'<row r="{row_number}"(?=[\s/>])[^>]*?(/>|>(.*?)</row>)', xml, re.DOTALL)
        if row is None:
            new_row = f'<row r="{row_number}">{new_cell}</row>'
            later = next((m for m in re.finditer(r'<row r="(\d+)"', xml) if int(m.group(1)) > row_number), None)
            at = later.start() if later else xml.index("</sheetData>")
            return xml[:at] + new_row + xml[at:]
        if row.group(1) == "/>":
            start_tag = row.group(0)[:-2] + ">"
            return xml[: row.start()] + start_tag + new_cell + "</row>" + xml[row.end():]
        column = _column_number(coordinate)
        body_start = row.start(2)
        for cell in re.finditer(r'<c r="([A-Z]+\d+)"', row.group(2)):
            if _column_number(cell.group(1)) > column:
                at = body_start + cell.start()
                return xml[:at] + new_cell + xml[at:]
        at = row.end(2)
        return xml[:at] + new_cell + xml[at:]

    @staticmethod
    def _append_style_entry(styles: str, collection: str, entry: str) -> tuple[str, int]:
        """
        Append one entry to a counted list in styles.xml (fills, borders, cellXfs).
        Takes the styles XML, the list's element name and the entry's XML.
        Returns (the updated styles XML, the new entry's index).
        """
        block = re.search(rf'<{collection} count="(\d+)"[^>]*>.*?</{collection}>', styles, re.DOTALL)
        index = int(block.group(1))
        updated = block.group(0).replace(f"</{collection}>", entry + f"</{collection}>")
        updated = updated.replace(f'count="{index}"', f'count="{index + 1}"', 1)
        return styles[: block.start()] + updated + styles[block.end():], index

    def _derive_style(self, kind: str, style: Optional[str], change: Callable[[str, str, str], tuple[str, str, str]]) -> str:
        """
        Add (once per source style and kind) a copy of a cell style with one change applied.
        Takes a name for the change, the style index to copy (None for the default style) and a function turning
        (styles XML, the xf's start tag, the rest of the xf) into their changed versions.
        Returns the new style's index; asking again for the same kind and source returns the same index.
        """
        key = (kind, style)
        if key in self._style_cache:
            return self._style_cache[key]
        styles = self._text("xl/styles.xml")
        cell_xfs = re.search(r"<cellXfs\b[^>]*>(.*?)</cellXfs>", styles, re.DOTALL)
        source = re.findall(r"<xf\b[^>]*?(?:/>|>.*?</xf>)", cell_xfs.group(1), re.DOTALL)[int(style or 0)]
        start_tag = re.match(r"<xf\b[^>]*?/?>", source).group(0)
        styles, start_tag, rest = change(styles, start_tag, source[len(start_tag):])
        styles, xf_id = self._append_style_entry(styles, "cellXfs", start_tag + rest)
        self._write("xl/styles.xml", styles)
        self._style_cache[key] = str(xf_id)
        return str(xf_id)

    def highlighted_style(self, style: Optional[str]) -> str:
        """
        Get a copy of a cell style with a light-gray fill and a medium border, to mark one choice in a row of them.
        Takes the style index to copy (None for the default style).
        Returns the new style's index.
        """
        def change(styles: str, start_tag: str, rest: str) -> tuple[str, str, str]:
            fill = '<fill><patternFill patternType="solid"><fgColor rgb="FFBFBFBF"/><bgColor indexed="64"/></patternFill></fill>'
            styles, fill_id = self._append_style_entry(styles, "fills", fill)
            side = '<{0} style="medium"><color auto="1"/></{0}>'
            border = "<border>" + "".join(side.format(s) for s in ("left", "right", "top", "bottom")) + "<diagonal/></border>"
            styles, border_id = self._append_style_entry(styles, "borders", border)
            for name, value in (("fillId", fill_id), ("applyFill", 1), ("borderId", border_id), ("applyBorder", 1)):
                start_tag = _set_attribute(start_tag, name, str(value))
            return styles, start_tag, rest

        return self._derive_style("highlighted", style, change)

    def _alignment_style(self, kind: str, style: Optional[str], name: str, value: str) -> str:
        """
        Get a copy of a cell style with one alignment setting changed, keeping its other alignment settings.
        Takes a name for the change (the cache key), the style index to copy (None for the default style), and the
        <alignment> attribute and value to set (e.g. "horizontal", "left").
        Returns the new style's index.
        """
        def change(styles: str, start_tag: str, rest: str) -> tuple[str, str, str]:
            start_tag = _set_attribute(start_tag, "applyAlignment", "1")
            alignment = re.search(r"<alignment\b[^>]*/>", rest)
            if alignment:
                rest = rest.replace(alignment.group(0), _set_attribute(alignment.group(0), name, value), 1)
            elif start_tag.endswith("/>"):
                start_tag, rest = start_tag[:-2] + ">", f'<alignment {name}="{value}"/></xf>'
            else:
                rest = f'<alignment {name}="{value}"/>' + rest
            return styles, start_tag, rest

        return self._derive_style(kind, style, change)

    def left_aligned_style(self, style: Optional[str]) -> str:
        """
        Get a copy of a cell style aligned left horizontally, keeping its other alignment settings (vertical, wrap...).
        Takes the style index to copy (None for the default style).
        Returns the new style's index.
        """
        return self._alignment_style("left", style, "horizontal", "left")

    def wrap_text_style(self, style: Optional[str]) -> str:
        """
        Get a copy of a cell style that wraps its text, keeping its other alignment settings (horizontal, vertical...).
        Takes the style index to copy (None for the default style).
        Returns the new style's index.
        """
        return self._alignment_style("wrap", style, "wrapText", "1")

    def align_left(self, sheet: str, coordinate: str) -> None:
        """
        Left-align one cell, keeping the rest of its style.
        Takes the sheet name and cell reference.
        Returns nothing.
        """
        self.set_style(sheet, coordinate, self.left_aligned_style(self.cell_style(sheet, coordinate)))

    def wrap_cell(self, sheet: str, coordinate: str) -> None:
        """
        Make one cell wrap its text, keeping the rest of its style.
        Takes the sheet name and cell reference.
        Returns nothing.
        """
        self.set_style(sheet, coordinate, self.wrap_text_style(self.cell_style(sheet, coordinate)))

    def center_cell(self, sheet: str, coordinate: str) -> None:
        """
        Centre one cell's text horizontally and vertically, keeping the rest of its style.
        Takes the sheet name and cell reference.
        Returns nothing.
        """
        style = self._alignment_style("center-h", self.cell_style(sheet, coordinate), "horizontal", "center")
        self.set_style(sheet, coordinate, self._alignment_style("center-v", style, "vertical", "center"))

    def center_across(self, sheet: str, coordinates: list[str]) -> None:
        """
        Centre the first cell's text across a run of adjacent cells without merging them (Excel's "Center Across
        Selection"), and vertically. Takes the sheet name and the run's cell references, left to right.
        Returns nothing; the cells after the first are emptied, as the text only spans empty cells.
        """
        for coordinate in coordinates[1:]:
            self.set_cell(sheet, coordinate, None)
        for coordinate in coordinates:
            style = self.cell_style(sheet, coordinate)
            style = self._alignment_style("across-h", style, "horizontal", "centerContinuous")
            self.set_style(sheet, coordinate, self._alignment_style("center-v", style, "vertical", "center"))

    def shrink_to_fit_cell(self, sheet: str, coordinate: str) -> None:
        """
        Let one cell shrink its text to fit on its line (Excel's "Shrink to fit"), keeping the rest of its style.
        Takes the sheet name and cell reference.
        Returns nothing.
        """
        style = self.cell_style(sheet, coordinate)
        self.set_style(sheet, coordinate, self._alignment_style("shrink", style, "shrinkToFit", "1"))

    def font_style(self, style: Optional[str], points: Optional[float] = None, bold: bool = False,
                   rgb: Optional[str] = None) -> str:
        """
        Get a copy of a cell style with its font changed: a size, bold, and / or a colour, keeping the font's name and
        anything not asked for.
        Takes the style index to copy (None for the default style), the size in points, whether to make it bold, and an
        ARGB colour like "FFFF0000" (each optional).
        Returns the new style's index; the cell's own font is copied, never edited (several styles share fonts).
        """
        def put(font: str, tag: str, element: str) -> str:
            if re.search(rf"<{tag}\b[^>]*/>", font):
                return re.sub(rf"<{tag}\b[^>]*/>", element, font, count=1)
            if font.endswith("/>"):
                return font[:-2] + ">" + element + "</font>"
            return re.sub(r"(<font\b[^>]*>)", lambda m: m.group(1) + element, font, count=1)

        def change(styles: str, start_tag: str, rest: str) -> tuple[str, str, str]:
            fonts = re.search(r"<fonts\b[^>]*>(.*?)</fonts>", styles, re.DOTALL)
            font_id = re.search(r'\sfontId="(\d+)"', start_tag)
            font = re.findall(r"<font\b[^>]*?(?:/>|>.*?</font>)", fonts.group(1), re.DOTALL)[int(font_id.group(1)) if font_id else 0]
            if points is not None:
                font = put(font, "sz", f'<sz val="{points:g}"/>')
            if bold:
                font = put(font, "b", "<b/>")
            if rgb is not None:
                font = put(font, "color", f'<color rgb="{rgb}"/>')
            styles, new_font_id = self._append_style_entry(styles, "fonts", font)
            start_tag = _set_attribute(_set_attribute(start_tag, "fontId", str(new_font_id)), "applyFont", "1")
            return styles, start_tag, rest

        kind = f"font:{'' if points is None else f'{points:g}'}:{'b' if bold else ''}:{rgb or ''}"
        return self._derive_style(kind, style, change)

    def font_size_style(self, style: Optional[str], points: float) -> str:
        """
        Get a copy of a cell style whose font is a different size, keeping the font's name, weight and colour.
        Takes the style index to copy (None for the default style) and the size in points.
        Returns the new style's index.
        """
        return self.font_style(style, points=points)

    def set_font_size(self, sheet: str, coordinate: str, points: float) -> None:
        """
        Change one cell's font size, keeping the rest of its style.
        Takes the sheet name, cell reference and the size in points.
        Returns nothing.
        """
        self.set_style(sheet, coordinate, self.font_size_style(self.cell_style(sheet, coordinate), points))

    def set_row_height(self, sheet: str, row: int, points: float) -> None:
        """
        Fix a row's height (Excel never auto-fits rows with merged cells, so wrapped text there needs this).
        Takes the sheet name, the row number and the height in points.
        Returns nothing; raises KeyError if the template has no such row.
        """
        xml = self._sheet(sheet)
        start_tag = re.search(rf'<row r="{row}"(?=[\s/>])[^>]*?/?>', xml)
        if start_tag is None:
            raise KeyError(f"{sheet} row {row} not in the template")
        tag = _set_attribute(_set_attribute(start_tag.group(0), "ht", f"{points:g}"), "customHeight", "1")
        self._write(self._sheet_paths[sheet], xml[: start_tag.start()] + tag + xml[start_tag.end():])

    def fit_to_letter_page(self, sheet: str) -> None:
        """
        Set a sheet to print portrait on US Letter, scaled to one page wide and one page tall.
        Takes the sheet name.
        Returns nothing.
        """
        xml = self._sheet(sheet)
        sheet_pr = re.search(r"<sheetPr\b[^>]*?(/>|>(.*?)</sheetPr>)", xml, re.DOTALL)
        fit = '<pageSetUpPr fitToPage="1"/>'
        if sheet_pr is None:
            xml = re.sub(r"(<worksheet\b[^>]*>)", r"\1<sheetPr>" + fit + "</sheetPr>", xml, count=1)
        elif sheet_pr.group(1) == "/>":
            xml = xml[: sheet_pr.start()] + sheet_pr.group(0)[:-2] + ">" + fit + "</sheetPr>" + xml[sheet_pr.end():]
        elif "<pageSetUpPr" in sheet_pr.group(2):
            body = re.sub(r"<pageSetUpPr\b[^>]*/>", lambda m: _set_attribute(m.group(0), "fitToPage", "1"), sheet_pr.group(0))
            xml = xml[: sheet_pr.start()] + body + xml[sheet_pr.end():]
        else:
            xml = xml[: sheet_pr.end()].removesuffix("</sheetPr>") + fit + "</sheetPr>" + xml[sheet_pr.end():]

        settings = {"paperSize": LETTER_PAPER, "orientation": "portrait", "fitToWidth": "1", "fitToHeight": "1"}
        page_setup = re.search(r"<pageSetup\b[^>]*/>", xml)
        if page_setup:
            tag = page_setup.group(0)
            for name, value in settings.items():
                tag = _set_attribute(tag, name, value)
            xml = xml[: page_setup.start()] + tag + xml[page_setup.end():]
        else:
            tag = "<pageSetup " + " ".join(f'{k}="{v}"' for k, v in settings.items()) + "/>"
            margins = re.search(r"<pageMargins\b[^>]*/>", xml)
            at = margins.end() if margins else xml.index("</sheetData>") + len("</sheetData>")
            xml = xml[:at] + tag + xml[at:]
        self._write(self._sheet_paths[sheet], xml)

    def show_only(self, sheets: list[str]) -> None:
        """
        Hide every sheet except the given ones, and open the workbook on the first of them.
        Takes the sheet names to leave visible, in the order wanted (the first becomes the active tab).
        Returns nothing.
        """
        workbook = self._text("xl/workbook.xml")

        def visibility(match: re.Match) -> str:
            tag = re.sub(r'\sstate="[^"]*"', "", match.group(0))
            name = re.search(r'name="([^"]+)"', tag).group(1)
            return tag if name in sheets else _set_attribute(tag, "state", "hidden")

        workbook = re.sub(r"<sheet [^>]*/>", visibility, workbook)
        active = str(self._sheet_order.index(sheets[0]))
        workbook = re.sub(r"<workbookView\b[^>]*?/?>",
                          lambda m: _set_attribute(_set_attribute(m.group(0), "activeTab", active), "firstSheet", "0"),
                          workbook, count=1)
        self._write("xl/workbook.xml", workbook)

        for name, part in self._sheet_paths.items():
            xml = self._text(part)
            cleared = xml.replace(' tabSelected="1"', "")
            if name == sheets[0]:
                cleared = re.sub(r"<sheetView\b[^>]*?(?=/?>)", lambda m: m.group(0) + ' tabSelected="1"', cleared, count=1)
            if cleared != xml:
                self._write(part, cleared)

    def move_sheet(self, sheet: str, after: str) -> None:
        """
        Move a sheet's tab to directly after another, keeping sheet-scoped names (print areas) on their sheets.
        Takes the sheet to move and the sheet it should follow.
        Returns nothing.
        """
        old_order = list(self._sheet_order)
        new_order = [name for name in old_order if name != sheet]
        new_order.insert(new_order.index(after) + 1, sheet)
        new_index = {old_order.index(name): new_order.index(name) for name in old_order}

        workbook = self._text("xl/workbook.xml")
        tags = {re.search(r'name="([^"]+)"', tag).group(1): tag for tag in re.findall(r"<sheet [^>]*/>", workbook)}
        sheets_block = re.search(r"<sheets>.*?</sheets>", workbook, re.DOTALL)
        workbook = (workbook[: sheets_block.start()] + "<sheets>" + "".join(tags[n] for n in new_order) + "</sheets>"
                    + workbook[sheets_block.end():])
        workbook = re.sub(r'(<definedName\b[^>]*?\slocalSheetId=")(\d+)"',
                          lambda m: f'{m.group(1)}{new_index[int(m.group(2))]}"', workbook)
        self._write("xl/workbook.xml", workbook)
        self._sheet_order = new_order

    def _drop_calc_chain(self) -> None:
        """
        Remove the calculation chain, which lists formula cells and goes stale once a formula is overwritten with a value
        (Excel then reports the file as damaged). Excel rebuilds it on open.
        Takes nothing.
        Returns nothing.
        """
        if "xl/calcChain.xml" not in self._parts:
            return
        del self._parts["xl/calcChain.xml"]
        self._infos = [info for info in self._infos if info.filename != "xl/calcChain.xml"]
        rels = re.sub(r"<Relationship [^>]*calcChain[^>]*/>", "", self._text("xl/_rels/workbook.xml.rels"))
        self._write("xl/_rels/workbook.xml.rels", rels)
        types = re.sub(r'<Override PartName="/xl/calcChain.xml"[^>]*/>', "", self._text("[Content_Types].xml"))
        self._write("[Content_Types].xml", types)

    def to_bytes(self) -> bytes:
        """
        Serialize the package back into an .xlsx file, without the calculation chain.
        Takes nothing.
        Returns the file's bytes.
        """
        self._drop_calc_chain()
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
            for info in self._infos:
                package.writestr(info, self._parts[info.filename])
        return buffer.getvalue()
