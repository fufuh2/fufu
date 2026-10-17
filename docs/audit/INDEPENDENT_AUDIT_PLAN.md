# Denetim Planı

**Tarih:** 2026-10-17 · **Denetlenen commit:** `6f2bfac` (dal `ccr-5ace6090-44fj25`)

## Bağımsızlık beyanı
Bu denetimi, kodu yazan aynı yapay zekâ oturumu yürütüyor. İstemin tanımıyla **bağımsız değildir**.
Bu zayıflığı azaltmak için:
- `docs/STATUS.md` ve `REQUIREMENTS.md` içindeki iddialar doğru kabul edilmez, yeniden test edilir.
- Maliyet/stok doğrulaması uygulamanın fonksiyonlarını çağırmayan, ayrı yazılmış bir referans
  modelle (test içinde) yapılır.
- Mevcut test paketine güvenmek yerine yeni, düşmanca (adversarial) senaryolar yazılır.
- Gerçek bağımsızlık için bir insan veya farklı bir araç/ekip tarafından ikinci inceleme önerilir.

## Kapsam
Repo'daki tek uygulama `backend/` (FastAPI + PostgreSQL). Frontend, entegrasyon, vardiya, kalite,
satın alma modülleri **yok**; bunlar NOT IMPLEMENTED olarak sınıflandırılır, test edilmiş sayılmaz.

## Ortam
- İzole PostgreSQL 16 (yerel konteyner içi), test veritabanları `opsx_test`, `opsx_audit_*`.
- Üretim veritabanı yok; dış servis çağrısı yok.
- Docker daemon yok → container testleri BLOCKED.

## Riskler (öncelikli inceleme alanları)
1. Sayısal taşma / sıfıra yuvarlanan miktarlar → 500 veya bozuk kayıt.
2. Geriye tarihli hareketlerin sayım sonrası bakiyeyi bozması.
3. Tenant/şube filtresi unutulmuş endpoint.
4. Login brute-force, hassas veri yansıtma, güvenli olmayan varsayılan ayarlar.
5. Hesap motoru ile raporların kendi aralarında tutarsızlığı (yuvarlama).
6. Kurulumun yalnızca yazarın ortamında çalışması.

## Yöntemler
| Alan | Yöntem |
|---|---|
| Mevcut testler | `pytest`, lint, mypy, migration check — temel çizgi, ayrı kaydedilir |
| Hesap doğrulama | Hypothesis ile rastgele işlem dizileri + bağımsız referans model |
| Uç durumlar | El ile yazılmış düşmanca HTTP istekleri (taşma, sıfır, geriye tarih, büyük sayı) |
| Güvenlik | Endpoint × rol matrisi, IDOR, token, rate limit, hata gövdeleri, bandit, secret scan, pip-audit |
| Veri bütünlüğü | Şema incelemesi (FK, nullable, check), bakiye ↔ defter uzlaştırma, yedek/geri yükleme |
| Kurulum | Temiz Python 3.12 venv'de README adımları; GitHub Actions sonucu |

## Kabul kriterleri
- P0/P1 açık bulgu yoksa ve kritik akış + hesaplar bağımsız doğrulanmışsa en fazla
  **CONDITIONAL PASS** (frontend ve entegrasyon olmadığından tam PASS mümkün değil).
- Herhangi bir P0/P1 açıksa **FAIL**.
- Her bulgu için yeniden üretim + regresyon testi; düzeltilemeyen bulgu açık kalır.
