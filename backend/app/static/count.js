"use strict";
// Aylık sayım ve ürün listesi ekranları. app.js'teki yardımcıları (el, api, message, can, state)
// kullanır; veriler yalnızca textContent ile basılır.

const MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos",
  "Eylül", "Ekim", "Kasım", "Aralık"];
const STATUS_LABEL = { fazla: "FAZLA", eksik: "EKSİK", tamam: "TAMAM", sayilmadi: "SAYILMADI" };
const qtyFmt = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 3 });
const countDirty = { lines: new Map(), header: {} };

function currentPeriod() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}
function periodLabel(p) {
  const [y, m] = p.split("-");
  return `${MONTHS[Number(m) - 1]} ${y}`;
}
// "12,5" ya da "12.5" -> "12.5"; boş -> null; geçersiz -> undefined
function parseQty(text) {
  const t = String(text).trim().replace(/\s/g, "").replace(",", ".");
  if (t === "") return null;
  if (!/^\d{1,11}(\.\d{1,3})?$/.test(t)) return undefined;
  return t;
}
// Ekranda anlık hesap: binde birlik tamsayılarla (kayan nokta hatası olmadan).
function milli(v) { return v === null || v === undefined || v === "" ? null : Math.round(Number(v) * 1000); }
function fromMilli(m) { return m === null ? null : m / 1000; }
function showQty(v) { return v === null || v === undefined ? "—" : qtyFmt.format(Number(v)); }
function showMoney(v) { return v === null || v === undefined ? "—" : money.format(Number(v)); }

function hasUnsaved() { return countDirty.lines.size > 0 || Object.keys(countDirty.header).length > 0; }
window.addEventListener("beforeunload", (ev) => { if (hasUnsaved()) { ev.preventDefault(); ev.returnValue = ""; } });
window.addEventListener("hashchange", () => { countDirty.lines.clear(); countDirty.header = {}; });

async function ensureBranch() {
  if (state.branch) return true;
  if (!can("branch.manage")) { main().replaceChildren(el("p", {}, "Erişebildiğiniz aktif restoran yok. Yöneticinize başvurun.")); return false; }
  const name = el("input", { name: "name", required: true, maxlength: 200, placeholder: "Örn. Taff Burger Merkez" });
  main().replaceChildren(el("h1", {}, "Restoranınızı ekleyin"), el("form", { class: "card row", onsubmit: async (ev) => {
    ev.preventDefault();
    const res = await api("POST", "/branches", { name: name.value });
    if (!res.ok) { message(res.error); return; }
    state.me = null; await bootstrapSession(); state.branch = res.data.id; save("branch", state.branch); route();
  } }, el("label", {}, "Restoran adı", name), el("button", { type: "submit" }, "Kaydet")));
  return false;
}

async function downloadExcel(period) {
  const res = await fetch(`/api/v1/branches/${state.branch}/monthly-counts/${period}/export.xlsx`, {
    headers: { Authorization: `Bearer ${state.token}` } });
  if (!res.ok) { message(describe(res.status, null)); return; }
  const url = URL.createObjectURL(await res.blob());
  const a = el("a", { href: url, download: `sayim-${period}.xlsx` });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

async function renderCount() {
  if (!(await ensureBranch())) return;
  const period = load("period") || currentPeriod();
  const res = await api("GET", `/branches/${state.branch}/monthly-counts/${period}`);
  if (!res.ok) {
    main().replaceChildren(el("h1", {}, `Aylık Sayım — ${periodLabel(period)}`), el("p", {}, res.error), monthPicker(period));
    return;
  }
  countDirty.lines.clear(); countDirty.header = {};
  drawSheet(res.data);
}

function monthPicker(period) {
  const input = el("input", { type: "month", value: period, "aria-label": "Sayım ayı" });
  input.addEventListener("change", () => {
    if (!input.value) return;
    if (hasUnsaved() && !confirm("Kaydedilmemiş değişiklikler var. Yine de ay değiştirilsin mi?")) { input.value = period; return; }
    save("period", input.value); renderCount();
  });
  return el("label", {}, "Ay", input);
}

function drawSheet(sheet) {
  const closed = sheet.status === "closed";
  const editable = !closed && can("stock.count");
  const branchName = (state.branches.find((b) => b.id === state.branch) || {}).name || "";
  const summary = el("div", { class: "grid" });
  const search = el("input", { type: "search", placeholder: "Ürün ara…", "aria-label": "Ürün ara" });
  const filter = el("select", { "aria-label": "Filtre" },
    [["all", "Tüm ürünler"], ["diff", "Sadece farkı olanlar"], ["eksik", "Eksikler"], ["fazla", "Fazlalar"], ["sayilmadi", "Sayılmayanlar"]]
      .map(([v, t]) => el("option", { value: v }, t)));
  const saveBtn = el("button", { type: "button", disabled: true }, "Kaydet");
  const rows = [];

  // header fields
  const who = el("input", { name: "counted_by_name", value: sheet.counted_by_name || "", maxlength: 200, disabled: !editable });
  const when = el("input", { type: "date", name: "count_date", value: sheet.count_date || "", disabled: !editable });
  const note = el("input", { name: "note", value: sheet.note || "", maxlength: 1000, disabled: !editable, placeholder: "İsteğe bağlı" });
  for (const [input, key] of [[who, "counted_by_name"], [when, "count_date"], [note, "note"]]) {
    input.addEventListener("input", () => { countDirty.header[key] = input.value || null; saveBtn.disabled = false; });
  }

  function lineRow(line) {
    const cells = {};
    const mk = (field, value, locked) => {
      const input = el("input", { inputmode: "decimal", class: "qty", value: value === null ? "" : qtyFmt.format(Number(value)).replace(/\./g, ""),
        disabled: !editable || locked, "aria-label": `${line.name} ${field}` });
      input.addEventListener("input", () => onEdit(line, field, input));
      cells[field] = input;
      return input;
    };
    const expected = el("td", { class: "num" });
    const diff = el("td", { class: "num strong" });
    const status = el("td");
    const value = el("td", { class: "num" });
    const tr = el("tr", {},
      el("td", { class: "name" }, line.name, line.opening_locked ? el("span", { class: "lock", title: "Devir önceki kapanmış aydan geliyor" }, " 🔒") : ""),
      el("td", {}, line.unit),
      el("td", { class: "num" }, mk("opening", line.opening, line.opening_locked)),
      el("td", { class: "num" }, mk("incoming", line.incoming, false)),
      el("td", { class: "num" }, mk("used", line.used, false)),
      el("td", { class: "num" }, mk("waste", line.waste, false)),
      expected,
      el("td", { class: "num" }, mk("counted", line.counted, false)),
      diff, status, value);
    const row = { line, tr, cells, expected, diff, status, value, current: { ...line } };
    rows.push(row);
    paint(row);
    return tr;
  }

  function compute(c) {
    const e = milli(c.opening) + milli(c.incoming) - milli(c.used) - milli(c.waste);
    const k = milli(c.counted);
    const d = k === null ? null : k - e;
    const st = d === null ? "sayilmadi" : d > 0 ? "fazla" : d < 0 ? "eksik" : "tamam";
    const price = c.unit_price === null || c.unit_price === undefined ? null : Number(c.unit_price);
    const val = d === null || price === null ? null : Math.round((d / 1000) * price * 100) / 100;
    const wv = price === null ? null : Math.round((milli(c.waste) / 1000) * price * 100) / 100;
    return { expected: fromMilli(e), difference: fromMilli(d), status: st, value: val, wasteValue: wv };
  }

  function paint(row) {
    const r = compute(row.current);
    row.expected.textContent = showQty(r.expected);
    row.diff.textContent = r.difference === null ? "—" : (r.difference > 0 ? "+" : "") + qtyFmt.format(r.difference);
    row.diff.className = `num strong ${r.status}`;
    row.status.replaceChildren(el("span", { class: `badge st-${r.status}` }, STATUS_LABEL[r.status]));
    row.value.textContent = showMoney(r.value);
    row.result = r;
  }

  function onEdit(line, field, input) {
    const parsed = parseQty(input.value);
    input.classList.toggle("invalid", parsed === undefined || (parsed === null && field !== "counted"));
    if (parsed === undefined || (parsed === null && field !== "counted")) return;
    const row = rows.find((r) => r.line.item_id === line.item_id);
    row.current[field] = parsed;
    const pending = countDirty.lines.get(line.item_id) || { item_id: line.item_id };
    if (field === "counted" && parsed === null) { pending.clear_counted = true; delete pending.counted; }
    else { pending[field] = parsed; if (field === "counted") delete pending.clear_counted; }
    countDirty.lines.set(line.item_id, pending);
    saveBtn.disabled = false;
    paint(row); refreshSummary(); applyFilter();
  }

  function refreshSummary() {
    let counted = 0, fazla = 0, eksik = 0, fv = 0, ev = 0, zayi = 0, zv = 0;
    for (const r of rows) {
      if (milli(r.current.waste) > 0) { zayi += 1; zv += r.result.wasteValue || 0; }
      if (r.result.status !== "sayilmadi") counted += 1;
      if (r.result.status === "fazla") { fazla += 1; fv += r.result.value || 0; }
      if (r.result.status === "eksik") { eksik += 1; ev += r.result.value || 0; }
    }
    const kpi = (v, l, cls = "") => el("div", { class: `card kpi ${cls}` }, el("div", { class: "v" }, v), el("div", { class: "l" }, l));
    summary.replaceChildren(
      kpi(`${counted} / ${rows.length}`, "Sayılan ürün"),
      kpi(String(eksik), `Eksik ürün · ${money.format(ev)}`, "eksik"),
      kpi(String(fazla), `Fazla ürün · ${money.format(fv)}`, "fazla"),
      kpi(String(zayi), `Zayi olan ürün · ${money.format(zv)}`, "zayi"),
      kpi(money.format(fv + ev), `Net fark (${sheet.value_coverage})`));
  }

  function applyFilter() {
    const q = search.value.trim().toLocaleLowerCase("tr-TR");
    const f = filter.value;
    for (const r of rows) {
      const st = r.result.status;
      const ok = (!q || r.line.name.toLocaleLowerCase("tr-TR").includes(q)) &&
        (f === "all" || (f === "diff" ? st === "fazla" || st === "eksik" : st === f));
      r.tr.hidden = !ok;
    }
  }
  search.addEventListener("input", applyFilter);
  filter.addEventListener("change", applyFilter);

  async function doSave() {
    const body = { ...countDirty.header, lines: [...countDirty.lines.values()] };
    saveBtn.disabled = true;
    const r = await api("PUT", `/branches/${state.branch}/monthly-counts/${sheet.period}`, body);
    if (!r.ok) { message(r.error); saveBtn.disabled = false; return false; }
    countDirty.lines.clear(); countDirty.header = {};
    message("Sayım kaydedildi.", "info");
    drawSheet(r.data);
    return true;
  }
  saveBtn.addEventListener("click", doSave);

  const actions = [saveBtn];
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
  actions.push(el("button", { type: "button", class: "secondary", onclick: () => downloadExcel(sheet.period) }, "Excel indir"));
  actions.push(el("button", { type: "button", class: "secondary", onclick: () => window.print() }, "Yazdır"));

  const head = el("thead", {}, el("tr", {}, ["Malzeme", "Birim", "Devir", "Gelen", "Satılan / Kullanılan", "Zayi", "Beklenen", "Sayılan", "Fark", "Durum", "Fark ₺"]
    .map((h, i) => el("th", { scope: "col", class: i >= 2 && i !== 9 ? "num" : "" }, h))));
  const bodies = sheet.groups.map((g) => el("tbody", {},
    el("tr", { class: "group" }, el("th", { colspan: 11, scope: "rowgroup" }, g.name)),
    ...g.lines.map((line) => lineRow(line))));

  main().replaceChildren(
    el("div", { class: "sheet-head" },
      el("h1", {}, `Aylık Sayım — ${periodLabel(sheet.period)}`),
      el("span", { class: `badge ${closed ? "ok" : "warn"}` }, closed ? "KAPATILDI" : "TASLAK")),
    el("p", { class: "empty" }, `${branchName} · Devir: ${sheet.opening_from ? `${periodLabel(sheet.opening_from)} sayımından (kilitli)` : "önceki kapanmış ay yok, elle girilir"} · Beklenen = Devir + Gelen − Satılan/Kullanılan − Zayi · Fark = Sayılan − Beklenen`),
    el("div", { class: "card toolbar" }, monthPicker(sheet.period), el("label", {}, "Sayımı yapan", who),
      el("label", {}, "Sayım tarihi", when), el("label", { class: "grow" }, "Not", note)),
    summary,
    el("div", { class: "card toolbar no-print" }, el("label", { class: "grow" }, "Ara", search), el("label", {}, "Göster", filter), ...actions),
    el("div", { class: "table-wrap count" }, el("table", { class: "count-table" }, head, ...bodies)),
    el("div", { class: "no-print" }, el("h2", {}, "Geçmiş aylar"), pastMonths()),
  );
  refreshSummary();
}

function pastMonths() {
  const box = el("div", {}, "Yükleniyor…");
  api("GET", `/branches/${state.branch}/monthly-counts`).then((r) => {
    if (!r.ok) { box.textContent = r.error; return; }
    box.replaceChildren(table([["Ay"], ["Durum"], ["Sayımı yapan"], [""]], r.data.map((m) => {
      const p = m.period.slice(0, 7);
      return [[periodLabel(p)], [m.status === "closed" ? "Kapatıldı" : "Taslak"], [m.counted_by_name || "—"],
        [el("button", { type: "button", class: "secondary", onclick: () => { save("period", p); renderCount(); } }, "Aç")]];
    }), "Henüz sayım yok."));
  }).catch((err) => { if (!(err instanceof SessionEnded)) throw err; });
  return box;
}

async function renderItems() {
  const r = await api("GET", "/count-items?include_inactive=true");
  if (!r.ok) { message(r.error); return; }
  const manage = can("catalog.manage");
  const groups = [...new Set(r.data.map((i) => i.group_name))];
  const rows = r.data.map((i) => {
    const price = el("input", { inputmode: "decimal", class: "qty", value: i.unit_price === null ? "" : String(i.unit_price).replace(".", ","), disabled: !manage, "aria-label": `${i.name} birim fiyat` });
    const active = el("input", { type: "checkbox", checked: i.is_active, disabled: !manage, "aria-label": `${i.name} aktif` });
    const saveRow = async (fields) => {
      const res = await api("PATCH", `/count-items/${i.id}`, fields);
      if (!res.ok) { message(res.error); return; }
      message(`${i.name} güncellendi.`, "info");
    };
    price.addEventListener("change", () => {
      const v = price.value.trim().replace(",", ".");
      if (v !== "" && !/^\d{1,9}(\.\d{1,2})?$/.test(v)) { price.classList.add("invalid"); return; }
      price.classList.remove("invalid");
      saveRow({ unit_price: v === "" ? null : v });
    });
    active.addEventListener("change", () => saveRow({ is_active: active.checked }));
    return [[i.group_name], [i.name], [i.unit], [price, "num"], [active]];
  });
  const children = [el("h1", {}, "Ürün Listesi"),
    el("p", { class: "empty" }, "Birim fiyat (KDV hariç, ₺) girilen ürünlerde fark TL olarak da hesaplanır. Pasif ürünler yeni sayımlarda görünmez; geçmiş sayımlar korunur.")];
  if (manage) {
    const g = el("input", { name: "group", list: "groups", required: true, maxlength: 100 });
    const n = el("input", { name: "name", required: true, maxlength: 200 });
    const u = el("select", { name: "unit" }, ["KG", "ADET", "LT", "PKT", "GR", "ML", "KOLİ"].map((x) => el("option", { value: x }, x)));
    const p = el("input", { name: "price", inputmode: "decimal", placeholder: "İsteğe bağlı" });
    children.push(el("form", { class: "card row", onsubmit: async (ev) => {
      ev.preventDefault();
      const price = p.value.trim().replace(",", ".");
      const res = await api("POST", "/count-items", { group_name: g.value, name: n.value.toLocaleUpperCase("tr-TR"), unit: u.value, unit_price: price === "" ? null : price });
      if (!res.ok) { message(res.error); return; }
      message("Ürün eklendi.", "info"); renderItems();
    } }, el("datalist", { id: "groups" }, groups.map((x) => el("option", { value: x }))),
      el("label", {}, "Grup", g), el("label", { class: "grow" }, "Malzeme adı", n), el("label", {}, "Birim", u),
      el("label", {}, "Birim fiyat ₺", p), el("button", { type: "submit" }, "Ürün ekle")));
    if (r.data.length === 0) {
      children.push(el("div", { class: "card" }, el("p", {}, "Ürün listesi boş."),
        el("button", { type: "button", onclick: async () => {
          const res = await api("POST", "/count-items/load-defaults");
          if (!res.ok) { message(res.error); return; }
          message(`Sayım formundaki ${res.data.added} ürün eklendi.`, "info"); renderItems();
        } }, "Taff Burger sayım formundaki 98 ürünü yükle")));
    }
  }
  children.push(table([["Grup"], ["Malzeme"], ["Birim"], ["Birim fiyat ₺", "num"], ["Aktif"]], rows, "Henüz ürün yok."));
  main().replaceChildren(...children);
}

routes["#/sayim"] = renderCount;
routes["#/urunler"] = renderItems;
