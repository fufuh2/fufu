"""Independent verification of stock and cost arithmetic.

The reference model below re-implements the documented rules (docs/architecture/FORMULAS.md)
without importing anything from app.domain or app.services. Hypothesis drives random
operation sequences through the HTTP API and the model, and every observable value must
match exactly.
"""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import StockBalance, StockMovement
from tests.conftest import new_tenant

C6 = Decimal("0.000001")
C2 = Decimal("0.01")
BEEF_PER_BURGER = Decimal("150")  # g
BUN_PER_BURGER = Decimal("1")


def r6(x: Decimal) -> Decimal:
    return x.quantize(C6, rounding=ROUND_HALF_EVEN)


def r2(x: Decimal) -> Decimal:
    return x.quantize(C2, rounding=ROUND_HALF_EVEN)


class Model:
    def __init__(self) -> None:
        self.qty = {"beef": Decimal(0), "bun": Decimal(0)}
        self.avg = {"beef": Decimal(0), "bun": Decimal(0)}
        self.revenue = Decimal(0)
        self.cogs = Decimal(0)

    def receive(self, item: str, q: Decimal, cost: Decimal) -> None:
        if self.qty[item] <= 0:
            self.avg[item] = r6(cost)
        else:
            self.avg[item] = r6((self.qty[item] * self.avg[item] + q * cost) / (self.qty[item] + q))
        self.qty[item] += q

    def waste(self, item: str, q: Decimal) -> bool:
        if self.qty[item] - q < 0:
            return False
        self.qty[item] -= q
        return True

    def order(self, n: int, price: Decimal) -> None:
        self.revenue += price * n
        self.cogs += r2(
            n * BEEF_PER_BURGER * self.avg["beef"] + n * BUN_PER_BURGER * self.avg["bun"]
        )
        self.qty["beef"] -= n * BEEF_PER_BURGER
        self.qty["bun"] -= n * BUN_PER_BURGER

    def count(self, item: str, counted: Decimal) -> None:
        diff = counted - self.qty[item]
        if diff > 0 and self.qty[item] <= 0:
            pass  # surplus valued at current average; average unchanged either way
        self.qty[item] = counted


qty = st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("5000"), places=4)
cost = st.decimals(min_value=Decimal("0"), max_value=Decimal("50"), places=4)
op = st.one_of(
    st.tuples(st.just("receive"), st.sampled_from(["beef", "bun"]), qty, cost),
    st.tuples(st.just("waste"), st.sampled_from(["beef", "bun"]), qty),
    st.tuples(st.just("order"), st.integers(min_value=1, max_value=20)),
    st.tuples(
        st.just("count"),
        st.sampled_from(["beef", "bun"]),
        st.decimals(min_value=Decimal("0"), max_value=Decimal("5000"), places=4),
    ),
)


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(ops=st.lists(op, min_size=1, max_size=15))
def test_api_matches_reference_model(client, session: Session, ops: list) -> None:
    api = new_tenant(session, client)
    branch = api.post("/branches", {"name": "B"}).json()["id"]
    ids = {
        "beef": api.post("/items", {"sku": "BEEF", "name": "b", "base_unit": "g"}).json()["id"],
        "bun": api.post("/items", {"sku": "BUN", "name": "u", "base_unit": "pcs"}).json()["id"],
    }
    unit = {"beef": "g", "bun": "pcs"}
    price = Decimal("250.00")
    burger = api.post("/products", {"sku": "BG", "name": "g", "price": str(price)}).json()["id"]
    api.post(
        f"/products/{burger}/recipes",
        {
            "lines": [
                {"item_id": ids["beef"], "quantity": str(BEEF_PER_BURGER), "unit": "g"},
                {"item_id": ids["bun"], "quantity": "1", "unit": "pcs"},
            ]
        },
    )
    m = Model()
    start = datetime.now(UTC) - timedelta(minutes=1)

    for i, o in enumerate(ops):
        kind = o[0]
        if kind == "receive":
            _, item, q, c = o
            res = api.post(
                f"/branches/{branch}/receipts",
                {
                    "lines": [
                        {
                            "item_id": ids[item],
                            "quantity": str(q),
                            "unit": unit[item],
                            "unit_cost": str(c),
                        }
                    ]
                },
            )
            assert res.status_code == 201, res.text
            m.receive(item, q, c)
        elif kind == "waste":
            _, item, q = o
            res = api.post(
                f"/branches/{branch}/waste",
                {"item_id": ids[item], "quantity": str(q), "unit": unit[item], "reason": "unknown"},
            )
            expected_ok = m.waste(item, q)
            assert res.status_code == (201 if expected_ok else 409), res.text
        elif kind == "order":
            res = api.post(
                f"/branches/{branch}/orders",
                {"lines": [{"product_id": burger, "quantity": o[1]}]},
                headers={"Idempotency-Key": f"o{i}"},
            )
            assert res.status_code == 201, res.text
            m.order(o[1], price)
        else:
            _, item, c = o
            res = api.post(
                f"/branches/{branch}/counts",
                {"lines": [{"item_id": ids[item], "quantity": str(c), "unit": unit[item]}]},
            )
            assert res.status_code == 201, res.text
            m.count(item, c)

        stock = {r["sku"]: r for r in api.get(f"/branches/{branch}/stock").json()}
        for item, sku in (("beef", "BEEF"), ("bun", "BUN")):
            if stock[sku]["quantity"] is None:
                assert m.qty[item] == 0
                continue
            assert Decimal(stock[sku]["quantity"]) == m.qty[item], (item, ops[: i + 1])
            assert Decimal(stock[sku]["avg_cost"]) == m.avg[item], (item, ops[: i + 1])

    end = datetime.now(UTC) + timedelta(minutes=1)
    margin = api.get(
        f"/branches/{branch}/reports/margin",
        params={"from": start.isoformat(), "to": end.isoformat()},
    ).json()["total"]
    assert Decimal(margin["net_revenue"]) == r2(m.revenue)
    assert Decimal(margin["cogs"]) == r2(m.cogs)
    assert Decimal(margin["gross_profit"]) == r2(m.revenue - m.cogs)
    if m.revenue:
        assert Decimal(margin["margin_pct"]) == r2((r2(m.revenue - m.cogs)) / m.revenue * 100)

    # Ledger reconciliation computed straight from the tables
    for bal in session.scalars(select(StockBalance).where(StockBalance.branch_id == branch)):
        total = session.scalar(
            select(func.sum(StockMovement.quantity_base)).where(
                StockMovement.branch_id == bal.branch_id, StockMovement.item_id == bal.item_id
            )
        )
        assert (total or 0) == bal.quantity
