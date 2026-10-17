# Denetim Raporu

**Tarih:** 2026-10-17 · **Başlangıç commit'i:** `6f2bfac` · Kanıtlar: `TEST_EVIDENCE.md` · Bulgular: `FINDINGS.md`

> **Bağımsızlık sınırı:** Bu denetimi kodu yazan aynı yapay zekâ oturumu yaptı. Referans model
> ve düşmanca testler ayrı yazıldı, beklenen değerler elle hesaplandı. Yine de aynı yazarın kör
> noktaları paylaşılıyor olabilir. Gerçek bağımsız inceleme yapılmış sayılmamalı.

## Modül sınıflandırması

| Alan | Durum | Kanıt / not |
|---|---|---|
| Kimlik doğrulama | PASS | Token, sahte/süresi geçmiş/`alg=none`, deneme sınırı, pasif kullanıcı testleri |
| Yetkilendirme (rol/izin) | PASS | 90 çağrılık rol × endpoint matrisi, 0 uyuşmazlık |
| Tenant ve şube izolasyonu | PASS (uygulama katmanı) | IDOR ve gövde içi çapraz ID testleri; RLS yok (FND-014) |
| İşletme oluşturma | PASS | Yalnızca CLI |
| Şube / kullanıcı oluşturma | PASS | — |
| Şube / kullanıcı / ürün güncelleme, pasife alma | NOT IMPLEMENTED | FND-013 |
| Sipariş ve satış | PASS | Idempotency (sıralı + eşzamanlı), sunucu fiyatı, indirim sınırı |
| Varyant / modifier | NOT IMPLEMENTED | — |
| Reçete (versiyon, kayıp) | PASS | Tarih yeniden yazılmıyor; geriye tarihleme reddediliyor |
| Alt reçete | NOT IMPLEMENTED | — |
| Malzeme ve ölçü birimleri | PASS | g/kg/ml/l/adet + malzemeye özel birim; yuvarlanıp sıfır olan miktar reddi |
| Stok hareketleri ve sayım | PASS, bir açık hata ile | Append-only, eşzamanlılık, uzlaşma; FND-012 açık |
| Mal kabulü | PASS | Fiyat değişimi ve ortalama maliyet doğrulandı |
| Satın alma, tedarikçi, kısmi teslim | NOT IMPLEMENTED | — |
| Fire | PASS | 9 neden; stok yetersizliği; sayım öncesi tarih reddi |
| İptal | PASS | Orijinal maliyetle ters kayıt; çift iptal 409 |
| İade (kısmi) | NOT IMPLEMENTED | — |
| Şubeler arası transfer / çok depo | NOT IMPLEMENTED | Şube = tek stok lokasyonu |
| Maliyet ve kârlılık | PASS | Referans modelle 60 rastgele senaryo + elle hesaplanan canlı akış |
| KDV, işçilik, genel gider | NOT IMPLEMENTED | Fiyatlar KDV hariç varsayılıyor |
| Vardiya ve görev | NOT IMPLEMENTED | — |
| Kalite kontrol | NOT IMPLEMENTED | — |
| Yönetim paneli (UI) | NOT IMPLEMENTED | Frontend yok |
| Raporlama (API) | PASS | Fark ve marj raporları kaynak satırlarla tutarlı; bilinmeyen değer `null` |
| Rapor dışa aktarma | NOT IMPLEMENTED | — |
| Bildirimler, otomasyon | NOT IMPLEMENTED | — |
| Harici entegrasyonlar | NOT IMPLEMENTED | Mock adaptör de yok |
| Denetim izi | PASS | Append-only (geri yüklemeden sonra da); login olayları |
| Hata yönetimi | PASS | 500 yerine 422; iç hata gövdesi sızdırmıyor |
| Gözlemlenebilirlik (metrik, trace) | NOT IMPLEMENTED | Yalnızca stdlib log |
| Migration | PASS | upgrade/downgrade/check; temiz kurulum |
| Yedekleme / geri yükleme | PASS (manuel) | dump/restore + bütünlük; zamanlama/PITR yok |
| Kurulum (Python) | PASS | Temiz klon, Python 3.12, lock dosyası; GitHub Actions yeşil |
| Docker / compose | BLOCKED | Daemon yok |
| Yük / performans | UNVERIFIED | — |

## İstemdeki 10 soruya cevaplar

1. **Gerçekten çalışan özellikler:** Yukarıda PASS olanlar. Hepsi gerçek PostgreSQL ve gerçek HTTP sunucusu üzerinde test edildi.
2. **Yalnızca arayüz veya mock olanlar:** Yok. Kodda sahte veri, mock servis veya sahte başarı mesajı bulunmadı.
   Tek "dummy" ifadesi zamanlama saldırısına karşı kullanılan sahte parola hash'i ve bu kasıtlı.
   Frontend olmadığı için "yalnızca görsel" özellik de yok.
3. **Uçtan uca test edilen akışlar:** İstemin 17 adımlık akışı. Hem TestClient ile (`test_full_operational_flow`)
   hem canlı uvicorn ve ayrı DB bağlantısıyla (`scripts/audit_e2e.py`, 36/36) koştu. Adım 7 için endpoint
   olmadığından satış satırı maliyetiyle doğrulandı.
4. **Stok ve maliyet bağımsız doğrulandı mı:** Evet, iki yoldan: elle hesaplanmış senaryo ve uygulama kodunu
   import etmeyen referans model (60 rastgele dizi). Model mutasyonlarla sınandı. Sınır: model belgelenmiş
   formüllerden yazıldı, yani formülün kendisi iş açısından yanlışsa model de yanlış olur.
5. **Veri kaybı veya yinelenen işlem riski:** Yinelenen sipariş, eşzamanlı tüketim ve kısmi yazım için risk test
   edildi ve bulunmadı. **Açık risk:** Sayımdan önceki tarihle gelen satış tüketimi iki kez düşüyor (FND-012).
   Aynı sorun fire ve mal kabulü için vardı ve düzeltildi.
6. **Yetkisiz kullanıcı hangi verilere erişebilir:** Testlerde başka tenant'ın veya atanmamış şubenin hiçbir
   verisine erişilemedi. Kendi tenant'ında her rol ürün listesini ve fiyatları görebiliyor (FND-020).
   Bir tenant sahibi başka tenant'taki bir e-posta adresinin varlığını öğrenebiliyor (FND-016).
   Çalınan token 30 dakika geçerli kalıyor (FND-015).
7. **Başarısız veya çalıştırılamayan testler:** Başarısız 0. Bilinen açık 1 (xfail, FND-012).
   Çalıştırılamayanlar: Docker, tarayıcı/UI, entegrasyon, yük testleri.
8. **Düzeltilen ve yeniden test edilenler:** FND-001…011 (2 P1, 3 P2, 6 P3). Ayrıntı `REMEDIATION_LOG.md`'de.
9. **Canlıya almaya engel eksikler:** `RELEASE_READINESS.md`.
10. **Sonraki en önemli adım:** FND-012 için iş kararı ve uygulaması, ardından POS adaptörü. POS senkronu
    olmadan sistem satışları elle girmeyi gerektiriyor; POS senkronu geldiğinde ise FND-012 doğrudan tetiklenir.
    Bu yüzden ikisi birlikte ele alınmalı.
