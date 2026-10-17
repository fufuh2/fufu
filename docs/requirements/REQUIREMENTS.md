# Gereksinimler — Faz 1

Durum değerleri: **Done** (kod + geçen test), **Partial**, **Not started**.
Test ID'leri `backend/tests/` altındaki pytest fonksiyon adlarıdır.

## Roller

`owner`, `regional_manager`, `branch_manager`, `shift_lead`, `purchasing`, `kitchen`, `quality`,
`finance`, `auditor`. Rol → izin eşlemesi: `backend/app/domain/permissions.py`.
Platform yöneticisi HTTP rolü değildir (ADR-0004). İstemdeki "depo ve satın alma sorumlusu"
`purchasing`, "mutfak çalışanı" `kitchen`, "salt okunur denetçi" `auditor` olarak eşlendi.

---

### REQ-STK-01 Değiştirilemez stok hareketleri
- **Hikâye:** Şube müdürü olarak her stok değişiminin kim, ne zaman, neden yaptığını görmek istiyorum.
- **Gerekçe:** Açıklanamayan farkların kök nedeni izlenebilirlik eksikliği.
- **Kabul:** Her değişim bir `stock_movements` satırı; UPDATE/DELETE DB'de reddedilir; bakiye = Σ hareket.
- **Hata senaryosu:** Doğrudan SQL ile güncelleme → `append-only` hatası.
- **Yetki:** Okuma `stock.read`. **Öncelik:** MUST. **Test:** `test_ledgers_are_append_only`, `test_full_operational_flow` (invariant). **Durum:** Done

### REQ-STK-02 Eşzamanlı tüketimde tutarlılık
- **Kabul:** N paralel işlem aynı malzemeyi tüketince sonuç bakiye kesin olarak doğru; fire stoğu aşamaz.
- **Öncelik:** MUST. **Test:** `test_concurrent_waste_cannot_overdraw`, `test_concurrent_orders_consume_exactly`. **Durum:** Done

### REQ-STK-03 Ölçü birimi dönüşümü
- **Kabul:** kg↔g, l↔ml standart; malzemeye özel birim (paket=6 adet); farklı boyutlar arası tahmin yok → 422.
- **Öncelik:** MUST. **Test:** `TestUnits`, `test_invalid_waste_inputs_rejected`, `test_duplicate_skus_and_units`. **Durum:** Done

### REQ-STK-04 Mal kabul
- **Kabul:** Birim maliyet girilen birim başına; baz birime çevrilir; ortalama maliyet güncellenir.
- **Yetki:** `stock.receive`. **Öncelik:** MUST. **Test:** `test_full_operational_flow`, `test_moving_average`. **Durum:** Done
- **Eksik:** Tedarikçi kaydı, satın alma siparişi ile eşleştirme, fatura karşılaştırma → Not started.

### REQ-STK-05 Fire kaydı
- **Kabul:** 9 neden kategorisi; miktar > 0; stoktan fazla olamaz (409); gelecekteki tarih 422; zaman dilimsiz tarih 422.
- **Yetki:** `stock.waste`. **Öncelik:** MUST. **Test:** `test_invalid_waste_inputs_rejected`, `test_sale_may_go_negative_but_waste_may_not`. **Durum:** Done

### REQ-STK-06 Sayım ve mutabakat
- **Kabul:** Sayılan miktar ile sistem miktarı farkı `count_adjustment` hareketi olur; sayım satırı sistem miktarını ve farkı saklar; aynı malzeme iki kez → 422.
- **Yetki:** `stock.count`. **Öncelik:** MUST. **Test:** `test_full_operational_flow`, `test_count_rejects_duplicates_and_negative`. **Durum:** Done

### REQ-STK-07 Minimum stok uyarısı
- **Kabul:** `below_min` true/false; minimum tanımsızsa `null`.
- **Öncelik:** SHOULD. **Test:** `test_low_stock_flag`. **Durum:** Partial (bildirim/push yok, sadece rapor alanı)

### REQ-RCP-01 Versiyonlu reçete
- **Kabul:** Yeni versiyon öncekini kapatır; satış, satış anındaki versiyonu ve maliyeti saklar; geriye tarihleme 422.
- **Yetki:** `recipe.manage`. **Öncelik:** MUST. **Test:** `test_recipe_versions_do_not_rewrite_history`. **Durum:** Done

### REQ-RCP-02 Hazırlama kaybı
- **Kabul:** net 180 g, kayıp %10 → stoktan 200 g düşer.
- **Öncelik:** MUST. **Test:** `test_preparation_loss_increases_gross_issue`, `test_gross_quantity_with_loss`. **Durum:** Done

### REQ-RCP-03 Alt reçete / yarı mamul — **Not started** (SHOULD)
### REQ-RCP-04 Alerjen — **Partial**: malzeme üzerinde `allergens` listesi var, ürün seviyesinde türetme yok.

### REQ-ORD-01 Idempotent sipariş
- **Kabul:** `Idempotency-Key` zorunlu; aynı anahtar + aynı gövde → 200 ve aynı sipariş; farklı gövde → 409; eşzamanlı aynı anahtar → tek sipariş, tek stok düşümü.
- **Öncelik:** MUST. **Test:** `test_idempotent_replay_and_conflict`, `test_concurrent_same_idempotency_key_creates_one_order`. **Durum:** Done

### REQ-ORD-02 Siparişten stok tüketimi
- **Kabul:** Fiyat sunucudan (istemci fiyatı kabul edilmez); indirim satır tutarını aşamaz; reçeteye göre tüketim; reçetesizse maliyet `null`.
- **Öncelik:** MUST. **Test:** `test_full_operational_flow`, `test_missing_recipe_means_unknown_cost_not_zero`, `test_fully_discounted_sale_margin_undefined`. **Durum:** Done

### REQ-ORD-03 İptal
- **Kabul:** Stok orijinal maliyetle geri döner; ikinci iptal 409; iptal edilen sipariş marj raporunda yok; neden zorunlu.
- **Yetki:** `order.cancel`. **Öncelik:** MUST. **Test:** `test_cancel_reverses_stock_at_original_cost`. **Durum:** Done
- **Eksik:** Kısmi iade, değiştirici (modifier) ve varyant → Not started.

### REQ-ORD-04 POS entegrasyonu — **Not started**. Test adaptörü de yok; `channel=pos` yalnızca etiket.

### REQ-RPT-01 Teorik / gerçek tüketim farkı
- **Kabul:** teorik, kayıtlı fire (nedene göre), açıklanamayan fark, gerçek tüketim; sayım yoksa `null` ve `no_count_in_period`; "hırsızlık" etiketi yok.
- **Yetki:** `report.read`. **Öncelik:** MUST. **Test:** `test_full_operational_flow`, `test_variance_without_count_is_unknown`. **Durum:** Done
- **Sınırlama:** Dönem başı/sonu sayımına dayalı klasik "açılış + giriş − kapanış" formülü değil; dönem içindeki sayım düzeltmelerinin toplamı kullanılıyor. Ayrıntı: `docs/architecture/FORMULAS.md`.

### REQ-RPT-02 Ürün bazlı marj
- **Kabul:** net gelir, indirim, SMM, brüt kâr, marj %; sıfır gelirde marj `null`; eksik maliyette kâr `null`.
- **Öncelik:** MUST. **Test:** `test_full_operational_flow`, `test_missing_recipe_means_unknown_cost_not_zero`, `test_fully_discounted_sale_margin_undefined`. **Durum:** Done
- **Eksik:** KDV, işçilik, genel gider, şube karşılaştırması → Not started.

### REQ-RPT-03 Rapor meta verisi
- **Kabul:** Her rapor `generated_at` ve `source` döner. **Test:** `test_full_operational_flow`. **Durum:** Done

### REQ-SEC-01 Kimlik doğrulama
- **Kabul:** Argon2 parola hash; JWT HS256, 30 dk; süresi geçmiş / sahte / `alg=none` token 401; giriş hatası hangi alanın yanlış olduğunu söylemez; parola ≥ 12 karakter.
- **Test:** `test_requires_authentication`, `test_expired_and_forged_tokens_rejected`, `test_login_does_not_reveal_which_part_was_wrong`. **Durum:** Partial (rate limiting, MFA, token iptali yok)

### REQ-SEC-02 Yetkilendirme ve tenant izolasyonu
- **Test:** `test_tenant_isolation`, `test_branch_scope_for_branch_users`, `test_role_permissions_enforced`, `test_deactivated_user_token_stops_working`. **Durum:** Done (uygulama katmanı; RLS yok)

### REQ-SEC-03 Hata mesajları ve güvenlik başlıkları
- **Test:** `test_errors_do_not_leak_internals`. **Durum:** Done (HSTS/CSP yok — TLS terminasyonu ve frontend olmadan anlamsız)

### REQ-AUD-01 Denetim izi
- **Kabul:** Tüm yazma işlemleri `audit_events`'e; tablo append-only; şube kullanıcısı sadece kendi şubesinin olaylarını görür.
- **Test:** `test_full_operational_flow`, `test_ledgers_are_append_only`, `test_audit_log_is_branch_scoped`. **Durum:** Done (dışa aktarma yok)

## Faz 2 gereksinimleri

| ID | Gereksinim (kabul kriteri özet) | Test | Durum |
|---|---|---|---|
| REQ-STK-08 | Sayımdan önce tarihli fire/mal kabul reddedilir; geç gelen satış offset ile fiziksel sayıma uyar | `test_backdated_waste_before_last_count_is_rejected`, `test_backdated_sale_before_count_does_not_double_consume` | Done |
| REQ-CAT-01 | Malzeme/ürün/şube/kullanıcı güncelleme ve pasifleştirme, önce/sonra denetim izi | `test_management.py` (4 test) | Done |
| REQ-SEC-04 | Token iptali (rol/aktiflik/parola değişimi, logout-all) | `test_role_change_and_deactivation_revoke_tokens`, `test_password_change_and_logout_all`, `test_token_without_version_is_rejected` | Done |
| REQ-RCP-05 | Ürün teorik maliyeti ve food cost %, bilinmeyen maliyet `null` | `test_recipe_cost_endpoint` | Done |
| REQ-TRF-01 | İki aşamalı transfer, maliyetle; eksik teslim `transit_loss`; iptal | `test_transfers.py` (6 test) | Done |
| REQ-PUR-01 | Tedarikçi, fiyat geçmişi, satın alma siparişi yaşam döngüsü, dört göz onayı | `test_purchasing.py` | Done |
| REQ-PUR-02 | Kısmi mal kabul, fazla teslim reddi, %5 fiyat sapması uyarısı, fatura karşılaştırma | `test_po_lifecycle_with_partial_receipts_and_invoice` | Done |
| REQ-PUR-03 | Açık siparişleri dikkate alan sipariş önerisi (min/hedef) | `test_suggestions_include_open_orders` | Done |
| REQ-QC-01 | Kontrol listesi şablonu (evet/hayır, aralıklı sayı, kanıt referansı) | `test_checklist_validation` | Done |
| REQ-QC-02 | Değiştirilemez kontrol kaydı, gerekçeli düzeltme | `test_runs_are_immutable_and_corrections_supersede` | Done |
| REQ-QC-03 | Uygunsuzluk: otomatik açılış, atama, düzeltici faaliyet; şube skoru, kaçırılan günlük kontrol | `test_checklist_run_opens_nonconformity_and_resolves`, `test_missed_daily_checklist_is_reported` | Done |
| REQ-SHF-01 | Çakışmasız vardiya; çalışan yalnızca kendi vardiyasını görür | `test_shifts_overlap_and_visibility` | Done |
| REQ-SHF-02 | Giriş/çıkış, yönetici düzeltmesi (gerekçe + önce/sonra), saat raporu, eşik aşımı | `test_attendance_and_hours` | Done (yasal eşikler doğrulanmadı) |
| REQ-SHF-03 | Görev atama/tamamlama, zamanında tamamlama oranı, değiştirilemez devir notu | `test_tasks_and_handover` | Done |
| REQ-INT-01 | İmzalı POS webhook, tekrar/zaman kontrolü, inbox, idempotent sipariş, eşlenmemiş kod yeniden deneme | `test_pos_webhook.py` (7 test) | Done (gerçek POS markası yok) |
| REQ-UI-01 | Web arayüzü: giriş, özet, sipariş, stok işlemleri, raporlar, kalite, görevler | `tests/ui/test_browser.py` (9 test) | Partial: transfer/satın alma/vardiya/entegrasyon ekranları yok (API var) |

### Hâlâ başlanmamış
REQ-RCP-03 alt reçete · REQ-LOT-* lot/SKT/FEFO · varyant/modifier · kısmi iade · KDV · işçilik maliyetinin
ürün maliyetine dağıtımı · REQ-OBS-* metrik/trace · bildirim (e-posta/push) · rapor dışa aktarma (CSV/PDF) ·
fotoğraf/dosya yükleme · PostgreSQL RLS.
