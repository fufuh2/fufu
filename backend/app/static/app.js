"use strict";
// OperationOS-X web UI. No build step, no third-party code.
// All data is rendered with textContent (never innerHTML) to avoid XSS.

// Thrown when a request comes back 401: the session is gone, so the page that was being
// drawn must stop instead of painting over the login screen.
class SessionEnded extends Error {}

const state = { token: null, me: null, branch: null, branches: [], items: [], products: [] };

// ---- storage (sessionStorage: cleared when the tab closes) --------------------------------
function load(key) { try { return sessionStorage.getItem(key); } catch { return null; } }
function save(key, value) {
  try { value === null ? sessionStorage.removeItem(key) : sessionStorage.setItem(key, value); } catch { /* ignore */ }
}

// ---- DOM helpers ---------------------------------------------------------------------------
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "class") node.className = v;
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}
const main = () => document.getElementById("main");

function message(text, kind = "error") {
  const box = document.getElementById("msg");
  box.replaceChildren(text ? el("div", { class: kind }, text) : "");
}

// ---- formatting ----------------------------------------------------------------------------
const money = new Intl.NumberFormat("tr-TR", { style: "currency", currency: "TRY" });
const number = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 4 });
function unknown() { return el("span", { class: "unknown" }, "bilinmiyor"); }
function fmtMoney(v) { return v === null || v === undefined ? unknown() : money.format(Number(v)); }
function fmtQty(v, unit) { return v === null || v === undefined ? unknown() : `${number.format(Number(v))} ${unit || ""}`.trim(); }
function fmtPct(v) { return v === null || v === undefined ? unknown() : `%${number.format(Number(v))}`; }
function fmtDate(v) { return v ? new Date(v).toLocaleString("tr-TR") : "—"; }
function newKey() {
  // randomUUID needs a secure context (HTTPS or localhost); fall back to getRandomValues.
  if (crypto.randomUUID) return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}
function can(perm) { return !!state.me && state.me.permissions.includes(perm); }

// ---- API -----------------------------------------------------------------------------------
async function api(method, path, body, headers = {}) {
  const opts = { method, headers: { ...headers } };
  if (state.token) opts.headers.Authorization = `Bearer ${state.token}`;
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(`/api/v1${path}`, opts);
  } catch {
    return { ok: false, status: 0, data: null, error: "Sunucuya ulaşılamadı. Bağlantıyı kontrol edip tekrar deneyin." };
  }
  let data = null;
  if (res.status !== 204) { try { data = await res.json(); } catch { data = null; } }
  if (res.status === 401 && path !== "/auth/login") {
    logout("Oturumunuz sona erdi. Lütfen tekrar giriş yapın.");
    throw new SessionEnded();
  }
  return { ok: res.ok, status: res.status, data, error: res.ok ? null : describe(res.status, data) };
}

function describe(status, data) {
  if (status === 403) return "Bu işlem için yetkiniz yok.";
  if (status === 404) return "Kayıt bulunamadı.";
  if (status === 429) return "Çok fazla hatalı deneme. Lütfen daha sonra tekrar deneyin.";
  const d = data && data.detail;
  if (Array.isArray(d)) return "Geçersiz giriş: " + d.map((e) => `${(e.loc || []).slice(1).join(".")} — ${e.msg}`).join("; ");
  if (d && d.message) return d.message;
  return `İşlem başarısız (HTTP ${status}).`;
}

// ---- auth ----------------------------------------------------------------------------------
function logout(reason) {
  state.token = null; state.me = null;
  save("token", null);
  document.getElementById("top").hidden = true;
  location.hash = "#/login";
  if (reason) message(reason);
}

async function bootstrapSession() {
  const me = await api("GET", "/me");
  if (!me.ok) return false;
  state.me = me.data;
  const branches = await api("GET", "/branches");
  state.branches = branches.ok ? branches.data.filter((b) => b.is_active) : [];
  const saved = load("branch");
  state.branch = state.branches.find((b) => b.id === saved) ? saved : (state.branches[0] || {}).id || null;
  const sel = document.getElementById("branch-select");
  sel.replaceChildren(...state.branches.map((b) => el("option", { value: b.id, selected: b.id === state.branch }, b.name)));
  document.getElementById("whoami").textContent = `${state.me.email} · ${state.me.role}`;
  for (const a of document.querySelectorAll("#nav a")) a.parentElement.hidden = !can(a.dataset.perm);
  document.getElementById("top").hidden = false;
  return true;
}

function renderLogin() {
  document.getElementById("top").hidden = true;
  const email = el("input", { type: "email", name: "email", required: true, autocomplete: "username" });
  const password = el("input", { type: "password", name: "password", required: true, autocomplete: "current-password" });
  const button = el("button", { type: "submit" }, "Giriş yap");
  const form = el("form", {
    onsubmit: async (ev) => {
      ev.preventDefault();
      button.disabled = true;
      const res = await api("POST", "/auth/login", { email: email.value, password: password.value });
      button.disabled = false;
      if (!res.ok) { message(res.status === 401 ? "E-posta veya parola hatalı." : res.error); return; }
      state.token = res.data.access_token;
      save("token", state.token);
      message("");
      if (!(await bootstrapSession())) return;
      // Only leave the login page if the user has not already navigated elsewhere.
      if (location.hash === "#/login" || location.hash === "") location.hash = "#/dashboard";
      else route();
    },
  }, el("label", {}, "E-posta", email), el("label", {}, "Parola", password), button);
  main().replaceChildren(el("section", { class: "card login" }, el("h1", {}, "Giriş"), form));
  email.focus();
}

// ---- shared loaders ------------------------------------------------------------------------
async function loadCatalog() {
  const [items, products] = await Promise.all([api("GET", "/items?limit=500"), api("GET", "/products?limit=500")]);
  state.items = items.ok ? items.data.filter((i) => i.is_active) : [];
  state.products = products.ok ? products.data.filter((p) => p.is_active) : [];
}

function itemSelect(name = "item") {
  return el("select", { name, required: true }, state.items.map((i) => el("option", { value: i.id }, `${i.sku} — ${i.name} (${i.base_unit})`)));
}
function unitInput(name = "unit") {
  return el("input", { name, required: true, list: "units", size: 6, value: "g" });
}
function table(headers, rows, emptyText) {
  if (!rows.length) return el("p", { class: "empty" }, emptyText || "Kayıt yok.");
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, headers.map(([h, cls]) => el("th", { class: cls, scope: "col" }, h)))),
    el("tbody", {}, rows.map((r) => el("tr", {}, r.map(([v, cls]) => el("td", { class: cls }, v)))))));
}
function today() {
  const d = new Date(); d.setHours(0, 0, 0, 0);
  const e = new Date(d); e.setDate(e.getDate() + 1);
  return [d, e];
}
function toLocalInput(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function fromLocalInput(v) { return new Date(v).toISOString(); }

// ---- pages ---------------------------------------------------------------------------------
async function renderDashboard() {
  const b = state.branch;
  if (!b) { main().replaceChildren(el("p", {}, "Erişebildiğiniz aktif şube yok.")); return; }
  const [stock, ncs, tasks] = await Promise.all([
    api("GET", `/branches/${b}/stock`),
    api("GET", `/branches/${b}/nonconformities?status=open`),
    api("GET", `/branches/${b}/tasks?status=open`),
  ]);
  if (!stock.ok) { message(stock.error); return; }
  const rows = stock.data.filter((r) => r.is_active);
  const low = rows.filter((r) => r.below_min === true).length;
  const neg = rows.filter((r) => r.negative).length;
  const kpi = (v, l) => el("div", { class: "card kpi" }, el("div", { class: "v" }, v), el("div", { class: "l" }, l));
  main().replaceChildren(
    el("h1", {}, "Özet"),
    el("div", { class: "grid" },
      kpi(String(low), "Minimumun altındaki malzeme"),
      kpi(String(neg), "Negatif bakiye (sayım gerekli)"),
      kpi(ncs.ok ? String(ncs.data.length) : "—", "Açık kalite uygunsuzluğu"),
      kpi(tasks.ok ? String(tasks.data.length) : "—", "Açık görev")),
    el("h2", {}, "Stok durumu"),
    table(
      [["SKU"], ["Malzeme"], ["Miktar", "num"], ["Ort. maliyet", "num"], ["Değer", "num"], ["Durum"]],
      rows.map((r) => [
        [r.sku], [r.name], [fmtQty(r.quantity, r.base_unit), "num"], [fmtMoney(r.avg_cost), "num"],
        [fmtMoney(r.stock_value), "num"],
        [r.negative ? el("span", { class: "badge bad" }, "negatif")
          : r.below_min === true ? el("span", { class: "badge warn" }, "minimum altı")
          : r.quantity === null ? el("span", { class: "unknown" }, "hareket yok")
          : el("span", { class: "badge ok" }, "normal")],
      ]),
      "Bu şubede malzeme yok."),
  );
}

async function renderOrders() {
  const b = state.branch;
  await loadCatalog();
  let key = newKey(); // reused on retry so a lost response never double-books
  const lines = [];
  const lineList = el("ul", { "aria-label": "Sipariş kalemleri" });
  const product = el("select", { name: "product", required: true }, state.products.map((p) => el("option", { value: p.id }, `${p.name} — ${money.format(Number(p.price))}`)));
  const qty = el("input", { type: "number", name: "quantity", min: 1, max: 10000, step: 1, value: 1, required: true });
  const discount = el("input", { type: "number", name: "discount", min: 0, step: "0.01", value: 0 });
  const submit = el("button", { type: "button", disabled: true }, "Siparişi kaydet");
  function redrawLines() {
    lineList.replaceChildren(...lines.map((l, i) => el("li", {}, `${l.label} × ${l.quantity} (indirim ${money.format(Number(l.discount))}) `,
      el("button", { type: "button", class: "secondary", onclick: () => { lines.splice(i, 1); redrawLines(); } }, "Kaldır"))));
    submit.disabled = lines.length === 0;
  }
  const addForm = el("form", { class: "row", onsubmit: (ev) => {
    ev.preventDefault();
    const p = state.products.find((x) => x.id === product.value);
    if (!p) return;
    lines.push({ product_id: p.id, quantity: Number(qty.value), discount: String(discount.value || 0), label: p.name });
    redrawLines();
  } }, el("label", {}, "Ürün", product), el("label", {}, "Adet", qty), el("label", {}, "İndirim (₺)", discount), el("button", { type: "submit", class: "secondary" }, "Kalem ekle"));
  submit.addEventListener("click", async () => {
    submit.disabled = true;
    const res = await api("POST", `/branches/${b}/orders`, { channel: "manual", lines: lines.map(({ label, ...l }) => l) }, { "Idempotency-Key": key });
    if (!res.ok) { message(res.status === 0 ? res.error + " Tekrar denerseniz sipariş iki kez kaydedilmez." : res.error); submit.disabled = false; return; }
    message(res.status === 200 ? "Bu sipariş zaten kaydedilmişti." : "Sipariş kaydedildi.", "info");
    key = newKey(); lines.length = 0; redrawLines(); await drawList();
  });
  const listBox = el("div");
  async function drawList() {
    const res = await api("GET", `/branches/${b}/orders?limit=50`);
    if (!res.ok) { listBox.replaceChildren(el("p", {}, res.error)); return; }
    listBox.replaceChildren(table(
      [["Tarih"], ["Kanal"], ["Durum"], ["Net", "num"], ["Maliyet", "num"], [""]],
      res.data.map((o) => {
        const cost = o.lines.some((l) => l.cost_total === null) ? null : o.lines.reduce((s, l) => s + Number(l.cost_total), 0);
        return [[fmtDate(o.ordered_at)], [o.channel], [o.status === "cancelled" ? "iptal" : "tamamlandı"],
          [fmtMoney(o.net_total), "num"], [fmtMoney(cost), "num"],
          [o.status === "completed" && can("order.cancel") ? cancelForm(o) : ""]];
      }), "Henüz sipariş yok."));
  }
  function cancelForm(o) {
    const reason = el("input", { name: "reason", required: true, placeholder: "İptal nedeni", "aria-label": "İptal nedeni" });
    return el("form", { class: "row", onsubmit: async (ev) => {
      ev.preventDefault();
      const res = await api("POST", `/orders/${o.id}/cancel`, { reason: reason.value });
      if (!res.ok) { message(res.error); return; }
      message("Sipariş iptal edildi; stok iade edildi.", "info"); await drawList();
    } }, reason, el("button", { type: "submit", class: "secondary" }, "İptal et"));
  }
  main().replaceChildren(el("h1", {}, "Siparişler"),
    el("section", { class: "card" }, el("h2", {}, "Yeni sipariş"), addForm, lineList, submit),
    el("h2", {}, "Son siparişler"), listBox);
  await drawList();
}

async function renderStock() {
  const b = state.branch;
  await loadCatalog();
  const reasons = [["spoilage_expiry", "Bozulma / SKT"], ["prep_loss", "Hazırlama kaybı"], ["cooking_loss", "Pişirme kaybı"],
    ["production_error", "Hatalı üretim"], ["customer_return", "Müşteri iadesi"], ["staff_meal", "Personel tüketimi"],
    ["spill_accident", "Dökülme / kaza"], ["unknown", "Sebebi bilinmiyor"]];
  function opForm(title, perm, fields, build, path, okText) {
    if (!can(perm)) return null;
    const btn = el("button", { type: "submit" }, "Kaydet");
    const form = el("form", { class: "row", onsubmit: async (ev) => {
      ev.preventDefault(); btn.disabled = true;
      const res = await api("POST", path, build(new FormData(form)));
      btn.disabled = false;
      if (!res.ok) { message(res.error); return; }
      message(okText, "info"); form.reset();
    } }, ...fields, btn);
    return el("section", { class: "card" }, el("h2", {}, title), form);
  }
  main().replaceChildren(el("h1", {}, "Stok işlemleri"),
    el("datalist", { id: "units" }, ["g", "kg", "ml", "l", "pcs"].map((u) => el("option", { value: u }))),
    opForm("Mal kabul", "stock.receive", [
      el("label", {}, "Malzeme", itemSelect()), el("label", {}, "Miktar", el("input", { name: "quantity", type: "number", step: "0.0001", min: "0.0001", required: true })),
      el("label", {}, "Birim", unitInput()), el("label", {}, "Birim fiyat (₺, KDV hariç)", el("input", { name: "unit_cost", type: "number", step: "0.000001", min: "0", required: true })),
      el("label", {}, "Tedarikçi", el("input", { name: "supplier", maxlength: 200 }))],
      (f) => ({ supplier: f.get("supplier") || null, lines: [{ item_id: f.get("item"), quantity: f.get("quantity"), unit: f.get("unit"), unit_cost: f.get("unit_cost") }] }),
      `/branches/${b}/receipts`, "Mal kabul kaydedildi."),
    opForm("Fire", "stock.waste", [
      el("label", {}, "Malzeme", itemSelect()), el("label", {}, "Miktar", el("input", { name: "quantity", type: "number", step: "0.0001", min: "0.0001", required: true })),
      el("label", {}, "Birim", unitInput()), el("label", {}, "Neden", el("select", { name: "reason" }, reasons.map(([v, t]) => el("option", { value: v }, t))))],
      (f) => ({ item_id: f.get("item"), quantity: f.get("quantity"), unit: f.get("unit"), reason: f.get("reason") }),
      `/branches/${b}/waste`, "Fire kaydedildi."),
    opForm("Sayım", "stock.count", [
      el("label", {}, "Malzeme", itemSelect()), el("label", {}, "Sayılan miktar", el("input", { name: "quantity", type: "number", step: "0.0001", min: "0", required: true })),
      el("label", {}, "Birim", unitInput())],
      (f) => ({ lines: [{ item_id: f.get("item"), quantity: f.get("quantity"), unit: f.get("unit") }] }),
      `/branches/${b}/counts`, "Sayım kaydedildi; fark düzeltme hareketi oluşturuldu."),
  );
}

async function renderReports() {
  const b = state.branch;
  const [d0, d1] = today();
  const from = el("input", { type: "datetime-local", name: "from", value: toLocalInput(d0), required: true });
  const to = el("input", { type: "datetime-local", name: "to", value: toLocalInput(d1), required: true });
  const out = el("div");
  async function run() {
    const q = `from=${encodeURIComponent(fromLocalInput(from.value))}&to=${encodeURIComponent(fromLocalInput(to.value))}`;
    const [margin, variance] = await Promise.all([api("GET", `/branches/${b}/reports/margin?${q}`), api("GET", `/branches/${b}/reports/variance?${q}`)]);
    if (!margin.ok) { message(margin.error); return; }
    const t = margin.data.total;
    out.replaceChildren(
      el("h2", {}, "Kârlılık"),
      el("p", {}, "Toplam: net satış ", fmtMoney(t.net_revenue), " · SMM ", fmtMoney(t.cogs), " · brüt kâr ", fmtMoney(t.gross_profit), " · marj ", fmtPct(t.margin_pct),
        t.status === "incomplete_cost" ? el("span", { class: "badge warn" }, " reçetesiz satış var: maliyet eksik") : null,
        t.status === "undefined_zero_revenue" ? el("span", { class: "badge warn" }, " gelir sıfır: marj tanımsız") : null),
      table([["Ürün"], ["Adet", "num"], ["Net satış", "num"], ["SMM", "num"], ["Brüt kâr", "num"], ["Marj", "num"]],
        margin.data.lines.map((l) => [[l.name], [String(l.quantity), "num"], [fmtMoney(l.net_revenue), "num"], [fmtMoney(l.cogs), "num"], [fmtMoney(l.gross_profit), "num"], [fmtPct(l.margin_pct), "num"]]),
        "Bu dönemde satış yok."),
      el("h2", {}, "Teorik / gerçek tüketim farkı"),
      variance.ok ? table([["Malzeme"], ["Teorik", "num"], ["Fire", "num"], ["Açıklanamayan", "num"], ["Gerçek", "num"], ["Durum"]],
        variance.data.lines.map((l) => [[l.sku], [fmtQty(l.theoretical_qty, l.base_unit), "num"], [fmtQty(l.recorded_waste_qty, l.base_unit), "num"],
          [fmtQty(l.unexplained_qty, l.base_unit), "num"], [fmtQty(l.actual_qty, l.base_unit), "num"],
          [{ needs_review: "incelenmeli", ok: "uyumlu", no_count_in_period: "dönemde sayım yok" }[l.status] || l.status]]),
        "Bu dönemde hareket yok.") : el("p", {}, variance.error),
      el("p", { class: "empty" }, `Kaynak: ${margin.data.source}. Oluşturulma: ${fmtDate(margin.data.generated_at)}.`),
    );
  }
  main().replaceChildren(el("h1", {}, "Raporlar"),
    el("form", { class: "row card", onsubmit: (ev) => { ev.preventDefault(); run(); } },
      el("label", {}, "Başlangıç", from), el("label", {}, "Bitiş", to), el("button", { type: "submit" }, "Göster")), out);
  await run();
}

async function renderQuality() {
  const b = state.branch;
  const templates = await api("GET", "/checklist-templates");
  const active = templates.ok ? templates.data.filter((t) => t.is_active) : [];
  const box = el("div");
  const pick = el("select", { name: "template", "aria-label": "Kontrol listesi" }, active.map((t) => el("option", { value: t.id }, t.name)));
  function drawForm() {
    const t = active.find((x) => x.id === pick.value);
    if (!t) { box.replaceChildren(el("p", { class: "empty" }, "Aktif kontrol listesi yok.")); return; }
    const inputs = t.items.map((it) => it.answer_type === "check"
      ? [it, el("input", { type: "checkbox", name: `i${it.id}` })]
      : [it, el("input", { type: "number", step: "0.001", name: `i${it.id}`, required: true })]);
    const evidence = t.items.filter((it) => it.evidence_required).map((it) => [it.id, el("input", { name: `e${it.id}`, required: true, placeholder: "Kanıt referansı" })]);
    const form = el("form", { onsubmit: async (ev) => {
      ev.preventDefault();
      const answers = inputs.map(([it, input]) => {
        const a = it.answer_type === "check" ? { item_id: it.id, checked: input.checked } : { item_id: it.id, value: input.value };
        const e = evidence.find(([id]) => id === it.id);
        if (e) a.evidence_ref = e[1].value;
        return a;
      });
      const res = await api("POST", `/branches/${b}/checklist-runs`, { template_id: t.id, answers });
      if (!res.ok) { message(res.error); return; }
      message(res.data.passed ? "Kontrol kaydedildi: uygun." : "Kontrol kaydedildi: uygunsuzluk açıldı.", "info");
      await drawNcs();
    } }, inputs.map(([it, input]) => el("p", {}, el("label", {}, `${it.label}${it.unit ? ` (${it.unit})` : ""}${it.min_value !== null || it.max_value !== null ? ` [${it.min_value ?? "—"} … ${it.max_value ?? "—"}]` : ""}`, input),
      ...(evidence.filter(([id]) => id === it.id).map(([, e]) => el("label", {}, "Kanıt", e))))), el("button", { type: "submit" }, "Kaydet"));
    box.replaceChildren(form);
  }
  pick.addEventListener("change", drawForm);
  const ncBox = el("div");
  async function drawNcs() {
    const res = await api("GET", `/branches/${b}/nonconformities?status=open`);
    ncBox.replaceChildren(res.ok ? table([["Açılış"], ["Açıklama"], ["Son tarih"], [""]], res.data.map((n) => {
      const action = el("input", { required: true, placeholder: "Düzeltici faaliyet", "aria-label": "Düzeltici faaliyet" });
      return [[fmtDate(n.created_at)], [n.description], [fmtDate(n.due_at)], [el("form", { class: "row", onsubmit: async (ev) => {
        ev.preventDefault();
        const r = await api("POST", `/nonconformities/${n.id}/resolve`, { corrective_action: action.value });
        if (!r.ok) { message(r.error); return; }
        message("Uygunsuzluk kapatıldı.", "info"); await drawNcs();
      } }, action, el("button", { type: "submit", class: "secondary" }, "Kapat"))]];
    }), "Açık uygunsuzluk yok.") : el("p", {}, res.error));
  }
  main().replaceChildren(el("h1", {}, "Kalite kontrol"), el("section", { class: "card" }, el("label", {}, "Kontrol listesi", pick), box),
    el("h2", {}, "Açık uygunsuzluklar"), ncBox);
  drawForm(); await drawNcs();
}

async function renderTasks() {
  const b = state.branch;
  const res = await api("GET", `/branches/${b}/tasks?status=open`);
  if (!res.ok) { message(res.error); return; }
  main().replaceChildren(el("h1", {}, "Görevler"), table([["Görev"], ["Son tarih"], [""]], res.data.map((t) => [[t.title], [fmtDate(t.due_at)],
    [el("button", { type: "button", class: "secondary", onclick: async () => {
      const r = await api("POST", `/tasks/${t.id}/complete`);
      if (!r.ok) { message(r.error); return; }
      message("Görev tamamlandı.", "info"); renderTasks();
    } }, "Tamamlandı")]]), "Açık görev yok."));
}

// ---- router --------------------------------------------------------------------------------
const routes = { "#/dashboard": renderDashboard, "#/orders": renderOrders, "#/stock": renderStock,
  "#/reports": renderReports, "#/quality": renderQuality, "#/tasks": renderTasks };

async function route() {
  const hash = location.hash || "#/dashboard";
  if (!state.token || hash === "#/login") { renderLogin(); return; }
  if (!state.me && !(await bootstrapSession())) { logout(); return; }
  const page = routes[hash] || renderDashboard;
  for (const a of document.querySelectorAll("#nav a")) {
    if (a.getAttribute("href") === hash) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  main().replaceChildren(el("p", { class: "empty" }, "Yükleniyor…"));
  try {
    await page();
  } catch (err) {
    if (err instanceof SessionEnded) return; // logout() already showed the login page
    message("Beklenmeyen bir hata oluştu. Sayfayı yenileyin.");
    throw err;
  }
  main().focus();
}

document.addEventListener("DOMContentLoaded", () => {
  state.token = load("token");
  document.getElementById("logout").addEventListener("click", async () => {
    try { await api("POST", "/auth/logout-all"); } catch (err) { if (!(err instanceof SessionEnded)) throw err; }
    logout("Çıkış yapıldı.");
  });
  document.getElementById("branch-select").addEventListener("change", (ev) => { state.branch = ev.target.value; save("branch", state.branch); route(); });
  window.addEventListener("hashchange", () => { message(""); route(); });
  window.addEventListener("unhandledrejection", (ev) => { if (ev.reason instanceof SessionEnded) ev.preventDefault(); });
  route();
});
