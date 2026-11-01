"use strict";
// Aylık sayım formu, geçmiş aylar ve ürün listesi. app.js'teki yardımcıları kullanır.

const FIELDS = [
  ["opening", "Devir"], ["incoming", "Gelen"], ["used", "Satılan / Kullanılan (elle)"], ["waste", "Zayi"], ["counted", "Sayılan"],
];

// Ekranda anlık hesap: binde birlik tamsayılarla (kayan nokta hatası olmadan).
function milli(v) { return v === null || v === undefined || v === "" ? null : Math.round(Number(v) * 1000); }
function fromMilli(m) { return m === null ? null : m / 1000; }
function computeLine(c) {
  const e = milli(c.opening) + milli(c.incoming) - milli(c.recipe_used) - milli(c.used) - milli(c.waste);
  const k = milli(c.counted);
  const d = k === null ? null : k - e;
  const st = d === null ? "sayilmadi" : d > 0 ? "fazla" : d < 0 ? "eksik" : "tamam";
  const price = c.unit_price === null || c.unit_price === undefined ? null : Number(c.unit_price);
  const val = d === null || price === null ? null : roundHalfEven((d / 1000) * price * 100) / 100;
  const wv = price === null ? null : roundHalfEven((milli(c.waste) / 1000) * price * 100) / 100;
  return { expected: fromMilli(e), difference: fromMilli(d), status: st, value: val, wasteValue: wv };
}

async function renderCount() {
  if (!(await ensureBranch())) return;
  const period = getPeriod();
  const res = await api("GET", `/branches/${state.branch}/monthly-counts/${period}`);
  if (!res.ok) {
    main().replaceChildren(pageHead(`Aylık Sayım — ${periodLabel(period)}`, null,
      monthInput(period, (p) => { setPeriod(p); renderCount(); })),
      el("div", { class: "card" }, el("p", {}, res.status === 403 ? "Bu ay için sayım henüz başlatılmadı; başlatma yetkiniz yok." : res.error)));
    return;
  }
  drawSheet(res.data);
}

function drawSheet(sheet) {
  const closed = sheet.status === "closed";
  const editable = !closed && can("count.edit");
  const dirty = { lines: new Map(), header: {}, sales: new Map() };
  const hasUnsaved = () => dirty.lines.size > 0 || Object.keys(dirty.header).length > 0 || dirty.sales.size > 0;
  state.guard = hasUnsaved;

  const summary = el("div", { class: "grid" });
  const search = el("input", { type: "search", placeholder: "Ürün ara…", "aria-label": "Ürün ara" });
  const filter = el("select", { "aria-label": "Filtre" },
    [["all", "Tüm ürünler"], ["diff", "Farkı olanlar"], ["eksik", "Eksikler"], ["fazla", "Fazlalar"], ["zayi", "Zayi olanlar"], ["sayilmadi", "Sayılmayanlar"]]
      .map(([v, t]) => el("option", { value: v }, t)));
  const saveBtn = el("button", { type: "button", disabled: true }, "Kaydet");
  const rows = [];
  const groupRows = [];
  const recipes = sheet.recipes; // taslakta: item_id -> [{product_id, per_unit}]; kapalı ayda null
  const sales = new Map(sheet.sales.map((x) => [x.product_id, String(x.quantity)]));
  const productName = new Map(sheet.sales.map((x) => [x.product_id, x.name]));
  const recipeUsed = (itemId) => {
    if (!recipes) return null;
    const uses = recipes[itemId] || [];
    return fromMilli(roundHalfEven(uses.reduce((acc, u) => acc + Number(sales.get(u.product_id) || 0) * Number(u.per_unit), 0) * 1000));
  };
  const recipeTitle = (itemId, unit) => {
    const uses = (recipes || {})[itemId] || [];
    if (!uses.length) return "Bu malzeme hiçbir reçetede yok";
    return uses.map((u) => `${productName.get(u.product_id) || "?"}: ${showQty(sales.get(u.product_id) || 0)} × ${showQty(Number(u.per_unit))} ${unit}`).join("\n");
  };

  const who = el("input", { name: "counted_by_name", value: sheet.counted_by_name || "", maxlength: 200, disabled: !editable });
  const when = el("input", { type: "date", name: "count_date", value: sheet.count_date || "", disabled: !editable });
  const note = el("input", { name: "note", value: sheet.note || "", maxlength: 1000, disabled: !editable, placeholder: "İsteğe bağlı" });
  for (const [input, key] of [[who, "counted_by_name"], [when, "count_date"], [note, "note"]]) {
    input.addEventListener("input", () => { dirty.header[key] = input.value || null; markDirty(); });
  }
  function markDirty() {
    const n = dirty.lines.size;
    saveBtn.disabled = !hasUnsaved();
    saveBtn.textContent = n ? `Kaydet (${n} ürün)` : "Kaydet";
  }

  function lineRow(line) {
    const cells = {};
    let row = null;
    const mk = (field) => {
      const locked = field === "opening" && line.opening_locked;
      const input = el("input", { inputmode: "decimal", autocomplete: "off", class: `qty${field === "counted" && line.counted !== null ? " counted" : ""}`,
        value: numForInput(line[field]), disabled: !editable || locked, "aria-label": `${line.name} ${field}` });
      input.addEventListener("input", () => onEdit(row, field, input));
      input.addEventListener("keydown", (ev) => onKey(ev, row, field));
      cells[field] = input;
      return el("td", { class: "num" }, input);
    };
    const expected = el("td", { class: "num" });
    const fromSales = el("td", { class: "num recipe", title: recipeTitle(line.item_id, line.unit) });
    const diff = el("td", { class: "num strong" });
    const status = el("td");
    const value = el("td", { class: "num" });
    const tr = el("tr", {},
      el("td", { class: "name" }, line.name, line.opening_locked ? el("span", { class: "lock", title: "Devir önceki kapanmış aydan geliyor" }, " 🔒") : ""),
      el("td", {}, line.unit),
      mk("opening"), mk("incoming"), fromSales, mk("used"), mk("waste"), expected, mk("counted"), diff, status, value);
    row = { line, tr, cells, expected, fromSales, diff, status, value, current: { ...line } };
    rows.push(row);
    paint(row);
    return tr;
  }

  function paint(row) {
    const live = recipeUsed(row.line.item_id);
    if (live !== null) row.current.recipe_used = live;
    row.fromSales.textContent = Number(row.current.recipe_used) ? showQty(row.current.recipe_used) : "—";
    row.fromSales.title = recipes ? recipeTitle(row.line.item_id, row.line.unit) : "Kapatılan ayın değeri";
    const r = computeLine(row.current);
    row.expected.textContent = showQty(r.expected);
    row.diff.textContent = signedQty(r.difference);
    row.diff.className = `num strong ${r.status}`;
    row.status.replaceChildren(el("span", { class: `badge st-${r.status}` }, STATUS_LABEL[r.status]));
    row.value.textContent = showMoney(r.value);
    row.result = r;
  }

  function setValue(row, field, parsed) {
    row.current[field] = parsed;
    const pending = dirty.lines.get(row.line.item_id) || { item_id: row.line.item_id };
    if (field === "counted" && parsed === null) { pending.clear_counted = true; delete pending.counted; }
    else { pending[field] = parsed; if (field === "counted") delete pending.clear_counted; }
    dirty.lines.set(row.line.item_id, pending);
    row.cells.counted.classList.toggle("counted", row.current.counted !== null);
    paint(row);
  }

  function onEdit(row, field, input) {
    const parsed = parseDecimal(input.value);
    const bad = parsed === undefined || (parsed === null && field !== "counted");
    input.classList.toggle("invalid", bad);
    if (bad) return;
    setValue(row, field, parsed);
    markDirty(); refreshSummary(); applyFilter();
  }

  // Enter / ok tuşları: aynı sütunda alttaki (ya da üstteki) görünür ürüne geç.
  function onKey(ev, row, field) {
    let step = 0;
    if (ev.key === "Enter") step = ev.shiftKey ? -1 : 1;
    else if (ev.key === "ArrowDown") step = 1;
    else if (ev.key === "ArrowUp") step = -1;
    if (!step) return;
    ev.preventDefault();
    let i = rows.indexOf(row) + step;
    while (i >= 0 && i < rows.length && (rows[i].tr.hidden || rows[i].cells[field].disabled)) i += step;
    if (i >= 0 && i < rows.length) { rows[i].cells[field].focus(); rows[i].cells[field].select(); }
  }

  function refreshSummary() {
    let counted = 0, fazla = 0, eksik = 0, fv = 0, ev = 0, zayi = 0, zv = 0;
    for (const r of rows) {
      if (milli(r.current.waste) > 0) { zayi += 1; zv += r.result.wasteValue || 0; }
      if (r.result.status !== "sayilmadi") counted += 1;
      if (r.result.status === "fazla") { fazla += 1; fv += r.result.value || 0; }
      if (r.result.status === "eksik") { eksik += 1; ev += r.result.value || 0; }
    }
    const progress = el("progress", { max: rows.length || 1, value: counted, "aria-label": "Sayım ilerlemesi" });
    summary.replaceChildren(
      el("div", { class: "card kpi" }, el("div", { class: "l" }, "Sayılan ürün"), el("div", { class: "v" }, `${counted} / ${rows.length}`), progress),
      kpi("Eksik", money.format(ev), `${eksik} ürün`, "eksik"),
      kpi("Fazla", money.format(fv), `${fazla} ürün`, "fazla"),
      kpi("Zayi", money.format(zv), `${zayi} ürün`, "zayi"),
      kpi("Net fark", money.format(fv + ev), sheet.value_coverage));
    for (const g of groupRows) {
      const done = g.rows.filter((r) => r.result.status !== "sayilmadi").length;
      const val = g.rows.reduce((s, r) => s + (r.result.value || 0), 0);
      g.meta.textContent = `${done}/${g.rows.length} sayıldı · fark ${money.format(val)}`;
    }
  }

  function applyFilter() {
    const q = search.value.trim().toLocaleLowerCase("tr-TR");
    const f = filter.value;
    for (const r of rows) {
      const st = r.result.status;
      const match = f === "all" || (f === "diff" ? st === "fazla" || st === "eksik" : f === "zayi" ? milli(r.current.waste) > 0 : st === f);
      r.tr.hidden = !((!q || r.line.name.toLocaleLowerCase("tr-TR").includes(q)) && match);
    }
    for (const g of groupRows) g.tr.hidden = g.rows.every((r) => r.tr.hidden);
  }
  search.addEventListener("input", applyFilter);
  filter.addEventListener("change", applyFilter);

  async function doSave() {
    const body = { ...dirty.header, lines: [...dirty.lines.values()],
      sales: [...dirty.sales].map(([product_id, quantity]) => ({ product_id, quantity })) };
    saveBtn.disabled = true;
    // Kayıt sürerken yazılan değer kaybolmasın: alanlar kayıt bitene kadar kilitlenir.
    const locked = [...main().querySelectorAll("input, select, button")].filter((x) => !x.disabled);
    for (const x of locked) x.disabled = true;
    let r;
    try {
      r = await api("PUT", `/branches/${state.branch}/monthly-counts/${sheet.period}`, body);
    } finally {
      if (!r || !r.ok) for (const x of locked) x.disabled = false;
    }
    if (!r.ok) { message(r.error); markDirty(); return false; }
    dirty.lines.clear(); dirty.header = {}; dirty.sales.clear();
    message("Sayım kaydedildi.", "info");
    drawSheet(r.data);
    return true;
  }
  saveBtn.addEventListener("click", doSave);

  const actions = [];
  if (editable) {
    actions.push(saveBtn);
    actions.push(el("button", { type: "button", class: "secondary", title: "Sayılan alanı boş olan ürünlere 0 yazar (elde yok)", onclick: () => {
      const empty = rows.filter((r) => r.current.counted === null);
      if (!empty.length) { message("Sayılmayan ürün yok.", "info"); return; }
      if (!confirm(`${empty.length} ürünün sayılan miktarı boş. Hepsine 0 yazılsın mı? (Kaydet'e basana kadar kaydedilmez.)`)) return;
      for (const r of empty) { r.cells.counted.value = "0"; setValue(r, "counted", "0"); }
      markDirty(); refreshSummary(); applyFilter();
    } }, "Boşlara 0 yaz"));
  }
  if (!closed && can("count.close")) {
    actions.push(el("button", { type: "button", class: "secondary", onclick: async () => {
      if (!confirm(`${periodLabel(sheet.period)} sayımı kapatılsın mı? Kapatılan ay değiştirilemez; sayılan miktarlar sonraki ayın devri olur.`)) return;
      if (hasUnsaved() && !(await doSave())) return;
      const r = await api("POST", `/branches/${state.branch}/monthly-counts/${sheet.period}/close`);
      if (!r.ok) { message(r.error); return; }
      message("Ay kapatıldı.", "info"); drawSheet(r.data);
    } }, "Ayı kapat"));
  }
  if (closed && can("count.close")) {
    actions.push(el("button", { type: "button", class: "secondary", onclick: async () => {
      const reason = prompt("Yeniden açma nedeni:");
      if (!reason) return;
      const r = await api("POST", `/branches/${state.branch}/monthly-counts/${sheet.period}/reopen`, { reason });
      if (!r.ok) { message(r.error); return; }
      message("Ay yeniden açıldı.", "info"); drawSheet(r.data);
    } }, "Yeniden aç"));
  }
  actions.push(el("button", { type: "button", class: "secondary", onclick: () => download(`/branches/${state.branch}/monthly-counts/${sheet.period}/export.xlsx`, `sayim-${sheet.period}.xlsx`) }, "Excel indir"));
  actions.push(el("button", { type: "button", class: "secondary", onclick: () => window.print() }, "Yazdır"));

  document.onkeydown = (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key === "s" && document.body.contains(saveBtn)) {
      ev.preventDefault(); if (!saveBtn.disabled) doSave();
    }
  };

  const headers = ["Malzeme", "Birim", "Devir", "Gelen", "Satıştan (reçete)", "Diğer kullanım (elle)", "Zayi", "Beklenen", "Sayılan", "Fark", "Durum", "Fark ₺"];
  const head = el("thead", {}, el("tr", {}, headers.map((h, i) => el("th", { scope: "col", class: i >= 2 && i !== 10 ? "num" : "" }, h))));

  // Menü satışları: adet girilince reçetedeki malzemelerin "Satıştan" değeri anında değişir.
  const salesInputs = sheet.sales.map((x) => {
    const input = el("input", { inputmode: "decimal", class: "qty", value: numForInput(x.quantity), disabled: !editable, "aria-label": `${x.name} satılan` });
    input.addEventListener("input", () => {
      const v = parseDecimal(input.value);
      const bad = v === undefined;
      input.classList.toggle("invalid", bad);
      if (bad) return;
      sales.set(x.product_id, v === null ? "0" : v);
      dirty.sales.set(x.product_id, v === null ? "0" : v);
      for (const r of rows) paint(r);
      markDirty(); refreshSummary(); applyFilter();
    });
    input.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter") return;
      ev.preventDefault();
      const all = [...salesBox.querySelectorAll("input")];
      const next = all[all.indexOf(input) + (ev.shiftKey ? -1 : 1)];
      if (next) { next.focus(); next.select(); }
    });
    return el("label", {}, x.name, input);
  });
  const salesBox = el("div", { class: "sales-grid" }, salesInputs);
  const salesCard = el("details", { class: "card sales no-print", open: sheet.sales.length > 0 && editable },
    el("summary", {}, el("strong", {}, "Menü satışları"), el("span", { class: "hint" },
      sheet.sales.length ? ` · ${sheet.sales.length} ürün · bu ay satılan adetler; reçeteye göre malzeme kullanımı hesaplanır` : " · menü ürünü yok — Menü & Reçeteler sayfasından ekleyin")),
    sheet.sales.length ? salesBox : el("p", { class: "empty" }, can("item.manage") ? "Burgerlerinizi ve reçetelerini Menü & Reçeteler sayfasında tanımlayın." : "Menü ürünleri henüz tanımlanmadı."));
  const bodies = sheet.groups.map((g) => {
    const meta = el("span", { class: "gmeta" });
    const gtr = el("tr", { class: "group" }, el("th", { colspan: headers.length, scope: "rowgroup" }, g.name, meta));
    const start = rows.length;
    const trs = g.lines.map((line) => lineRow(line));
    groupRows.push({ tr: gtr, meta, rows: rows.slice(start) });
    return el("tbody", {}, gtr, ...trs);
  });

  const periodChange = (p, input) => {
    if (hasUnsaved() && !confirm("Kaydedilmemiş değişiklikler var. Yine de ay değiştirilsin mi?")) { input.value = sheet.period; return; }
    state.guard = null; setPeriod(p); renderCount();
  };
  main().replaceChildren(
    pageHead(`Aylık Sayım — ${periodLabel(sheet.period)}`,
      `${branchName()} · Devir: ${sheet.opening_from ? `${periodLabel(sheet.opening_from)} sayımından (kilitli)` : "önceki kapanmış ay yok, elle girilir"} · Beklenen = Devir + Gelen − Satıştan (reçete) − Satılan/Kullanılan − Zayi · Fark = Sayılan − Beklenen`,
      statusBadge(sheet.status)),
    el("div", { class: "card toolbar" }, monthInput(sheet.period, periodChange), el("label", {}, "Sayımı yapan", who),
      el("label", {}, "Sayım tarihi", when), el("label", { class: "grow" }, "Not", note)),
    summary,
    sheet.recipe_warnings && sheet.recipe_warnings.length ? el("div", { class: "card warn-box no-print" },
      el("strong", {}, `${sheet.recipe_warnings.length} malzeme reçetede var ama satıştan düşülmüyor: `),
      sheet.recipe_warnings.map((w) => `${w.item_name} (${w.item_unit} ↔ ${w.recipe_units.join("/")})`).join(", "),
      ". Ürün Listesi'nde paket içeriğini girin (örn. 1 KG cheddar = 50 ADET dilim).") : null,
    salesCard,
    el("div", { class: "card toolbar no-print" }, el("label", { class: "grow" }, "Ara", search), el("label", {}, "Göster", filter), ...actions),
    editable ? el("p", { class: "hint no-print" }, "İpucu: Enter ile aynı sütunda alttaki ürüne geçilir, Shift+Enter yukarı. Ctrl+S kaydeder. Ondalık için virgül kullanabilirsiniz (12,5).") : null,
    el("div", { class: "table-wrap count" }, el("table", { class: "count-table" }, head, ...bodies)),
  );
  refreshSummary();
}

// ---- geçmiş aylar --------------------------------------------------------------------------
async function renderHistory() {
  if (!(await ensureBranch())) return;
  const r = await api("GET", `/branches/${state.branch}/monthly-counts`);
  if (!r.ok) { message(r.error); return; }
  const open = (p) => { setPeriod(p); location.hash = "#/sayim"; };
  const signCls = (v) => (Number(v) < 0 ? "neg" : Number(v) > 0 ? "pos" : "");
  const rows = r.data.map((m) => {
    const t = m.totals;
    const excel = el("button", { type: "button", class: "secondary", onclick: (ev) => {
      ev.stopPropagation(); download(`/branches/${state.branch}/monthly-counts/${m.period}/export.xlsx`, `sayim-${m.period}.xlsx`);
    } }, "Excel");
    const tr = el("tr", { class: "clickable", title: "Sayım formunu aç" },
      el("td", {}, el("strong", {}, periodLabel(m.period))), el("td", {}, statusBadge(m.status)), el("td", {}, m.counted_by_name || "—"),
      el("td", { class: "num" }, `${t.counted} / ${t.items}`),
      el("td", { class: "num neg" }, showMoney(t.eksik_value)), el("td", { class: "num pos" }, showMoney(t.fazla_value)),
      el("td", { class: "num" }, showMoney(t.waste_value)),
      el("td", { class: `num strong ${signCls(t.net_difference_value)}` }, showMoney(t.net_difference_value)),
      el("td", { class: "num" }, showMoney(t.stock_value)),
      el("td", {}, el("button", { type: "button", class: "secondary" }, "Aç"), " ", excel));
    tr.addEventListener("click", () => open(m.period));
    return tr;
  });
  main().replaceChildren(
    pageHead("Geçmiş Aylar", `${branchName()} · Her ayın sayım sonucu. Satıra tıklayınca o ayın sayım formu açılır.`),
    el("div", { class: "card" }, table([["Ay"], ["Durum"], ["Sayımı yapan"], ["Sayılan", "num"], ["Eksik ₺", "num"], ["Fazla ₺", "num"], ["Zayi ₺", "num"], ["Net fark ₺", "num"], ["Stok değeri ₺", "num"], [""]],
      rows, "Henüz sayım yok. Aylık Sayım sayfasından ilk sayımı başlatın.")));
}

// ---- ürün listesi --------------------------------------------------------------------------
const UNITS = ["KG", "ADET", "LT", "PKT", "GR", "ML", "KOLİ"];

async function renderItems() {
  const r = await api("GET", "/count-items?include_inactive=true");
  if (!r.ok) { message(r.error); return; }
  const manage = can("item.manage");
  const groups = [...new Set(r.data.map((i) => i.group_name))];
  const search = el("input", { type: "search", placeholder: "Ürün ara…", "aria-label": "Ürün ara" });
  const groupFilter = el("select", { "aria-label": "Grup filtresi" }, el("option", { value: "" }, "Tüm gruplar"), groups.map((g) => el("option", { value: g }, g)));
  const rows = r.data.map((i) => {
    const patch = async (fields, label) => {
      const res = await api("PATCH", `/count-items/${i.id}`, fields);
      if (!res.ok) { message(res.error); return false; }
      Object.assign(i, res.data);
      message(`${i.name} güncellendi (${label}).`, "info");
      return true;
    };
    const name = el("input", { value: i.name, maxlength: 200, disabled: !manage, "aria-label": `${i.name} adı` });
    const group = el("input", { value: i.group_name, list: "groups", maxlength: 100, disabled: !manage, "aria-label": `${i.name} grup` });
    const unit = el("select", { disabled: !manage, "aria-label": `${i.name} birim` }, UNITS.map((u) => el("option", { value: u, selected: u === i.unit }, u)));
    const price = el("input", { inputmode: "decimal", class: "qty", value: i.unit_price === null ? "" : String(i.unit_price).replace(".", ","), disabled: !manage, "aria-label": `${i.name} birim fiyat` });
    const active = el("input", { type: "checkbox", checked: i.is_active, disabled: !manage, "aria-label": `${i.name} aktif` });
    const cQty = el("input", { inputmode: "decimal", class: "qty", value: numForInput4(i.content_qty), disabled: !manage, placeholder: "—", "aria-label": `${i.name} paket içeriği` });
    const cUnit = el("select", { disabled: !manage, "aria-label": `${i.name} paket içeriği birimi` }, el("option", { value: "" }, "—"),
      UNITS.map((u) => el("option", { value: u, selected: u === i.content_unit }, u)));
    const saveContent = async () => {
      const q = parseDecimal(cQty.value, 4, 7);
      cQty.classList.toggle("invalid", q === undefined);
      if (q === undefined) return;
      if ((q === null) !== (cUnit.value === "")) return; // ikisi birlikte girilince kaydedilir
      if (!(await patch({ content_qty: q, content_unit: cUnit.value || null }, "paket içeriği"))) {
        cQty.value = numForInput4(i.content_qty); cUnit.value = i.content_unit || "";
      }
    };
    cQty.addEventListener("change", saveContent);
    cUnit.addEventListener("change", saveContent);
    name.addEventListener("change", async () => {
      const v = name.value.trim().toLocaleUpperCase("tr-TR");
      if (!v || !(await patch({ name: v }, "ad"))) name.value = i.name; else name.value = v;
    });
    group.addEventListener("change", async () => {
      const v = group.value.trim();
      if (!v || !(await patch({ group_name: v }, "grup"))) group.value = i.group_name;
    });
    unit.addEventListener("change", () => patch({ unit: unit.value }, "birim"));
    price.addEventListener("change", () => {
      const v = parseDecimal(price.value, 2, 9);
      price.classList.toggle("invalid", v === undefined);
      if (v !== undefined) patch({ unit_price: v }, "fiyat");
    });
    active.addEventListener("change", () => patch({ is_active: active.checked }, active.checked ? "aktif" : "pasif"));
    const tr = el("tr", {}, el("td", {}, group), el("td", { class: "wrap" }, name), el("td", {}, unit), el("td", { class: "num" }, price),
      el("td", {}, el("span", { class: "pkg" }, `1 ${i.unit} =`, cQty, cUnit)), el("td", {}, active));
    return { i, tr };
  });
  const tbl = table([["Grup"], ["Malzeme"], ["Birim"], ["Birim fiyat ₺", "num"], ["Paket içeriği"], ["Aktif"]], rows.map((x) => x.tr), "Henüz ürün yok.");
  const counter = el("p", { class: "hint" });
  function applyFilter() {
    const q = search.value.trim().toLocaleLowerCase("tr-TR");
    let n = 0;
    for (const { i, tr } of rows) {
      tr.hidden = !((!q || i.name.toLocaleLowerCase("tr-TR").includes(q)) && (!groupFilter.value || i.group_name === groupFilter.value));
      if (!tr.hidden) n += 1;
    }
    counter.textContent = `${n} ürün gösteriliyor · ${r.data.filter((x) => x.is_active).length} aktif · ${r.data.filter((x) => x.unit_price !== null).length} üründe fiyat var`;
  }
  search.addEventListener("input", applyFilter);
  groupFilter.addEventListener("change", applyFilter);

  const children = [pageHead("Ürün Listesi", "Birim fiyat (KDV hariç, ₺) girilen ürünlerde fark ve zayi TL olarak da hesaplanır. Pasif ürünler yeni sayımlarda görünmez; geçmiş sayımlar korunur. Kapatılmış aylar kapandıkları günkü fiyatı kullanır. Paket içeriği: adetle sayılan ürünün içindeki miktar (örn. 1 ADET kova ketçap = 9,5 KG); reçetede gram yazılabilmesi için gerekir.")];
  if (manage) {
    const g = el("input", { name: "group", list: "groups", required: true, maxlength: 100 });
    const n = el("input", { name: "name", required: true, maxlength: 200 });
    const u = el("select", { name: "unit" }, UNITS.map((x) => el("option", { value: x }, x)));
    const p = el("input", { name: "price", inputmode: "decimal", placeholder: "İsteğe bağlı" });
    children.push(el("form", { class: "card row", onsubmit: async (ev) => {
      ev.preventDefault();
      const price = parseDecimal(p.value, 2, 9);
      if (price === undefined) { p.classList.add("invalid"); return; }
      const res = await api("POST", "/count-items", { group_name: g.value.trim(), name: n.value.trim().toLocaleUpperCase("tr-TR"), unit: u.value, unit_price: price });
      if (!res.ok) { message(res.error); return; }
      message("Ürün eklendi.", "info"); renderItems();
    } }, el("datalist", { id: "groups" }, groups.map((x) => el("option", { value: x }))),
      el("label", {}, "Grup", g), el("label", { class: "grow" }, "Malzeme adı", n), el("label", {}, "Birim", u),
      el("label", {}, "Birim fiyat ₺", p), el("button", { type: "submit" }, "Ürün ekle")));
    const CONF = { kesin: ["ok", "KESİN"], cikarim: ["warn", "ÇIKARIM"], tahmin: ["bad", "TAHMİN"] };
    const applySupplier = async (overwrite) => {
      if (overwrite && !confirm("Elle girdiğiniz birim fiyat ve paket içerikleri tedarikçi verisiyle değiştirilsin mi?")) return;
      const res = await api("POST", `/count-items/apply-supplier-data${overwrite ? "?overwrite=true" : ""}`);
      if (!res.ok) { message(res.error); return; }
      const d = res.data;
      await renderItems();
      message(`Tedarikçi verisi: ${d.applied.length} üründe fiyat/paket bilgisi güncellendi, ${d.unchanged.length} ürün zaten güncel ya da elle girilmiş.`, "info");
      const box = document.getElementById("supplier-report");
      if (!box) return;
      const row = (a) => [[a.item], [a.supplier, "wrap"], [Object.values(a.changes).join(" · ") || "—", "wrap"],
        [el("span", { class: `badge ${CONF[a.confidence][0]}` }, CONF[a.confidence][1])], [a.note || "", "wrap"]];
      box.replaceChildren(el("section", { class: "card" }, el("h2", {}, "Tedarikçi verisi raporu"), el("p", { class: "hint" }, d.source),
        el("p", { class: "hint" }, "KESİN: paket ve fiyat kapsamı sitede yazıyor · ÇIKARIM: paket yazıyor, fiyatın kapsamı fiyat seviyesinden çıkarıldı · TAHMİN: eşleştirme ya da paket boyutu varsayım; kontrol edin."),
        table([["Ürün"], ["Tedarikçi ürünü"], ["Yapılan"], ["Güven"], ["Not"]], [...d.applied, ...d.unchanged].map(row)),
        el("details", {}, el("summary", {}, `Uygulanmayan ${d.not_applied.length} satır (bilgi eksik)`),
          table([["Tedarikçi ürünü"], ["Fiyat", "num"], ["Neden"]], d.not_applied.map((x) => [[x.supplier, "wrap"], [x.supplier_price || "—", "num"], [x.reason, "wrap"]]))),
        el("details", {}, el("summary", {}, `Sayım listesinde karşılığı olmayan ${d.not_in_count_list.rows.length} tedarikçi ürünü`),
          el("p", { class: "hint" }, d.not_in_count_list.note),
          table([["Tedarikçi ürünü"], ["Fiyat", "num"]], d.not_in_count_list.rows.map(([n, p]) => [[n, "wrap"], [p, "num"]]))),
        el("p", {}, el("button", { type: "button", class: "danger", onclick: () => applySupplier(true) }, "Elle girilen değerlerin üzerine yaz"))));
    };
    children.push(el("div", { class: "card toolbar" }, el("span", { class: "grow" }, "Tedarikçi listesindeki (52 ürün) birim fiyat ve paket içeriği bilgileri hazır. Yalnızca boş alanlar doldurulur; sizin girdiğiniz değerler korunur."),
      el("button", { type: "button", class: "secondary", onclick: () => applySupplier(false) }, "Tedarikçi fiyat ve paket bilgilerini uygula")));
    children.push(el("div", { id: "supplier-report" }));
    if (r.data.length === 0) {
      children.push(el("div", { class: "card" }, el("p", {}, "Ürün listesi boş."),
        el("button", { type: "button", onclick: async () => {
          const res = await api("POST", "/count-items/load-defaults");
          if (!res.ok) { message(res.error); return; }
          message(`Sayım formundaki ${res.data.added} ürün eklendi.`, "info"); renderItems();
        } }, "Taff Burger sayım formundaki 98 ürünü yükle")));
    }
  }
  children.push(el("div", { class: "card" }, el("div", { class: "toolbar" }, el("label", { class: "grow" }, "Ara", search), el("label", {}, "Grup", groupFilter)), counter, tbl));
  main().replaceChildren(...children);
  applyFilter();
}

routes["#/sayim"] = renderCount;
routes["#/gecmis"] = renderHistory;
routes["#/urunler"] = renderItems;
