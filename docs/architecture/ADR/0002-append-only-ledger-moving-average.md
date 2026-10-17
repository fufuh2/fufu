# ADR-0002: Değiştirilemez stok defteri + hareketli ağırlıklı ortalama maliyet

**Durum:** Kabul edildi (faz 1)

## Problem
Stok miktarı "ekrandaki bir sayı" olmamalı; her değişimin kaynağı, kullanıcısı, zamanı ve
maliyeti izlenebilmeli. Geçmiş maliyetler sessizce yeniden yazılmamalı.

## Karar
- `stock_movements`: işaretli miktar (`quantity_base`), o anki `unit_cost`, `total_cost`,
  `balance_after`, `occurred_at` (ekonomik tarih), `recorded_at` (sistem tarihi), `user_id`, `ref`.
  UPDATE/DELETE PostgreSQL trigger'ı ile reddedilir. Düzeltme = yeni hareket.
- `stock_balances`: (şube, malzeme) başına materyalize bakiye + ortalama maliyet. Her değişim
  `SELECT ... FOR UPDATE` altında, hareketle aynı transaction'da. Çok kalemli işlemler kilitleri
  `item_id` sırasıyla alır (deadlock önleme).
- Maliyet yöntemi: **hareketli ağırlıklı ortalama**. Giriş ortalamayı günceller; çıkışlar o anki
  ortalamadan değerlenir ve bu değer harekete yazılır, böylece SMM tarihsel olarak sabittir.
- Eldeki stok ≤ 0 iken giriş olursa yeni ortalama = giriş maliyeti (eski ortalama fiziksel stoğu
  temsil etmiyor).
- Hassasiyet: miktar `NUMERIC(18,4)`, birim maliyet `NUMERIC(18,6)`, para `NUMERIC(18,2)`,
  yuvarlama ROUND_HALF_EVEN, Python tarafında `Decimal`.

## Reddedilenler
- FIFO katmanları: daha doğru ama karmaşık; lot/SKT modülü ile birlikte değerlendirilecek.
- Sadece bakiye tablosu: denetim izi yok.
- Bakiyeyi her sorguda defterden hesaplamak: doğru ama yavaş; invariant testi (`bakiye = Σ hareket`)
  ile ikisinin uyumu doğrulanıyor.

## Bilinen sınırlamalar
- `TRUNCATE` trigger ile engellenmiyor; üretimde uygulama rolüne TRUNCATE yetkisi verilmemeli.
- Veritabanı süper kullanıcısı trigger'ı kapatabilir; trigger uygulama hatalarına karşı korur,
  kötü niyetli DBA'ya karşı değil.
- Satır başına yuvarlama nedeniyle sipariş satırı maliyetlerinin toplamı ile hareket
  `total_cost` toplamı kuruş düzeyinde farklı olabilir.
