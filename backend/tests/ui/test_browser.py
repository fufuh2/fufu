"""Real-browser tests of the web UI against a live uvicorn server and the test database.
Requires the `playwright` package and a Chromium build (OPSX_CHROMIUM_PATH or the default
`playwright install chromium` location)."""

import os
import socket
import threading
import time
from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import Order
from tests.conftest import PASSWORD, new_tenant, setup_kitchen, stock_of

if os.environ.get("OPSX_REQUIRE_BROWSER") == "1":
    import playwright.sync_api as sync_api  # fail loudly in CI instead of skipping
else:
    sync_api = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    import uvicorn

    from app.main import create_app

    port = _free_port()
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    path = os.environ.get("OPSX_CHROMIUM_PATH") or (
        "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None
    )
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=path) if path else p.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            if os.environ.get("OPSX_REQUIRE_BROWSER") == "1":
                pytest.fail(f"OPSX_REQUIRE_BROWSER=1 but Chromium could not start: {exc}")
            pytest.skip(f"no Chromium available: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser, server):  # type: ignore[no-untyped-def]
    ctx = browser.new_context(base_url=server, locale="tr-TR")
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.errors = errors
    yield pg
    ctx.close()


def _login(page, email: str, password: str = PASSWORD) -> None:  # type: ignore[no-untyped-def]
    page.goto("/ui/#/login")
    page.get_by_label("E-posta").fill(email)
    page.get_by_label("Parola").fill(password)
    page.get_by_role("button", name="Giriş yap").click()
    if password == PASSWORD:
        sync_api.expect(page.get_by_role("heading", name="Özet")).to_be_visible()


def _owner_email(session: Session) -> str:
    return session.execute(
        text("SELECT email FROM users WHERE role='owner' ORDER BY created_at DESC")
    ).scalar()


def test_login_errors_and_dashboard(page, client, session: Session) -> None:
    api = new_tenant(session, client)
    setup_kitchen(api)
    email = _owner_email(session)
    page.goto("/ui/#/login")
    page.get_by_label("E-posta").fill(email)
    page.get_by_label("Parola").fill("wrong-password-123")
    page.get_by_role("button", name="Giriş yap").click()
    sync_api.expect(page.locator("#msg")).to_contain_text("E-posta veya parola hatalı")
    _login(page, email)
    sync_api.expect(page.get_by_role("heading", name="Özet")).to_be_visible()
    sync_api.expect(page.locator("table")).to_contain_text("BEEF")
    # currency and number formatting in Turkish locale
    sync_api.expect(page.locator("table")).to_contain_text("₺")
    # the only console error allowed is the browser's log of the deliberate 401 above
    assert [e for e in page.errors if "401" not in e] == []
    headers = page.request.get("/ui/").headers
    assert "default-src 'self'" in headers["content-security-policy"]


def test_order_create_and_cancel_updates_database(page, client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    _login(page, _owner_email(session))
    page.get_by_role("link", name="Siparişler").click()
    page.get_by_label("Adet").fill("3")
    page.get_by_role("button", name="Kalem ekle").click()
    save = page.get_by_role("button", name="Siparişi kaydet")
    save.dblclick()  # a double click must not book twice
    sync_api.expect(page.locator("#msg")).to_contain_text("Sipariş kaydedildi")
    sync_api.expect(page.locator("table")).to_contain_text("tamamlandı")
    assert session.scalar(select(func.count()).select_from(Order)) == 1
    assert stock_of(api, k["branch"], k["bun"]) == 27
    page.get_by_label("İptal nedeni").fill("müşteri vazgeçti")
    page.get_by_role("button", name="İptal et").click()
    sync_api.expect(page.locator("#msg")).to_contain_text("iptal edildi")
    sync_api.expect(page.locator("table")).to_contain_text("iptal")
    assert stock_of(api, k["branch"], k["bun"]) == 30
    page.reload()  # persisted, not just client state
    sync_api.expect(page.locator("table")).to_contain_text("iptal")
    assert page.errors == []


def test_waste_form_and_server_side_validation(page, client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    _login(page, _owner_email(session))
    page.get_by_role("link", name="Stok işlemleri").click()
    fire = page.locator("section", has=page.get_by_role("heading", name="Fire"))
    fire.get_by_label("Malzeme").select_option(k["beef"])
    fire.get_by_label("Miktar").fill("250")
    fire.get_by_label("Birim").fill("g")
    fire.get_by_role("button", name="Kaydet").click()
    sync_api.expect(page.locator("#msg")).to_contain_text("Fire kaydedildi")
    assert stock_of(api, k["branch"], k["beef"]) == Decimal("9750")
    fire.get_by_label("Miktar").fill("999")
    fire.get_by_label("Birim").fill("kg")
    fire.get_by_role("button", name="Kaydet").click()
    sync_api.expect(page.locator("#msg")).to_contain_text("insufficient stock")
    assert stock_of(api, k["branch"], k["beef"]) == Decimal("9750")


def test_reports_show_unknown_not_zero(page, client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    api.post(
        f"/branches/{k['branch']}/orders",
        {"lines": [{"product_id": k["burger"], "quantity": 2}]},
        headers={"Idempotency-Key": "r1"},
    )
    _login(page, _owner_email(session))
    page.get_by_role("link", name="Raporlar").click()
    sync_api.expect(page.get_by_role("heading", name="Kârlılık")).to_be_visible()
    body = page.locator("main")
    sync_api.expect(body).to_contain_text("500,00")  # 2 x 250
    sync_api.expect(body).to_contain_text("dönemde sayım yok")
    sync_api.expect(body).to_contain_text("bilinmiyor")


def test_permissions_hide_and_block(page, client, session: Session) -> None:
    owner = new_tenant(session, client)
    k = setup_kitchen(owner)
    owner.post(
        "/users",
        {
            "email": "cook@example.com",
            "password": PASSWORD,
            "role": "kitchen",
            "branch_ids": [k["branch"]],
        },
    )
    _login(page, "cook@example.com")
    sync_api.expect(page.get_by_role("link", name="Siparişler")).to_be_visible()
    sync_api.expect(page.get_by_role("link", name="Raporlar")).to_be_hidden()
    page.goto("/ui/#/reports")  # typing the URL does not bypass the API
    sync_api.expect(page.locator("#msg")).to_contain_text("yetkiniz yok")
    page.goto("/ui/#/stock")
    sync_api.expect(page.get_by_role("heading", name="Fire")).to_be_visible()
    sync_api.expect(page.get_by_role("heading", name="Mal kabul")).to_have_count(0)


def test_xss_payload_is_rendered_as_text(page, client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    evil = '<img src=x onerror="window.__pwned=1">'
    api.post("/items", {"sku": "XSS", "name": evil, "base_unit": "g"})
    _login(page, _owner_email(session))
    sync_api.expect(page.locator("table")).to_contain_text(evil)
    assert page.evaluate("window.__pwned") is None
    assert page.locator("main img").count() == 0
    assert k


def test_expired_session_returns_to_login(page, client, session: Session) -> None:
    api = new_tenant(session, client)
    setup_kitchen(api)
    _login(page, _owner_email(session))
    sync_api.expect(page.get_by_role("heading", name="Özet")).to_be_visible()
    api.post("/auth/logout-all")  # revokes every token of this user, like an expiry
    page.get_by_role("link", name="Siparişler").click()
    sync_api.expect(page.get_by_role("heading", name="Giriş")).to_be_visible()
    sync_api.expect(page.locator("#msg")).to_contain_text("Oturumunuz sona erdi")


def test_mobile_layout_and_keyboard(browser, server, client, session: Session) -> None:
    api = new_tenant(session, client)
    setup_kitchen(api)
    ctx = browser.new_context(
        base_url=server, viewport={"width": 375, "height": 740}, locale="tr-TR"
    )
    pg = ctx.new_page()
    pg.goto("/ui/#/login")
    # keyboard only: e-mail is focused, Tab moves to the password, Enter submits
    pg.keyboard.type(_owner_email(session))
    pg.keyboard.press("Tab")
    assert pg.evaluate("document.activeElement.name") == "password"
    pg.keyboard.type(PASSWORD)
    pg.keyboard.press("Enter")
    sync_api.expect(pg.get_by_role("heading", name="Özet")).to_be_visible()
    assert pg.evaluate("document.activeElement.id") == "main"  # focus moves to content
    overflow = pg.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 0, f"page scrolls horizontally by {overflow}px"
    ctx.close()


def test_quality_checklist_through_ui(page, client, session: Session) -> None:
    api = new_tenant(session, client)
    setup_kitchen(api)
    api.post(
        "/checklist-templates",
        {
            "name": "Açılış",
            "kind": "opening",
            "items": [
                {"label": "Zemin temiz", "answer_type": "check"},
                {
                    "label": "Buzdolabı",
                    "answer_type": "number",
                    "unit": "C",
                    "min_value": "0",
                    "max_value": "4",
                },
            ],
        },
    )
    _login(page, _owner_email(session))
    page.get_by_role("link", name="Kalite").click()
    page.get_by_label("Zemin temiz").check()
    page.get_by_label("Buzdolabı").fill("9")
    page.get_by_role("button", name="Kaydet").click()
    sync_api.expect(page.locator("#msg")).to_contain_text("uygunsuzluk açıldı")
    sync_api.expect(page.locator("table")).to_contain_text("Buzdolabı = 9")
    page.get_by_label("Düzeltici faaliyet").fill("Termostat ayarlandı")
    page.get_by_role("button", name="Kapat").click()
    sync_api.expect(page.locator("main")).to_contain_text("Açık uygunsuzluk yok")
    assert session.execute(text("SELECT status FROM nonconformities")).scalar() == "resolved"
    assert page.errors == []
