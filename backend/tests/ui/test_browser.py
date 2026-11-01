"""Real-browser tests of the web UI against a live uvicorn server and the test database.
Requires the `playwright` package and a Chromium build (OPSX_CHROMIUM_PATH or the default
`playwright install chromium` location)."""

import os
import re
import socket
import threading
import time
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import PASSWORD, Api, new_tenant, setup_branch

if os.environ.get("OPSX_REQUIRE_BROWSER") == "1":
    import playwright.sync_api as sync_api  # fail loudly in CI instead of skipping
else:
    sync_api = pytest.importorskip("playwright.sync_api")

HOME = re.compile("^Panel")  # landing page after login
SHEET = re.compile("^Aylık Sayım")


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
        sync_api.expect(page.get_by_role("heading", name=HOME)).to_be_visible()


def _owner_email(session: Session) -> str:
    return session.execute(
        text("SELECT email FROM users WHERE role='owner' ORDER BY created_at DESC")
    ).scalar()


def _nav(page, name: str) -> None:  # type: ignore[no-untyped-def]
    page.get_by_role("navigation").get_by_role("link", name=name, exact=True).click()


def test_login_errors_and_panel(page, client, session: Session) -> None:
    api = new_tenant(session, client)
    setup_branch(api)
    email = _owner_email(session)
    page.goto("/ui/#/login")
    page.get_by_label("E-posta").fill(email)
    page.get_by_label("Parola").fill("wrong-password-123")
    page.get_by_role("button", name="Giriş yap").click()
    sync_api.expect(page.locator("#msg")).to_contain_text("E-posta veya parola hatalı")
    _login(page, email)
    sync_api.expect(page.locator("main")).to_contain_text("sayım henüz başlatılmadı")
    # Only the monthly count remains: none of the removed modules are offered.
    nav = page.get_by_role("navigation")
    for gone in ("Siparişler", "Stok işlemleri", "Raporlar", "Kalite", "Görevler", "Özet"):
        sync_api.expect(nav.get_by_role("link", name=gone)).to_have_count(0)
    for name in (
        "Panel",
        "Aylık Sayım",
        "Geçmiş Aylar",
        "Ürün Analizi",
        "Ürün Listesi",
        "Kullanıcılar",
        "Şubeler",
        "Kayıt Geçmişi",
        "Hesabım",
    ):
        sync_api.expect(nav.get_by_role("link", name=name, exact=True)).to_be_visible()
    page.get_by_role("button", name="Sayımı başlat").click()
    sync_api.expect(page.get_by_role("heading", name=SHEET)).to_be_visible()
    # the only console error allowed is the browser's log of the deliberate wrong password
    assert [e for e in page.errors if "401" not in e] == []


def test_xss_payload_is_rendered_as_text(page, client, session: Session) -> None:
    api = new_tenant(session, client)
    branch = setup_branch(api)
    evil = '<img src=x onerror="window.__pwned=1">'
    item = api.post("/count-items", {"group_name": evil, "name": "X" + evil, "unit": "KG"}).json()
    api.put(
        f"/branches/{branch}/monthly-counts/2026-08",
        {"counted_by_name": evil, "lines": [{"item_id": item["id"], "waste": "1", "counted": "0"}]},
    )
    page.goto("/ui/#/login")
    page.evaluate("sessionStorage.setItem('period', '2026-08')")
    _login(page, _owner_email(session))
    sync_api.expect(page.locator("main")).to_contain_text("X" + evil)  # panel: top waste
    _nav(page, "Aylık Sayım")
    sync_api.expect(page.locator(".count-table")).to_contain_text("X" + evil)
    sync_api.expect(page.locator(".count-table")).to_contain_text(evil)
    _nav(page, "Geçmiş Aylar")
    sync_api.expect(page.locator("main table")).to_contain_text(evil)
    _nav(page, "Kayıt Geçmişi")
    sync_api.expect(page.locator("main table")).to_contain_text("Sayımı kaydetti")
    assert page.evaluate("window.__pwned") is None
    assert page.locator("main img").count() == 0


def test_expired_session_returns_to_login(page, client, session: Session) -> None:
    api = new_tenant(session, client)
    setup_branch(api)
    _login(page, _owner_email(session))
    api.post("/auth/logout-all")  # revokes every token of this user, like an expiry
    _nav(page, "Ürün Listesi")
    sync_api.expect(page.get_by_role("heading", name="Giriş")).to_be_visible()
    sync_api.expect(page.locator("#msg")).to_contain_text("Oturumunuz sona erdi")


def test_mobile_layout_and_keyboard(browser, server, client, session: Session) -> None:
    api = new_tenant(session, client)
    setup_branch(api)
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
    sync_api.expect(pg.get_by_role("heading", name=HOME)).to_be_visible()
    assert pg.evaluate("document.activeElement.id") == "main"  # focus moves to content
    for hash_ in ("#/panel", "#/sayim", "#/urunler"):
        pg.goto(f"/ui/{hash_}")
        pg.wait_for_selector("main h1")
        overflow = pg.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert overflow <= 0, f"{hash_} scrolls horizontally by {overflow}px"
    # the side menu is hidden behind the toggle on a phone
    sync_api.expect(pg.get_by_role("link", name="Kullanıcılar")).not_to_be_in_viewport()
    pg.get_by_role("button", name="Menü").click()
    pg.get_by_role("link", name="Kullanıcılar").click()
    sync_api.expect(pg.get_by_role("heading", name="Kullanıcılar")).to_be_visible()
    ctx.close()


def test_password_change_through_ui(page, client, session: Session) -> None:
    api = new_tenant(session, client)
    setup_branch(api)
    email = _owner_email(session)
    _login(page, email)
    _nav(page, "Hesabım")
    page.get_by_label("Mevcut parola").fill(PASSWORD)
    page.get_by_label("Yeni parola (en az 12 karakter)").fill("a-brand-new-password")
    page.get_by_label("Yeni parola (tekrar)").fill("a-brand-new-password")
    page.get_by_role("button", name="Parolayı değiştir").click()
    sync_api.expect(page.locator("#msg")).to_contain_text("Parolanız değişti")
    sync_api.expect(page.get_by_role("heading", name="Giriş")).to_be_visible()
    _login(page, email, "a-brand-new-password")
    sync_api.expect(page.get_by_role("heading", name=HOME)).to_be_visible()


def test_monthly_count_through_ui(page, client, session: Session, tmp_path) -> None:
    import openpyxl

    api = new_tenant(session, client)
    api.post("/branches", {"name": "Taff Burger Merkez"})
    page.goto("/ui/#/login")
    page.evaluate("sessionStorage.setItem('period', '2026-08')")
    _login(page, _owner_email(session))
    _nav(page, "Ürün Listesi")
    page.get_by_role("button", name="Taff Burger sayım formundaki 98 ürünü yükle").click()
    sync_api.expect(page.locator("#msg")).to_contain_text("98 ürün eklendi")
    page.get_by_label("DANA KIYMA birim fiyat").fill("400")
    page.get_by_label("DANA KIYMA birim fiyat").press("Tab")
    sync_api.expect(page.locator("#msg")).to_contain_text("DANA KIYMA güncellendi")
    page.get_by_label("Ürün ara").fill("ketçap")
    sync_api.expect(page.locator("main tbody tr:visible")).to_have_count(3)  # 3 ketçap

    _nav(page, "Aylık Sayım")
    sync_api.expect(page.get_by_role("heading", name=SHEET)).to_be_visible()
    for field, value in (("opening", "10"), ("incoming", "50"), ("used", "45"), ("counted", "12")):
        page.get_by_label(f"DANA KIYMA {field}").fill(value)
    row = page.locator("tr", has=page.get_by_role("cell", name="DANA KIYMA", exact=True))
    # computed live, before saving: 10 + 50 - 45 = 15 expected, 12 counted -> -3 eksik
    sync_api.expect(row).to_contain_text("15")
    sync_api.expect(row).to_contain_text("-3")
    sync_api.expect(row).to_contain_text("EKSİK")
    sync_api.expect(row).to_contain_text("1.200,00")
    # 2 kg zayi is deducted: expected 13, difference -1 (-400 ₺); zayi 800 ₺ in the summary
    page.get_by_label("DANA KIYMA waste").fill("2")
    sync_api.expect(row).to_contain_text("13")
    sync_api.expect(row).to_contain_text("-1")
    sync_api.expect(row).to_contain_text("400,00")
    sync_api.expect(page.locator(".kpi.zayi")).to_contain_text("800,00")
    # Enter moves down the same column
    page.get_by_label("DANA KIYMA counted").press("Enter")
    assert page.evaluate("document.activeElement.getAttribute('aria-label')").endswith("counted")
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") != (
        "DANA KIYMA counted"
    )
    page.get_by_label("PATATES counted").fill("12,5")  # Turkish decimal comma
    page.get_by_label("Sayımı yapan").fill("Ali Veli")

    # leaving with unsaved changes asks first; dismissing keeps the user on the sheet
    page.once("dialog", lambda d: d.dismiss())
    _nav(page, "Panel")
    sync_api.expect(page.get_by_role("heading", name=SHEET)).to_be_visible()
    sync_api.expect(page.get_by_label("PATATES counted")).to_have_value("12,5")

    page.get_by_role("button", name=re.compile("^Kaydet")).click()
    sync_api.expect(page.locator("#msg")).to_contain_text("Sayım kaydedildi")
    page.reload()
    sync_api.expect(page.get_by_label("DANA KIYMA counted")).to_have_value("12")
    sync_api.expect(page.get_by_label("DANA KIYMA waste")).to_have_value("2")
    sync_api.expect(page.get_by_label("PATATES counted")).to_have_value("12,5")
    sync_api.expect(page.get_by_label("Sayımı yapan")).to_have_value("Ali Veli")
    db = session.execute(
        text(
            "SELECT l.counted FROM monthly_count_lines l JOIN count_items i ON i.id = l.item_id "
            "WHERE i.name = 'PATATES'"
        )
    ).scalar()
    assert str(db) == "12.500"

    page.get_by_label("Filtre").select_option("eksik")
    sync_api.expect(page.locator(".count-table tbody tr:not(.group):visible")).to_have_count(1)
    page.get_by_label("Filtre").select_option("all")

    with page.expect_download() as dl:
        page.get_by_role("button", name="Excel indir").click()
    path = tmp_path / "sayim.xlsx"
    dl.value.save_as(path)
    ws = openpyxl.load_workbook(path).active
    names = [ws.cell(r, 2).value for r in range(1, ws.max_row + 1)]
    assert "DANA KIYMA" in names and "TOP DOWN KETÇAP" in names

    page.on("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Ayı kapat").click()
    sync_api.expect(page.locator("#msg")).to_contain_text("ürün sayılmadı")
    page.get_by_role("button", name="Boşlara 0 yaz").click()
    page.get_by_role("button", name="Ayı kapat").click()
    sync_api.expect(page.locator("#msg")).to_contain_text("Ay kapatıldı")
    sync_api.expect(page.locator("main")).to_contain_text("KAPATILDI")
    sync_api.expect(page.get_by_label("DANA KIYMA counted")).to_be_disabled()

    _nav(page, "Panel")
    sync_api.expect(page.locator(".kpi.eksik")).to_contain_text("400,00")
    sync_api.expect(page.locator("main")).to_contain_text("En çok eksik veren ürünler")
    page.get_by_role("cell", name="DANA KIYMA").first.click()
    sync_api.expect(page.get_by_role("heading", name="Ürün Analizi")).to_be_visible()
    sync_api.expect(page.locator("main table")).to_contain_text("Ağustos 2026")

    _nav(page, "Geçmiş Aylar")
    sync_api.expect(page.locator("main table")).to_contain_text("Ali Veli")
    sync_api.expect(page.locator("main table")).to_contain_text("98 / 98")
    # the only console error allowed is the browser's log of that deliberate 422
    assert [e for e in page.errors if "422" not in e] == []


def test_users_and_roles_through_ui(page, client, session: Session) -> None:
    api = new_tenant(session, client)
    branch = setup_branch(api)
    api.put(f"/branches/{branch}/monthly-counts/2026-08", {"lines": []})
    page.goto("/ui/#/login")
    page.evaluate("sessionStorage.setItem('period', '2026-08')")
    _login(page, _owner_email(session))
    _nav(page, "Kullanıcılar")
    page.get_by_label("E-posta").fill("sayim@example.com")
    page.get_by_label("İlk parola (en az 12 karakter)").fill(PASSWORD)
    page.get_by_label("Yeni kullanıcı rolü").select_option("counter")
    page.get_by_role("button", name="Kullanıcı ekle").click()
    sync_api.expect(page.locator("#msg")).to_contain_text("sayim@example.com eklendi")
    sync_api.expect(page.get_by_label("sayim@example.com rol")).to_have_value("counter")

    # a counter enters numbers but cannot close the month or manage anything
    Api(client, "sayim@example.com")
    page.get_by_role("button", name="Çıkış").click()
    _login(page, "sayim@example.com")
    nav = page.get_by_role("navigation")
    for hidden in ("Kullanıcılar", "Şubeler", "Kayıt Geçmişi"):
        sync_api.expect(nav.get_by_role("link", name=hidden)).to_be_hidden()
    _nav(page, "Aylık Sayım")
    sync_api.expect(page.get_by_label("DANA KIYMA counted")).to_be_enabled()
    sync_api.expect(page.get_by_role("button", name="Ayı kapat")).to_have_count(0)
    _nav(page, "Ürün Listesi")
    sync_api.expect(page.get_by_label("DANA KIYMA birim fiyat")).to_be_disabled()
    assert page.errors == []


def test_recipes_and_sales_through_ui(page, client, session: Session) -> None:
    api = new_tenant(session, client)
    branch = setup_branch(api)
    items = {i["name"]: i["id"] for i in api.get("/count-items").json()}
    page.goto("/ui/#/login")
    page.evaluate("sessionStorage.setItem('period', '2026-08')")
    _login(page, _owner_email(session))

    # package content for a bucket counted in pieces
    _nav(page, "Ürün Listesi")
    page.get_by_label("KOVA KETÇAP paket içeriği", exact=True).fill("9,5")
    page.get_by_label("KOVA KETÇAP paket içeriği birimi").select_option("KG")
    sync_api.expect(page.locator("#msg")).to_contain_text("KOVA KETÇAP güncellendi")

    _nav(page, "Menü & Reçeteler")
    for name, sauce in (("Klasik Burger", "20"), ("Smash Burger", "15")):
        page.get_by_label("Yeni menü ürünü").fill(name)
        page.get_by_role("button", name="Ekle", exact=True).click()
        sync_api.expect(page.locator("#msg")).to_contain_text(f"{name} eklendi")
        page.get_by_label(f"{name} malzeme 1").select_option(items["YOUKİ SOS"])
        page.get_by_label(f"{name} miktar 1").fill(sauce)
        card = page.locator(".recipe-card", has=page.get_by_label(f"{name} malzeme 1"))
        card.get_by_role("button", name="Malzeme ekle").click()
        page.get_by_label(f"{name} malzeme 2").select_option(items["KOVA KETÇAP"])
        page.get_by_label(f"{name} miktar 2").fill("19")
        card.get_by_role("button", name="Reçeteyi kaydet").click()
        sync_api.expect(page.locator("#msg")).to_contain_text(f"{name} reçetesi kaydedildi")
    sync_api.expect(page.locator("main")).to_contain_text("= 0,02 KG / adet")

    _nav(page, "Aylık Sayım")
    page.get_by_label("Klasik Burger satılan").fill("120")
    page.get_by_label("Smash Burger satılan").fill("80")
    row = page.locator("tr", has=page.get_by_role("cell", name="YOUKİ SOS", exact=True))
    # 120 × 20 g + 80 × 15 g = 3,6 kg, live before saving
    sync_api.expect(row.locator("td.recipe")).to_have_text("3,6")
    # 200 × 19 g = 3,8 kg = 0,4 bucket of 9,5 kg
    kova = page.locator("tr", has=page.get_by_role("cell", name="KOVA KETÇAP", exact=True))
    sync_api.expect(kova.locator("td.recipe")).to_have_text("0,4")
    page.get_by_label("YOUKİ SOS opening").fill("5")
    page.get_by_label("YOUKİ SOS counted").fill("1")
    sync_api.expect(row).to_contain_text("1,4")  # expected 5 - 3,6
    sync_api.expect(row).to_contain_text("-0,4")
    page.get_by_role("button", name=re.compile("^Kaydet")).click()
    sync_api.expect(page.locator("#msg")).to_contain_text("Sayım kaydedildi")
    page.reload()
    sync_api.expect(page.get_by_label("Klasik Burger satılan")).to_have_value("120")
    sync_api.expect(row.locator("td.recipe")).to_have_text("3,6")
    db = session.execute(
        text(
            "SELECT l.recipe_used FROM monthly_count_lines l JOIN count_items i "
            "ON i.id = l.item_id WHERE i.name = 'YOUKİ SOS'"
        )
    ).scalar()
    assert str(db) == "3.600"
    assert branch
    assert page.errors == []


def test_menu_boards_through_ui(page, client, session: Session) -> None:
    api = new_tenant(session, client)
    setup_branch(api)
    _login(page, _owner_email(session))
    _nav(page, "Menü & Reçeteler")
    page.get_by_role("button", name="Taff Burger menüsünü yükle").click()
    sync_api.expect(page.locator("#msg")).to_contain_text("19 menü ürünü")
    report = page.locator("#menu-report")
    sync_api.expect(report).to_contain_text("Sayım listesinde olmayan 18 malzeme")
    sync_api.expect(report).to_contain_text("CHEDAR PEYNİRİ")
    page.get_by_role("button", name=re.compile("malzemeyi Ürün Listesi'ne ekle")).click()
    sync_api.expect(page.locator("#msg")).to_contain_text("18 yeni malzeme")
    sync_api.expect(page.get_by_label("Menü ürünü adı").first).to_have_value("YOUKI")
    sync_api.expect(page.locator("main")).to_contain_text("= 0,075 KG / adet")
    sync_api.expect(page.locator("main")).to_contain_text("düşülmüyor")
    _nav(page, "Aylık Sayım")
    sync_api.expect(page.locator(".warn-box")).to_contain_text("CHEDAR PEYNİRİ")
    sync_api.expect(page.get_by_label("YOUKI satılan")).to_be_visible()
    assert page.errors == []


def test_supplier_data_and_food_cost_through_ui(page, client, session: Session) -> None:
    api = new_tenant(session, client)
    setup_branch(api)
    api.post("/menu-products/load-defaults?add_missing_items=true")
    _login(page, _owner_email(session))
    _nav(page, "Ürün Listesi")
    page.get_by_role("button", name="Tedarikçi fiyat ve paket bilgilerini uygula").click()
    sync_api.expect(page.locator("#msg")).to_contain_text("güncellendi")
    report = page.locator("#supplier-report")
    sync_api.expect(report).to_contain_text("TÜRKOVA")
    sync_api.expect(report).to_contain_text("1 KG = 70 ADET")
    sync_api.expect(page.get_by_label("CHEDAR PEYNİRİ birim fiyat")).to_have_value("505,00")
    _nav(page, "Menü & Reçeteler")
    summary = page.locator("section", has=page.get_by_role("heading", name="Maliyet özeti"))
    sync_api.expect(summary).to_contain_text("YOUKI")
    sync_api.expect(summary).to_contain_text("67,91")
    sync_api.expect(page.get_by_label("YOUKI satış fiyatı")).to_have_value("610")
    assert page.errors == []
