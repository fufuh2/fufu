# Aylık Sayım — Kullanım

Adres: https://opsx-api.onrender.com/ui/ → giriş → **Panel** (açılış sayfası).

## Sayfalar
| Sayfa | Ne işe yarar |
|---|---|
| Panel | Seçili ayın ilerlemesi, eksik / fazla / zayi / net fark (₺), stok değeri; son 12 ayın net fark ve zayi grafiği; en çok eksik veren ve en çok zayi olan 10 ürün; grup özeti; birden çok şube varsa şubeler tablosu |
| Aylık Sayım | Sayım formu (aşağıda) |
| Geçmiş Aylar | Her ayın durumu, sayan kişi, eksik/fazla/zayi/net fark ve stok değeri; tıklayınca o ay açılır; Excel |
| Ürün Analizi | Bir ürünün ay ay devir, gelen, satılan, zayi, beklenen, sayılan, fark ve TL değerleri, fark grafiği |
| Ürün Listesi | Ürün ekleme, ad / grup / birim / fiyat / paket içeriği düzenleme, pasife alma, arama ve grup filtresi |
| Menü & Reçeteler | Burgerler / menüler ve 1 adedinde kullanılan malzemeler (örn. Klasik Burger: 20 GR YOUKİ SOS, 150 GR DANA KIYMA, 1 ADET ekmek) |
| Kullanıcılar | (İşletme sahibi) kullanıcı ekleme, rol ve şube atama, pasife alma, parola sıfırlama |
| Şubeler | (İşletme sahibi) şube ekleme, ad değiştirme, pasife alma |
| Kayıt Geçmişi | Kim, ne zaman, ne yaptı (sayım kaydı, ay kapatma, fiyat değişikliği, giriş…); değiştirilemez |
| Hesabım | Bilgiler ve parola değiştirme |

## Hesap
| Sütun | Anlamı |
|---|---|
| Devir | Ay başındaki miktar. Önceki ay **kapatıldıysa** onun "Sayılan" değeri gelir ve kilitlidir (🔒). İlk ayda elle girilir. |
| Gelen | Ay içinde gelen (satın alınan) miktar |
| Satıştan (reçete) | **Otomatik:** ayın menü satışları × reçete. Bir sos birden çok burgerde kullanılıyorsa hepsinin toplamı |
| Diğer kullanım (elle) | Reçetede olmayan kullanım (temizlik malzemesi, personel yemeği, ikram…) elle girilir |
| Zayi | Bozulan, dökülen, son kullanma tarihi geçen veya atılan miktar |
| **Beklenen** | **Devir + Gelen − Satıştan (reçete) − Diğer kullanım − Zayi** (otomatik) |
| Sayılan | Ay sonunda fiilen sayılan miktar |
| **Fark** | **Sayılan − Beklenen** → artı: **FAZLA**, eksi: **EKSİK**, sıfır: **TAMAM** |
| Fark ₺ | Fark × birim fiyat (Ürün Listesi'nde fiyat girilmişse) |

Kayda geçirilen zayi "eksik" görünmez; kalan **eksik** açıklanamayan kayıptır. Sayılmamış ürün "SAYILMADI"
görünür, sıfır kabul edilmez.

## Soslar ve reçeteler
Örnek: YOUKİ SOS Klasik Burger'de 20 gr, Smash Burger'de 15 gr. Ayda 120 Klasik ve 80 Smash satıldıysa
120 × 20 + 80 × 15 = 3.600 gr = **3,6 KG** "Satıştan (reçete)" sütununa otomatik yazılır ve beklenenden düşülür.
Sayılan miktar bundan azsa fark **EKSİK** çıkar: porsiyon fazla konuyor, zayi yazılmıyor ya da kayıp var demektir.

1. **Menü & Reçeteler**: her burger / menü ürününü ekleyin; 1 adet için malzemeleri GR, KG, ML, LT veya ADET ile girin.
2. Adetle sayılan ürünler (KOVA KETÇAP, BBQ SOS…) için **Ürün Listesi → Paket içeriği** girin (örn. 1 ADET = 9,5 KG).
   Böylece reçetede 19 GR yazılabilir; 1000 satış × 19 gr = 19 kg = 2 kova düşülür.
3. Her ay **Aylık Sayım → Menü satışları** kutusuna o ay satılan adetleri yazın (POS raporundan). Değerler anında hesaplanır.
4. "Satıştan" hücresinin üzerine gelince hangi üründen ne kadar düşüldüğü görünür.

Reçete değişirse **taslak** aylar yeniden hesaplanır; **kapatılmış** ay kapandığı günkü değeri korur.

### Hazır menü (19 burger)
**Menü & Reçeteler → Taff Burger menüsünü yükle**, menü panolarındaki et, tavuk ve light burgerleri
(`backend/app/data/taffburger_menu.json`) yükler. Rapor iki eksik gösterir:
- **Sayım listesinde olmayan malzemeler** (Iceberg, kaplamalı tavuk, Dynamite/Seul/Mexican/Royale/Bacon Jam/TSO/
  Parmesan Garlic/Tahin sos, domates, soğanlar, jalapeno, Doritos, falafel, mücver): butonla listeye eklenip reçetelere bağlanır.
- **Çevrimi eksik malzemeler**: cheddar (dilim), salatalık ve halka turşusu (adet), adetle sayılan ketçap / hardal /
  mayonez / BBQ. Ürün Listesi'nde paket içeriği girilene kadar (örn. 1 KG cheddar = 50 ADET) düşülmez; sayım formu uyarır.

Varsayımlar rapordadır: ekmek her burgere 1 adet (Balance hariç), salatalık turşusu = KÜHNE ALMAN TURŞU,
karamelize soğan = FAKE SOĞANI, özel barbekü = BBO SOS, ketçap/hardal = TOP DOWN, mayonez = KOVA MAYONEZ.
Shogun'un menü metninde tavuk yok; gerekiyorsa reçeteye elle ekleyin.

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
- Gelen miktarlar ve menü satış adetleri elle girilir; POS veya fatura entegrasyonu yok.
- Reçete bir varsayımdır: gerçek porsiyon farkı "eksik/fazla" olarak görünür (amaç da budur).
- Zayinin nedeni tutulmaz.
- Aynı ayı iki kişi aynı anda düzenlerse son kaydeden kazanır.
- Excel'den sayım yükleme yok.
