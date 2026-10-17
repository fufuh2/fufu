"""Audit end-to-end run against a live server and a real database.

Usage: python scripts/audit_e2e.py BASE_URL DATABASE_DSN OWNER_EMAIL OWNER_PASSWORD
Expected values are hand-computed in the comments, not taken from application code.
Exits non-zero on the first mismatch. Never point it at a production database.
"""

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import httpx
import psycopg

base, dsn, email, password = sys.argv[1:5]
if "audit" not in dsn:  # explicit check: asserts vanish under python -O
    sys.exit("refusing to run outside an audit database")
c = httpx.Client(base_url=base + "/api/v1", timeout=30)
results: list[tuple[str, bool, str]] = []


def check(step: str, cond: bool, detail: object = "") -> None:
    results.append((step, bool(cond), str(detail)))
    print(("PASS " if cond else "FAIL ") + step + (f" -- {detail}" if not cond else ""))


def login(e: str, p: str) -> dict[str, str]:
    r = c.post("/auth/login", json={"email": e, "password": p})
    if r.status_code != 200:
        sys.exit(f"login failed: {r.text}")
    return {"Authorization": "Bearer " + r.json()["access_token"]}


h = login(email, password)
db = psycopg.connect(dsn.replace("+psycopg", ""), autocommit=True)


def q(sql: str, *args: object) -> object:
    return db.execute(sql, args).fetchone()[0]


# 1-2 branch and users
br = c.post("/branches", json={"name": "Kadikoy"}, headers=h).json()["id"]
check("1 branch created in DB", q("SELECT count(*) FROM branches WHERE id=%s", br) == 1)
pw = "kitchen-user-password"
r = c.post(
    "/users",
    headers=h,
    json={"email": "cook@audit-example.com", "password": pw, "role": "kitchen", "branch_ids": [br]},
)
check("2 kitchen user created", r.status_code == 201, r.text)

# 3 items and units
beef = c.post("/items", headers=h, json={"sku": "BEEF", "name": "Beef", "base_unit": "g"}).json()[
    "id"
]
bun = c.post(
    "/items",
    headers=h,
    json={"sku": "BUN", "name": "Bun", "base_unit": "pcs", "conversions": {"pack": "6"}},
).json()["id"]

# 4 receipts: 10 kg @400/kg, 5 packs @30/pack, then price change 5 kg @520/kg
r1 = c.post(
    f"/branches/{br}/receipts",
    headers=h,
    json={
        "supplier": "Test Tedarik",
        "lines": [
            {"item_id": beef, "quantity": "10", "unit": "kg", "unit_cost": "400"},
            {"item_id": bun, "quantity": "5", "unit": "pack", "unit_cost": "30"},
        ],
    },
)
r2 = c.post(
    f"/branches/{br}/receipts",
    headers=h,
    json={
        "supplier": "Test Tedarik",
        "lines": [{"item_id": beef, "quantity": "5", "unit": "kg", "unit_cost": "520"}],
    },
)
check("4 receipts accepted", r1.status_code == 201 and r2.status_code == 201)


def stock() -> dict[str, dict]:
    return {x["sku"]: x for x in c.get(f"/branches/{br}/stock", headers=h).json()}


# 5 balance: 15000 g, avg (10000*0.40 + 5000*0.52)/15000 = 0.44 ; 30 buns @5.00
s = stock()
check("5 beef qty 15000 g", D(s["BEEF"]["quantity"]) == 15000, s["BEEF"])
check("5 beef avg 0.44", D(s["BEEF"]["avg_cost"]) == D("0.44"), s["BEEF"])
check("5 bun qty 30 @5", D(s["BUN"]["quantity"]) == 30 and D(s["BUN"]["avg_cost"]) == 5, s["BUN"])

# 6 product + recipe
burger = c.post(
    "/products", headers=h, json={"sku": "BURGER", "name": "Burger", "price": "250.00"}
).json()["id"]
r = c.post(
    f"/products/{burger}/recipes",
    headers=h,
    json={
        "lines": [
            {"item_id": beef, "quantity": "150", "unit": "g"},
            {"item_id": bun, "quantity": "1", "unit": "pcs"},
        ]
    },
)
check("6 recipe v1", r.status_code == 201 and r.json()["version"] == 1, r.text)

# 7 theoretical product cost: 150*0.44 + 5 = 71.00 -- no endpoint exists; computed from order below
# 8 order: 4 burgers, discount 100 -> net 900.00, cost 4*71 = 284.00
o1 = c.post(
    f"/branches/{br}/orders",
    headers={**h, "Idempotency-Key": "audit-o1"},
    json={"channel": "test", "lines": [{"product_id": burger, "quantity": 4, "discount": "100"}]},
)
check("8 order created", o1.status_code == 201, o1.text)
o1j = o1.json()
check("8 net 900.00", D(o1j["net_total"]) == D("900.00"), o1j)
check("7/8 line cost 284.00 (=4x71.00)", D(o1j["lines"][0]["cost_total"]) == D("284.00"), o1j)
rep = c.post(
    f"/branches/{br}/orders",
    headers={**h, "Idempotency-Key": "audit-o1"},
    json={"channel": "test", "lines": [{"product_id": burger, "quantity": 4, "discount": "100"}]},
)
check("8 replay returns same order, 200", rep.status_code == 200 and rep.json()["id"] == o1j["id"])

# 9 consumption: beef 15000-600 = 14400, bun 26
s = stock()
check("9 beef 14400", D(s["BEEF"]["quantity"]) == 14400, s["BEEF"])
check("9 bun 26", D(s["BUN"]["quantity"]) == 26, s["BUN"])

# 10 second order then cancel -> stock unchanged afterwards
o2 = c.post(
    f"/branches/{br}/orders",
    headers={**h, "Idempotency-Key": "audit-o2"},
    json={"lines": [{"product_id": burger, "quantity": 2}]},
).json()
cx = c.post(f"/orders/{o2['id']}/cancel", headers=h, json={"reason": "customer left"})
check("10 cancel ok", cx.status_code == 200 and cx.json()["status"] == "cancelled", cx.text)
s = stock()
check("10 stock restored", D(s["BEEF"]["quantity"]) == 14400 and D(s["BUN"]["quantity"]) == 26, s)
check("10 avg unchanged after reversal", D(s["BEEF"]["avg_cost"]) == D("0.44"), s["BEEF"])

# 11 waste 300 g -> cost 132.00
w = c.post(
    f"/branches/{br}/waste",
    headers=h,
    json={"item_id": beef, "quantity": "300", "unit": "g", "reason": "spoilage_expiry"},
)
check(
    "11 waste cost -132.00",
    w.status_code == 201 and D(w.json()["total_cost"]) == D("-132.00"),
    w.text,
)

# 12 count: beef 14 kg (system 14100 -> -100 g), bun 26 (no diff)
cnt = c.post(
    f"/branches/{br}/counts",
    headers=h,
    json={
        "lines": [
            {"item_id": beef, "quantity": "14", "unit": "kg"},
            {"item_id": bun, "quantity": "26", "unit": "pcs"},
        ]
    },
)
check("12 count posted", cnt.status_code == 201, cnt.text)

now = datetime.now(UTC)
period = {
    "from": (now - timedelta(hours=1)).isoformat(),
    "to": (now + timedelta(hours=1)).isoformat(),
}
# 13 variance beef: theoretical 600 (cancelled order nets out), waste 300, unexplained 100, actual 1000
v = {
    x["sku"]: x
    for x in c.get(f"/branches/{br}/reports/variance", headers=h, params=period).json()["lines"]
}
bv = v["BEEF"]
check("13 theoretical 600", D(bv["theoretical_qty"]) == 600, bv)
check("13 waste 300", D(bv["recorded_waste_qty"]) == 300, bv)
check(
    "13 unexplained 100 / 44.00",
    D(bv["unexplained_qty"]) == 100 and D(bv["unexplained_cost"]) == D("44.00"),
    bv,
)
check("13 actual 1000 = 600+300+100", D(bv["actual_qty"]) == 1000, bv)
check("13 bun ok", v["BUN"]["status"] == "ok" and D(v["BUN"]["unexplained_qty"]) == 0, v["BUN"])

# 14 margin: 900 - 284 = 616 ; 616/900 = 68.44 %
m = c.get(f"/branches/{br}/reports/margin", headers=h, params=period).json()["total"]
check(
    "14 revenue 900 / cogs 284 / profit 616 / 68.44%",
    (D(m["net_revenue"]), D(m["cogs"]), D(m["gross_profit"]), D(m["margin_pct"]))
    == (D("900.00"), D("284.00"), D("616.00"), D("68.44")),
    m,
)

# 15 reports vs source rows
db_rev = q(
    "SELECT sum(line_net) FROM order_lines l JOIN orders o ON o.id=l.order_id "
    "WHERE o.branch_id=%s AND o.status='completed'",
    br,
)
check("15 margin revenue == SUM(order_lines)", db_rev == D(m["net_revenue"]), db_rev)
for item in (beef, bun):
    led = q(
        "SELECT coalesce(sum(quantity_base),0) FROM stock_movements WHERE branch_id=%s AND item_id=%s",
        br,
        item,
    )
    bal = q("SELECT quantity FROM stock_balances WHERE branch_id=%s AND item_id=%s", br, item)
    check(f"15 ledger == balance ({item[:8]})", led == bal, (led, bal))
check("15 exactly 2 orders (no duplicate)", q("SELECT count(*) FROM orders") == 2)
types = dict(
    db.execute("SELECT movement_type, count(*) FROM stock_movements GROUP BY 1").fetchall()
)
check(
    "15 movement counts",
    types
    == {"receipt": 3, "sale_consumption": 4, "sale_reversal": 2, "waste": 1, "count_adjustment": 1},
    types,
)
check("15 beef final 14000", D(stock()["BEEF"]["quantity"]) == 14000)

# 16 unauthorised access
ck = login("cook@audit-example.com", pw)
check(
    "16 kitchen margin 403",
    c.get(f"/branches/{br}/reports/margin", headers=ck, params=period).status_code == 403,
)
check(
    "16 kitchen receipt 403",
    c.post(
        f"/branches/{br}/receipts",
        headers=ck,
        json={"lines": [{"item_id": beef, "quantity": "1", "unit": "g", "unit_cost": "1"}]},
    ).status_code
    == 403,
)
check(
    "16 kitchen cancel 403",
    c.post(f"/orders/{o1j['id']}/cancel", headers=ck, json={"reason": "x"}).status_code == 403,
)
check("16 no token 401", c.get(f"/branches/{br}/stock").status_code == 401)

# 17 audit trail
actions = {e["action"] for e in c.get("/audit-events", headers=h, params={"limit": 500}).json()}
need = {
    "branch.create",
    "user.create",
    "catalog.item.create",
    "stock.receive",
    "catalog.product.create",
    "recipe.version.create",
    "order.create",
    "order.cancel",
    "stock.waste",
    "stock.count",
    "auth.login",
}
check("17 audit has all actions", need <= actions, need - actions)
check(
    "17 order.create logged once for o1",
    q("SELECT count(*) FROM audit_events WHERE action='order.create' AND entity_id=%s", o1j["id"])
    == 1,
)

failed = [r for r in results if not r[1]]
print(f"\n{len(results)} checks, {len(results) - len(failed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
