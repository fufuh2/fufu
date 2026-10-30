from decimal import Decimal

import pytest

from app.domain.permissions import ROLE_LABELS, ROLE_PERMISSIONS, Perm, Role, has_perm

D = Decimal


class TestPermissions:
    def test_every_role_has_mapping(self) -> None:
        assert set(ROLE_PERMISSIONS) == set(Role)

    def test_least_privilege(self) -> None:
        assert set(ROLE_LABELS) == set(Role)
        assert has_perm(Role.OWNER, Perm.USER_MANAGE)
        assert not has_perm(Role.BRANCH_MANAGER, Perm.USER_MANAGE)
        assert not has_perm(Role.BRANCH_MANAGER, Perm.ITEM_MANAGE)
        assert has_perm(Role.BRANCH_MANAGER, Perm.COUNT_CLOSE)
        assert not has_perm(Role.COUNTER, Perm.COUNT_CLOSE)
        assert has_perm(Role.COUNTER, Perm.COUNT_EDIT)
        assert ROLE_PERMISSIONS[Role.VIEWER] == {Perm.COUNT_READ, Perm.AUDIT_READ}


class TestConfig:
    def test_database_url_normalization(self) -> None:
        from app.config import normalize_database_url as n

        assert n("postgres://u:p@h:5432/d") == "postgresql+psycopg://u:p@h:5432/d"
        assert (
            n("postgresql://u:p@h/d?sslmode=require")
            == "postgresql+psycopg://u:p@h/d?sslmode=require"
        )
        assert n("postgresql+psycopg://u@h/d") == "postgresql+psycopg://u@h/d"


class TestMonthlyCount:
    def test_expected_and_difference(self) -> None:
        from app.domain.monthly import compute_line

        # devir 10 + gelen 50 - kullanılan 45 = 15 beklenen; sayılan 12 -> 3 eksik
        r = compute_line(D("10"), D("50"), D("45"), D("12"), D("400"))
        assert (r.expected, r.difference, r.status) == (D("15"), D("-3"), "eksik")
        assert (r.difference_value, r.counted_value) == (D("-1200.00"), D("4800.00"))
        r = compute_line(D("0"), D("24"), D("20"), D("5"), None)
        assert (r.difference, r.status, r.difference_value) == (D("1"), "fazla", None)
        assert compute_line(D("1"), D("0"), D("1"), D("0"), None).status == "tamam"

    def test_not_counted_is_unknown_not_zero(self) -> None:
        from app.domain.monthly import compute_line

        r = compute_line(D("5"), D("0"), D("0"), None, D("1"))
        assert (r.difference, r.status, r.difference_value) == (None, "sayilmadi", None)

    def test_decimals_kg(self) -> None:
        from app.domain.monthly import compute_line

        r = compute_line(D("2.350"), D("10.125"), D("11.200"), D("1.250"), D("399.90"))
        assert r.expected == D("1.275") and r.difference == D("-0.025")
        assert r.difference_value == D("-10.00")  # -0.025 * 399.90 = -9.9975 -> -10.00

    def test_period_parsing(self) -> None:
        from app.domain.monthly import parse_period

        assert parse_period("2026-08") == (2026, 8)
        for bad in ("2026-13", "26-08", "2026/08", "2026-8", "abcd-ef"):
            with pytest.raises(ValueError):
                parse_period(bad)

    def test_waste_is_deducted_and_valued(self) -> None:
        from app.domain.monthly import compute_line

        # devir 10 + gelen 50 - satılan 45 - zayi 2 = 13 beklenen; sayılan 12 -> 1 eksik
        r = compute_line(D("10"), D("50"), D("45"), D("12"), D("400"), D("2"))
        assert (r.expected, r.difference, r.status) == (D("13"), D("-1"), "eksik")
        assert (r.difference_value, r.waste_value) == (D("-400.00"), D("800.00"))
        # recorded waste explains the loss: no unexplained difference left
        r = compute_line(D("10"), D("0"), D("0"), D("7"), None, D("3"))
        assert (r.status, r.waste_value) == ("tamam", None)
