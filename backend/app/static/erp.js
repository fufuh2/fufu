"use strict";
// İşletme modülleri: satın alma, satışlar, giderler, personel ve kâr-zarar. app.js'teki
// yardımcıları kullanır; veriler yalnızca textContent ile basılır.

function todayIso() { return new Date().toISOString().slice(0, 10); }
function dayLabel(d) { return new Date(`${d}T00:00:00`).toLocaleDateString("tr-TR", { day: "2-digit", month: "short", weekday: "short" }); }
function periodOf(d) { return d.slice(0, 7); }
function del(path, after) {
  return el("button", { type: "button", class: "danger", onclick: async () => {
    if (!confirm("Bu kayıt silinsin mi?")) return;
    const r = await fetch(`/api/v1${path}`, { method: "DELETE", headers: { Authorization: `Bearer ${state.token}` } });
    if (r.status === 401) { logout("Oturumunuz sona erdi. Lütfen tekrar giriş yapın."); return; }
    if (!r.ok) { let d = null; try { d = await r.json(); } catch { /* boş */ } message(describe(r.status, d)); return; }
    message("Silindi.", "info"); after();
  } }, "Sil");
}
function monthHead(title, sub, render) {
  const period = getPeriod();
  return pageHead(`${title} — ${periodLabel(period)}`, `${branchName()} · ${sub}`, monthInput(period, (p) => { setPeriod(p); render(); }));
}

// ---- satın alma -----------------------------------------------------------------------------
async function renderPurchases() {
  if (!(await ensureBranch())) return;
  const period = getPeriod();
  const [rec, items, sups] = await Promise.all([
    api("GET", `/branches/${state.branch}/receipts?period=${period}`),
    api("GET", "/count-items"),
    api("GET", "/suppliers"),
  ]);
  if (!rec.ok) { message(rec.error); return; }
  const manage = can("purchase.manage");
  const parts = [monthHead("Satın Alma", "Mal kabul (irsaliye / fatura). Kaydedilen miktarlar o ayın sayımındaki 'Gelen' sütununa otomatik eklenir.", renderPurchases)];
  parts.push(el("div", { class: "grid" },
    kpi("Bu ay alım", showMoney(rec.data.total), `${rec.data.receipts.length} belge`),
    ...rec.data.by_supplier.slice(0, 4).map((b) => kpi(b.supplier, showMoney(b.total), "tedarikçi toplamı"))));

  if (manage && items.ok) {
    const byGroup = new Map();
    for (const i of items.data) { if (!byGroup.has(i.group_name)) byGroup.set(i.group_name, []); byGroup.get(i.group_name).push(i); }
    const itemById = new Map(items.data.map((i) => [i.id, i]));
    const day = el("input", { type: "date", value: period === currentPeriod() ? todayIso() : `${period}-01`, required: true, "aria-label": "Belge tarihi" });
    const sup = el("select", { "aria-label": "Tedarikçi" }, el("option", { value: "" }, "— tedarikçi seçin —"),
      (sups.ok ? sups.data.filter((x) => x.is_active) : []).map((x) => el("option", { value: x.id }, x.name)));
    const doc = el("input", { maxlength: 60, placeholder: "İrsaliye / fatura no", "aria-label": "Belge no" });
    const upd = el("input", { type: "checkbox", checked: true, "aria-label": "Fiyatları güncelle" });
    const body = el("tbody");
    const lines = [];
    const total = el("strong", {}, showMoney(0));
    const recalc = () => {
      let t = 0;
      for (const l of lines) { const q = parseDecimal(l.qty.value); const p = parseDecimal(l.price.value, 2, 9); if (q && p) t += Number(q) * Number(p); }
      total.textContent = showMoney(t);
    };
    const addLine = () => {
      const n = lines.length + 1;
      const item = el("select", { "aria-label": `Ürün ${n}` }, el("option", { value: "" }, "Ürün seçin…"),
        [...byGroup].map(([g, list]) => el("optgroup", { label: g }, list.map((i) => el("option", { value: i.id }, `${i.name} (${i.unit})`)))));
      const qty = el("input", { inputmode: "decimal", class: "qty", "aria-label": `Miktar ${n}` });
      const price = el("input", { inputmode: "decimal", class: "qty", "aria-label": `Birim fiyat ${n}` });
      const unit = el("span", { class: "hint" });
      item.addEventListener("change", () => {
        const it = itemById.get(item.value);
        unit.textContent = it ? it.unit : "";
        if (it && !price.value && it.unit_price !== null) price.value = numForInput(it.unit_price);
        recalc();
      });
      qty.addEventListener("input", recalc); price.addEventListener("input", recalc);
      const rec_ = { item, qty, price };
      lines.push(rec_);
      body.append(el("tr", {}, el("td", {}, item), el("td", { class: "num" }, qty, " ", unit), el("td", { class: "num" }, price)));
    };
    for (let i = 0; i < 3; i++) addLine();
    const save = el("button", { type: "button", onclick: async () => {
      const out = [];
      for (const l of lines) {
        if (!l.item.value && !l.qty.value.trim()) continue;
        const q = parseDecimal(l.qty.value); const p = parseDecimal(l.price.value, 2, 9);
        if (!l.item.value || !q || Number(q) <= 0 || p === undefined) { message("Her satırda ürün ve 0'dan büyük miktar olmalı; fiyat sayı olmalı."); return; }
        out.push({ item_id: l.item.value, quantity: q, unit_price: p });
      }
      if (!out.length) { message("En az bir ürün satırı girin."); return; }
      save.disabled = true;
      const r = await api("POST", `/branches/${state.branch}/receipts`, { date: day.value, supplier_id: sup.value || null, document_no: doc.value, lines: out, update_prices: upd.checked });
      save.disabled = false;
      if (!r.ok) { message(r.error); return; }
      setPeriod(periodOf(day.value));
      await renderPurchases();
      message(`Mal kabul kaydedildi (${out.length} ürün). Sayımdaki 'Gelen' güncellendi.`, "info");
    } }, "Mal kabulü kaydet");
    parts.push(el("section", { class: "card" }, el("h2", {}, "Yeni mal kabul"),
      el("div", { class: "toolbar" }, el("label", {}, "Tarih", day), el("label", { class: "grow" }, "Tedarikçi", sup), el("label", {}, "Belge no", doc)),
      el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["Ürün", "Miktar (sayım birimi)", "Birim fiyat ₺"].map((h, i) => el("th", { scope: "col", class: i ? "num" : "" }, h)))), body)),
      el("div", { class: "toolbar" }, el("button", { type: "button", class: "secondary", onclick: addLine }, "Satır ekle"),
        el("label", { class: "check" }, upd, "Ürün fiyatlarını bu alış fiyatıyla güncelle"), el("span", { class: "grow" }), el("span", {}, "Toplam: ", total), save)));
  }

  parts.push(el("section", { class: "card" }, el("h2", {}, "Bu ayın belgeleri"),
    rec.data.receipts.length ? el("div", {}, rec.data.receipts.map((r) => el("details", { class: "receipt" },
      el("summary", {}, el("strong", {}, dayLabel(r.date)), ` · ${r.supplier || "tedarikçisiz"}${r.document_no ? ` · ${r.document_no}` : ""} · ${r.lines.length} ürün · `, el("strong", {}, showMoney(r.total))),
      table([["Ürün"], ["Miktar", "num"], ["Birim fiyat", "num"], ["Tutar", "num"]], r.lines.map((l) => [[l.item_name], [`${showQty(l.quantity)} ${l.unit}`, "num"], [showMoney(l.unit_price), "num"], [showMoney(l.total), "num"]])),
      manage ? el("p", {}, del(`/receipts/${r.id}`, renderPurchases), el("span", { class: "hint" }, " Silinirse miktarlar 'Gelen'den düşülür.")) : null))) : el("p", { class: "empty" }, "Bu ay mal kabul yok.")));

  if (manage) {
    const name = el("input", { required: true, maxlength: 200, placeholder: "Tedarikçi adı", "aria-label": "Yeni tedarikçi" });
    const phone = el("input", { maxlength: 50, placeholder: "Telefon", "aria-label": "Telefon" });
    parts.push(el("details", { class: "card" }, el("summary", {}, el("strong", {}, `Tedarikçiler (${sups.ok ? sups.data.length : 0})`)),
      el("form", { class: "row", onsubmit: async (ev) => {
        ev.preventDefault();
        const r = await api("POST", "/suppliers", { name: name.value.trim(), phone: phone.value.trim() || null });
        if (!r.ok) { message(r.error); return; }
        await renderPurchases(); message(`${r.data.name} eklendi.`, "info");
      } }, el("label", { class: "grow" }, "Ad", name), el("label", {}, "Telefon", phone), el("button", { type: "submit" }, "Tedarikçi ekle")),
      sups.ok ? table([["Ad"], ["Telefon"], ["Aktif"]], sups.data.map((x) => {
        const act = el("input", { type: "checkbox", checked: x.is_active, "aria-label": `${x.name} aktif` });
        act.addEventListener("change", async () => { const r = await api("PATCH", `/suppliers/${x.id}`, { is_active: act.checked }); if (!r.ok) { message(r.error); act.checked = !act.checked; } });
        return [[x.name], [x.phone || "—"], [act]];
      }), "Tedarikçi yok.") : null));
  }
  main().replaceChildren(...parts);
}

// ---- satışlar -------------------------------------------------------------------------------
async function renderSales() {
  if (!(await ensureBranch())) return;
  const saved = load("sales_day");
  const day = saved && saved.startsWith(getPeriod()) ? saved : (getPeriod() === currentPeriod() ? todayIso() : `${getPeriod()}-01`);
  const [d, rep] = await Promise.all([
    api("GET", `/branches/${state.branch}/sales/day?date=${day}`),
    api("GET", `/branches/${state.branch}/sales?period=${periodOf(day)}`),
  ]);
  if (!d.ok) { message(d.error); return; }
  const edit = can("sales.manage");
  const dayInput = el("input", { type: "date", value: day, "aria-label": "Satış günü" });
  dayInput.addEventListener("change", () => { if (dayInput.value) { save("sales_day", dayInput.value); setPeriod(periodOf(dayInput.value)); renderSales(); } });
  const rows = d.data.products.map((p) => {
    const qty = el("input", { inputmode: "decimal", class: "qty", value: Number(p.quantity) ? numForInput(p.quantity) : "", disabled: !edit, "aria-label": `${p.name} adet` });
    const price = el("input", { inputmode: "decimal", class: "qty", value: p.unit_price === null ? "" : numForInput(p.unit_price), disabled: !edit, "aria-label": `${p.name} fiyat` });
    const sum = el("td", { class: "num" });
    const upd = () => { const q = parseDecimal(qty.value) || 0; const pr = parseDecimal(price.value, 2, 9) || 0; sum.textContent = showMoney(Number(q) * Number(pr)); refresh(); };
    qty.addEventListener("input", upd); price.addEventListener("input", upd);
    qty.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); const all = rows.map((r) => r.qty); const n = all[all.indexOf(qty) + 1]; if (n) { n.focus(); n.select(); } } });
    return { p, qty, price, sum, tr: el("tr", {}, el("td", {}, p.name), el("td", { class: "num" }, qty), el("td", { class: "num" }, price), sum) };
  });
  const dayTotal = el("strong");
  function refresh() {
    let t = 0, n = 0;
    for (const r of rows) { const q = Number(parseDecimal(r.qty.value) || 0); n += q; t += q * Number(parseDecimal(r.price.value, 2, 9) || 0); }
    dayTotal.textContent = `${showQty(n)} adet · ${showMoney(t)}`;
  }
  rows.forEach((r) => { r.sum.textContent = showMoney(Number(r.p.quantity) * Number(r.p.unit_price || 0)); });
  refresh();
  const saveBtn = el("button", { type: "button", onclick: async () => {
    const entries = [];
    for (const r of rows) {
      const q = parseDecimal(r.qty.value); const pr = parseDecimal(r.price.value, 2, 9);
      if (q === undefined || pr === undefined) { message(`${r.p.name}: geçersiz sayı.`); return; }
      entries.push({ product_id: r.p.product_id, quantity: q || "0", unit_price: pr });
    }
    saveBtn.disabled = true;
    const res = await api("PUT", `/branches/${state.branch}/sales/day?date=${dayInput.value}`, { entries });
    saveBtn.disabled = false;
    if (!res.ok) { message(res.error); return; }
    await renderSales();
    message(`${dayLabel(dayInput.value)} satışları kaydedildi; ayın menü satışları ve sayımdaki 'Satıştan' güncellendi.`, "info");
  } }, "Günü kaydet");
  const r = rep.ok ? rep.data : null;
  main().replaceChildren(
    pageHead("Satışlar", `${branchName()} · Günlük menü satışları. Ayın toplamı sayımdaki 'Menü satışları'na otomatik yazılır; ciro Kâr-Zarar'a gider.`, el("label", {}, "Gün", dayInput)),
    r ? el("div", { class: "grid" }, kpi(`${periodLabel(r.period)} ciro`, showMoney(r.revenue), `${showQty(r.quantity)} adet · ${r.days.length} gün girildi`),
      kpi("Günlük ortalama", showMoney(r.days.length ? Number(r.revenue) / r.days.length : 0), "girilen günler"),
      r.products[0] ? kpi("En çok ciro", r.products[0].name, showMoney(r.products[0].revenue)) : null) : null,
    el("section", { class: "card" }, el("h2", {}, `${dayLabel(dayInput.value)} satışları`),
      d.data.products.length ? el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["Ürün", "Adet", "Satış fiyatı ₺", "Tutar"].map((h, i) => el("th", { scope: "col", class: i ? "num" : "" }, h)))), el("tbody", {}, rows.map((x) => x.tr))))
        : el("p", { class: "empty" }, "Menü ürünü yok. Menü & Reçeteler sayfasından ekleyin."),
      el("div", { class: "toolbar" }, el("span", { class: "grow" }, "Gün toplamı: ", dayTotal), edit ? saveBtn : null)),
    r && r.days.length ? el("div", { class: "cols" },
      el("section", { class: "card" }, el("h2", {}, "Günler"), table([["Gün"], ["Adet", "num"], ["Ciro", "num"]], r.days.map((x) => {
        const tr = el("tr", { class: "clickable" }, el("td", {}, dayLabel(x.date)), el("td", { class: "num" }, showQty(x.quantity)), el("td", { class: "num" }, showMoney(x.revenue)));
        tr.addEventListener("click", () => { save("sales_day", String(x.date)); renderSales(); });
        return tr;
      }))),
      el("section", { class: "card" }, el("h2", {}, "Ürünler"), table([["Ürün"], ["Adet", "num"], ["Ciro", "num"]], r.products.map((x) => [[x.name], [showQty(x.quantity), "num"], [showMoney(x.revenue), "num"]])))) : null,
  );
}

// ---- giderler -------------------------------------------------------------------------------
async function renderExpenses() {
  if (!(await ensureBranch())) return;
  const period = getPeriod();
  const r = await api("GET", `/branches/${state.branch}/expenses?period=${period}`);
  if (!r.ok) { message(r.error); return; }
  const parts = [monthHead("Giderler", "Kira, fatura ve diğer işletme giderleri. Personel maaşları Personel sayfasından otomatik gelir.", renderExpenses),
    el("div", { class: "grid" }, kpi("Bu ay gider", showMoney(r.data.total), `${r.data.expenses.length} kayıt`),
      ...r.data.by_category.slice(0, 5).map((c) => kpi(c.label, showMoney(c.total))))];
  if (can("expense.manage")) {
    const day = el("input", { type: "date", value: period === currentPeriod() ? todayIso() : `${period}-01`, required: true, "aria-label": "Gider tarihi" });
    const cat = el("select", { "aria-label": "Gider türü" }, r.data.categories.map((c) => el("option", { value: c.value }, c.label)));
    const amount = el("input", { inputmode: "decimal", required: true, "aria-label": "Tutar" });
    const note = el("input", { maxlength: 300, placeholder: "İsteğe bağlı", "aria-label": "Açıklama" });
    parts.push(el("form", { class: "card row", onsubmit: async (ev) => {
      ev.preventDefault();
      const a = parseDecimal(amount.value, 2, 9);
      if (!a || Number(a) <= 0) { amount.classList.add("invalid"); return; }
      const res = await api("POST", `/branches/${state.branch}/expenses`, { date: day.value, category: cat.value, amount: a, note: note.value || null });
      if (!res.ok) { message(res.error); return; }
      setPeriod(periodOf(day.value)); await renderExpenses(); message("Gider eklendi.", "info");
    } }, el("label", {}, "Tarih", day), el("label", {}, "Tür", cat), el("label", {}, "Tutar ₺", amount), el("label", { class: "grow" }, "Açıklama", note), el("button", { type: "submit" }, "Gider ekle")));
  }
  parts.push(el("section", { class: "card" }, table([["Tarih"], ["Tür"], ["Açıklama"], ["Tutar", "num"], [""]],
    r.data.expenses.map((e) => [[dayLabel(e.date)], [e.category_label], [e.note || "—", "wrap"], [showMoney(e.amount), "num strong"], [can("expense.manage") ? del(`/expenses/${e.id}`, renderExpenses) : ""]]), "Bu ay gider yok.")));
  main().replaceChildren(...parts);
}

// ---- personel -------------------------------------------------------------------------------
async function renderStaff() {
  if (!(await ensureBranch())) return;
  const period = getPeriod();
  const r = await api("GET", `/branches/${state.branch}/staff?period=${period}`);
  if (!r.ok) { message(r.error); return; }
  const manage = can("staff.manage");
  const st = r.data;
  const parts = [monthHead("Personel", "Personel, vardiyalar ve aylık işçilik maliyeti (saatlik: saat × ücret, aylık: aylık maliyet).", renderStaff),
    el("div", { class: "grid" }, kpi("İşçilik maliyeti", showMoney(st.labor_cost), "bu ay"), kpi("Çalışılan saat", showQty(st.hours), `${st.shifts.length} vardiya`),
      kpi("Aktif personel", String(st.employees.filter((e) => e.is_active).length)))];
  if (manage) {
    const name = el("input", { required: true, maxlength: 200, "aria-label": "Personel adı" });
    const title = el("input", { maxlength: 100, placeholder: "Usta, kasiyer…", "aria-label": "Görev" });
    const pay = el("select", { "aria-label": "Ücret türü" }, el("option", { value: "monthly" }, "Aylık"), el("option", { value: "hourly" }, "Saatlik"));
    const wage = el("input", { inputmode: "decimal", required: true, "aria-label": "Ücret" });
    parts.push(el("details", { class: "card", open: !st.employees.length }, el("summary", {}, el("strong", {}, "Personel ekle")),
      el("form", { class: "row", onsubmit: async (ev) => {
        ev.preventDefault();
        const w = parseDecimal(wage.value, 2, 9);
        if (w === undefined || w === null) { wage.classList.add("invalid"); return; }
        const res = await api("POST", `/branches/${state.branch}/employees`, { name: name.value.trim(), title: title.value.trim() || null, pay_type: pay.value, wage: w });
        if (!res.ok) { message(res.error); return; }
        await renderStaff(); message(`${name.value.trim()} eklendi.`, "info");
      } }, el("label", { class: "grow" }, "Ad soyad", name), el("label", {}, "Görev", title), el("label", {}, "Ücret türü", pay),
        el("label", {}, "Ücret ₺ (aylık maliyet ya da saatlik)", wage), el("button", { type: "submit" }, "Ekle"))));
    const active = st.employees.filter((e) => e.is_active);
    if (active.length) {
      const emp = el("select", { "aria-label": "Vardiya personeli" }, active.map((e) => el("option", { value: e.id }, e.name)));
      const day = el("input", { type: "date", value: period === currentPeriod() ? todayIso() : `${period}-01`, "aria-label": "Vardiya günü" });
      const start = el("input", { type: "time", "aria-label": "Başlangıç" });
      const end = el("input", { type: "time", "aria-label": "Bitiş" });
      const hours = el("input", { inputmode: "decimal", placeholder: "boşsa saatlerden", "aria-label": "Saat" });
      parts.push(el("form", { class: "card row", onsubmit: async (ev) => {
        ev.preventDefault();
        const h = parseDecimal(hours.value, 2, 2);
        if (h === undefined) { hours.classList.add("invalid"); return; }
        const res = await api("POST", `/employees/${emp.value}/shifts`, { date: day.value, start: start.value || null, end: end.value || null, hours: h });
        if (!res.ok) { message(res.error); return; }
        setPeriod(periodOf(day.value)); await renderStaff(); message(`Vardiya eklendi (${showQty(res.data.hours)} saat).`, "info");
      } }, el("label", {}, "Personel", emp), el("label", {}, "Gün", day), el("label", {}, "Başlangıç", start), el("label", {}, "Bitiş", end),
        el("label", {}, "Saat", hours), el("button", { type: "submit" }, "Vardiya ekle")));
    }
  }
  parts.push(el("section", { class: "card" }, el("h2", {}, "Personel"), table([["Ad"], ["Görev"], ["Ücret"], ["Saat", "num"], ["Maliyet", "num"], ["Aktif"]],
    st.employees.map((e) => {
      const act = el("input", { type: "checkbox", checked: e.is_active, disabled: !manage, "aria-label": `${e.name} aktif` });
      act.addEventListener("change", async () => { const res = await api("PATCH", `/employees/${e.id}`, { is_active: act.checked }); if (!res.ok) { message(res.error); act.checked = !act.checked; } else renderStaff(); });
      return [[e.name], [e.title || "—"], [`${showMoney(e.wage)} / ${e.pay_type === "hourly" ? "saat" : "ay"}`], [showQty(e.hours), "num"], [showMoney(e.cost), "num strong"], [act]];
    }), "Personel yok.")));
  parts.push(el("section", { class: "card" }, el("h2", {}, "Vardiyalar"), table([["Gün"], ["Personel"], ["Saat aralığı"], ["Saat", "num"], [""]],
    [...st.shifts].reverse().map((s) => [[dayLabel(s.date)], [s.employee || "?"], [s.start ? `${s.start}–${s.end || "?"}` : "—"], [showQty(s.hours), "num"], [manage ? del(`/shifts/${s.id}`, renderStaff) : ""]]), "Bu ay vardiya yok.")));
  main().replaceChildren(...parts);
}

// ---- kâr-zarar ------------------------------------------------------------------------------
async function renderProfitLoss() {
  if (!(await ensureBranch())) return;
  const period = getPeriod();
  const r = await api("GET", `/branches/${state.branch}/profit-loss?period=${period}`);
  if (!r.ok) { message(r.error); return; }
  const d = r.data;
  const pct = (v) => (v === null || v === undefined ? "" : ` (%${showQty(v)})`);
  const row = (label, value, cls = "", sub = "") => el("tr", { class: cls }, el("td", {}, label, sub ? el("span", { class: "hint" }, ` · ${sub}`) : ""), el("td", { class: "num" }, showMoney(value)));
  const neg = (v) => -Number(v);
  main().replaceChildren(
    monthHead("Kâr-Zarar", "Satış geliri − malzeme − giderler − işçilik. Kaynaklar: Satışlar, Aylık Sayım, Satın Alma, Giderler, Personel.", renderProfitLoss),
    el("div", { class: "grid" },
      kpi("Ciro", showMoney(d.revenue), d.revenue_basis),
      kpi("Malzeme maliyeti", showMoney(d.material_cost), `${d.food_cost_pct === null ? "—" : `%${showQty(d.food_cost_pct)}`} · ${d.material_basis}`),
      kpi("İşçilik", showMoney(d.labor_cost), d.labor_pct === null ? "" : `%${showQty(d.labor_pct)}`),
      kpi("Net kâr", showMoney(d.net_profit), d.net_pct === null ? "" : `%${showQty(d.net_pct)} net marj`, Number(d.net_profit) < 0 ? "eksik" : "fazla")),
    el("section", { class: "card" }, el("h2", {}, "Gelir tablosu"), el("div", { class: "table-wrap" }, el("table", { class: "pl" }, el("tbody", {},
      row("Satış geliri (ciro)", d.revenue, "strong", d.revenue_basis),
      row("Malzeme maliyeti", neg(d.material_cost), "", d.material_basis + pct(d.food_cost_pct)),
      row("Brüt kâr", d.gross_profit, "strong total"),
      ...d.expenses.map((e) => row(e.label, neg(e.total))),
      row("İşçilik (Personel)", neg(d.labor_cost), "", `${showQty(d.labor_hours)} saat` + pct(d.labor_pct)),
      row("Net kâr", d.net_profit, `strong total ${Number(d.net_profit) < 0 ? "neg" : "pos"}`))))),
    el("div", { class: "cols" },
      el("section", { class: "card" }, el("h2", {}, "Malzeme detayı"), el("div", { class: "table-wrap" }, el("table", {}, el("tbody", {},
        row("Reçeteye göre (teorik) maliyet", d.theoretical_cost),
        d.actual_cost === null ? el("tr", {}, el("td", {}, "Gerçek tüketim"), el("td", { class: "num unknown" }, "ay sayılınca")) : row("Gerçek tüketim (devir + gelen − sayılan)", d.actual_cost),
        row("Bu ay alım (Satın Alma)", d.purchases),
        row("Zayi", d.waste_value),
        row("Sayım eksiği", d.missing_value))))),
      el("section", { class: "card" }, el("h2", {}, "Notlar"), d.notes.length ? el("ul", {}, d.notes.map((n) => el("li", {}, n))) : el("p", { class: "empty" }, "Hesap tam."),
        el("p", { class: "hint" }, "Gerçek tüketim ile teorik maliyet arasındaki fark; porsiyon kaçağı, kayıtsız zayi ve sayım eksiğinden gelir. KDV ayrımı yapılmaz; tutarlar girildiği gibidir."))),
  );
}

routes["#/satin-alma"] = renderPurchases;
routes["#/satislar"] = renderSales;
routes["#/giderler"] = renderExpenses;
routes["#/personel"] = renderStaff;
routes["#/kar-zarar"] = renderProfitLoss;
