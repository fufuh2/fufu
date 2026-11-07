"""Full backup of one business (tenant) as a JSON file, and restore into a fresh database.

The file holds everything needed to continue on another database host: branches, users
(with password hashes, so logins keep working), products, prices, recipes, every month's
count and sales, and the audit log. Restore is only allowed into a business that has no
branches and no products yet, i.e. right after the first owner was created on a new database.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Table, func, insert, select
from sqlalchemy.orm import Session

from app.domain.permissions import Perm
from app.errors import Conflict, Invalid
from app.models import (
    AuditEvent,
    Branch,
    CountItem,
    DailySale,
    Employee,
    Expense,
    MenuProduct,
    MonthlyCount,
    MonthlyCountLine,
    MonthlySale,
    PurchaseReceipt,
    PurchaseReceiptLine,
    RecipeLine,
    Shift,
    Supplier,
    Tenant,
    User,
    UserBranchAccess,
)
from app.security import Actor
from app.services import audit

FORMAT = "taffburger-sayim-yedek"
FORMAT_VERSION = 1
# Restore order: parents before children.
TABLES: list[Table] = [
    Branch.__table__,  # type: ignore[list-item]
    User.__table__,  # type: ignore[list-item]
    UserBranchAccess.__table__,  # type: ignore[list-item]
    CountItem.__table__,  # type: ignore[list-item]
    MenuProduct.__table__,  # type: ignore[list-item]
    RecipeLine.__table__,  # type: ignore[list-item]
    MonthlyCount.__table__,  # type: ignore[list-item]
    MonthlyCountLine.__table__,  # type: ignore[list-item]
    MonthlySale.__table__,  # type: ignore[list-item]
    Supplier.__table__,  # type: ignore[list-item]
    PurchaseReceipt.__table__,  # type: ignore[list-item]
    PurchaseReceiptLine.__table__,  # type: ignore[list-item]
    DailySale.__table__,  # type: ignore[list-item]
    Expense.__table__,  # type: ignore[list-item]
    Employee.__table__,  # type: ignore[list-item]
    Shift.__table__,  # type: ignore[list-item]
    AuditEvent.__table__,  # type: ignore[list-item]
]
# Autoincrement ids nothing points to; a restore lets the new database number them.
FRESH_IDS = {
    "monthly_count_lines",
    "recipe_lines",
    "audit_events",
    "purchase_receipt_lines",
    "daily_sales",
    "shifts",
}


def _plain(v: Any) -> Any:
    if isinstance(v, uuid.UUID | Decimal):
        return str(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    return v


def _rows(session: Session, table: Table, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    c = table.c
    if "tenant_id" in c:
        stmt = select(table).where(c.tenant_id == tenant_id)
    elif table.name == "user_branch_access":
        stmt = select(table).where(
            c.user_id.in_(select(User.id).where(User.tenant_id == tenant_id))
        )
    elif table.name == "recipe_lines":
        stmt = select(table).where(
            c.product_id.in_(select(MenuProduct.id).where(MenuProduct.tenant_id == tenant_id))
        )
    elif table.name == "purchase_receipt_lines":
        stmt = select(table).where(
            c.receipt_id.in_(
                select(PurchaseReceipt.id).where(PurchaseReceipt.tenant_id == tenant_id)
            )
        )
    else:  # monthly_count_lines, monthly_sales
        stmt = select(table).where(
            c.count_id.in_(select(MonthlyCount.id).where(MonthlyCount.tenant_id == tenant_id))
        )
    return [{k: _plain(v) for k, v in r.items()} for r in session.execute(stmt).mappings()]


def export(session: Session, actor: Actor) -> dict[str, Any]:
    actor.require(Perm.USER_MANAGE)
    tenant = session.get(Tenant, actor.tenant_id)
    if tenant is None:
        raise Invalid("işletme bulunamadı")
    revision = session.execute(select(func.max(_alembic_version()))).scalar()
    tables = {t.name: _rows(session, t, actor.tenant_id) for t in TABLES}
    data: dict[str, Any] = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "schema_revision": revision,
        "created_at": datetime.now(UTC).isoformat(),
        "tenant": {"name": tenant.name, "currency": tenant.currency},
        "tables": tables,
    }
    audit.record(
        session,
        actor,
        "backup.export",
        "tenant",
        actor.tenant_id,
        {"counts": {k: len(v) for k, v in tables.items()}},
    )
    session.commit()
    return data


def _alembic_version() -> Any:
    from sqlalchemy import column, table

    return table("alembic_version", column("version_num")).c.version_num


def _value(col: Any, v: Any) -> Any:
    if v is None:
        return None
    t = col.type.python_type
    if t is uuid.UUID:
        return uuid.UUID(v)
    if t is Decimal:
        return Decimal(v)
    if t is datetime:
        return datetime.fromisoformat(v)
    if t is date:
        return date.fromisoformat(v)
    return v


def restore(session: Session, actor: Actor, data: dict[str, Any]) -> dict[str, int]:
    """One transaction: either the whole backup is restored or nothing is."""
    actor.require(Perm.USER_MANAGE)
    if data.get("format") != FORMAT or data.get("format_version") != FORMAT_VERSION:
        raise Invalid("bu dosya bir sayım yedeği değil")
    current = session.execute(select(func.max(_alembic_version()))).scalar()
    if data.get("schema_revision") != current:
        raise Invalid(
            f"yedek farklı bir sistem sürümünden ({data.get('schema_revision')}); "
            f"bu sistem {current}. Yedeği aynı sürüme geri yükleyin."
        )
    for model in (Branch, CountItem):
        if session.scalar(
            select(func.count()).select_from(model).where(model.tenant_id == actor.tenant_id)
        ):
            raise Conflict(
                "bu işletmede zaten şube veya ürün var; yedek yalnızca boş bir sisteme "
                "(yeni veritabanında ilk giriş) geri yüklenir"
            )
    tables = data.get("tables") or {}
    # Users already here (the owner who just logged in) keep their account and password;
    # the backup's rows for the same e-mail are mapped onto them.
    existing = {
        u.email: u.id
        for u in session.scalars(select(User).where(User.tenant_id == actor.tenant_id))
    }
    user_map: dict[str, uuid.UUID] = {}
    for u in tables.get("users", []):
        if u["email"] in existing:
            user_map[u["id"]] = existing[u["email"]]
    if session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.email.in_([u["email"] for u in tables.get("users", [])]))
        .where(User.tenant_id != actor.tenant_id)
    ):
        raise Conflict("yedekteki bir e-posta bu veritabanında başka bir işletmeye kayıtlı")
    # Every reference must point inside the file itself, so an edited file cannot attach rows
    # to another business's months, products, branches or users.
    own = {
        name: {r.get("id") for r in tables.get(name, [])}
        for name in (
            "branches",
            "users",
            "count_items",
            "menu_products",
            "monthly_counts",
            "suppliers",
            "purchase_receipts",
            "employees",
        )
    }
    refs = {
        "branch_id": "branches",
        "user_id": "users",
        "created_by": "users",
        "closed_by": "users",
        "item_id": "count_items",
        "product_id": "menu_products",
        "count_id": "monthly_counts",
        "supplier_id": "suppliers",
        "receipt_id": "purchase_receipts",
        "employee_id": "employees",
    }
    for name, rows_ in tables.items():
        if not isinstance(rows_, list):
            raise Invalid("yedek dosyası bozuk")
        for raw in rows_:
            for ref, target in refs.items():
                v = raw.get(ref)
                if v is not None and v not in own[target]:
                    raise Invalid(
                        f"yedek dosyası tutarsız: {name}.{ref} dosyada olmayan bir kayda bağlı"
                    )
    user_cols = {"user_id", "created_by", "closed_by", "service_user_id"}
    counts: dict[str, int] = {}
    for table in TABLES:
        rows = []
        for raw in tables.get(table.name, []):
            if table.name == "users" and raw["id"] in user_map:
                continue
            row = {}
            for col in table.columns:
                if col.name not in raw:
                    continue
                if col.name == "id" and table.name in FRESH_IDS:
                    continue
                v = raw[col.name]
                if col.name == "tenant_id":
                    v = str(actor.tenant_id)
                elif col.name in user_cols and v in user_map:
                    v = str(user_map[v])
                row[col.name] = _value(col, v)
            rows.append(row)
        if rows:
            session.execute(insert(table), rows)
        counts[table.name] = len(rows)
    audit.record(
        session,
        actor,
        "backup.restore",
        "tenant",
        actor.tenant_id,
        {"counts": counts, "backup_created_at": data.get("created_at")},
    )
    session.commit()
    return counts
