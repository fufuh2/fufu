# Canlıya Alma: GitLab + Render

## Canlı durum (2026-10-17)
| Bileşen | Durum |
|---|---|
| GitLab | `gitlab.com/taffburger9/fufu` (private), `main` korumalı, merge yalnızca başarılı pipeline ile. **CI pipeline job çalıştırmadan düşüyor**: hesapta shared runner doğrulaması gerekiyor olabilir (GitLab → Settings → CI/CD veya hesap doğrulama). Aynı kontroller GitHub Actions'ta yeşil. |
| Render DB | `opsx-db`, PostgreSQL 16, Frankfurt, **free: 2026-11-16'da sona eriyor** (Render API'nin döndürdüğü tarih). |
| Render web | `opsx-api` → https://opsx-api.onrender.com, free, Frankfurt, sağlık kontrolü `/readyz`, otomatik deploy açık. **Kaynak GitHub** (`fufuh2/fufu`, dal `ccr-5ace6090-44fj25`): Render hesabı GitLab'a bağlı değil, private GitLab reposunu okuyamıyor. |
| Sırlar | `OPSX_JWT_SECRET` ve `OPSX_INTEGRATION_KEY` Render'da üretildi; git'te yok. |
| İlk sahip | **Henüz oluşturulmadı**; aşağıdaki "Yapman gerekenler" bölümüne bak. |

## Yapman gerekenler (yaklaşık 5 dakika)
1. Render → `opsx-api` → **Settings → Start Command**'ı şununla değiştir:
   `alembic upgrade head && python -m app.cli bootstrap-if-empty && uvicorn app.main:create_app --factory --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips '*'`
2. **Environment** bölümüne şunları ekle: `OPSX_BOOTSTRAP_TENANT` (işletme adı), `OPSX_BOOTSTRAP_OWNER_EMAIL`,
   `OPSX_BOOTSTRAP_OWNER_PASSWORD` (en az 12 karakter, kendin belirle). Ardından **Save, rebuild and deploy**.
3. https://opsx-api.onrender.com/ui/ adresinden giriş yap. Sonra bu üç değişkeni **sil**. (İşletme
   varken komut zaten bir şey yapmaz, ama parola ortam değişkeninde kalmamalı.)
4. Kaynağı GitLab'a çevirmek istersen: Render → Account Settings → Git Providers → GitLab'ı bağla.
   Sonra `opsx-api` → Settings → Repository'yi `gitlab.com/taffburger9/fufu`, dalı `main` yap.
5. Sohbete yazılan iki token'ı iptal et ve yenile.

Repo deploy için hazır:
- `render.yaml`: Render blueprint (PostgreSQL 16 + web servisi, Frankfurt, free plan)
- `.gitlab-ci.yml`: GitHub CI ile aynı kontroller
- Uygulama Render'ın `postgres://` adresini kendisi çevirir; production'da HSTS gönderir, `/docs` kapalıdır.
- Gizli değerler (`OPSX_JWT_SECRET`, `OPSX_INTEGRATION_KEY`) Render tarafından üretilir, git'e girmez.

## 1. GitLab'a gönder (private)
```bash
# GitLab'da boş, private "fufu" projesi oluştur (New project → Create blank project → Private)
git remote add gitlab https://gitlab.com/<kullanici>/fufu.git
git push gitlab ccr-5ace6090-44fj25:main
```
Token'ı remote URL'ye yazma. Git, parola sorduğunda token'ı oradan gir.

## 2. Render'da blueprint ile kur
1. Render Dashboard → **New → Blueprint**.
2. GitLab (veya GitHub) hesabını bağla, `fufu` reposunu ve `main` dalını seç.
3. Render `render.yaml` dosyasını okur ve `opsx-db` ile `opsx-api` servislerini oluşturur. **Apply**'a bas.
4. İlk deploy: build → `alembic upgrade head` → uvicorn → `/readyz` sağlık kontrolü.

## 3. İlk işletme sahibini oluştur
**Shell'siz (free plan) yol:** Servise geçici olarak `OPSX_BOOTSTRAP_TENANT`, `OPSX_BOOTSTRAP_OWNER_EMAIL`,
`OPSX_BOOTSTRAP_OWNER_PASSWORD` ekle ve deploy et. Başlatma komutundaki `bootstrap-if-empty`, veritabanında
işletme yoksa sahibi oluşturur; varsa hiçbir şey yapmaz. Girişi doğruladıktan sonra üç değişkeni **sil**
ve arayüzde **Hesabım → Parola değiştir** ile parolayı değiştir.

**Yerel bağlantı yolu (alternatif):**
Render'da `opsx-db` → **Connect → External Database URL**'yi kopyala. Ardından yerelde:
```bash
cd backend
export OPSX_DATABASE_URL='<external database url>'
export OPSX_JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")  # yalnızca CLI için
OPSX_OWNER_PASSWORD='<en az 12 karakter>' python -m app.cli create-tenant --name "<İşletme adı>" --owner-email <e-posta>
```
Sonra `https://opsx-api.onrender.com/ui/` adresinden giriş yap (adres Render'ın verdiğiyle değişebilir).

## 4. Doğrulama
```bash
curl -s https://<servis>.onrender.com/readyz      # {"status":"ok","database":"ok"}
curl -sI https://<servis>.onrender.com/ui/ | grep -i strict-transport-security
python scripts/audit_e2e.py ...                    # YALNIZCA ayrı bir test veritabanında; canlı DB'de çalıştırma
```

## Free plan sınırları (Render dokümanından kontrol et; burada doğrulanmadı)
- Free web servisi bir süre trafik almazsa uyur; ilk istek yavaş gelir.
- Free PostgreSQL'in kullanım süresi ve kapasitesi sınırlıdır. Gerçek işletme verisi için ücretli plan ve otomatik yedek gerekir.
- Free planda shell veya one-off job olmayabilir; bu yüzden 3. adım yerelden yapılır.

## Güvenlik
- Sohbete yazılan GitLab ve Render token'larını **iptal edip yenile**.
- Render'da `OPSX_INTEGRATION_KEY`'i yalnızca POS kullanılacaksa paylaş. Değiştirmek tüm POS imzalarını geçersiz kılar.
- IP bazlı istek sınırı yok (FND-002 kalan riski). Gerekirse Render önüne Cloudflare gibi bir katman koy.
