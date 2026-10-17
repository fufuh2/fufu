# OperationOS-X (fufu)

Çok şubeli restoran / hızlı servis işletmeleri için operasyon backend'i. **Faz 1**: sipariş → reçete
→ stok → fire → sayım → fark → maliyet/marj zinciri, denetim izi ve tenant izolasyonu ile.

Neyin yapıldığı, neyin yapılmadığı ve kanıtları: **[docs/STATUS.md](docs/STATUS.md)**.

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
alembic upgrade head
OPSX_OWNER_PASSWORD='en-az-12-karakter' python -m app.cli create-tenant --name "Demo" --owner-email owner@example.com
uvicorn app.main:create_app --factory --reload   # http://127.0.0.1:8000/docs
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

## API özeti (`/api/v1`)

| Metot | Yol | İzin |
|---|---|---|
| POST | `/auth/login` | — |
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
