# İşletme modülleri (ERP)

Menüde **İşletme** başlığı altında. Hepsi aylık sayıma bağlıdır; aynı bilgi iki kez girilmez.

| Sayfa | Ne yapar | Sayıma etkisi |
|---|---|---|
| **Satışlar** | Gün gün menü ürünü satış adedi ve satış fiyatı (varsayılan: menü fiyatı). Ay cirosu, günlük ortalama, ürün sıralaması. | Ayın toplamı sayımdaki **Menü satışları**na yazılır → **Satıştan (reçete)** otomatik. Günlük giriş olan ayda sayım ekranındaki satış kutusu kilitlenir (çift sayım olmasın). |
| **Satın Alma** | Tedarikçiler; mal kabul (irsaliye/fatura no, ürünler, miktar, birim fiyat). Ay toplamı ve tedarikçi bazında alım. | Miktarlar o ayın **Gelen** sütununa eklenir; belge silinirse geri düşülür. İsteğe bağlı: ürün birim fiyatı son alış fiyatıyla güncellenir. Kapatılmış aya belge girilmez. |
| **Giderler** | Kira, elektrik, su, doğalgaz, internet, komisyon, bakım, reklam, vergi, diğer. | — |
| **Personel** | Personel (aylık veya saatlik ücret), vardiyalar (başlangıç–bitiş; gece yarısını geçen vardiya desteklenir), aylık saat ve işçilik maliyeti. | — |
| **Kâr-Zarar** | Ciro − malzeme maliyeti = brüt kâr; − giderler − işçilik = net kâr; oranlar (%). | Malzeme maliyeti: ay **sayıldıysa** gerçek tüketim (devir + gelen − sayılan) × fiyat, sayılmadıysa reçeteye göre (teorik). |

## Yetkiler
| Rol | Satış | Mal kabul | Gider | Personel | Kâr-Zarar |
|---|---|---|---|---|---|
| İşletme sahibi | ✓ | ✓ | ✓ | ✓ | ✓ |
| Şube müdürü | ✓ | ✓ | ✓ | ✓ | ✓ |
| Sayım personeli | ✓ | ✓ | — | — | — (para görmez) |
| İzleyici | okur | okur | okur | okur | ✓ |

## Sınırlar
- KDV ayrımı yok; tutarlar girildiği gibi. Resmi muhasebe / e-fatura / bordro yerine geçmez.
- Aylık ücretli personel, ay içinde aktifse tam ay maliyetiyle sayılır (kısmi ay hesaplanmaz).
- Ciro, günlük satış yoksa aylık menü satışları × menü fiyatından hesaplanır.
- POS entegrasyonu yok; satışlar elle girilir.
