# ADR-0005: Faz 2 modülleri — transfer, satın alma, kalite, vardiya, POS, web arayüzü

**Durum:** Kabul edildi

## Geç gelen satış (FND-012)
Sayımdan önce yapılmış ama sayımdan sonra kaydedilen satış, tüketimi yine yazar (satış verisi
kaybolmaz). Ek olarak sayım anına tarihli `late_sale_offset` (+miktar) yazar. Böylece bakiye fiziksel
sayımla eşleşir, fark raporu bu satırı sayım düzeltmesiyle netleştirir ve satış teorik tüketimde görünür.
İptal, siparişin tüm hareketlerini (tüketim ve offset) birlikte tersine çevirir.
**Karşı argüman:** Offset, sayımın tamamen doğru olduğunu varsayar. Sayım yanlışsa offset de yanlış
düzeltir. Alternatif olan "dönem kapanışı" modeli daha katıdır ama daha karmaşıktır.

## Transfer
İki aşamalı (gönder → teslim al / iptal). Yoldaki stok iki şubede birden görünmez. Eksik teslim,
hedefte `transit_loss` nedenli fire olarak maliyetiyle yazılır. Transfer hareketleri tüketim sayılmaz.

## Satın alma
Sistem tedarikçiye hiçbir şey göndermez; "onaylandı" yalnızca iç karar kaydıdır. Limit üstü
siparişlerde dört göz kuralı uygulanır. Teslimde sipariş fiyatından %5'ten fazla sapma
`purchase.price_variance` olarak işaretlenir. Sipariş miktarından fazla teslim reddedilir; fazlası
ayrı bir mal kabul olarak girilmelidir.

## Kalite
Kontrol kayıtları ve cevapları DB trigger'ı ile değiştirilemez. Düzeltme, gerekçeli yeni bir kayıttır
ve skor hesabında orijinalin yerine geçer. Fotoğraf yükleme yoktur: `evidence_ref` serbest metin
referanstır (dosya deposu sonraki iş).

## Vardiya
Fazla mesai eşikleri parametredir (varsayılan 45 saat/hafta, 11 saat/gün). Bu değerler resmî kaynaktan
**doğrulanmadı**; rapor yasal uyum iddiası taşımaz. Çalışan yalnızca kendi vardiya ve devam kayıtlarını görür.

## POS
Genel imzalı webhook (HMAC-SHA256, ±5 dk). Entegrasyon sırları `OPSX_INTEGRATION_KEY`'den türetilir,
DB'de sır saklanmaz. Olaylar inbox'ta tekildir ve sipariş idempotency anahtarı
`pos:<entegrasyon>:<external_id>` olur. Belirli bir POS markasına bağlanmadı; bunun için o markanın
API'sini bu genel formata çeviren bir köprü gerekir.

## Web arayüzü
Build adımı ve üçüncü parti kod olmadan düz HTML/JS. CSP `default-src 'self'`. Veri `textContent`
ile basılır. Token sessionStorage'dadır: sekme kapanınca silinir, ancak XSS'e karşı tam koruma sağlamaz.
HttpOnly cookie'ye geçiş CSRF koruması gerektirir; bu sonraki iştir.
