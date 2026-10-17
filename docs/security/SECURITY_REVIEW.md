# Güvenlik İncelemesi — Faz 1

OWASP ASVS 5.0.0 bu oturumda kaynaktan okunmadı; bölüm numaraları ve madde kimlikleri bu yüzden
yazılmadı. Aşağıdaki eşleme **konu bazlıdır**. Sonraki adım: ASVS 5.0.0'ın resmî metnini
(https://owasp.org/www-project-application-security-verification-standard/) madde madde bu tabloya
eşlemek.

| Konu | Durum | Kanıt / Not |
|---|---|---|
| Parola saklama | Uygulandı | Argon2 (`argon2-cffi` varsayılan parametreleri), min 12 karakter |
| Kullanıcı varlığını sızdırmama | Uygulandı | Bilinmeyen e-postada sahte hash doğrulaması; tek tip hata mesajı |
| Brute-force / rate limiting | **YOK** | Açık risk. Ters proxy veya uygulama düzeyinde eklenmeli |
| MFA, parola sıfırlama | YOK | |
| Token | Kısmi | HS256, 30 dk, `exp` zorunlu, `alg=none` reddi test edildi; refresh/iptal yok |
| Oturum iptali | Kısmi | Pasif kullanıcı anında 401; aktif kullanıcının token'ını iptal mekanizması yok |
| Yetkilendirme | Uygulandı | İzin bazlı (`Perm`), her serviste `actor.require` |
| Tenant izolasyonu | Uygulandı (uygulama katmanı) | `scope.py`; RLS yok (ADR-0004) |
| IDOR | Test edildi | Çapraz tenant ve çapraz şube ID'leri 404 |
| Girdi doğrulama | Uygulandı | Pydantic, `extra="forbid"`, sayısal sınırlar, regex'li kodlar |
| SQL injection | Düşük risk | Yalnızca SQLAlchemy parametreli sorgular; ham SQL sadece migration ve testlerde |
| XSS | N/A (faz 1) | JSON API, HTML üretilmiyor; frontend fazında yeniden değerlendirilmeli |
| CSRF | N/A (faz 1) | Cookie yok, Bearer token; frontend cookie kullanırsa gerekli |
| Güvenlik başlıkları | Kısmi | `nosniff`, `X-Frame-Options`, `Referrer-Policy`, `Cache-Control: no-store`; HSTS/CSP yok |
| Hata mesajları | Uygulandı | 500'de genel mesaj, stack trace log'a; test edildi |
| Sırlar | Uygulandı | `OPSX_JWT_SECRET` ≥ 32 karakter yoksa uygulama başlamaz; `.env` git dışı; CLI parolayı argv yerine env'den alır |
| Bağımlılık açıkları | Tarandı | `pip-audit`: bilinen açık yok (2026-10-17). CI'da tekrar ediliyor |
| Denetim kayıtları | Uygulandı | Append-only; ama kimlik doğrulama olayları (başarılı/başarısız login) **kaydedilmiyor** |
| Dosya yükleme | N/A | Özellik yok |
| Veri saklama / silme (KVKK) | YOK | Araştırılmadı; append-only defter ile "silme hakkı" arasındaki gerilim çözülmeli |
| Docker | Kısmi | Root olmayan kullanıcı; imaj taranmadı, build edilmedi |
| Test verisi ↔ üretim | Uygulandı | Test fixture'ı adı `_test` ile bitmeyen DB'de çalışmayı reddeder |

## Bilinen açık riskler (öncelik sırasıyla)
1. Login rate limiting yok.
2. RLS yok — yeni bir sorguda tenant filtresi unutulursa veri sızar.
3. Login denemeleri denetim izine yazılmıyor.
4. Token iptali yok (30 dk pencere).
