# Tedarikçi verisi ve burger maliyet analizi (2026-11-01)

Kaynak: tedarikçi sipariş sitesi ekran görüntüleri (14 adet) ve menü panoları. Veri:
`backend/app/data/taffburger_supplier.json`, `backend/app/data/taffburger_menu.json`.
Çıkarılan 47 fiyat bağımsız bir ikinci kontrolde ekran görüntüleriyle rakam rakam karşılaştırıldı; hepsi
ve bölme hesapları doğru çıktı. Güven etiketleri o kontrolden sonra düzeltildi: 20 kesin, 7 çıkarım, 20 tahmin.

## Sistemde kullanımı
1. **Menü & Reçeteler → Taff Burger menüsünü yükle**, sonra raporda **eksik malzemeleri ekle**.
2. **Ürün Listesi → Tedarikçi fiyat ve paket bilgilerini uygula**: boş fiyat/paket alanlarını doldurur
   (cheddar 1 KG = 70 dilim, kova ketçap 9 kg, kova mayonez 8 kg, top down şişeler…). Elle girilen değerler korunur.
3. **Menü & Reçeteler → Maliyet özeti**: her burgerin malzeme maliyeti, satış fiyatına oranı, brüt kârı.

## Maliyet oranı (2026-11-01)
Köfte (HAMBURGER KÖFTE 970 ₺/kg) ve tavuk burger eti (295 ₺/kg) paketlerinin 1 kg olduğu işletme sahibince
doğrulandı. "Eksik" sütunundaki malzemelerin fiyatı ya da çevrimi hâlâ yok; o ürünlerde gerçek oran biraz daha yüksektir.

| Burger | Satış ₺ | Malzeme maliyeti ₺ | Oran | Brüt kâr ₺ | Maliyeti eksik |
|---|---:|---:|---:|---:|---|
| NASH | 450 | 226,43 | %50,3 | 223,57 | — |
| DIPLOMAT | 640 | 321,80 | %50,3 | 318,20 | Doritos |
| LILLY | 610 | 269,78 | %44,2 | 340,22 | soğan turşusu |
| CHEESE BURGER | 560 | 219,36 | %39,2 | 340,64 | salatalık turşusu |
| NORI | 610 | 229,56 | %37,6 | 380,44 | — |
| COY | 630 | 225,64 | %35,8 | 404,36 | — |
| EL TORATO | 610 | 214,97 | %35,2 | 395,03 | — |
| YOUKI | 610 | 213,41 | %35,0 | 396,59 | salatalık turşusu |
| FAKE DONALD'S | 610 | 210,97 | %34,6 | 399,03 | iceberg, salatalık turşusu, fake soğanı |
| TRUFFLE | 630 | 211,67 | %33,6 | 418,33 | iceberg, salatalık turşusu |
| ROYALE | 900 | 285,27 | %31,7 | 614,73 | Royale sos |
| SEUL | 430 | 127,80 | %29,7 | 302,20 | — |
| BALANCE | 590 | 170,80 | %28,9 | 419,20 | iceberg, kırmızı soğan, domates |
| NOUR | 390 | 106,65 | %27,3 | 283,35 | fake soğanı |
| CHEESE TAO | 410 | 97,83 | %23,9 | 312,17 | iceberg, halka turşu |
| TAFF CHICKEN | 410 | 96,31 | %23,5 | 313,69 | — |
| PUMPKIN CARROT | 390 | 88,59 | %22,7 | 301,41 | iceberg |
| OVERDOSE | 440 | 94,96 | %21,6 | 345,04 | iceberg |
| SHOGUN | 420 | 54,45 | %13,0 | 365,55 | iceberg, kuru soğan, halka turşu (tavuk menüde yazmıyor) |

Gözlemler:
- 150 gr köfte tek başına 145,50 ₺; et burgerlerinde maliyetin yaklaşık üçte ikisi.
- 50 gr dana füme ≈ 99 ₺ (250 gr paket 495 ₺). Nash ve Diplomat'ı %50'ye çıkaran bu.
- Diplomat = köfte 145,50 + füme 99 + 150 gr Diplomat sos 46,37 + cheddar 14,43 + ekmek 16,50.

## Sınırlar
- KDV: menü fiyatı KDV dahil varsayıldı; tedarikçi fiyatlarının KDV durumu sitede yazmıyor.
- Taff soslarında paket boyutu yazmıyor; fiyat 1 kg kabul edildi (tahmin). 1 kg değilse sos maliyetleri değişir.
- Ambalaj, yağ, baharat ve personel maliyeti dahil değil; bu yalnızca reçete malzeme maliyeti.
