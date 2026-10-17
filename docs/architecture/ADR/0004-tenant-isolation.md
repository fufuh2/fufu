# ADR-0004: Tenant ve şube izolasyonu uygulama katmanında

**Durum:** Kabul edildi (faz 1), RLS sonraki faz için açık soru

## Karar
- Tenant kimliği **istemciden alınmaz**. JWT yalnızca `sub` (kullanıcı id) taşır; her istekte
  kullanıcı DB'den okunur, `tenant_id`, rol ve şube listesi oradan gelir (`load_actor`).
- Tüm servis sorguları `tenant_id == actor.tenant_id` filtresi içerir (`app/services/scope.py`).
- Başka tenant'a ait ID → **404** (403 değil), varlık bilgisi sızmaz.
- `owner` rolü tenant'ın tüm şubelerini görür; diğer roller sadece `user_branch_access` kayıtlarını.
- İzin kontrolleri rol adına değil izne göre yapılır (`Perm`), rol = izin paketi.
- Pasif kullanıcının token'ı bir sonraki istekte 401 alır (her istekte DB kontrolü).

## Reddedilen / ertelenen
- **PostgreSQL Row-Level Security**: Bir sorguda filtre unutulursa son savunma hattı olurdu.
  Ertelendi çünkü bağlantı başına `SET app.tenant_id` ve havuz yönetimi gerektirir. Bu, faz 1'in
  en önemli güvenlik borcudur; mutasyon testi tenant filtresinin kaldırılmasını yakaladı ama
  her yeni sorgu için test yazılmasına bağımlıyız.
- Ayrı şema / ayrı veritabanı per tenant: küçük işletmeler için operasyon maliyeti yüksek.

## Platform yöneticisi
HTTP üzerinden platform yöneticisi yok. Tenant oluşturma yalnızca CLI (`python -m app.cli
create-tenant`) ile, veritabanı erişimi olan operatör tarafından yapılır.
