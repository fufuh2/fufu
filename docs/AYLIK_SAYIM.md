# Aylık Sayım — Kullanım

Adres: https://opsx-api.onrender.com/ui/ → giriş → **Panel** (açılış sayfası).

## Sayfalar
| Sayfa | Ne işe yarar |
|---|---|
| Panel | Seçili ayın ilerlemesi, eksik / fazla / zayi / net fark (₺), stok değeri; son 12 ayın net fark ve zayi grafiği; en çok eksik veren ve en çok zayi olan 10 ürün; grup özeti; birden çok şube varsa şubeler tablosu |
| Aylık Sayım | Sayım formu (aşağıda) |
| Geçmiş Aylar | Her ayın durumu, sayan kişi, eksik/fazla/zayi/net fark ve stok değeri; tıklayınca o ay açılır; Excel |
| Ürün Analizi | Bir ürünün ay ay devir, gelen, satılan, zayi, beklenen, sayılan, fark ve TL değerleri, fark grafiği |
| Ürün Listesi | Ürün ekleme, ad / grup / birim / fiyat düzenleme, pasife alma, arama ve grup filtresi |
| Kullanıcılar | (İşletme sahibi) kullanıcı ekleme, rol ve şube atama, pasife alma, parola sıfırlama |
| Şubeler | (İşletme sahibi) şube ekleme, ad değiştirme, pasife alma |
| Kayıt Geçmişi | Kim, ne zaman, ne yaptı (sayım kaydı, ay kapatma, fiyat değişikliği, giriş…); değiştirilemez |
| Hesabım | Bilgiler ve parola değiştirme |

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

Kayda geçirilen zayi "eksik" görünmez; kalan **eksik** açıklanamayan kayıptır. Sayılmamış ürün "SAYILMADI"
görünür, sıfır kabul edilmez.

## Ay sonu adımları
1. Gelen, satılan/kullanılan ve zayi miktarlarını girin (ay boyunca da girilebilir). **Kaydet** veya Ctrl+S.
2. Sayımı yapın, **Sayılan** sütununu doldurun. Enter aynı sütunda alttaki ürüne geçer, Shift+Enter yukarı.
   Ondalık için virgül kullanılabilir (12,5).
3. Elde olmayan ürünler için **Boşlara 0 yaz**. "Sayımı yapan" adını yazın, **Kaydet**.
4. **Göster** filtresiyle Eksikler / Fazlalar / Zayi olanlar'ı kontrol edin.
5. **Ayı kapat** (şube müdürü veya işletme sahibi). Kapatılan ay değiştirilemez; sayılanlar sonraki ayın devri olur,
   o anki birim fiyatlar sabitlenir.
6. **Excel indir** veya **Yazdır**.

Kaydedilmemiş değişiklik varken başka sayfaya, aya veya şubeye geçerken uyarı çıkar.
Hata fark edilirse **Yeniden aç** (neden zorunlu, kayda geçer). Sonraki ay kapatıldıysa önce o açılmalıdır.

## Roller
| Rol | Yetki |
|---|---|
| İşletme sahibi | Her şey, tüm şubeler |
| Şube müdürü | Atandığı şubelerde sayım girer, ayı kapatır / yeniden açar, kayıt geçmişini görür |
| Sayım personeli | Atandığı şubelerde sayım girer |
| İzleyici | Atandığı şubelerin sayımlarını ve kayıtlarını görür |

## Bilinen sınırlar
- Gelen ve satılan miktarlar elle girilir; POS veya fatura entegrasyonu yok.
- Zayinin nedeni tutulmaz.
- Aynı ayı iki kişi aynı anda düzenlerse son kaydeden kazanır.
- Excel'den sayım yükleme yok.
