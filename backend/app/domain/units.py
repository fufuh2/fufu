"""Unit-of-measure conversion.

Every item stores quantities in exactly one base unit (g, ml or pcs). Standard units convert
within their own dimension; anything else (e.g. "box" -> pcs, "pcs" -> g) needs an explicit
item-specific conversion. Cross-dimension conversion is never guessed.
"""

from collections.abc import Mapping
from decimal import Decimal

BASE_UNITS = frozenset({"g", "ml", "pcs"})

# unit -> (base unit, factor to base)
STANDARD_UNITS: dict[str, tuple[str, Decimal]] = {
    "g": ("g", Decimal("1")),
    "kg": ("g", Decimal("1000")),
    "ml": ("ml", Decimal("1")),
    "l": ("ml", Decimal("1000")),
    "pcs": ("pcs", Decimal("1")),
}


class ConversionError(ValueError):
    pass


def to_base(
    quantity: Decimal,
    unit: str,
    item_base_unit: str,
    item_conversions: Mapping[str, Decimal] | None = None,
) -> Decimal:
    """Convert `quantity` of `unit` into the item's base unit."""
    if item_base_unit not in BASE_UNITS:
        raise ConversionError(f"invalid base unit: {item_base_unit}")
    if item_conversions and unit in item_conversions:
        factor = item_conversions[unit]
        if factor <= 0:
            raise ConversionError(f"non-positive conversion factor for {unit}")
        return quantity * factor
    standard = STANDARD_UNITS.get(unit)
    if standard is None:
        raise ConversionError(f"unknown unit '{unit}' for this item")
    base, factor = standard
    if base != item_base_unit:
        raise ConversionError(f"cannot convert {unit} ({base}) to {item_base_unit}")
    return quantity * factor
