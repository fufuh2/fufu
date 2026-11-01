"""menu product price

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("menu_products", sa.Column("price", sa.Numeric(18, 2), nullable=True))
    op.create_check_constraint(
        "ck_menu_product_price", "menu_products", "price IS NULL OR price >= 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_menu_product_price", "menu_products")
    op.drop_column("menu_products", "price")
