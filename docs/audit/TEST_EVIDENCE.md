# Test Kanıtları

Önceki (geliştirme sırasındaki) sonuçlar ile bu denetimin sonuçları ayrı tutulmuştur.
Tüm komutlar `backend/` dizininde, izole PostgreSQL 16 üzerinde çalıştırıldı. Üretim verisi yok.

## A. Önceki sonuç (denetim öncesi, referans için)
Commit `6f2bfac`: yerelde 58 passed. GitHub Actions run **38985361339** (Python 3.12) `success`.
Bu denetimin ilk koşusu **58 error** verdi. Neden: konteyner yeniden başlamış ve PostgreSQL kapalıydı
(`16/main (port 5432): down`). Ürün hatası değil, ortam olayı. Servis başlatıldıktan sonra: **58 passed**.

## B. Denetimin son test koşusu
```
2026-10-17T11:30:32Z
OPSX_TEST_DATABASE_URL=postgresql+psycopg://ops:ops@localhost:5432/opsx_test
pytest -p no:warnings -o addopts="" -q -rxX
→ 79 passed, 1 xfailed in 52.15s
```
| Metrik | Değer |
|---|---|
| Toplanan test vakası | 80 (unit 20, integration 38, audit 22) |
| Benzersiz test fonksiyonu | 62 (fark: parametrize vakaları) |
| Geçen | 79 |
| Başarısız | 0 |
| Bilinen açık hata (xfail strict) | 1 — FND-012 |
| Engellenen (BLOCKED) | Docker build/compose, tarayıcı testleri (frontend yok) |
| Atlanan | 0 |
| Hypothesis | `test_api_matches_reference_model` 1 vaka olarak sayıldı; içinde 60 rastgele işlem dizisi (her biri en çok 15 işlem) |

Aynı paketin tekrar koşuları ayrı test olarak sayılmadı.

Statik kontroller (aynı ağaç): `ruff check .` temiz · `ruff format --check .` 40 dosya biçimli ·
`mypy app` 0 hata · `alembic check` "No new upgrade operations detected".

## C. Bağımsız hesap doğrulaması
`tests/audit/test_reference_model.py`, `app.domain` ve `app.services` modüllerini import etmeyen
ayrı bir referans model. Her işlemden sonra API'deki miktar ve ortalama maliyet, dönem sonunda da
gelir, SMM, brüt kâr ve marj modelle **birebir** karşılaştırılıyor. Bakiye ↔ defter uzlaşması doğrudan SQL ile yapılıyor.

Modelin hata yakalayıp yakalamadığı mutasyonla sınandı (kod geri alındı, `git status` temiz):
| Mutasyon | Sonuç |
|---|---|
| `moving_average_cost`: `on_hand <= 0` → `< 0` | Yakalanmadı. **Eşdeğer mutant**: on_hand = 0 iken iki formül de aynı sonucu verir. |
| Satır maliyetine +0,01 eklemek | Yakalandı (1 failed) |
| Ortalama paydasına +0,0001 eklemek | Yakalandı (1 failed) |

Geliştirme aşamasındaki mutasyonlar (`FOR UPDATE` kaldırma, negatif stok kontrolünü kaldırma, tenant
filtresini kaldırma) da ilgili testleri düşürmüştü (bkz. `docs/STATUS.md`).

## D. Kritik akış — canlı sunucu (istemin 5. bölümü)
Ortam: Sıfırdan oluşturulan `opsx_audit` veritabanı, `alembic upgrade head`, CLI ile tenant,
`uvicorn` süreci (port 8078). İstemci gerçek HTTP (`httpx`); DB doğrulaması ayrı `psycopg` bağlantısıyla yapıldı.
Komut: `python scripts/audit_e2e.py http://localhost:8078 $OPSX_DATABASE_URL owner@audit-example.com <parola>` → çıkış kodu 0.

Elle hesaplanan beklenen değerler: Et 10 kg @400 + 5 kg @520 → ort. (4000+2600)/15000 = **0,44 TL/g**.
Burger teorik maliyeti 150×0,44 + 5 = **71,00**. 4 burger, 100 TL indirim → gelir **900**, SMM **284**,
kâr **616**, marj 616/900 = **%68,44**. İptal edilen 2 burger netleşiyor. Fire 300 g = 132,00.
Sayım 14 kg (sistem 14 100) → açıklanamayan 100 g = 44,00. Gerçek tüketim 600+300+100 = 1 000 g.

```
PASS 1 branch created in DB
PASS 2 kitchen user created
PASS 4 receipts accepted
PASS 5 beef qty 15000 g
PASS 5 beef avg 0.44
PASS 5 bun qty 30 @5
PASS 6 recipe v1
PASS 8 order created
PASS 8 net 900.00
PASS 7/8 line cost 284.00 (=4x71.00)
PASS 8 replay returns same order, 200
PASS 9 beef 14400
PASS 9 bun 26
PASS 10 cancel ok
PASS 10 stock restored
PASS 10 avg unchanged after reversal
PASS 11 waste cost -132.00
PASS 12 count posted
PASS 13 theoretical 600
PASS 13 waste 300
PASS 13 unexplained 100 / 44.00
PASS 13 actual 1000 = 600+300+100
PASS 13 bun ok
PASS 14 revenue 900 / cogs 284 / profit 616 / 68.44%
PASS 15 margin revenue == SUM(order_lines)
PASS 15 ledger == balance (8ee258e8)
PASS 15 ledger == balance (9dbb0fa9)
PASS 15 exactly 2 orders (no duplicate)
PASS 15 movement counts
PASS 15 beef final 14000
PASS 16 kitchen margin 403
PASS 16 kitchen receipt 403
PASS 16 kitchen cancel 403
PASS 16 no token 401
PASS 17 audit has all actions
PASS 17 order.create logged once for o1

36 checks, 36 passed, 0 failed
```
Not: İstemdeki adım 3 (malzeme) ve adım 7 (teorik maliyet) için ayrı kontrol satırı yok. Adım 3,
sonraki adımların malzeme ID'lerine dayanmasıyla dolaylı doğrulandı. Adım 7 için endpoint yok (FND-019);
satış satırı maliyetiyle doğrulandı.

## E. Yedek / geri yükleme
```
pg_dump -Fc opsx_audit → 51 106 bayt ; pg_restore → opsx_audit_restore
                 movements orders audit Σbakiye     uyuşmazlık alembic
opsx_audit          11      2     16   14026.0000    0         0002
opsx_audit_restore  11      2     16   14026.0000    0         0002
UPDATE stock_movements (restore) → ERROR: stock_movements is append-only (UPDATE rejected)
DELETE FROM audit_events (restore) → ERROR: audit_events is append-only (DELETE rejected)
```
Otomatik yedekleme zamanlaması veya PITR **yok**; yalnızca manuel dump/restore doğrulandı.

## F. Migration
`opsx_gen` üzerinde: `upgrade head` → `check` → `downgrade base` → `upgrade head` → `check`, hepsi başarılı.
Test oturumu her koşuda şemayı silip `upgrade head` uyguluyor (ayrıca temiz kurulumda da).

## G. Temiz kurulum (README adımları)
Git'teki dosyalar ayrı bir dizine kopyalandı. `uv venv -p 3.12` (Python 3.12.3) ve
`requirements-dev.lock` ile yeni veritabanında: `alembic upgrade head` → CLI tenant → uvicorn →
`/healthz` 200, `/readyz` `{"database":"ok"}`, `/docs` 200 (`OPSX_ENV=development`) →
`pytest` **79 passed** (xfail eklenmeden önceki ağaç) · ruff temiz · mypy 0 · alembic check temiz.
README'den sapma: `pip` yerine `uv pip install` kullanıldı (aynı lock dosyası).

## H. Güvenlik taramaları
| Araç | Sonuç |
|---|---|
| `bandit -r app` | No issues identified (High/Medium/Low 0) |
| `pip-audit -r requirements.lock` | No known vulnerabilities found (2026-10-17) |
| `detect-secrets scan --all-files` | `app/config.py` ve `migrations/env.py` içinde varsayılan DSN → FND-010 düzeltildi. Kalan eşleşmeler test sabitleri ve araç cache etiketleri. |
| Sunucu log taraması | 36 satır; `password`, kullanılan parolalar ve `Bearer` için 0 eşleşme; 5xx yok |

## I. Rol × endpoint matrisi
`test_role_endpoint_matrix`: 9 rol × 10 yazma/okuma işlemi = 90 çağrı. Beklenti tablosu, kodun izin
eşlemesine bakılmadan gereksinim belgesindeki rol tanımlarından yazıldı. Uyuşmazlık: **0**.

## J. Çalıştırılamayanlar
| Kontrol | Durum | Neden |
|---|---|---|
| `docker build`, `docker compose up` | BLOCKED | Ortamda Docker daemon yok |
| Tarayıcı / UX / erişilebilirlik | NOT IMPLEMENTED | Frontend yok |
| Harici entegrasyon (POS, webhook) | NOT IMPLEMENTED | Entegrasyon yok; mock da yok |
| Yük / performans | UNVERIFIED | Çalıştırılmadı |
| `/readyz` DB kapalıyken 503 | UNVERIFIED | Çalıştırılmadı |

## K. Faz 2 son koşu (2026-10-17T12:57:27Z)
Komut: `pytest -o addopts="" -q -p no:warnings -rfsxX` (`backend/`), PostgreSQL 16, Chromium 141 (Playwright 1.64).

| Metrik | Değer |
|---|---|
| Toplanan test vakası | 126 (unit 20 · integration 75 · audit 22 · ui 9) |
| Benzersiz test fonksiyonu | 108 |
| Geçen / başarısız / atlanan / xfail | 126 / 0 / 0 / 0 — aynı ağaçta art arda 3 koşu |
| Kararsızlık avı | Tüm paket 6 tekrarda 1 hata (FND-024) → düzeltme → izole 30/30, UI paketi 5/5, tam paket 3/3 |
| Canlı sunucu akışı | 36/36, `OPSX_ENV=production`, `/docs` 404, `/ui/` 200, logda 500 yok |
| Migration | 0001→0007 upgrade, `alembic check`, base'e downgrade, tekrar upgrade: temiz; ayrıca CHECK kısıtı karşılaştırma testi |
| Statik | ruff temiz · mypy 0 hata (29 dosya) · bandit 0 bulgu (5 695 satır) · pip-audit (runtime ve dev lock) bilinen açık yok |

Mutasyon kontrolleri (kod geri alındı): FND-012 offset'i kapatmak → 1 test düştü. Token sürüm
kontrolünü kapatmak → 2 test düştü. 0005'ten CHECK kısıtını çıkarmak → şema testi düştü.

Tekrar koşuları ayrı test olarak sayılmadı.

**Bu tablo "hatasız" anlamına gelmez.** Gerçek kullanıcı, yük, Docker ve gerçek POS testleri yok (bkz. RELEASE_READINESS).
