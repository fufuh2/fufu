"""Role -> permission mapping. Roles are bundles; checks are always done on permissions."""

from enum import StrEnum


class Perm(StrEnum):
    BRANCH_MANAGE = "branch.manage"
    USER_MANAGE = "user.manage"
    CATALOG_MANAGE = "catalog.manage"
    RECIPE_MANAGE = "recipe.manage"
    STOCK_READ = "stock.read"
    STOCK_RECEIVE = "stock.receive"
    STOCK_WASTE = "stock.waste"
    STOCK_COUNT = "stock.count"
    ORDER_CREATE = "order.create"
    ORDER_CANCEL = "order.cancel"
    REPORT_READ = "report.read"
    AUDIT_READ = "audit.read"


class Role(StrEnum):
    OWNER = "owner"
    REGIONAL_MANAGER = "regional_manager"
    BRANCH_MANAGER = "branch_manager"
    SHIFT_LEAD = "shift_lead"
    PURCHASING = "purchasing"
    KITCHEN = "kitchen"
    QUALITY = "quality"
    FINANCE = "finance"
    AUDITOR = "auditor"


_ALL = frozenset(Perm)

ROLE_PERMISSIONS: dict[Role, frozenset[Perm]] = {
    Role.OWNER: _ALL,
    Role.REGIONAL_MANAGER: _ALL - {Perm.USER_MANAGE, Perm.BRANCH_MANAGE},
    Role.BRANCH_MANAGER: frozenset(
        {
            Perm.STOCK_READ,
            Perm.STOCK_RECEIVE,
            Perm.STOCK_WASTE,
            Perm.STOCK_COUNT,
            Perm.ORDER_CREATE,
            Perm.ORDER_CANCEL,
            Perm.REPORT_READ,
            Perm.AUDIT_READ,
        }
    ),
    Role.SHIFT_LEAD: frozenset(
        {Perm.STOCK_READ, Perm.STOCK_WASTE, Perm.STOCK_COUNT, Perm.ORDER_CREATE, Perm.ORDER_CANCEL}
    ),
    Role.PURCHASING: frozenset({Perm.STOCK_READ, Perm.STOCK_RECEIVE, Perm.STOCK_COUNT}),
    Role.KITCHEN: frozenset({Perm.STOCK_READ, Perm.STOCK_WASTE, Perm.ORDER_CREATE}),
    Role.QUALITY: frozenset({Perm.STOCK_READ, Perm.STOCK_WASTE}),
    Role.FINANCE: frozenset({Perm.STOCK_READ, Perm.REPORT_READ}),
    Role.AUDITOR: frozenset({Perm.STOCK_READ, Perm.REPORT_READ, Perm.AUDIT_READ}),
}

# Roles that see every branch of their tenant; others only see assigned branches.
TENANT_WIDE_ROLES = frozenset({Role.OWNER})


def has_perm(role: Role, perm: Perm) -> bool:
    return perm in ROLE_PERMISSIONS[role]
