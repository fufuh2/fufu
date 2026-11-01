"use strict";
// Taff Burger aylık sayım paneli: çekirdek (oturum, API, yönlendirme, ortak yardımcılar).
// Derleme adımı ve üçüncü parti kod yok. Veriler yalnızca textContent ile basılır (XSS yok).

// 401 gelince oturum bitmiştir; çizilmekte olan sayfa giriş ekranının üstüne boyamasın.
class SessionEnded extends Error {}
// Kullanıcı sayfa yüklenirken başka sayfaya geçtiyse eski sayfanın yanıtı çizilmez.
class StaleRoute extends Error {}
let routeGen = 0;

const state = { pendingMessage: null, token: null, me: null, branch: null, branches: [], allBranches: [], guard: null };
const routes = {};

// ---- storage (sessionStorage: sekme kapanınca silinir) ------------------------------------
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
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}
function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) node.setAttribute(k, String(v));
  for (const c of children.flat()) if (c !== null && c !== undefined) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}
const main = () => document.getElementById("main");

function message(text, kind = "error") {
  const box = document.getElementById("msg");
  box.replaceChildren(text ? el("div", { class: kind }, text) : "");
}

// ---- formatting ----------------------------------------------------------------------------
const money = new Intl.NumberFormat("tr-TR", { style: "currency", currency: "TRY" });
const qtyFmt = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 3 });
const MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
const STATUS_LABEL = { fazla: "FAZLA", eksik: "EKSİK", tamam: "TAMAM", sayilmadi: "SAYILMADI" };
function showQty(v) { return v === null || v === undefined ? "—" : qtyFmt.format(Number(v)); }
function showMoney(v) { return v === null || v === undefined ? "—" : money.format(Number(v)); }
function signedQty(v) { return v === null || v === undefined ? "—" : (Number(v) > 0 ? "+" : "") + qtyFmt.format(Number(v)); }
function fmtDate(v) { return v ? new Date(v).toLocaleString("tr-TR") : "—"; }
function currentPeriod() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}
function periodLabel(p) { const [y, m] = p.split("-"); return `${MONTHS[Number(m) - 1]} ${y}`; }
function periodShort(p) { const [y, m] = p.split("-"); return `${MONTHS[Number(m) - 1].slice(0, 3)} ${y.slice(2)}`; }
function getPeriod() { return load("period") || currentPeriod(); }
function setPeriod(p) { save("period", p); }
function can(perm) { return !!state.me && state.me.permissions.includes(perm); }
function branchName(id = state.branch) { return (state.allBranches.find((b) => b.id === id) || {}).name || ""; }
function statusBadge(st) {
  return st === "closed" ? el("span", { class: "badge ok" }, "KAPATILDI")
    : st === "draft" ? el("span", { class: "badge warn" }, "TASLAK")
    : el("span", { class: "badge muted" }, "BAŞLANMADI");
}
function kpi(label, value, sub = null, cls = "") {
  return el("div", { class: `card kpi ${cls}` }, el("div", { class: "l" }, label), el("div", { class: "v" }, value), sub ? el("div", { class: "s" }, sub) : null);
}
function table(headers, rows, emptyText, attrs = {}) {
  if (!rows.length) return el("p", { class: "empty" }, emptyText || "Kayıt yok.");
  return el("div", { class: "table-wrap" }, el("table", attrs,
    el("thead", {}, el("tr", {}, headers.map(([h, cls]) => el("th", { class: cls, scope: "col" }, h)))),
    el("tbody", {}, rows.map((r) => (r instanceof Node ? r : el("tr", {}, r.map(([v, cls]) => el("td", { class: cls }, v))))))));
}
function pageHead(title, sub, ...right) {
  return el("div", { class: "page-head" }, el("h1", {}, title), el("span", { class: "spacer" }), ...right, sub ? el("p", { class: "sub" }, sub) : null);
}
function monthInput(period, onChange) {
  const input = el("input", { type: "month", value: period, "aria-label": "Ay" });
  input.addEventListener("change", () => { if (input.value) onChange(input.value, input); });
  return el("label", {}, "Ay", input);
}
// "12,5" ya da "12.5" -> "12.5"; boş -> null; geçersiz -> undefined
function parseDecimal(text, decimals = 3, intDigits = 11) {
  const t = String(text).trim().replace(/\s/g, "").replace(/\./g, (m, i, s) => (s.includes(",") ? "" : m)).replace(",", ".");
  if (t === "") return null;
  const re = new RegExp(`^\\d{1,${intDigits}}(\\.\\d{1,${decimals}})?$`);
  return re.test(t) ? t : undefined;
}
function numForInput(v) { return v === null || v === undefined ? "" : qtyFmt.format(Number(v)).replace(/\./g, ""); }
const qty4Fmt = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 4 });
// Paket içeriği ve reçete miktarı 4 ondalıkla saklanır; 3'e yuvarlanıp geri yazılmasın.
function numForInput4(v) { return v === null || v === undefined ? "" : qty4Fmt.format(Number(v)).replace(/\./g, ""); }
// Sunucu ROUND_HALF_EVEN kullanır; ekranda aynı sonucu vermek için tam ortadaki değer çifte yuvarlanır.
function roundHalfEven(x) {
  const f = Math.floor(x);
  const d = x - f;
  if (Math.abs(d - 0.5) < 1e-9) return f % 2 === 0 ? f : f + 1;
  return Math.round(x);
}

// ---- API -----------------------------------------------------------------------------------
async function api(method, path, body, headers = {}) {
  const gen = routeGen;
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
  if (res.status === 401 && path !== "/auth/login" && path !== "/auth/password") {
    logout("Oturumunuz sona erdi. Lütfen tekrar giriş yapın.");
    throw new SessionEnded();
  }
  if (gen !== routeGen && method === "GET") throw new StaleRoute();
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

async function download(path, filename) {
  const res = await fetch(`/api/v1${path}`, { headers: { Authorization: `Bearer ${state.token}` } });
  if (res.status === 401) { logout("Oturumunuz sona erdi. Lütfen tekrar giriş yapın."); return; }
  if (!res.ok) { message(describe(res.status, null)); return; }
  const url = URL.createObjectURL(await res.blob());
  const a = el("a", { href: url, download: filename });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

// ---- auth ----------------------------------------------------------------------------------
function logout(reason) {
  // Paralel istekler birlikte 401 alabilir; yalnızca ilki çıkış yapar.
  if (state.token === null && location.hash === "#/login") return;
  state.token = null; state.me = null; state.guard = null;
  save("token", null);
  document.getElementById("top").hidden = true;
  document.getElementById("side").hidden = true;
  state.pendingMessage = reason || null;
  if (location.hash === "#/login") { renderLogin(); showPending(); }
  else location.hash = "#/login";
}

function showPending() {
  message(state.pendingMessage || "");
  state.pendingMessage = null;
}

async function loadBranches() {
  const branches = await api("GET", "/branches");
  state.allBranches = branches.ok ? branches.data : [];
  state.branches = state.allBranches.filter((b) => b.is_active);
  const saved = load("branch");
  state.branch = state.branches.find((b) => b.id === saved) ? saved : (state.branches[0] || {}).id || null;
  const sel = document.getElementById("branch-select");
  sel.replaceChildren(...state.branches.map((b) => el("option", { value: b.id, selected: b.id === state.branch }, b.name)));
  sel.parentElement.hidden = state.branches.length === 0;
}

async function bootstrapSession() {
  const me = await api("GET", "/me");
  if (!me.ok) return false;
  state.me = me.data;
  await loadBranches();
  document.getElementById("whoami").textContent = `${state.me.email} · ${state.me.role_label}`;
  for (const a of document.querySelectorAll("#nav a")) a.parentElement.hidden = !!a.dataset.perm && !can(a.dataset.perm);
  const anyAdmin = [...document.querySelectorAll("#nav a[data-perm]")].some((a) => ["user.manage", "branch.manage", "audit.read"].includes(a.dataset.perm) && !a.parentElement.hidden);
  document.querySelector('#nav [data-section="admin"]').hidden = !anyAdmin;
  document.getElementById("top").hidden = false;
  document.getElementById("side").hidden = false;
  return true;
}

function renderLogin() {
  document.getElementById("top").hidden = true;
  document.getElementById("side").hidden = true;
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
      if (location.hash === "#/login" || location.hash === "") location.hash = "#/panel";
      else route();
    },
  }, el("label", {}, "E-posta", email), el("label", {}, "Parola", password), button);
  main().replaceChildren(el("div", { class: "login-wrap" }, el("section", { class: "card login" },
    el("div", { class: "brand" }, el("span", { class: "logo", "aria-hidden": "true" }, "TB"), el("span", {}, el("strong", {}, "Taff Burger"), el("small", {}, "Aylık envanter sayımı"))),
    el("h1", {}, "Giriş"), form)));
  email.focus();
}

// Şube yoksa: yetkili kullanıcıya ilk şubeyi oluşturma formu, diğerlerine açıklama.
async function ensureBranch() {
  if (state.branch) return true;
  if (!can("branch.manage")) { main().replaceChildren(pageHead("Şube yok"), el("p", {}, "Erişebildiğiniz aktif şube yok. Yöneticinize başvurun.")); return false; }
  const name = el("input", { name: "name", required: true, maxlength: 200, placeholder: "Örn. Taff Burger Merkez" });
  main().replaceChildren(pageHead("Restoranınızı ekleyin", "Sayım şube bazında tutulur. İlk şubenizi ekleyin; daha sonra Şubeler sayfasından yenilerini ekleyebilirsiniz."),
    el("form", { class: "card row", onsubmit: async (ev) => {
      ev.preventDefault();
      const res = await api("POST", "/branches", { name: name.value });
      if (!res.ok) { message(res.error); return; }
      save("branch", res.data.id); await loadBranches(); route();
    } }, el("label", { class: "grow" }, "Şube adı", name), el("button", { type: "submit" }, "Kaydet")));
  return false;
}

// ---- router --------------------------------------------------------------------------------
let lastHash = null;
let ignoreHash = false;

async function route() {
  const hash = location.hash || "#/panel";
  if (!state.token || hash === "#/login") { renderLogin(); return; }
  if (!state.me && !(await bootstrapSession())) { logout(); return; }
  const page = routes[hash] || routes["#/panel"];
  const shown = routes[hash] ? hash : "#/panel";
  for (const a of document.querySelectorAll("#nav a")) {
    if (a.getAttribute("href") === shown) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  document.getElementById("side").classList.remove("open");
  document.getElementById("menu-toggle").setAttribute("aria-expanded", "false");
  state.guard = null;
  lastHash = hash;
  routeGen += 1;
  main().replaceChildren(el("p", { class: "empty" }, "Yükleniyor…"));
  try {
    await page();
  } catch (err) {
    if (err instanceof SessionEnded) return; // logout() giriş ekranını zaten gösterdi
    if (err instanceof StaleRoute) return; // kullanıcı bu arada başka sayfaya geçti
    message("Beklenmeyen bir hata oluştu. Sayfayı yenileyin.");
    throw err;
  }
  main().focus();
}

// Kaydedilmemiş değişiklik varsa sayfadan çıkmadan önce sorar.
function confirmLeave() {
  return !(state.guard && state.guard()) || confirm("Kaydedilmemiş değişiklikler var. Kaydetmeden çıkılsın mı?");
}

document.addEventListener("DOMContentLoaded", () => {
  state.token = load("token");
  document.getElementById("logout").addEventListener("click", async () => {
    if (!confirmLeave()) return;
    state.guard = null;
    try { await api("POST", "/auth/logout-all"); } catch (err) { if (!(err instanceof SessionEnded)) throw err; }
    logout("Çıkış yapıldı.");
  });
  document.getElementById("branch-select").addEventListener("change", (ev) => {
    if (!confirmLeave()) { ev.target.value = state.branch; return; }
    state.branch = ev.target.value; save("branch", state.branch); route();
  });
  document.getElementById("menu-toggle").addEventListener("click", (ev) => {
    const open = document.getElementById("side").classList.toggle("open");
    ev.currentTarget.setAttribute("aria-expanded", String(open));
  });
  window.addEventListener("hashchange", () => {
    if (ignoreHash) { ignoreHash = false; return; }
    if (location.hash !== "#/login" && !confirmLeave()) { ignoreHash = true; location.hash = lastHash; return; }
    showPending(); route();
  });
  window.addEventListener("beforeunload", (ev) => { if (state.guard && state.guard()) { ev.preventDefault(); ev.returnValue = ""; } });
  window.addEventListener("unhandledrejection", (ev) => { if (ev.reason instanceof SessionEnded || ev.reason instanceof StaleRoute) ev.preventDefault(); });
  // Sayfa betikleri (count.js, panel.js, admin.js) defer ile bu dosyadan sonra yüklenir.
  route();
});
