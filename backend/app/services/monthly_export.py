"""Excel export of a monthly count sheet.

Expected, difference, status and value cells are real Excel formulas rounded like the API
(3 decimals for quantities, 2 for money), so the line stays correct if someone edits a number
in Excel. The summary block below the table is a snapshot taken when the file was created.
Text from users is always written as text, never as a formula.
"""

from datetime import date
from decimal import Decimal
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HEADERS = [
    "Ürün grubu",
    "Malzeme",
    "Birim",
    "Devir",
    "Gelen",
    "Satıştan (reçete)",
    "Satılan / Kullanılan (elle)",
    "Zayi",
    "Beklenen",
    "Sayılan",
    "Fark (+fazla / −eksik)",
    "Durum",
    "Birim fiyat (₺)",
    "Fark tutarı (₺)",
    "Zayi tutarı (₺)",
]
WIDTHS = [26, 40, 8, 12, 12, 16, 18, 10, 12, 12, 20, 12, 15, 16, 16]
THIN = Side(style="thin", color="BBBBBB")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
QTY_FMT = "#,##0.###"
MONEY_FMT = '#,##0.00 "₺"'


def _put(ws: Any, row: int, col: int, value: Any) -> Any:
    """Writes a cell; strings are forced to text so '=HYPERLINK(...)' in a name stays inert."""
    c = ws.cell(row, col, value)
    if isinstance(value, str) and not getattr(value, "is_formula", False):
        c.data_type = "s"
    return c


class _F(str):
    """A formula the export itself wrote."""

    is_formula = True


def _num(v: Decimal | None) -> float | None:
    # openpyxl writes floats; values are already rounded to 3 (qty) / 2 (money) decimals.
    return None if v is None else float(v)


def build_workbook(sheet: dict[str, Any], restaurant: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = f"SAYIM {sheet['period']}"
    bold = Font(bold=True)
    _put(ws, 1, 1, f"{restaurant.upper()} — AYLIK SAYIM LİSTESİ").font = Font(bold=True, size=14)
    meta = [
        ("Dönem", sheet["period"]),
        (
            "Sayım tarihi",
            sheet["count_date"].isoformat()
            if isinstance(sheet["count_date"], date)
            else (sheet["count_date"] or ""),
        ),
        ("Sayımı yapan", sheet["counted_by_name"] or ""),
        ("Durum", "KAPATILDI" if sheet["status"] == "closed" else "TASLAK"),
        ("Devir kaynağı", sheet["opening_from"] or "elle girildi (önceki kapanmış ay yok)"),
    ]
    for i, (k, v) in enumerate(meta, start=2):
        _put(ws, i, 1, k).font = bold
        _put(ws, i, 2, v)
    head_row = len(meta) + 3
    for col, title in enumerate(HEADERS, start=1):
        c = ws.cell(head_row, col, title)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E79")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BOX
        ws.column_dimensions[get_column_letter(col)].width = WIDTHS[col - 1]
    ws.freeze_panes = ws.cell(head_row + 1, 3)
    r = head_row + 1
    first = r
    for group in sheet["groups"]:
        gc = _put(ws, r, 1, group["name"].upper())
        gc.font = bold
        gc.fill = PatternFill("solid", fgColor="DDEBF7")
        for col in range(1, len(HEADERS) + 1):
            ws.cell(r, col).fill = PatternFill("solid", fgColor="DDEBF7")
        r += 1
        for line in group["lines"]:
            values = [
                group["name"],
                line["name"],
                line["unit"],
                _num(line["opening"]),
                _num(line["incoming"]),
                _num(line["recipe_used"]),
                _num(line["used"]),
                _num(line["waste"]),
                _F(f"=ROUND(D{r}+E{r}-F{r}-G{r}-H{r},3)"),
                _num(line["counted"]),
                _F(f'=IF(J{r}="","",ROUND(J{r}-I{r},3))'),
                _F(f'=IF(K{r}="","SAYILMADI",IF(K{r}>0,"FAZLA",IF(K{r}<0,"EKSİK","TAMAM")))'),
                _num(line["unit_price"]),
                _F(f'=IF(OR(K{r}="",M{r}=""),"",ROUND(K{r}*M{r},2))'),
                _F(f'=IF(M{r}="","",ROUND(H{r}*M{r},2))'),
            ]
            if len(values) != len(HEADERS):
                # A silent shift would put numbers under the wrong headings.
                raise RuntimeError("export columns do not match headers")
            for col, v in enumerate(values, start=1):
                c = _put(ws, r, col, v)
                c.border = BOX
                if col in (4, 5, 6, 7, 8, 9, 10, 11):
                    c.number_format = QTY_FMT
                if col in (13, 14, 15):
                    c.number_format = MONEY_FMT
            r += 1
    last = r - 1
    red = PatternFill("solid", fgColor="F8CBAD")
    green = PatternFill("solid", fgColor="C6EFCE")
    rng = f"K{first}:K{last}"
    ws.conditional_formatting.add(rng, CellIsRule(operator="lessThan", formula=["0"], fill=red))
    ws.conditional_formatting.add(
        rng, CellIsRule(operator="greaterThan", formula=["0"], fill=green)
    )
    r += 1
    t = sheet["totals"]
    _put(ws, r, 2, "Özet (dosya oluşturulduğu andaki değerler)").font = bold
    r += 1
    summary = [
        ("Toplam ürün", t["items"]),
        ("Sayılan", t["counted"]),
        ("Fazla", t["fazla"]),
        ("Eksik", t["eksik"]),
        ("Tamam", t["tamam"]),
        ("Fazla tutarı (₺)", _num(t["fazla_value"])),
        ("Eksik tutarı (₺)", _num(t["eksik_value"])),
        ("Net fark (₺)", _num(t["net_difference_value"])),
        ("Zayi olan ürün", t["waste_lines"]),
        ("Zayi tutarı (₺)", _num(t["waste_value"])),
        ("Sayılan stok değeri (₺)", _num(t["stock_value"])),
        ("Not", sheet["value_coverage"]),
    ]
    for k, v in summary:
        _put(ws, r, 2, k).font = bold
        c = _put(ws, r, 3, v)
        if isinstance(v, float):
            c.number_format = MONEY_FMT
        r += 1
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.print_title_rows = f"{head_row}:{head_row}"
    sales = wb.create_sheet("Satışlar")
    _put(sales, 1, 1, "Menü ürünü").font = bold
    _put(sales, 1, 2, "Bu ay satılan").font = bold
    for i, sale in enumerate(sheet["sales"], start=2):
        _put(sales, i, 1, sale["name"])
        _put(sales, i, 2, _num(sale["quantity"]))
    sales.column_dimensions["A"].width = 40
    sales.column_dimensions["B"].width = 16
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
