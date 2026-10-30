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
| Panel, ay listesi toplamları, ürün geçmişi, şubeler özeti; panel ay oluşturmaz | `tests/integration/test_reports.py` |
| Roller ve şube kapsamı; tenant izolasyonu; kaldırılan uç noktalar 404 | `tests/integration/test_security.py` |
| Parola değişimi/sıfırlama ve rol değişimi oturumları kapatır | `test_management.py`, `test_reports.py` |
| Değiştirilemez kayıt geçmişi (DB trigger), e-posta ve filtre ile | `test_schema.py`, `test_reports.py` |
| Arayüz: canlı hesap, Enter ile gezinme, kaydedilmemiş değişiklik uyarısı, "Boşlara 0 yaz", rol bazlı menü, mobil düzen, XSS | `tests/ui/test_browser.py` (7 test, gerçek Chromium) |
| Migration 0010 eski verili veritabanında | Elle denendi (2026-10-30): roller eşlendi, POS hesabı pasif, 9 tablo kaldı, `alembic check` temiz. Otomatik testi yok. |

## Bilinen sınırlar
- Gelen ve satılan miktarlar elle girilir; POS veya fatura entegrasyonu yok (bilinçli olarak kaldırıldı).
- Zayi elle girilir ve nedeni tutulmaz; eksik zayi olarak yazılırsa açıklanamayan kayıp gizlenir.
- Aynı ayı iki kişi aynı anda düzenlerse son kaydeden kazanır; satır bazlı çakışma uyarısı yok.
- Excel'den sayım yükleme yok.
- Şifre sıfırlama e-postası yok; işletme sahibi Kullanıcılar sayfasından yeni parola belirler.
- MFA yok. Token süresi 30 dk.
- Ücretsiz Render veritabanı 2026-11-16'da sona eriyor (bkz. `DEPLOY.md`).
