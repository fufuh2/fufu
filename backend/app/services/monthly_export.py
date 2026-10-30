"""Excel export of a monthly count sheet.

Expected and difference cells are real Excel formulas, so the file stays correct if
someone edits a number in Excel; the same values are computed by the API (domain.monthly).
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
    "Satılan / Kullanılan",
    "Beklenen",
    "Sayılan",
    "Fark (+fazla / −eksik)",
    "Durum",
    "Birim fiyat (₺)",
    "Fark tutarı (₺)",
]
WIDTHS = [26, 40, 8, 12, 12, 18, 12, 12, 20, 12, 15, 16]
THIN = Side(style="thin", color="BBBBBB")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
QTY_FMT = "#,##0.###"
MONEY_FMT = '#,##0.00 "₺"'
STATUS_TR = {"fazla": "FAZLA", "eksik": "EKSİK", "tamam": "TAMAM", "sayilmadi": "SAYILMADI"}


def _num(v: Decimal | None) -> float | None:
    # openpyxl writes floats; values are already rounded to 3 (qty) / 2 (money) decimals.
    return None if v is None else float(v)


def build_workbook(sheet: dict[str, Any], restaurant: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = f"SAYIM {sheet['period']}"
    bold = Font(bold=True)
    ws["A1"] = f"{restaurant.upper()} — AYLIK SAYIM LİSTESİ"
    ws["A1"].font = Font(bold=True, size=14)
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
        ws.cell(i, 1, k).font = bold
        ws.cell(i, 2, v)
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
        gc = ws.cell(r, 1, group["name"].upper())
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
                _num(line["used"]),
                f"=D{r}+E{r}-F{r}",
                _num(line["counted"]),
                f'=IF(H{r}="","",H{r}-G{r})',
                STATUS_TR[line["status"]],
                _num(line["unit_price"]),
                f'=IF(OR(I{r}="",K{r}=""),"",I{r}*K{r})',
            ]
            for col, v in enumerate(values, start=1):
                c = ws.cell(r, col, v)
                c.border = BOX
                if col in (4, 5, 6, 7, 8, 9):
                    c.number_format = QTY_FMT
                if col in (11, 12):
                    c.number_format = MONEY_FMT
            r += 1
    last = r - 1
    red = PatternFill("solid", fgColor="F8CBAD")
    green = PatternFill("solid", fgColor="C6EFCE")
    rng = f"I{first}:I{last}"
    ws.conditional_formatting.add(rng, CellIsRule(operator="lessThan", formula=["0"], fill=red))
    ws.conditional_formatting.add(
        rng, CellIsRule(operator="greaterThan", formula=["0"], fill=green)
    )
    r += 1
    t = sheet["totals"]
    summary = [
        ("Toplam ürün", t["items"]),
        ("Sayılan", t["counted"]),
        ("Fazla", t["fazla"]),
        ("Eksik", t["eksik"]),
        ("Tamam", t["tamam"]),
        ("Fazla tutarı (₺)", _num(t["fazla_value"])),
        ("Eksik tutarı (₺)", _num(t["eksik_value"])),
        ("Net fark (₺)", _num(t["net_difference_value"])),
        ("Sayılan stok değeri (₺)", _num(t["stock_value"])),
        ("Not", sheet["value_coverage"]),
    ]
    for k, v in summary:
        ws.cell(r, 2, k).font = bold
        c = ws.cell(r, 3, v)
        if isinstance(v, float):
            c.number_format = MONEY_FMT
        r += 1
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.print_title_rows = f"{head_row}:{head_row}"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
