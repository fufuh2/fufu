# Taff Burger · Aylık Sayım (fufu)

Restoranın aylık envanter sayımı: her ürün için **devir + gelen − satıştan (menü satışı × reçete) −
diğer kullanım − zayi = beklenen**,
**sayılan − beklenen = fark** (fazla / eksik), TL karşılıkları, aylar arası devir, ay kapatma, Excel çıktısı,
panel ve ürün bazlı analiz. Sistemde yalnızca aylık sayım motoru vardır.

Canlı: https://opsx-api.onrender.com/ui/ · Kullanım: [docs/AYLIK_SAYIM.md](docs/AYLIK_SAYIM.md) ·
Kurulum: [docs/DEPLOY.md](docs/DEPLOY.md) · Durum ve sınırlar: [docs/STATUS.md](docs/STATUS.md)

## Yapı

```
backend/
  app/domain/      saf kurallar (sayım hesabı, roller ve izinler)
  app/services/    transaction'lı servisler (sayım, raporlar, Excel, yönetim)
  app/api/         FastAPI router'ı ve şemalar
  app/static/      web arayüzü (derleme adımı yok, üçüncü parti kod yok)
  app/data/        Taff Burger sayım formundaki 98 ürün
  migrations/      Alembic
  tests/           birim, entegrasyon (gerçek PostgreSQL) ve tarayıcı (Playwright) testleri
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
alembic upgrade head
OPSX_OWNER_PASSWORD='en-az-12-karakter' python -m app.cli create-tenant --name "Taff Burger" --owner-email owner@example.com
uvicorn app.main:create_app --factory --reload   # arayüz: http://127.0.0.1:8000/ui/
```

## Testler

Adı `_test` ile biten, içeriği silinebilir bir veritabanı gerekir:

```bash
createdb -O ops opsx_test
export OPSX_TEST_DATABASE_URL=postgresql+psycopg://ops:ops@localhost:5432/opsx_test
cd backend && pytest && ruff check . && mypy app && alembic check
```

Tarayıcı testleri (`tests/ui`) Playwright + Chromium ister. Tarayıcı yoksa **skipped** görünür;
`OPSX_REQUIRE_BROWSER=1` ile atlanmak yerine başarısız olur (CI böyle çalışır).

## Roller

| Rol | Yapabildikleri |
|---|---|
| İşletme sahibi (`owner`) | Her şey, tüm şubeler: kullanıcı, şube, ürün/fiyat, sayım, ay kapatma |
| Şube müdürü (`branch_manager`) | Atandığı şubelerde sayım girer, ayı kapatır / yeniden açar, kayıtları görür |
| Sayım personeli (`counter`) | Atandığı şubelerde sayım girer; ay kapatamaz, fiyat değiştiremez |
| İzleyici (`viewer`) | Atandığı şubelerin sayımlarını ve kayıtlarını görür; değiştiremez |

## API özeti (`/api/v1`)

| Metot | Yol | İzin |
|---|---|---|
| POST | `/auth/login` (hesap başına 15 dk'da 5 hatalı denemeden sonra 429) | — |
| GET · POST | `/me` · `/auth/password` · `/auth/logout-all` | oturum sahibi |
| GET · POST · PATCH | `/branches` · `/branches/{id}` | herkes (kapsamına göre) · `branch.manage` |
| GET · POST · PATCH | `/users` · `/users/{id}` · POST `/users/{id}/password` (parola sıfırlama) | `user.manage` |
| GET | `/audit-events?limit=&offset=&action=` | `audit.read` |
| GET · POST · PATCH | `/count-items` · `/count-items/{id}` · POST `/count-items/load-defaults` | `count.read` · `item.manage` |
| GET | `/branches/{id}/monthly-counts` (aylar ve toplamları) | `count.read` |
| GET · PUT | `/branches/{id}/monthly-counts/{YYYY-MM}` (GET ilk açılışta taslak oluşturur; PUT satırları ve `sales` alır) | `count.read` (+`count.edit` oluşturmak için) · `count.edit` |
| POST | `/branches/{id}/monthly-counts/{YYYY-MM}/close` · `/reopen` | `count.close` |
| GET | `/branches/{id}/monthly-counts/{YYYY-MM}/export.xlsx` | `count.read` |
| GET · POST · PATCH · PUT | `/menu-products` · `/menu-products/{id}` · `/menu-products/{id}/recipe` | `count.read` · `item.manage` |
| GET · POST | `/backup` (JSON yedek) · `/restore` (boş işletmeye geri yükleme) | `user.manage` |
| GET | `/branches/{id}/dashboard?period=` · `/branches/{id}/count-items/{item}/history` · `/monthly-counts/overview?period=` | `count.read` |
