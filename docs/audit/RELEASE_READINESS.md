# Canlıya Hazırlık Kararı (faz 2 sonrası)

## Karar: **CONDITIONAL PASS — kontrollü pilot için**

Önceki karar (faz 1) yalnızca backend API içindi. Faz 2 ile ürün düzeyindeki engellerin çoğu kapandı:
arayüz, POS adaptörü, transfer, satın alma, kalite ve vardiya artık var ve testli.
**Bu, "hatasız" veya "eksiksiz" demek değildir.** Kanıtlanan şu: tanımlı kapsamın kabul kriterleri
testle doğrulandı, açık P0/P1 yok, bilinen kalan sorunlar aşağıda.

### Kanıt
- Test paketi: sonuçlar `TEST_EVIDENCE.md` bölüm K'de (birim, entegrasyon, düşmanca, referans model,
  gerçek tarayıcı).
- Canlı sunucu akışı (`scripts/audit_e2e.py`) faz 2 kodu ve production ayarıyla 36/36 geçti, sunucu logunda 500 yok.
- Açık bulgular: P0 0 · P1 0 · P2 1 (FND-014, RLS yok) · P3 3.
- Statik kontroller: ruff, mypy, bandit (0), pip-audit (bilinen açık yok), secret taraması (yalnızca test sabitleri).

### Kararın en güçlü karşı argümanı
Denetimi ve geliştirmeyi aynı oturum yaptı. Testleri yazan, kodu da yazdı; ortak kör noktalar kalmış
olabilir. Faz 2'de testler 6 gerçek hata yakaladı (FND-021…026): bu testlerin işe yaradığını gösteriyor,
ama aynı zamanda bulunmamış hatalar olma olasılığının düşük olmadığını da gösteriyor.
Gerçek kullanıcıyla hiç denenmedi.

## Kalan engeller ve eksikler
| # | Konu | Durum | Etki |
|---|---|---|---|
| 1 | Docker imajı ve compose | BLOCKED (ortamda Docker yok) | Dağıtım yolu doğrulanmadı |
| 2 | PostgreSQL RLS | OPEN (FND-014) | Tenant izolasyonu tek katman |
| 3 | Gerçek POS markası | NOT IMPLEMENTED | Genel adaptör var; markaya özel köprü yazılmalı |
| 4 | Arayüzde transfer, satın alma, vardiya, entegrasyon ekranları | NOT IMPLEMENTED | Bu işlemler şimdilik yalnızca API ile yapılabiliyor |
| 5 | Alt reçete, lot/SKT/FEFO, varyant, kısmi iade, KDV | NOT IMPLEMENTED | Bu ihtiyacı olan işletme için eksik |
| 6 | Fotoğraf/dosya yükleme, bildirim, rapor dışa aktarma | NOT IMPLEMENTED | — |
| 7 | Yük testi, otomatik yedek/PITR, metrik/trace | UNVERIFIED / NOT IMPLEMENTED | Kapasite bilinmiyor |
| 8 | Türk iş hukuku eşikleri, KVKK | Araştırılmadı | Rapor yasal uyum iddiası taşımaz |
| 9 | Rakip araştırması, ASVS madde eşlemesi | Yapılmadı | Önceliklendirme kanıtsız |

## Pilot koşulları
1. Tek işletme, az sayıda şube; gerçek POS yerine elle sipariş veya imzalı webhook ile test köprüsü.
2. HTTPS ters proxy, IP bazlı istek sınırı, `OPSX_ENV=production`, güçlü `OPSX_JWT_SECRET` ve
   `OPSX_INTEGRATION_KEY`. Uygulama DB rolüne TRUNCATE yetkisi verilmemeli.
3. Günlük `pg_dump` ve haftalık geri yükleme provası.
4. İlk hafta sayım farkları elle ikinci kez kontrol edilmeli (hesaplar testli, ama gerçek veriyle değil).

## Nasıl doğrularsın
```bash
cd backend
createdb opsx_test
playwright install chromium   # veya OPSX_CHROMIUM_PATH
OPSX_TEST_DATABASE_URL=postgresql+psycopg://USER:PASS@localhost:5432/opsx_test pytest -o addopts="" -rfsxX
```
Ardından arayüzü `http://localhost:8000/ui/` adresinden açıp bir sipariş gir; stok tablosundaki
düşümü reçeteden elle hesaplayarak karşılaştır.
