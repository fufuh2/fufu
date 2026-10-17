# Düzeltme Kaydı

Her düzeltme şu sırayla yapıldı: hatayı yeniden üreten testi yaz → düşmesini gör → en küçük
düzeltmeyi yap → testi ve tüm paketi yeniden çalıştır → canlı kritik akışı yeniden çalıştır.

| Bulgu | Değişen dosyalar | Düzeltmeden önce | Sonra | Ek doğrulama |
|---|---|---|---|---|
| FND-001 | `app/services/inventory.py` (`check_after_last_count`, kilit altında) | 201, bakiye 9 600 | 422, bakiye 9 800 | Tam paket + canlı akış |
| FND-002, 003 | `app/services/admin.py` (`authenticate`), `app/api/routes.py`, `app/errors.py`, `app/models.py` (indeks), `migrations/versions/0002_login_failure_index.py` | 8 × 401, log yok | 6. denemeden itibaren 429; `auth.login_failed` ve `auth.login` olayları | `alembic` upgrade/downgrade/check; canlı akış adım 17 |
| FND-004 | `app/services/inventory.py` (`convert`) | 500 DivisionByZero | 422 | — |
| FND-005 | `app/main.py` (`DataError` işleyici) | 500 NumericValueOutOfRange | 422, stok değişmedi | — |
| FND-006 | `app/main.py` (`RequestValidationError` işleyici) | Parola 422 gövdesinde | Yok | Mevcut 422 testleri hâlâ geçiyor |
| FND-007 | `app/config.py`, `app/main.py`, `docker-compose.yml`, `.env.example`, `README.md` | `/docs` 200 (env yok) | 404; `development`'ta 200 | Temiz kurulumda `/docs` 200 (dev) |
| FND-008 | `app/main.py` (`/readyz`) | 404 | 200 `database: ok` | Temiz kurulum, canlı sunucu |
| FND-009 | `app/services/admin.py` (`bootstrap_tenant`) | Giriş yapılamayan sahip oluşuyor | `Invalid` | — |
| FND-010 | `app/config.py`, `migrations/env.py` | Varsayılan `ops:ops` DSN | Değişken zorunlu | CI zaten değişkeni veriyor |
| FND-011 | `docs/STATUS.md` | "CI koşmadı" | Run 38985361339 kaydedildi | — |

Ayrıca:
- `hypothesis` dev bağımlılığına eklendi ve `requirements-dev.lock` yeniden üretildi. Aksi halde yeni
  testler CI'da import hatası verirdi.
- `scripts/audit_e2e.py`: Güvenlik kontrolü `assert` yerine açık `if` ile yapılıyor
  (`python -O` altında assert'ler kaldırılır).

## Regresyon kanıtı
Düzeltmeler sonrası son koşu: **79 passed, 1 xfailed** (FND-012, bilinçli olarak açık).
Düzeltmelerden önce geçen 58 testin hiçbiri silinmedi veya gevşetilmedi. `git diff` ile
`tests/unit` ve `tests/integration` altında değişiklik yok.

## Düzeltilmeyenler ve gerekçe
FND-012 (iş kararı gerekiyor), FND-013 (yeni özellik), FND-014 (mimari iş), FND-015 (yeni özellik),
FND-016–020 (P3). Bunlar denetim kapsamında "en küçük güvenli düzeltme" sınırını aşıyor.
