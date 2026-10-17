# Canlıya Hazırlık Kararı

## Karar: **CONDITIONAL PASS — yalnızca faz-1 backend API kapsamı için**

Bu karar **canlıya alma onayı değildir.** Anlamı şu: Tanımlı backend kapsamında (stok, reçete,
sipariş, fire, sayım, maliyet/marj, yetki) açık P0/P1 bulgu yok ve kritik hesaplar bağımsız yöntemlerle
doğrulandı. Sistem iç pilot ve entegrasyon geliştirmesi için kullanılabilir.

**Restoran işletmelerinin kullanacağı bir ürün olarak canlıya alınamaz.** Gerekçesi aşağıdaki engeller.

### Kararın dayandığı kanıt
- 79 passed, 0 failed, 1 xfailed (bilinen açık P2); canlı akış 36/36; GitHub Actions yeşil (önceki commit).
- Açık bulgular: P0 0 · P1 0 · P2 4 · P3 5.
- Bu denetimde 11 bulgu düzeltildi (2'si P1), her biri için regresyon testi var.

### Bu kararın en güçlü karşı argümanı
"CONDITIONAL PASS" etiketi "neredeyse hazır" diye okunabilir. Ürünün kullanıcıya dönük yarısı (arayüz,
POS, satın alma, vardiya, kalite) hiç yok. Denetimi kodu yazan oturumun yapması da kararın güvenilirliğini
düşürüyor. İstemdeki kategorilerle ürün düzeyinde değerlendirilseydi, kullanıcıya dönük kritik işlevler
eksik olduğu için karar **FAIL** olurdu. Bu ayrım bilinçli olarak açık yazılmıştır.

## Canlıya alma engelleri (ürün düzeyinde)
| # | Engel | Bulgu / durum |
|---|---|---|
| 1 | Kullanıcı arayüzü yok; personel API'yi doğrudan kullanamaz | NOT IMPLEMENTED |
| 2 | POS entegrasyonu yok; satışlar elle girilmeli (istemdeki sorun 1) | NOT IMPLEMENTED |
| 3 | Geç gelen satış stoku iki kez düşüyor; POS senkronu ile kesin tetiklenir | FND-012 (P2) |
| 4 | Fiyat ve kayıt güncellenemiyor, pasife alınamıyor | FND-013 (P2) |
| 5 | Şubeler arası transfer yok; transfer yapan işletmede fark raporu yanıltıcı olur | NOT IMPLEMENTED |
| 6 | Docker imajı hiç build edilmedi; dağıtım yolu doğrulanmadı | BLOCKED |
| 7 | Otomatik yedekleme/PITR yok; yük testi yok | NOT IMPLEMENTED / UNVERIFIED |

## Backend pilotu için koşullar
1. Pilotta POS senkronu olmamalı veya satışlar sayımdan sonra tarihli gelmeli (FND-012).
2. Ters proxy'de IP bazlı istek sınırı ve TLS (FND-002 kalan riski, HSTS).
3. `OPSX_ENV=production`, güçlü `OPSX_JWT_SECRET`, uygulama DB rolüne TRUNCATE yetkisi verilmemeli (ADR-0002).
4. Docker imajı CI'da build edilip çalıştırılarak doğrulanmalı.
5. Günlük `pg_dump` ve geri yükleme provası.

## Bu kararı nasıl doğrularsın
```bash
cd backend
createdb opsx_test
OPSX_TEST_DATABASE_URL=postgresql+psycopg://USER:PASS@localhost:5432/opsx_test \
  pytest -o addopts="" -rxX            # beklenen: 79 passed, 1 xfailed
# canlı akış: ayrı bir 'audit' DB, migration, CLI ile tenant, uvicorn, sonra:
python scripts/audit_e2e.py http://localhost:8000 $OPSX_DATABASE_URL owner@example.com '<parola>'
```
Hesapları elle kontrol etmek için `TEST_EVIDENCE.md` bölüm D'deki aritmetiği kullan.
