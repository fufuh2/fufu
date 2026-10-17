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
    STOCK_TRANSFER = "stock.transfer"
    PURCHASE_MANAGE = "purchase.manage"
    PURCHASE_APPROVE = "purchase.approve"
    QUALITY_RECORD = "quality.record"
    QUALITY_MANAGE = "quality.manage"
    SHIFT_MANAGE = "shift.manage"
    SHIFT_SELF = "shift.self"
    TASK_COMPLETE = "task.complete"
    INTEGRATION_MANAGE = "integration.manage"


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
    Role.REGIONAL_MANAGER: _ALL - {Perm.USER_MANAGE, Perm.BRANCH_MANAGE, Perm.INTEGRATION_MANAGE},
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
            Perm.STOCK_TRANSFER,
            Perm.PURCHASE_MANAGE,
            Perm.PURCHASE_APPROVE,
            Perm.QUALITY_RECORD,
            Perm.SHIFT_MANAGE,
            Perm.SHIFT_SELF,
            Perm.TASK_COMPLETE,
        }
    ),
    Role.SHIFT_LEAD: frozenset(
        {
            Perm.STOCK_READ,
            Perm.STOCK_WASTE,
            Perm.STOCK_COUNT,
            Perm.ORDER_CREATE,
            Perm.ORDER_CANCEL,
            Perm.QUALITY_RECORD,
            Perm.SHIFT_SELF,
            Perm.TASK_COMPLETE,
        }
    ),
    Role.PURCHASING: frozenset(
        {
            Perm.STOCK_READ,
            Perm.STOCK_RECEIVE,
            Perm.STOCK_COUNT,
            Perm.STOCK_TRANSFER,
            Perm.PURCHASE_MANAGE,
            Perm.SHIFT_SELF,
            Perm.TASK_COMPLETE,
        }
    ),
    Role.KITCHEN: frozenset(
        {
            Perm.STOCK_READ,
            Perm.STOCK_WASTE,
            Perm.ORDER_CREATE,
            Perm.QUALITY_RECORD,
            Perm.SHIFT_SELF,
            Perm.TASK_COMPLETE,
        }
    ),
    Role.QUALITY: frozenset(
        {
            Perm.STOCK_READ,
            Perm.STOCK_WASTE,
            Perm.QUALITY_RECORD,
            Perm.QUALITY_MANAGE,
            Perm.SHIFT_SELF,
            Perm.TASK_COMPLETE,
        }
    ),
    Role.FINANCE: frozenset({Perm.STOCK_READ, Perm.REPORT_READ}),
    Role.AUDITOR: frozenset({Perm.STOCK_READ, Perm.REPORT_READ, Perm.AUDIT_READ}),
}

# Roles that see every branch of their tenant; others only see assigned branches.
TENANT_WIDE_ROLES = frozenset({Role.OWNER})


def has_perm(role: Role, perm: Perm) -> bool:
    return perm in ROLE_PERMISSIONS[role]
