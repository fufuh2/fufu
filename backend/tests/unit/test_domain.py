from decimal import Decimal

import pytest

from app.domain.costing import gross_margin, gross_quantity, money, moving_average_cost
from app.domain.permissions import ROLE_PERMISSIONS, Perm, Role, has_perm
from app.domain.units import ConversionError, to_base

D = Decimal


class TestUnits:
    def test_standard_conversions(self) -> None:
        assert to_base(D("1.5"), "kg", "g") == D("1500")
        assert to_base(D("0.25"), "l", "ml") == D("250")
        assert to_base(D("3"), "pcs", "pcs") == D("3")

    def test_item_specific_conversion(self) -> None:
        assert to_base(D("2"), "pack", "pcs", {"pack": D("6")}) == D("12")
        # e.g. eggs counted in pieces but weighed in grams
        assert to_base(D("4"), "pcs", "g", {"pcs": D("55")}) == D("220")

    @pytest.mark.parametrize(
        ("unit", "base"), [("kg", "ml"), ("l", "g"), ("pcs", "g"), ("box", "pcs"), ("g", "kg")]
    )
    def test_rejects_unknown_or_cross_dimension(self, unit: str, base: str) -> None:
        with pytest.raises(ConversionError):
            to_base(D("1"), unit, base)

    def test_rejects_non_positive_factor(self) -> None:
        with pytest.raises(ConversionError):
            to_base(D("1"), "pack", "pcs", {"pack": D("0")})


class TestCosting:
    def test_moving_average(self) -> None:
        # 100 g at 0.40 + 100 g at 0.60 -> 0.50
        assert moving_average_cost(D("100"), D("0.4"), D("100"), D("0.6")) == D("0.5")

    def test_moving_average_resets_when_stock_not_positive(self) -> None:
        assert moving_average_cost(D("-20"), D("0.4"), D("100"), D("0.7")) == D("0.7")
        assert moving_average_cost(D("0"), D("9"), D("1"), D("1.25")) == D("1.25")

    def test_moving_average_rejects_bad_input(self) -> None:
        with pytest.raises(ValueError):
            moving_average_cost(D("1"), D("1"), D("0"), D("1"))
        with pytest.raises(ValueError):
            moving_average_cost(D("1"), D("1"), D("1"), D("-1"))

    def test_gross_quantity_with_loss(self) -> None:
        assert gross_quantity(D("90"), D("10")) == D("100")
        assert gross_quantity(D("150"), D("0")) == D("150")
        with pytest.raises(ValueError):
            gross_quantity(D("1"), D("100"))

    def test_money_rounds_half_even(self) -> None:
        assert money(D("0.125")) == D("0.12")
        assert money(D("0.135")) == D("0.14")

    def test_margin(self) -> None:
        m = gross_margin(D("1000"), D("350"))
        assert (m.gross_profit, m.margin_pct, m.status) == (D("650.00"), D("65.00"), "ok")

    def test_margin_zero_revenue_is_undefined_not_error(self) -> None:
        m = gross_margin(D("0"), D("40"))
        assert m.margin_pct is None
        assert m.gross_profit == D("-40.00")
        assert m.status == "undefined_zero_revenue"

    def test_margin_unknown_cost_is_not_zero(self) -> None:
        m = gross_margin(D("100"), None)
        assert (m.cogs, m.gross_profit, m.margin_pct, m.status) == (
            None,
            None,
            None,
            "incomplete_cost",
        )

    def test_decimal_not_float(self) -> None:
        # 0.1 + 0.2 must be exact
        assert money(D("0.1") + D("0.2")) == D("0.30")


class TestPermissions:
    def test_every_role_has_mapping(self) -> None:
        assert set(ROLE_PERMISSIONS) == set(Role)

    def test_least_privilege_examples(self) -> None:
        assert not has_perm(Role.KITCHEN, Perm.STOCK_RECEIVE)
        assert not has_perm(Role.AUDITOR, Perm.ORDER_CREATE)
        assert not has_perm(Role.FINANCE, Perm.STOCK_WASTE)
        assert not has_perm(Role.BRANCH_MANAGER, Perm.USER_MANAGE)
        assert has_perm(Role.OWNER, Perm.USER_MANAGE)

    def test_read_only_roles_have_no_write_perms(self) -> None:
        writes = {
            Perm.BRANCH_MANAGE,
            Perm.USER_MANAGE,
            Perm.CATALOG_MANAGE,
            Perm.RECIPE_MANAGE,
            Perm.STOCK_RECEIVE,
            Perm.STOCK_WASTE,
            Perm.STOCK_COUNT,
            Perm.ORDER_CREATE,
            Perm.ORDER_CANCEL,
        }
        for role in (Role.AUDITOR, Role.FINANCE):
            assert not (ROLE_PERMISSIONS[role] & writes)


class TestConfig:
    def test_database_url_normalization(self) -> None:
        from app.config import normalize_database_url as n

        assert n("postgres://u:p@h:5432/d") == "postgresql+psycopg://u:p@h:5432/d"
        assert (
            n("postgresql://u:p@h/d?sslmode=require")
            == "postgresql+psycopg://u:p@h/d?sslmode=require"
        )
        assert n("postgresql+psycopg://u@h/d") == "postgresql+psycopg://u@h/d"
