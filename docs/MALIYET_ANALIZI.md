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

## Bugünkü veriyle maliyet oranı (alt sınır)
Eksik sütunundaki malzemelerin fiyatı ya da çevrimi yok; gerçek oran bundan **yüksektir**.
En büyük eksik **DANA KIYMA** (her et burgerde 150 gr) ve **KAPLAMALI TAVUK** (120 gr): tedarikçi listesinde
paket boyutu yazmıyor (HAMBURGER KÖFTE 970 ₺, TAFF TAVUK BURGER ETİ 295 ₺).

| Burger | Satış ₺ | Bilinen maliyet ₺ | Oran | Maliyeti eksik |
|---|---:|---:|---:|---|
| NASH | 450 | 191,03 | %42,5 | kaplamalı tavuk |
| DIPLOMAT | 640 | 176,30 | %27,5 | dana kıyma, Doritos |
| NOUR | 390 | 106,65 | %27,3 | fake soğanı |
| PUMPKIN CARROT | 390 | 88,59 | %22,7 | iceberg |
| SEUL | 430 | 92,40 | %21,5 | kaplamalı tavuk |
| LILLY | 610 | 124,28 | %20,4 | dana kıyma, soğan turşusu |
| CHEESE TAO | 410 | 62,43 | %15,2 | kaplamalı tavuk, iceberg, halka turşu |
| TAFF CHICKEN | 410 | 60,91 | %14,9 | kaplamalı tavuk |
| NORI | 610 | 84,06 | %13,8 | dana kıyma |
| OVERDOSE | 440 | 59,56 | %13,5 | kaplamalı tavuk, iceberg |
| CHEESE BURGER | 560 | 73,86 | %13,2 | dana kıyma, salatalık turşusu |
| SHOGUN | 420 | 54,45 | %13,0 | iceberg, kuru soğan, halka turşu |
| COY | 630 | 80,14 | %12,7 | dana kıyma |
| EL TORATO | 610 | 69,47 | %11,4 | dana kıyma |
| YOUKI | 610 | 67,91 | %11,1 | dana kıyma, salatalık turşusu |
| FAKE DONALD'S | 610 | 65,47 | %10,7 | dana kıyma, iceberg, salatalık turşusu, fake soğanı |
| TRUFFLE | 630 | 66,17 | %10,5 | dana kıyma, iceberg, salatalık turşusu |
| ROYALE | 900 | 67,02 | %7,4 | dana kıyma, Royale sos |
| BALANCE | 590 | 25,30 | %4,3 | dana kıyma, iceberg, kırmızı soğan, domates |

Gözlem: Nash ve Diplomat'ta 50 gr dana füme tek başına ≈99 ₺ (250 gr paket 495 ₺ → 1.980 ₺/kg).
Nash'te 150 gr özel BBQ (37 ₺) ve 50 gr soğan kıtırı (31 ₺) eklenince, tavuk hariç bile oran %42.

## Sınırlar
- KDV: menü fiyatı KDV dahil varsayıldı; tedarikçi fiyatlarının KDV durumu sitede yazmıyor.
- Taff soslarında paket boyutu yazmıyor; fiyat 1 kg kabul edildi (tahmin). 1 kg değilse sos maliyetleri değişir.
- Ambalaj, yağ, baharat ve personel maliyeti dahil değil; bu yalnızca reçete malzeme maliyeti.
