# Aylık Sayım — Kullanım

Adres: https://opsx-api.onrender.com/ui/ → giriş → **Aylık Sayım** (açılış sayfası).

## Hesap
| Sütun | Anlamı |
|---|---|
| Devir | Ay başındaki miktar. Önceki ay **kapatıldıysa** onun "Sayılan" değeri gelir ve kilitlidir (🔒). İlk ayda elle girilir. |
| Gelen | Ay içinde gelen (satın alınan) miktar |
| Satılan / Kullanılan | Ay içinde satılan, kullanılan veya tüketilen miktar |
| Zayi | Bozulan, dökülen, son kullanma tarihi geçen veya atılan miktar |
| **Beklenen** | **Devir + Gelen − Satılan/Kullanılan − Zayi** (otomatik) |
| Sayılan | Ay sonunda fiilen sayılan miktar |
| **Fark** | **Sayılan − Beklenen** → artı: **FAZLA**, eksi: **EKSİK**, sıfır: **TAMAM** |
| Fark ₺ | Fark × birim fiyat (Ürün Listesi'nde fiyat girilmişse) |

Zayi beklenenden düşüldüğü için, kayda geçirilen zayi "eksik" olarak görünmez; kalan **eksik**
açıklanamayan kayıptır. Zayinin TL karşılığı (zayi × birim fiyat) özet kartında ve Excel'de ayrıca gösterilir.

Sayılmamış ürün "SAYILMADI" görünür; sıfır kabul edilmez. Elde olmayan ürüne 0 yazın.

## Ay sonu adımları
1. Gelen, satılan/kullanılan ve zayi miktarlarını girin (ay boyunca da girilebilir, **Kaydet**).
2. Sayımı yapın, **Sayılan** sütununu doldurun, "Sayımı yapan" adını yazın, **Kaydet**.
3. Filtre ile **Eksikler / Fazlalar**'ı kontrol edin.
4. **Ayı kapat**: tüm ürünler sayılmış olmalı. Kapatılan ay değiştirilemez; sayılanlar sonraki ayın devri olur ve o anki birim fiyatlar sabitlenir.
5. **Excel indir** veya **Yazdır** ile raporu alın (Excel'de Beklenen ve Fark formülle hesaplanır).

Hata fark edilirse **Yeniden aç** (neden zorunlu, kayda geçer). Sonraki ay kapatıldıysa önce o açılmalıdır.

## Ürün Listesi
- "Taff Burger sayım formundaki 98 ürünü yükle" butonu, gönderilen Excel'deki 6 grup ve 98 ürünü ekler.
- Yeni ürün eklenebilir, birim fiyat girilebilir, ürün pasife alınabilir (geçmiş sayımlar korunur).

## Yetkiler
Sayım girme: owner, bölge müdürü, şube müdürü, vardiya sorumlusu, satın alma. Ayı kapatma/yeniden açma: owner,
bölge müdürü, şube müdürü. Ürün ve fiyat değiştirme: owner, bölge müdürü.

## Bilinen sınırlar
- Gelen ve satılan miktarlar elle girilir; POS veya satın alma modülünden otomatik gelmez.
- Uygulama içinde sayfa değiştirirken kaydedilmemiş değişiklik uyarısı yok (yalnızca sekme kapatılırken/yenilenirken).
- Excel'den dosya yükleme yok; ürünler listeden veya formdan eklenir.
