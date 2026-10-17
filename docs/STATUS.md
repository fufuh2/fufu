# Durum Raporu — Faz 1

Bu dosya neyin yapıldığını, neyin **yapılmadığını** ve her iddianın kanıtını listeler.
Kanıtı olmayan bir şey "tamam" sayılmaz.

## Başlangıç durumu

Repo, yalnızca `README.md` içeren tek bir commit'ten oluşuyordu. Korunacak mevcut kod, şema veya
kullanıcı çalışması yoktu; proje sıfırdan kuruldu.

## Kapsam kararı

Ana istem bir yazılım şirketinin yıllarca yapacağı işi tarif ediyor. Faz 1, **sipariş → reçete →
stok → fire → sayım → fark → maliyet/marj** zincirinin uçtan uca, denetim izli ve tenant
izolasyonlu çalışan bir backend dilimi olarak sınırlandı. Bu zincir, istemdeki 12 sorunun
1–6, 10 ve 12. maddelerinin çekirdeğidir. Diğer modüller bilinçli olarak sonraki fazlara bırakıldı.

## Yapılanlar (kanıtlı)

| Alan | Kanıt |
|---|---|
| Değiştirilemez stok defteri (UPDATE/DELETE DB trigger'ı ile reddedilir) | `migrations/versions/0001_initial_schema.py`, `test_ledgers_are_append_only` |
| Eşzamanlı stok tüketiminde kayıp güncelleme yok | `test_concurrent_waste_cannot_overdraw`, `test_concurrent_orders_consume_exactly` |
| Sipariş idempotency (tekrar, farklı gövde, eşzamanlı aynı anahtar) | `test_idempotent_replay_and_conflict`, `test_concurrent_same_idempotency_key_creates_one_order` |
| Ölçü birimi dönüşümü, boyutlar arası tahmin yok | `app/domain/units.py`, `TestUnits` |
| Reçete versiyonlama; eski satış maliyeti yeniden yazılmaz; geriye tarihleme reddedilir | `test_recipe_versions_do_not_rewrite_history` |
| Hazırlama kaybı (loss %) ile brüt çıkış miktarı | `test_preparation_loss_increases_gross_issue` |
| Hareketli ağırlıklı ortalama maliyet, Decimal aritmetik | `app/domain/costing.py`, `TestCosting` |
| Fire nedenleri (9 kategori, "hırsızlık" etiketi yok) | `WASTE_REASONS`, `test_invalid_waste_inputs_rejected` |
| Sayım → fark hareketi; sayım yoksa fark "bilinmiyor" (0 değil) | `test_variance_without_count_is_unknown`, e2e testi |
| Marj: sıfır gelirde tanımsız, reçetesiz satışta "eksik maliyet" | `test_fully_discounted_sale_margin_undefined`, `test_missing_recipe_means_unknown_cost_not_zero` |
| İptal → stok iadesi, orijinal maliyetle | `test_cancel_reverses_stock_at_original_cost` |
| Tenant izolasyonu (okuma, yazma, çapraz ID kullanımı) | `test_tenant_isolation` |
| Şube kapsamlı roller, rol bazlı izin, yetki yükseltme engeli | `test_branch_scope_for_branch_users`, `test_role_permissions_enforced` |
| JWT: süresi geçmiş, sahte imza, `alg=none` reddi; pasif kullanıcı token'ı geçersiz | `test_expired_and_forged_tokens_rejected`, `test_deactivated_user_token_stops_working` |
| Denetim izi (append-only, şube kapsamlı okuma) | `audit_events`, e2e testinde aksiyon listesi, `test_audit_log_is_branch_scoped` |
| Bölüm 10.4 senaryosu, sayısal doğrulamalı | `tests/integration/test_e2e_flow.py` |
| Migration ↔ model tutarlılığı, upgrade/downgrade | `alembic check`, CI adımı |

### Bu oturumda çalıştırılan komutlar ve sonuçları

- `pytest` → **58 passed** (PostgreSQL 16, gerçek veritabanı, mock yok)
- `ruff check .` → temiz; `ruff format --check .` → temiz; `mypy app` → 0 hata
- `alembic upgrade head && alembic check && alembic downgrade base && alembic upgrade head` → başarılı
- `pip-audit` (venv) → "No known vulnerabilities found" (tarama tarihi: 2026-10-17)
- Uvicorn ile gerçek HTTP duman testi: `/healthz` 200, login, şube oluşturma, yanlış parola 401
- **Mutasyon kontrolü** (testlerin gerçekten hata yakaladığını görmek için kod bilerek bozuldu):
  - `FOR UPDATE` kaldırıldı → 2 eşzamanlılık testi düştü
  - negatif stok kontrolü kaldırıldı → 2 test düştü
  - şube sorgusundan tenant filtresi kaldırıldı → 2 test düştü
  - idempotency hızlı yolu kaldırıldı → test düşmedi. Neden: unique constraint + savepoint yolu
    aynı korumayı sağlıyor; hızlı yol yalnızca optimizasyon. Bu bir test boşluğu değil, yedekli tasarım.

## Doğrulanmayanlar

- **Docker / docker-compose**: Bu ortamda Docker daemon yok. `Dockerfile` ve
  `docker-compose.yml` yazıldı ama **hiç çalıştırılmadı**.
- **GitHub Actions CI**: Workflow yazıldı, GitHub'da **koşmadı**. Yerelde aynı adımlar
  Python 3.13 ile koştu; CI Python 3.12 kullanıyor (lock dosyaları 3.12 için üretildi).
- **Rakip analizi**: Bu oturumda web kaynağı taranmadı. `docs/research/COMPETITOR_ANALYSIS.md`
  bunu açıkça belirtir.
- **Türk iş hukuku / KVKK**: Araştırılmadı. Vardiya modülü yazılmadığı için henüz bağlayıcı değil.
- **Performans / yük testi**: Yapılmadı. Sadece 12 eşzamanlı sipariş ile doğruluk testi var.
- **Yedekleme/geri yükleme testi**: Yapılmadı.

## Yapılmayanlar (bilinçli olarak sonraki fazlara bırakıldı)

| Modül / özellik | Durum |
|---|---|
| Frontend (React) ve Playwright E2E | Yok. E2E akışı API seviyesinde test ediliyor. |
| Gerçek POS entegrasyonu | Yok. `channel` alanı ve idempotency anahtarı entegrasyon için hazır; adaptör yok. |
| Alt reçete / yarı mamul | Yok |
| Lot / SKT / FEFO | Yok |
| Şubeler arası transfer | Yok (hareket tipi yok) |
| Satın alma siparişi, tedarikçi kataloğu, fatura eşleştirme, onay akışı | Yok; mal kabulde serbest metin tedarikçi adı var |
| Vardiya, personel, devam, görev | Yok |
| Kalite kontrol, sıcaklık, yağ ölçümü, düzeltici faaliyet | Yok |
| KDV, işçilik maliyeti, genel gider | Yok; fiyatlar KDV hariç kabul ediliyor |
| Rate limiting (login brute-force) | Yok — **güvenlik açığı olarak açık** |
| Parola sıfırlama, MFA, refresh token, token iptali | Yok; pasif kullanıcı anında reddediliyor ama aktif kullanıcının token'ı süresi dolana kadar (30 dk) geçerli |
| PostgreSQL Row-Level Security | Yok; izolasyon uygulama katmanında (ADR-0004) |
| Anomali tespiti, AI ajanları | Yok; faz 1'de bilinçli olarak deterministik kurallar |
| Gözlemlenebilirlik (OpenTelemetry, metrik) | Yok; sadece stdlib logging |
| Geriye tarihli hareketlerde `balance_after` | Hareketler kayıt sırasına göre bakiyeye uygulanıyor; `occurred_at` geçmiş olabilir. `balance_after` kayıt sırasındaki bakiyedir, ekonomik tarihteki değil. |
