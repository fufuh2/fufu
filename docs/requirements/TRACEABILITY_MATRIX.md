# İzlenebilirlik Matrisi — Faz 1

| Gereksinim | Kod | Test(ler) | Durum |
|---|---|---|---|
| REQ-STK-01 | `models.StockMovement`, migration trigger, `services/inventory.apply_movement` | `test_ledgers_are_append_only`, `test_full_operational_flow` | Done |
| REQ-STK-02 | `inventory.lock_balance` (`FOR UPDATE`), sıralı kilit | `test_concurrent_waste_cannot_overdraw`, `test_concurrent_orders_consume_exactly` | Done |
| REQ-STK-03 | `domain/units.py`, `inventory.convert` | `TestUnits` (4), `test_invalid_waste_inputs_rejected`, `test_duplicate_skus_and_units` | Done |
| REQ-STK-04 | `inventory.receive`, `domain/costing.moving_average_cost` | `test_full_operational_flow`, `test_moving_average*` | Done |
| REQ-STK-05 | `inventory.record_waste`, `WASTE_REASONS` | `test_invalid_waste_inputs_rejected` (11 vaka), `test_sale_may_go_negative_but_waste_may_not` | Done |
| REQ-STK-06 | `inventory.post_count` | `test_full_operational_flow`, `test_count_rejects_duplicates_and_negative` | Done |
| REQ-STK-07 | `reports.stock_on_hand` | `test_low_stock_flag` | Partial |
| REQ-RCP-01 | `catalog.create_recipe_version`, `catalog.recipe_at` | `test_recipe_versions_do_not_rewrite_history` | Done |
| REQ-RCP-02 | `domain/costing.gross_quantity` | `test_preparation_loss_increases_gross_issue`, `test_gross_quantity_with_loss` | Done |
| REQ-ORD-01 | `orders.create_order`, `uq_orders_idempotency` | `test_idempotent_replay_and_conflict`, `test_concurrent_same_idempotency_key_creates_one_order` | Done |
| REQ-ORD-02 | `orders.create_order` | `test_full_operational_flow`, `test_missing_recipe_means_unknown_cost_not_zero` | Done |
| REQ-ORD-03 | `orders.cancel_order` | `test_cancel_reverses_stock_at_original_cost` | Done |
| REQ-RPT-01 | `reports.variance` | `test_full_operational_flow`, `test_variance_without_count_is_unknown` | Done |
| REQ-RPT-02 | `reports.margin`, `domain/costing.gross_margin` | `test_full_operational_flow`, `test_margin*`, `test_fully_discounted_sale_margin_undefined` | Done |
| REQ-RPT-03 | `reports.*` (`generated_at`, `source`) | `test_full_operational_flow` | Done |
| REQ-SEC-01 | `security.py`, `api/routes.login` | `test_requires_authentication`, `test_expired_and_forged_tokens_rejected`, `test_login_does_not_reveal_which_part_was_wrong` | Partial |
| REQ-SEC-02 | `services/scope.py`, `admin.load_actor`, `domain/permissions.py` | `test_tenant_isolation`, `test_branch_scope_for_branch_users`, `test_role_permissions_enforced`, `test_deactivated_user_token_stops_working`, `TestPermissions` | Done |
| REQ-SEC-03 | `main.py` exception handler + middleware | `test_errors_do_not_leak_internals` | Done |
| REQ-AUD-01 | `services/audit.py`, `admin.list_audit` | `test_full_operational_flow`, `test_ledgers_are_append_only`, `test_audit_log_is_branch_scoped` | Done |

Faz 1 tablosu. Faz 2 gereksinimleri ve testleri: `REQUIREMENTS.md` "Faz 2 gereksinimleri" tablosu.
Güncel toplam: 126 test vakası (108 fonksiyon), son koşu 126 passed (bkz. `docs/audit/TEST_EVIDENCE.md` K).
