# ADR-0001: Modüler monolit — FastAPI + SQLAlchemy 2 + PostgreSQL

**Durum:** Kabul edildi (faz 1)

## Problem
Boş repo. Stok, maliyet ve sipariş verisi tek bir tutarlılık sınırı içinde değişmeli
(sipariş + stok düşümü + maliyet aynı transaction'da).

## Seçenekler
1. Mikroservisler (sipariş, stok, maliyet ayrı) — dağıtık transaction/saga gerektirir.
2. **Modüler monolit, tek PostgreSQL** — modüller `app/services/*` altında, ortak transaction.
3. Node/TypeScript backend — frontend ile dil birliği.

## Karar
Seçenek 2. Python + FastAPI (OpenAPI otomatik), SQLAlchemy 2 (senkron, `FOR UPDATE` desteği),
psycopg 3, Alembic migration, PostgreSQL 16 (`NUMERIC`, `CHECK`, trigger, `ON CONFLICT`).

## Reddedilenler
- Mikroservis: stok tutarlılığı için iki-fazlı commit ya da saga gerekir; ilk sürüm için
  maliyeti faydasından yüksek.
- TypeScript backend: makul alternatif. Python seçildi çünkü ileride istatistik/anomali
  işleri aynı dilde yapılabilir. Bu zayıf bir gerekçe; ekip TS biliyorsa karar ters çevrilebilir.

## Değişimin maliyeti
Servis katmanı FastAPI'ye bağlı değil (saf fonksiyonlar + `Session`), bu yüzden HTTP katmanını
değiştirmek görece ucuz. PostgreSQL'e özgü özellikler (trigger, `ON CONFLICT`, JSONB) başka bir
veritabanına geçişi pahalı yapar — bilinçli bir bağımlılık.

## Bu karar ne zaman çöker
Tek şube başına saniyede yüzlerce sipariş ve tek bir sıcak malzeme (ör. ekmek) varsa, satır kilidi
darboğaz olur. Faz 1'de ölçülmedi.
