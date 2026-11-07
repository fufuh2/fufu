# Durum

Son güncelleme: 2026-10-30.

## Kapsam
Sistem yalnızca aylık envanter sayımıdır. Önceki sürümdeki sipariş, reçete, stok defteri, transfer,
satın alma, kalite kontrol, vardiya/görev ve POS webhook modülleri **kodla birlikte veritabanından da
kaldırıldı** (migration `0010`). Bu migration geri alınamaz; kaldırılan tablolardaki veriler yalnızca
yedekten dönebilir. Eski kodu görmek isteyen git geçmişine bakabilir (`git log -- backend/app/services`).

Migration `0010` ayrıca rolleri dörde indirir: `regional_manager → branch_manager`;
`shift_lead`, `purchasing`, `kitchen`, `quality → counter`; `finance`, `auditor → viewer`.
Rolü değişen kullanıcıların açık oturumları kapanır. POS servis hesapları pasife alınır.

## Yapılanlar (kanıtlı)
| Alan | Kanıt |
|---|---|
| Hesap: beklenen = devir + gelen − satılan − zayi; fark = sayılan − beklenen; TL değerleri yarım-çift yuvarlama | `app/domain/monthly.py`, `tests/unit/test_domain.py::TestMonthlyCount` |
| Sayılmamış ürün 0 değil "SAYILMADI" | `test_not_counted_is_unknown_not_zero` |
| Devir önceki kapanmış aydan gelir ve kilitlenir; yeniden açılan ayda kilit kalkar | `tests/integration/test_monthly_count.py` |
| Kapatma: tüm ürünler sayılmış ve "sayımı yapan" dolu olmalı; fiyatlar sabitlenir | `test_monthly_count.py` |
| Excel: Beklenen/Fark/Fark ₺/Zayi ₺ gerçek formül; sütun-başlık kayması hata verir | `test_monthly_count.py` (Excel testi), `monthly_export.py` |
| Reçete: menü satışı × reçete = "Satıştan"; GR→KG, ML→LT ve paket içeriği (1 ADET = 9,5 KG) çevrimi; kapatılan ay reçete değişince değişmez | `tests/integration/test_recipes.py`, `test_recipes_and_sales_through_ui` |
| Tedarikçi verisi (47 fiyat, paket içeriği) ve burger maliyeti / oranı; elle girilen fiyat korunur | `test_supplier_prices_and_food_cost`, `docs/MALIYET_ANALIZI.md` |
| Bağımsız kod incelemesi (2026-11-01) 9 bulgu: ay kapatma sırası, pasif ürünün kapatmayı kilitlemesi, kullanılmış ürünün birim değişikliği, Excel formül enjeksiyonu, kayıt sırasında yazılanın kaybolması, ekran/sunucu yuvarlama farkı, 4 ondalığın 3'e düşmesi, boş alanın yanlış hata vermesi, eski sayfanın yenisinin üstüne çizilmesi — hepsi düzeltildi | `test_review_fixes`, `test_setup_status_lists_what_is_missing`, tarayıcı testleri |
| Yedek / geri yükleme: tek dosya, tek transaction, yalnız boş işletmeye; dosya dışına referans ve sürüm farkı reddedilir; aynı e-postalı sahip korunur | `tests/integration/test_backup.py` |
| Aynı ayın eşzamanlı düzenlenmesi: sürüm numarası, eski sürümle kayıt 409 | `test_concurrent_edits_do_not_overwrite_each_other` |
| Veritabanı taşıma: `copy-from-if-empty` tek transaction, boş hedef ve aynı şema şartı | `tests/integration/test_copy.py` |
| Panel, ay listesi toplamları, ürün geçmişi, şubeler özeti; panel ay oluşturmaz | `tests/integration/test_reports.py` |
| Roller ve şube kapsamı; tenant izolasyonu; kaldırılan uç noktalar 404 | `tests/integration/test_security.py` |
| Parola değişimi/sıfırlama ve rol değişimi oturumları kapatır | `test_management.py`, `test_reports.py` |
| Değiştirilemez kayıt geçmişi (DB trigger), e-posta ve filtre ile | `test_schema.py`, `test_reports.py` |
| Arayüz: canlı hesap, Enter ile gezinme, kaydedilmemiş değişiklik uyarısı, "Boşlara 0 yaz", rol bazlı menü, mobil düzen, XSS | `tests/ui/test_browser.py` (11 test, gerçek Chromium) |
| Migration 0010 eski verili veritabanında | Elle denendi (2026-10-30): roller eşlendi, POS hesabı pasif, 9 tablo kaldı, `alembic check` temiz. Otomatik testi yok. |

## Bilinen sınırlar
- Gelen miktarlar ve menü satış adetleri elle girilir; POS veya fatura entegrasyonu yok.
- Zayi elle girilir ve nedeni tutulmaz; eksik zayi olarak yazılırsa açıklanamayan kayıp gizlenir.
- Eşzamanlı düzenlemede ikinci kayıt reddedilir; satır bazında birleştirme yok (yenileyip tekrar girilir).
- Excel'den sayım yükleme yok.
- Şifre sıfırlama e-postası yok; işletme sahibi Kullanıcılar sayfasından yeni parola belirler.
- MFA yok. Token süresi 30 dk.
- Ücretsiz Render veritabanı 2026-11-16'da sona eriyor (bkz. `DEPLOY.md`).
- Uyandırma görevi (GitHub Actions, 10 dk) GitHub tarafından seyrekleştiriliyor: 31 Ekim–1 Kasım arası 24 saatte yalnızca 5 kez çalıştı. Uyku ekranını güvenilir biçimde önlemiyor.
