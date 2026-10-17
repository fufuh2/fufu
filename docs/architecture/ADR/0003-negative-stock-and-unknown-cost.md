# ADR-0003: Negatif stok politikası ve "bilinmeyen ≠ sıfır"

**Durum:** Kabul edildi (faz 1)

## Problem
1. POS'tan gelen satış gerçekte olmuştur. Sistem stok yetersiz diye reddederse satış verisi kaybolur.
2. Fire veya sayım gibi manuel işlemler ise sistemdeki stoktan fazla olamaz.
3. Reçetesi olmayan ürün satıldığında maliyeti 0 yazmak marjı şişirir.

## Karar
- **Satış tüketimi** bakiyeyi negatife düşürebilir; stok raporunda `negative: true` ile işaretlenir.
- **Fire** negatife düşüremez → `409 insufficient_stock`.
- **Sayım** sayılan miktarı (≥ 0) yazar; fark hareketi oluşturur.
- Satış anında geçerli reçete yoksa sipariş kabul edilir, satır `recipe_version_id = NULL`,
  `cost_total = NULL`. Marj raporu bu ürünü ve toplamı `incomplete_cost` olarak döner,
  brüt kâr `null` olur.
- Sayım olmayan dönemde açıklanamayan fark `null` + `no_count_in_period` döner.
- Fark hiçbir zaman "hırsızlık" olarak etiketlenmez; durum `needs_review`.

## En güçlü karşı argüman
Negatif stoka izin vermek, mal kabulü girilmemiş teslimatları gizleyebilir ve ortalama maliyeti
bozabilir. Karşılık: negatif bakiye görünür işaretleniyor ve stok ≤ 0 iken gelen girişte ortalama
sıfırlanıyor. Yine de tenant bazlı "satışı da engelle" ayarı ileride gerekebilir.

## Bu karar ne zaman çöker
İşletme gerçek zamanlı stok rezervasyonu istiyorsa (ör. online siparişte "tükendi" göstermek),
bu politika yetersizdir; ayrı bir rezervasyon katmanı gerekir.
