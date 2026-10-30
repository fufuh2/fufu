"""Monthly count arithmetic. Pure functions, Decimal only.

expected   = opening + incoming - used - waste   (zayi düşülür)
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
) -> LineResult:
    expected = (opening + incoming - used - waste).quantize(Q3, rounding=ROUND_HALF_EVEN)
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
