# Canlıya Alma: GitLab + Render

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
