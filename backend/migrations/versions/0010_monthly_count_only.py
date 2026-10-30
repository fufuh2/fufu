"""monthly count only: drop the order / recipe / stock / purchasing / quality / workforce /
POS modules and reduce roles to owner, branch_manager, counter, viewer

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-30

Irreversible: the dropped tables and their rows are not restored by a downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Children first, so no CASCADE is needed and nothing outside this list can be dropped.
DROPPED = (
    "pos_events",
    "pos_product_mappings",
    "integrations",
    "handover_notes",
    "tasks",
    "attendance",
    "shifts",
    "nonconformities",
    "checklist_answers",
    "checklist_runs",
    "checklist_items",
    "checklist_templates",
    "purchase_invoices",
    "purchase_order_lines",
    "purchase_orders",
    "supplier_prices",
    "suppliers",
    "transfer_lines",
    "transfers",
    "stock_count_lines",
    "stock_counts",
    "order_lines",
    "orders",
    "stock_movements",
    "stock_balances",
    "recipe_lines",
    "recipe_versions",
    "products",
    "item_unit_conversions",
    "items",
)

ROLE_MAP = {
    "regional_manager": "branch_manager",
    "shift_lead": "counter",
    "purchasing": "counter",
    "kitchen": "counter",
    "quality": "counter",
    "finance": "viewer",
    "auditor": "viewer",
}


def upgrade() -> None:
    # POS service accounts have no purpose without the integration.
    op.execute(
        "UPDATE users SET is_active = false, token_version = token_version + 1 "
        "WHERE id IN (SELECT service_user_id FROM integrations)"
    )
    for old, new in ROLE_MAP.items():
        # Changing a role revokes the user's tokens, as the API does.
        op.execute(
            sa.text(
                "UPDATE users SET role = :new, token_version = token_version + 1 WHERE role = :old"
            ).bindparams(old=old, new=new)
        )
    for table in DROPPED:
        op.drop_table(table)
    op.drop_column("tenants", "po_approval_limit")
    op.create_check_constraint(
        "ck_user_role", "users", "role IN ('owner','branch_manager','counter','viewer')"
    )


def downgrade() -> None:
    raise NotImplementedError("0010 drops modules and their data; restore from a backup")
