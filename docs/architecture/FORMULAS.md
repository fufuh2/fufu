# Hesaplama Formülleri

Tüm hesaplamalar `Decimal` ile, `ROUND_HALF_EVEN` yuvarlama. Para birimi tenant düzeyinde
(`tenants.currency`, varsayılan TRY); çoklu para birimi yok.

| Kavram | Formül | Kod |
|---|---|---|
| Baz miktar | girilen miktar × birim faktörü | `domain/units.to_base` |
| Mal kabul birim maliyeti (baz) | girilen birim maliyet × girilen miktar ÷ baz miktar | `inventory.receive` |
| Ortalama maliyet (giriş sonrası) | (eldeki × ort + gelen × gelen_maliyet) ÷ (eldeki + gelen); eldeki ≤ 0 ise = gelen_maliyet | `costing.moving_average_cost` |
| Brüt çıkış (kayıplı) | net ÷ (1 − kayıp% / 100) | `costing.gross_quantity` |
| Satır net gelir | birim fiyat × adet − indirim (fiyat KDV hariç) | `orders.create_order` |
| Satır SMM | Σ (reçete brüt miktarı × adet × o anki ortalama maliyet) | `orders.create_order` |
| Brüt kâr | net gelir − SMM | `costing.gross_margin` |
| Brüt marj % | brüt kâr ÷ net gelir × 100; net gelir = 0 ise `null` (`undefined_zero_revenue`); SMM bilinmiyorsa kâr ve marj `null` (`incomplete_cost`) | `costing.gross_margin` |
| Teorik tüketim | −Σ(sale_consumption + sale_reversal) | `reports.variance` |
| Kayıtlı fire | −Σ(waste) | `reports.variance` |
| Açıklanamayan fark | −Σ(count_adjustment); dönemde sayım yoksa `null` | `reports.variance` |
| Gerçek tüketim | teorik + fire + açıklanamayan | `reports.variance` |

## Dönem sınırları
`[from, to)` — başlangıç dahil, bitiş hariç; `occurred_at` (ekonomik tarih) kullanılır.
Zaman dilimi olmayan tarih reddedilir.

## İstemdeki "stok farkı = gerçek − teorik" ile ilişki
Bu rapordaki `actual − theoretical` = fire + açıklanamayan. Fireyi ayrı göstermek, kayıtlı ve
açıklanamayan kaybı birbirinden ayırır. Açıklanamayan fark **kesin kayıp değildir**: dönem kesimi,
geç girilen mal kabulü ve (henüz olmayan) transferler de fark üretir.

## KDV, indirim, iade
- Fiyatlar KDV hariç kabul edilir; KDV hesabı yok.
- İndirim satır düzeyinde, net gelirden düşülmüş olarak raporlanır; ayrıca `discounts` kolonu.
- Tam iptal stoku iade eder ve siparişi marj raporundan çıkarır. Kısmi iade yok.
