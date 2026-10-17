"""Race conditions against a real PostgreSQL: each worker uses its own connection."""

import threading
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_sessionmaker
from app.errors import InsufficientStock
from app.models import Order, StockMovement
from app.services import inventory, orders
from app.services.admin import load_actor
from tests.conftest import new_tenant, setup_kitchen, stock_of


def _owner_actor(session: Session):  # type: ignore[no-untyped-def]
    from app.models import User

    user = session.scalars(select(User).where(User.role == "owner")).first()
    return load_actor(session, user.id)


def _run_parallel(n: int, fn) -> list[object]:  # type: ignore[no-untyped-def]
    barrier = threading.Barrier(n)
    results: list[object] = [None] * n

    def worker(i: int) -> None:
        with get_sessionmaker()() as s:
            barrier.wait()
            try:
                results[i] = fn(s, i)
            except Exception as exc:  # noqa: BLE001 - collected for assertions
                results[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results


def test_concurrent_waste_cannot_overdraw(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    actor = _owner_actor(session)
    branch, bun = uuid.UUID(k["branch"]), uuid.UUID(k["bun"])

    # 30 buns on hand; 8 workers each try to waste 6 -> at most 5 can succeed.
    results = _run_parallel(
        8,
        lambda s, i: inventory.record_waste(
            s, actor, branch, bun, Decimal("6"), "pcs", "spill_accident", None, None
        ),
    )
    ok = [r for r in results if not isinstance(r, Exception)]
    failed = [r for r in results if isinstance(r, Exception)]
    assert len(ok) == 5
    assert all(isinstance(e, InsufficientStock) for e in failed)
    assert stock_of(api, k["branch"], k["bun"]) == Decimal("0")


def test_concurrent_orders_consume_exactly(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    actor = _owner_actor(session)
    n = 12

    def place(s: Session, i: int) -> object:
        return orders.create_order(
            s,
            actor,
            orders.OrderIn(
                branch_id=uuid.UUID(k["branch"]),
                idempotency_key=f"conc-{i}",
                channel="test",
                external_ref=None,
                ordered_at=None,
                lines=[orders.OrderLineIn(uuid.UUID(k["burger"]), 1)],
            ),
        )

    results = _run_parallel(n, place)
    assert not [r for r in results if isinstance(r, Exception)], results
    assert stock_of(api, k["branch"], k["beef"]) == Decimal("10000") - 150 * n
    assert stock_of(api, k["branch"], k["bun"]) == Decimal("30") - n
    # balance_after values form an unbroken chain (no lost update)
    afters = sorted(
        session.scalars(
            select(StockMovement.balance_after).where(
                StockMovement.item_id == uuid.UUID(k["bun"]),
                StockMovement.movement_type == "sale_consumption",
            )
        )
    )
    assert afters == [Decimal(30 - i) for i in range(n, 0, -1)]


def test_concurrent_same_idempotency_key_creates_one_order(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    actor = _owner_actor(session)

    def place(s: Session, i: int) -> object:
        return orders.create_order(
            s,
            actor,
            orders.OrderIn(
                branch_id=uuid.UUID(k["branch"]),
                idempotency_key="same-key",
                channel="pos",
                external_ref="POS-1",
                ordered_at=None,
                lines=[orders.OrderLineIn(uuid.UUID(k["burger"]), 2)],
            ),
        )

    results = _run_parallel(6, place)
    assert not [r for r in results if isinstance(r, Exception)], results
    ids = {r[0].id for r in results}  # type: ignore[index]
    assert len(ids) == 1
    assert sum(1 for r in results if r[1]) == 1  # type: ignore[index]
    assert session.scalar(select(func.count()).select_from(Order)) == 1
    assert stock_of(api, k["branch"], k["bun"]) == Decimal("28")
