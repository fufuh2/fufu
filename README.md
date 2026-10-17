# OperationOS-X (fufu)

Çok şubeli restoran / hızlı servis işletmeleri için operasyon sistemi: sipariş → reçete → stok → fire →
sayım → fark → maliyet/marj; transfer, satın alma, kalite kontrol, vardiya, POS webhook ve web arayüzü
(`/ui`). Tamamen açık kaynak bileşenlerle, ücretli servis gerektirmeden çalışır.

Neyin yapıldığı, neyin yapılmadığı ve kanıtları: **[docs/STATUS.md](docs/STATUS.md)**.
Son denetim ve canlıya hazırlık kararı: **[docs/audit/RELEASE_READINESS.md](docs/audit/RELEASE_READINESS.md)**.

## Yapı

```
backend/
  app/domain/      saf iş kuralları (birim dönüşümü, maliyet, marj, izinler)
  app/services/    transaction'lı servisler (stok, reçete, sipariş, rapor, yönetim)
  app/api/         FastAPI router'ları ve şemalar
  migrations/      Alembic (append-only trigger'lar dahil)
  tests/           unit + gerçek PostgreSQL üzerinde entegrasyon / eşzamanlılık testleri
docs/
  STATUS.md, requirements/, architecture/ (ADR, formüller), security/, research/
```

## Yerelde çalıştırma

Gereken: Python ≥ 3.12, PostgreSQL 16.

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.lock

export OPSX_DATABASE_URL=postgresql+psycopg://ops:ops@localhost:5432/opsx
export OPSX_JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
export OPSX_ENV=development   # /docs yalnızca development'ta açık; varsayılan production
export OPSX_INTEGRATION_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))")  # POS webhook için
alembic upgrade head
OPSX_OWNER_PASSWORD='en-az-12-karakter' python -m app.cli create-tenant --name "Demo" --owner-email owner@example.com
uvicorn app.main:create_app --factory --reload   # arayüz: http://127.0.0.1:8000/ui/  API: /docs
```

Docker Compose: `cp .env.example .env`, değerleri doldur, `docker compose up --build`.
(Compose dosyası yazıldı ama bu repo oluşturulurken çalıştırılamadı — bkz. STATUS.)

## Testler

Testler adı `_test` ile biten, içeriği silinebilir bir veritabanı ister:

```bash
createdb -O ops opsx_test
export OPSX_TEST_DATABASE_URL=postgresql+psycopg://ops:ops@localhost:5432/opsx_test
cd backend && pytest && ruff check . && mypy app && alembic check
```

Tarayıcı testleri (`tests/ui`) Playwright + Chromium ister: `playwright install chromium`
(ya da `OPSX_CHROMIUM_PATH`). Tarayıcı yoksa bu testler **skipped** görünür, gizlenmez.

`OPSX_DATABASE_URL` ve `OPSX_JWT_SECRET` zorunludur; varsayılan değerleri yoktur.

Canlı sunucuya karşı denetim akışı (yalnızca adı `audit` içeren bir veritabanında çalışır):

```bash
python scripts/audit_e2e.py http://localhost:8000 $OPSX_DATABASE_URL owner@example.com '<parola>'
```

## API özeti (`/api/v1`)

| Metot | Yol | İzin |
|---|---|---|
| POST | `/auth/login` (hesap başına 15 dk'da 5 hatalı denemeden sonra 429) | — |
| POST/GET | `/branches` | `branch.manage` / herkes (kapsamına göre) |
| POST | `/users` | `user.manage` |
| POST/GET | `/items`, `/products` | `catalog.manage` |
| POST | `/products/{id}/recipes` | `recipe.manage` |
| POST | `/branches/{id}/receipts` · `/waste` · `/counts` | `stock.receive` · `stock.waste` · `stock.count` |
| GET | `/branches/{id}/stock` · `/movements` | `stock.read` |
| POST | `/branches/{id}/orders` (`Idempotency-Key` başlığı zorunlu) | `order.create` |
| POST | `/orders/{id}/cancel` | `order.cancel` |
| GET | `/branches/{id}/reports/variance?from=&to=` · `/reports/margin` | `report.read` |
| GET | `/audit-events` | `audit.read` |
| GET | `/me` · POST `/auth/password` · POST `/auth/logout-all` | oturum sahibi |
| PATCH | `/items/{id}` · `/products/{id}` · `/branches/{id}` · `/users/{id}` | `catalog.manage` · `branch.manage` · `user.manage` |
| GET | `/products/{id}/cost?branch_id=` (teorik reçete maliyeti) | `stock.read` |
| POST/GET | `/branches/{id}/transfers` · POST `/transfers/{id}/receive` · `/cancel` | `stock.transfer` · `stock.receive` |
| POST/GET | `/suppliers` · `/suppliers/{id}/prices` | `purchase.manage` |
| POST/GET | `/branches/{id}/purchase-orders` · `/purchase-orders/{id}/submit` · `/approve` · `/receipts` · `/invoices` · `/close` · `/cancel` | `purchase.manage` · `purchase.approve` · `stock.receive` |
| GET | `/branches/{id}/purchase-suggestions` | `stock.read` |
| POST/GET | `/checklist-templates` · `/branches/{id}/checklist-runs` · `/nonconformities/{id}/assign` · `/resolve` | `quality.manage` · `quality.record` |
| GET | `/branches/{id}/reports/quality` · `/reports/hours` · `/reports/tasks` | `report.read` · `shift.manage` |
| POST/GET | `/branches/{id}/shifts` · `/attendance/clock-in` · `/attendance/clock-out` · `/tasks` · `/handovers` | `shift.manage` · `shift.self` · `task.complete` |
| POST/PUT/GET | `/integrations` · `/integrations/{id}/mappings` · `/events` · `/rotate-secret` | `integration.manage` (yalnızca owner) |
| POST | `/webhooks/pos/{integration_id}` (HMAC imzalı, token yok) | imza |
