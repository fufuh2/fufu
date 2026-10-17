# Bulgular

Öncelik: **P0** veri kaybı / yetkisiz erişim · **P1** sipariş/stok/maliyet/güvenlikte kritik ·
**P2** önemli eksik veya ciddi sorun · **P3** düşük etki.
Durum: **FIXED** (düzeltildi + regresyon testi geçiyor) · **OPEN**.

Özet: 0 P0 · 2 P1 (ikisi de FIXED) · 7 P2 (3 FIXED, 4 OPEN) · 11 P3 (6 FIXED, 5 OPEN).

---

## FND-001 · P1 · Stok bütünlüğü · FIXED
**Sorun:** Sayımdan önceki bir tarihle girilen fire veya mal kabulü, bakiyeye bir kez daha uygulanıyordu.
**Yeniden üretim:** 10 000 g et → sayım 9 800 g (−200 düzeltme) → `occurred_at` = 2 saat önce, 200 g fire.
**Beklenen:** Red; fiziksel stok 9 800 g kalmalı. **Gerçek:** 201, bakiye 9 600 g (`balance_after: 9600.0000`).
**Kök neden:** Hareketler ekonomik tarihten bağımsız olarak kayıt anında bakiyeye uygulanıyor; sayım
o kaybı zaten düzeltmişti.
**Düzeltme:** `inventory.check_after_last_count`: Fire ve mal kabulünde `occurred_at`, ilgili
malzemenin son sayımından önceyse 422 dönüyor. Kontrol bakiye kilidi alındıktan sonra yapılıyor; böylece
eşzamanlı bir sayım araya giremiyor.
**Test:** `tests/audit/test_adversarial.py::test_backdated_waste_before_last_count_is_rejected` — PASS.
**Kalan risk:** Aynı sorun siparişler için açık (FND-012).

## FND-002 · P1 · Güvenlik / kimlik doğrulama · FIXED
**Sorun:** Login'de deneme sınırı yoktu; parola tahmini sınırsızdı.
**Yeniden üretim:** Aynı hesaba 8 yanlış parola. **Beklenen:** 429. **Gerçek:** 8 × 401.
**Düzeltme:** `admin.authenticate`: Hesap başına 15 dakikada 5 hatalı denemeden sonra 429
(doğru parola dahil). Sayaç append-only `audit_events` tablosunda tutuluyor; bu yüzden yeniden
başlatmada ve çoklu worker'da korunuyor. Migration `0002` kısmi indeks ekliyor.
**Test:** `test_login_is_rate_limited_per_account` — PASS.
**Kalan risk:** (a) IP bazlı sınır yok: farklı e-postaları deneyen saldırgan yavaşlatılmıyor ve
her adres için 5 satır log üretiyor. (b) Bilinen bir hesap kasıtlı olarak 15 dakika kilitlenebilir
(DoS). (c) Eşzamanlı patlamada sayaç 5'i birkaç deneme aşabilir. Ters proxy'de IP bazlı limit önerilir.

## FND-003 · P2 · Denetim izi · FIXED
**Sorun:** Başarılı ve başarısız girişler kaydedilmiyordu.
**Düzeltme:** `auth.login` ve `auth.login_failed` olayları yazılıyor. Bilinmeyen e-posta için kayıt `tenant_id = NULL` ile tutuluyor.
**Test:** `test_failed_logins_are_audited`, canlı akış adım 17 (`auth.login`) — PASS.

## FND-004 · P2 · Birim dönüşümü · FIXED
**Sorun:** 4 ondalığın altına yuvarlanan miktar (ör. 0,0001 "tutam" × 0,0001 g) `DivisionByZero` → HTTP 500.
**Kök neden:** `convert` sonucu 0 olabiliyordu; mal kabulde birim maliyet 0'a bölünüyordu.
**Düzeltme:** Pozitif girdinin baz karşılığı 0 ise 422.
**Test:** `test_tiny_conversion_rounding_to_zero_is_rejected` (mal kabul, fire, reçete) — PASS.

## FND-005 · P2 · Sayısal sınırlar · FIXED
**Sorun:** Şema sınırları içindeki ama çarpımı NUMERIC hassasiyetini aşan değerler
(1e9 kg × 1e9 TL; 1e9 × 1e9 dönüşüm; 1e12 g reçete × 10 000 adet) → `NumericValueOutOfRange` → 500.
**Düzeltme:** `sqlalchemy.exc.DataError` → 422 `out_of_range`. Transaction geri alınıyor.
**Test:** `test_overflowing_receipt_is_client_error[line0,line1]`, `test_overflowing_order_consumption_is_client_error`
(stoğun değişmediği de doğrulanıyor) — PASS.
**Kalan risk:** Sınır hâlâ veritabanında. Uygulama düzeyinde anlamlı üst limitler (ör. tek mal kabulde en çok 10 ton) iş kuralı olarak tanımlanmadı.

## FND-006 · P3 · Hassas veri · FIXED
**Sorun:** 422 doğrulama hataları gönderilen değeri (`input`) geri yansıtıyordu; buna kısa veya uzun parolalar da dahildi.
**Düzeltme:** Hata gövdesi yalnızca `type`, `loc` ve `msg` içeriyor. **Test:** `test_validation_errors_do_not_echo_passwords` — PASS.

## FND-007 · P3 · Güvenli varsayılan · FIXED
**Sorun:** `OPSX_ENV` verilmezse ortam `development` sayılıyor, `/docs` ve `/openapi.json` açık kalıyordu.
**Düzeltme:** Varsayılan `production`; dokümantasyon yalnızca `OPSX_ENV=development` iken açık.
Compose dosyası ve `.env.example` da güncellendi. **Test:** `test_api_docs_disabled_unless_explicitly_development` — PASS.

## FND-008 · P3 · İşletim · FIXED
**Sorun:** `/healthz` veritabanını kontrol etmiyordu. **Düzeltme:** `/readyz` (`SELECT 1`, hata durumunda 503).
**Test:** `test_readiness_checks_database`; temiz kurulum ve canlı akışta 200 — PASS.
**Doğrulanmayan:** Veritabanı kapalıyken 503 dönmesi test edilmedi.

## FND-009 · P3 · Kurulum · FIXED
**Sorun:** CLI, login'in reddettiği e-postayla (ör. `owner@audit.test`, rezerve TLD) sahip oluşturuyordu.
Böyle oluşturulan sahip hiç giriş yapamaz. Denetim sırasında tesadüfen bulundu.
**Düzeltme:** Bootstrap aynı `EmailStr` doğrulamasını kullanıyor. **Test:** `test_bootstrap_rejects_emails_login_would_reject` — PASS.

## FND-010 · P3 · Sırlar · FIXED
**Sorun:** `app/config.py` ve `migrations/env.py` içinde varsayılan `ops:ops` bağlantı dizesi vardı
(detect-secrets "Basic Auth Credentials" olarak işaretledi). Değişken unutulursa geliştirme kimlik bilgileri sessizce kullanılırdı.
**Düzeltme:** `OPSX_DATABASE_URL` zorunlu. **Test:** `test_database_url_has_no_builtin_default` — PASS.

## FND-011 · P3 · Dokümantasyon · FIXED
**Sorun:** `docs/STATUS.md` "CI koşmadı" diyordu; GitHub Actions run 38985361339 aslında başarıyla bitmişti.
**Düzeltme:** STATUS güncellendi.

---

## FND-012 · P2 · Stok bütünlüğü · OPEN
**Sorun:** Son sayımdan önce gerçekleşmiş ama sonra sisteme gelen satış (POS çevrimdışı senkronu), tüketimi ikinci kez düşüyor.
**Yeniden üretim:** `test_backdated_sale_before_count_does_not_double_consume` (xfail strict). Sayım 29 ekmek,
sayım öncesi tarihli 1 burger → sistem 28 gösteriyor, fiziksel stok 29.
**Neden düzeltilmedi:** Siparişi reddetmek satış verisini kaybettirir (ADR-0003). Doğru çözüm bir iş kararı gerektiriyor:
(a) tüketimi yazıp aynı miktarda telafi düzeltmesi eklemek ve inceleme kuyruğuna almak, ya da
(b) sayımları "dönem kapanışı" olarak modelleyip geç gelen satışları bir sonraki döneme atamak.
**Etki:** Çevrimdışı senkron yapan şubelerde açıklanamayan fark sistematik olarak şişer.

## FND-013 · P2 · Fonksiyon eksikliği · OPEN (NOT IMPLEMENTED)
Hiçbir PUT/PATCH/DELETE endpoint'i yok (20 route'un tamamı GET/POST). Ürün fiyatı değiştirilemiyor;
malzeme, ürün, şube ve kullanıcı pasife alınamıyor; rol değiştirilemiyor. Bunlar yalnızca doğrudan SQL ile yapılabiliyor.

## FND-014 · P2 · Güvenlik (derinlemesine savunma) · OPEN
PostgreSQL Row-Level Security yok; tenant izolasyonu yalnızca uygulama sorgularına bağlı (ADR-0004).
Mevcut tüm endpoint'ler test edildi (`test_tenant_isolation`, `test_cross_tenant_ids_inside_bodies`,
rol × endpoint matrisi). Ancak gelecekte yazılacak bir sorguda filtre unutulursa son savunma hattı yok.

## FND-015 · P2 · Oturum · OPEN
Token iptal mekanizması yok. Çalınan bir token 30 dakika geçerli kalır. Pasife alınan kullanıcı ve
rol değişikliği anında etkili, çünkü her istekte DB okunuyor.

## FND-016 · P3 · Bilgi sızıntısı · OPEN
E-posta adresleri global olarak tekil. Bir tenant sahibi `POST /users` ile 409 "e-mail already registered"
yanıtını izleyerek başka tenant'larda bir adresin kayıtlı olup olmadığını öğrenebilir.

## FND-017 · P3 · Raporlama · OPEN
Marj raporundaki SMM satır bazında, fark raporundaki teorik maliyet hareket bazında yuvarlanıyor.
İki rapor arasında kuruş düzeyinde fark olabilir. Büyüklüğü ölçülmedi.

## FND-018 · P3 · Operasyon · OPEN
Fazla satış nedeniyle bakiye negatife düştüğünde, sayım yapılana kadar o malzeme için fire kaydı 409 alıyor (`test_sale_may_go_negative_but_waste_may_not`).

## FND-019 · P3 · Fonksiyon eksikliği · OPEN
Ürünün teorik reçete maliyetini satıştan önce gösteren bir endpoint yok. Kritik akışın 7. adımı yalnızca sipariş satırı maliyeti üzerinden doğrulanabildi.

## FND-020 · P3 · API · OPEN
`/items`, `/products`, `/branches/{id}/stock` sayfalama yapmıyor. `GET /products` izin kontrolü yapmıyor:
her kimliği doğrulanmış rol, kendi tenant'ının ürün ve fiyatlarını görebiliyor. Tenant izolasyonu bu endpoint'te de geçerli.
