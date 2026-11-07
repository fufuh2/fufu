"""Role -> permission mapping. Roles are bundles; checks are always done on permissions."""

from enum import StrEnum


class Perm(StrEnum):
    BRANCH_MANAGE = "branch.manage"
    USER_MANAGE = "user.manage"
    ITEM_MANAGE = "item.manage"  # product list and unit prices
    COUNT_READ = "count.read"
    COUNT_EDIT = "count.edit"  # enter opening / incoming / used / waste / counted
    COUNT_CLOSE = "count.close"  # close and reopen a month
    AUDIT_READ = "audit.read"
    PURCHASE_MANAGE = "purchase.manage"  # suppliers and goods receipts
    SALES_MANAGE = "sales.manage"  # daily sales entry
    EXPENSE_MANAGE = "expense.manage"
    STAFF_MANAGE = "staff.manage"  # employees and shifts
    FINANCE_READ = "finance.read"  # profit and loss, sales and purchase reports


class Role(StrEnum):
    OWNER = "owner"  # İşletme sahibi: everything, every branch
    BRANCH_MANAGER = "branch_manager"  # Şube müdürü: counts its branches, closes months
    COUNTER = "counter"  # Sayım personeli: enters numbers, cannot close
    VIEWER = "viewer"  # İzleyici (muhasebe, denetçi): read only


_ALL = frozenset(Perm)

ROLE_PERMISSIONS: dict[Role, frozenset[Perm]] = {
    Role.OWNER: _ALL,
    Role.BRANCH_MANAGER: frozenset(
        {
            Perm.COUNT_READ,
            Perm.COUNT_EDIT,
            Perm.COUNT_CLOSE,
            Perm.AUDIT_READ,
            Perm.PURCHASE_MANAGE,
            Perm.SALES_MANAGE,
            Perm.EXPENSE_MANAGE,
            Perm.STAFF_MANAGE,
            Perm.FINANCE_READ,
        }
    ),
    # Counting staff also receive deliveries and enter the day's sales.
    Role.COUNTER: frozenset(
        {Perm.COUNT_READ, Perm.COUNT_EDIT, Perm.PURCHASE_MANAGE, Perm.SALES_MANAGE}
    ),
    Role.VIEWER: frozenset({Perm.COUNT_READ, Perm.AUDIT_READ, Perm.FINANCE_READ}),
}

ROLE_LABELS: dict[Role, str] = {
    Role.OWNER: "İşletme sahibi",
    Role.BRANCH_MANAGER: "Şube müdürü",
    Role.COUNTER: "Sayım personeli",
    Role.VIEWER: "İzleyici",
}

# Roles that see every branch of their tenant; others only see assigned branches.
TENANT_WIDE_ROLES = frozenset({Role.OWNER})


def has_perm(role: Role, perm: Perm) -> bool:
    return perm in ROLE_PERMISSIONS[role]
