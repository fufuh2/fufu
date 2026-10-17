"""Pure cost and margin rules. All money math uses Decimal; nothing here touches the DB."""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

MONEY_Q = Decimal("0.01")
UNIT_COST_Q = Decimal("0.000001")
QTY_Q = Decimal("0.0001")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_Q, rounding=ROUND_HALF_EVEN)


def unit_cost(value: Decimal) -> Decimal:
    return value.quantize(UNIT_COST_Q, rounding=ROUND_HALF_EVEN)


def qty(value: Decimal) -> Decimal:
    return value.quantize(QTY_Q, rounding=ROUND_HALF_EVEN)


def moving_average_cost(
    on_hand: Decimal, current_avg: Decimal, received_qty: Decimal, received_unit_cost: Decimal
) -> Decimal:
    """Weighted moving average after a receipt.

    If on-hand stock is zero or negative (sales recorded before the delivery was booked),
    the old average carries no information about stock physically present, so the receipt
    cost becomes the new average.
    """
    if received_qty <= 0:
        raise ValueError("received quantity must be positive")
    if received_unit_cost < 0:
        raise ValueError("unit cost cannot be negative")
    if on_hand <= 0:
        return unit_cost(received_unit_cost)
    total = on_hand * current_avg + received_qty * received_unit_cost
    return unit_cost(total / (on_hand + received_qty))


def gross_quantity(net_quantity: Decimal, loss_pct: Decimal) -> Decimal:
    """Quantity to issue from stock so that `net_quantity` remains after preparation loss."""
    if not (Decimal("0") <= loss_pct < Decimal("100")):
        raise ValueError("loss_pct must be in [0, 100)")
    return qty(net_quantity / (Decimal("1") - loss_pct / Decimal("100")))


@dataclass(frozen=True)
class Margin:
    net_revenue: Decimal
    cogs: Decimal | None
    gross_profit: Decimal | None
    margin_pct: Decimal | None
    status: str  # "ok" | "undefined_zero_revenue" | "incomplete_cost"


def gross_margin(net_revenue: Decimal, cogs: Decimal | None) -> Margin:
    """Gross profit = net revenue - COGS; margin % = gross profit / net revenue * 100.

    COGS None means at least one sale had no costable recipe: we refuse to report a profit
    that silently treats unknown cost as zero.
    """
    if cogs is None:
        return Margin(money(net_revenue), None, None, None, "incomplete_cost")
    profit = money(net_revenue - cogs)
    if net_revenue == 0:
        return Margin(money(net_revenue), money(cogs), profit, None, "undefined_zero_revenue")
    pct = (profit / net_revenue * Decimal("100")).quantize(MONEY_Q, rounding=ROUND_HALF_EVEN)
    return Margin(money(net_revenue), money(cogs), profit, pct, "ok")
