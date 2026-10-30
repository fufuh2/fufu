"""Monthly count arithmetic. Pure functions, Decimal only.

expected   = opening + incoming - recipe_used - used - waste
             recipe_used: satılan menü ürünleri × reçete miktarı (otomatik)
             used: elle girilen diğer kullanım; waste: zayi
difference = counted - expected            (None until the item is counted)
status     = "fazla" (> 0) | "eksik" (< 0) | "tamam" (= 0) | "sayilmadi" (not counted)
value      = difference * unit_price       (None when the price is unknown)
waste_value = waste * unit_price           (recorded loss, shown separately from the difference)
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

Q3 = Decimal("0.001")
Q2 = Decimal("0.01")


@dataclass(frozen=True)
class LineResult:
    expected: Decimal
    difference: Decimal | None
    status: str
    difference_value: Decimal | None
    counted_value: Decimal | None
    waste_value: Decimal | None = None


def compute_line(
    opening: Decimal,
    incoming: Decimal,
    used: Decimal,
    counted: Decimal | None,
    unit_price: Decimal | None,
    waste: Decimal = Decimal("0"),
    recipe_used: Decimal = Decimal("0"),
) -> LineResult:
    expected = (opening + incoming - recipe_used - used - waste).quantize(
        Q3, rounding=ROUND_HALF_EVEN
    )
    waste_value = (
        None if unit_price is None else (waste * unit_price).quantize(Q2, rounding=ROUND_HALF_EVEN)
    )
    if counted is None:
        return LineResult(expected, None, "sayilmadi", None, None, waste_value)
    diff = (counted - expected).quantize(Q3, rounding=ROUND_HALF_EVEN)
    status = "fazla" if diff > 0 else "eksik" if diff < 0 else "tamam"
    if unit_price is None:
        return LineResult(expected, diff, status, None, None, None)
    return LineResult(
        expected,
        diff,
        status,
        (diff * unit_price).quantize(Q2, rounding=ROUND_HALF_EVEN),
        (counted * unit_price).quantize(Q2, rounding=ROUND_HALF_EVEN),
        waste_value,
    )


def parse_period(text: str) -> tuple[int, int]:
    """'2026-10' -> (2026, 10). Raises ValueError for anything else."""
    parts = text.split("-")
    if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2:
        raise ValueError("period must look like YYYY-MM")
    year, month = int(parts[0]), int(parts[1])
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        raise ValueError("period is out of range")
    return year, month


# Units that convert into each other: (dimension, size in the smallest unit).
_MEASURE = {"KG": ("mass", 1000), "GR": ("mass", 1), "LT": ("volume", 1000), "ML": ("volume", 1)}


def to_item_units(
    qty: Decimal,
    unit: str,
    item_unit: str,
    content_qty: Decimal | None = None,
    content_unit: str | None = None,
) -> Decimal | None:
    """A recipe quantity expressed in the unit the item is counted in, or None when the two
    units cannot be converted. Package items (counted in ADET, KOVA…) convert through their
    content, e.g. 1 ADET = 9.5 KG makes 20 GR = 20 / 9500 ADET."""
    if unit == item_unit:
        return qty
    m = _MEASURE.get(unit)
    target = _MEASURE.get(item_unit)
    if m and target and m[0] == target[0]:
        return qty * m[1] / target[1]
    if content_qty and content_qty > 0 and content_unit:
        c = _MEASURE.get(content_unit)
        if content_unit == unit:
            return qty / content_qty
        if m and c and m[0] == c[0]:
            return qty * m[1] / (c[1] * content_qty)
    return None
