# Rakip ve Referans Sistem Analizi

## Önemli uyarı: bu belge bir araştırma sonucu değildir

Bu oturumda Toast/xtraCHEF, Square, Lightspeed, MarketMan, Restaurant365, 7shifts, Odoo,
ERPNext ve Dolibarr için **hiçbir web kaynağı açılıp okunmadı**. Bu ürünlerin özellikleri,
fiyatları, API'leri veya kullanıcı şikâyetleri hakkında burada doğrulanmış tek bir iddia yok.
Fiyat ve lisans bilgisi bilinçli olarak yazılmadı; ezberden yazılan fiyat bilgisi büyük olasılıkla
eskimiştir.

Faz 1 kararları rakip özelliklerine değil, ana istemdeki 12 operasyon sorununa dayanıyor.
Bu yüzden araştırmanın yapılmamış olması faz 1 kapsamını değiştirmiyor; faz 2 önceliklendirmesini
etkileyebilir.

## Özellik kararları (istemdeki sorunlara göre gerekçelendirilmiş)

| Özellik | Karar | Gerekçe | Faz 1 durumu |
|---|---|---|---|
| Değiştirilemez stok hareket defteri | MUST | Sorun 4, 5: farkların kaynağı izlenemiyorsa açıklanamaz | Done |
| Reçete bazlı teorik tüketim | MUST | Sorun 3, 4 | Done |
| Reçete versiyonlama | MUST | Sorun 6: tarihsel maliyet bozulmamalı | Done |
| Fire nedenleri | MUST | Sorun 5 | Done |
| Sayım ve mutabakat | MUST | Sorun 4, 5 | Done |
| Teorik/gerçek fark raporu | MUST | Sorun 4 | Done |
| Ürün maliyet ve marj | MUST | Sorun 6 | Done |
| Idempotent sipariş alımı | MUST | Sorun 11: tekrarlanan veri aktarımı | Done |
| Çok şube + rol/şube yetkisi | MUST | Sorun 9 | Done |
| Denetim izi | MUST | Sorun 10, 12 | Done |
| POS entegrasyonu (en az bir sağlayıcı) | MUST (faz 2) | Sorun 1, 2: elle tekrar giriş | Not started |
| Şubeler arası transfer | MUST (faz 2) | Transfer yoksa fark raporu yanlış sonuç verir | Not started |
| Mal kabul ↔ satın alma siparişi ↔ fatura | SHOULD | Sorun 6: fiyat değişimi ve eksik teslimat | Not started |
| Sipariş önerisi (min/hedef stok) | SHOULD | Sorun 1 | Not started |
| Alt reçete / yarı mamul | SHOULD | Mutfakta sos, hamur vb. yaygın | Not started |
| Lot / SKT / FEFO | SHOULD | Bozulma firesi | Not started |
| Kalite kontrol listeleri, sıcaklık | SHOULD | Sorun 8 | Not started |
| Vardiya, görev, devam | SHOULD | Sorun 7; hukuki araştırma gerektirir | Not started |
| İşçilik maliyeti, genel gider | LATER | Vardiya modülüne bağımlı | Not started |
| İstatistiksel anomali tespiti | LATER | Önce temiz veri ve en az birkaç haftalık geçmiş gerekir | Not started |
| AI ile açıklama / karar desteği | LATER | Deterministik katman olmadan güvenilmez | Not started |
| Muhasebe yazılımına aktarım | LATER | Muhasebe sistemi seçimi müşteriye bağlı | Not started |
| Kendi POS'umuzu yazmak | NOT APPLICABLE | Kurulu POS'larla entegre olmak daha gerçekçi | — |

## Yapılması gereken araştırma (faz 2 öncesi)

Her sistem için doğrulanacak, birincil kaynağı (resmî dokümantasyon, API referansı) olan sorular:

1. **POS sağlayıcıları (Türkiye'de yaygın olanlar dahil):** Sipariş/satış verisi webhook ile mi,
   polling ile mi alınıyor? Sipariş kimliği kararlı mı (idempotency anahtarı olarak kullanılabilir mi)?
   İptal/iade nasıl bildiriliyor? Bu sorular `REQ-ORD-04`'ün tasarımını doğrudan belirler.
2. **MarketMan / Restaurant365 / xtraCHEF:** Teorik-gerçek fark raporunu hangi dönem formülüyle
   hesaplıyorlar (açılış + giriş − kapanış mı, düzeltme toplamı mı)? Transferleri nasıl ele alıyorlar?
3. **Odoo / ERPNext:** Stok değerleme yöntemleri (ortalama, FIFO) ve lot/SKT veri modeli; açık kaynak
   olduğu için kod üzerinden doğrulanabilir.
4. **7shifts:** Vardiya modelinin Türkiye çalışma süresi kurallarına uyarlanabilirliği.
5. Kullanıcı şikâyetleri: bağımsız inceleme siteleri ve forumlar; pazarlama sayfası kanıt sayılmaz.

## Nasıl doğrulanır
Bu belgeyi güncelleyen kişi her satıra kaynak URL'si ve erişim tarihi eklemeli; kaynaksız satır
"doğrulanmadı" olarak kalmalı.
