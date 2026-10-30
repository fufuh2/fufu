"use strict";
// Yönetim: kullanıcılar, şubeler, kayıt geçmişi ve hesap. app.js'teki yardımcıları kullanır.

function roleLabel(value) { return ((state.me.roles || []).find((r) => r.value === value) || {}).label || value; }
function roleSelect(current, attrs = {}) {
  return el("select", attrs, state.me.roles
    .filter((r) => r.value !== "owner" || state.me.role === "owner")
    .map((r) => el("option", { value: r.value, selected: r.value === current }, r.label)));
}
function branchChecks(selected, name) {
  return state.allBranches.map((b) => el("label", { class: "check" },
    el("input", { type: "checkbox", name, value: b.id, checked: selected.includes(b.id) }), b.name + (b.is_active ? "" : " (pasif)")));
}
const ROLE_HELP = "Şube müdürü: sayım girer, ayı kapatır/açar. Sayım personeli: yalnızca sayım girer. İzleyici: yalnızca görür. İşletme sahibi: her şey, tüm şubeler.";

async function renderUsers() {
  const r = await api("GET", "/users");
  if (!r.ok) { message(r.error); return; }
  const meId = state.me.id;
  const rows = r.data.map((u) => {
    const self = u.id === meId;
    const role = roleSelect(u.role, { "aria-label": `${u.email} rol`, disabled: self });
    const active = el("input", { type: "checkbox", checked: u.is_active, disabled: self, "aria-label": `${u.email} aktif` });
    const checks = branchChecks(u.branch_ids, `b-${u.id}`);
    const summary = el("summary", {}, u.role === "owner" ? "Tüm şubeler" : `${u.branch_ids.length} şube`);
    const branches = el("details", { class: "branches" }, summary, el("div", {}, checks,
      el("button", { type: "button", class: "secondary", onclick: async () => {
        const ids = checks.map((c) => c.querySelector("input")).filter((c) => c.checked).map((c) => c.value);
        const res = await api("PATCH", `/users/${u.id}`, { branch_ids: ids });
        if (!res.ok) { message(res.error); return; }
        message(`${u.email}: şubeler güncellendi.`, "info"); summary.textContent = `${ids.length} şube`;
      } }, "Şubeleri kaydet")));
    role.addEventListener("change", async () => {
      const res = await api("PATCH", `/users/${u.id}`, { role: role.value });
      if (!res.ok) { message(res.error); role.value = u.role; return; }
      u.role = role.value; message(`${u.email}: rol ${roleLabel(u.role)} oldu; açık oturumları kapatıldı.`, "info");
    });
    active.addEventListener("change", async () => {
      const res = await api("PATCH", `/users/${u.id}`, { is_active: active.checked });
      if (!res.ok) { message(res.error); active.checked = !active.checked; return; }
      message(`${u.email}: ${active.checked ? "aktif edildi" : "pasif edildi, giriş yapamaz"}.`, "info");
    });
    const reset = self ? el("span", { class: "hint" }, "siz") : el("button", { type: "button", class: "secondary", onclick: async () => {
      const pw = prompt(`${u.email} için yeni parola (en az 12 karakter):`);
      if (!pw) return;
      const res = await api("POST", `/users/${u.id}/password`, { new_password: pw });
      if (!res.ok) { message(res.error); return; }
      message(`${u.email}: parola değişti. Yeni parolayı kişiye güvenli bir yoldan iletin.`, "info");
    } }, "Parola sıfırla");
    return el("tr", {}, el("td", {}, u.email), el("td", {}, role), el("td", {}, u.role === "owner" ? "Tüm şubeler" : branches), el("td", {}, active), el("td", {}, reset));
  });

  const email = el("input", { type: "email", name: "email", required: true, autocomplete: "off" });
  const pw = el("input", { type: "password", name: "password", required: true, minlength: 12, autocomplete: "new-password" });
  const role = roleSelect("counter", { name: "role", "aria-label": "Yeni kullanıcı rolü" });
  const checks = branchChecks(state.branch ? [state.branch] : [], "new-branch");
  const form = el("form", { class: "card", onsubmit: async (ev) => {
    ev.preventDefault();
    const ids = checks.map((c) => c.querySelector("input")).filter((c) => c.checked).map((c) => c.value);
    const res = await api("POST", "/users", { email: email.value, password: pw.value, role: role.value, branch_ids: ids });
    if (!res.ok) { message(res.error); return; }
    message(`${res.data.email} eklendi.`, "info"); renderUsers();
  } }, el("h2", {}, "Yeni kullanıcı"),
    el("div", { class: "toolbar" }, el("label", { class: "grow" }, "E-posta", email), el("label", {}, "İlk parola (en az 12 karakter)", pw), el("label", {}, "Rol", role)),
    el("fieldset", { class: "toolbar" }, el("legend", {}, "Şubeler (işletme sahibi için gerekmez)"), checks),
    el("button", { type: "submit" }, "Kullanıcı ekle"));
  main().replaceChildren(pageHead("Kullanıcılar", ROLE_HELP), form,
    el("section", { class: "card" }, table([["E-posta"], ["Rol"], ["Şubeler"], ["Aktif"], [""]], rows, "Kullanıcı yok.")));
}

async function renderBranches() {
  const rows = state.allBranches.map((b) => {
    const name = el("input", { value: b.name, maxlength: 200, "aria-label": `${b.name} adı` });
    const active = el("input", { type: "checkbox", checked: b.is_active, "aria-label": `${b.name} aktif` });
    const patch = async (fields) => {
      const res = await api("PATCH", `/branches/${b.id}`, fields);
      if (!res.ok) { message(res.error); return false; }
      message(`${res.data.name} güncellendi.`, "info"); await loadBranches(); return true;
    };
    name.addEventListener("change", async () => { if (!name.value.trim() || !(await patch({ name: name.value.trim() }))) name.value = b.name; });
    active.addEventListener("change", async () => { if (!(await patch({ is_active: active.checked }))) active.checked = !active.checked; });
    return [[name], [active]];
  });
  const name = el("input", { name: "name", required: true, maxlength: 200 });
  main().replaceChildren(pageHead("Şubeler", "Her şubenin sayımı ayrı tutulur; ürün listesi ve fiyatlar tüm şubelerde ortaktır. Pasif şubede yeni sayım yapılamaz; geçmişi okunabilir."),
    el("form", { class: "card row", onsubmit: async (ev) => {
      ev.preventDefault();
      const res = await api("POST", "/branches", { name: name.value.trim() });
      if (!res.ok) { message(res.error); return; }
      message(`${res.data.name} eklendi.`, "info"); await loadBranches(); renderBranches();
    } }, el("label", { class: "grow" }, "Yeni şube adı", name), el("button", { type: "submit" }, "Şube ekle")),
    el("section", { class: "card" }, table([["Şube adı"], ["Aktif"]], rows, "Şube yok.")));
}

const ACTION_LABEL = {
  "auth.login": "Giriş yaptı", "auth.login_failed": "Hatalı giriş denemesi", "auth.password_change": "Parolasını değiştirdi", "auth.logout_all": "Tüm oturumları kapattı",
  "branch.create": "Şube ekledi", "branch.update": "Şubeyi güncelledi", "user.create": "Kullanıcı ekledi", "user.update": "Kullanıcıyı güncelledi", "user.password_reset": "Kullanıcı parolasını sıfırladı",
  "count_item.create": "Ürün ekledi", "count_item.update": "Ürünü güncelledi", "count_item.load_defaults": "Sayım formu ürünlerini yükledi",
  "monthly_count.open": "Ay sayımını başlattı", "monthly_count.save": "Sayımı kaydetti", "monthly_count.close": "Ayı kapattı", "monthly_count.reopen": "Ayı yeniden açtı",
  "tenant.bootstrap": "İşletme kuruldu",
};
function auditDetail(e) {
  const d = e.data || {};
  const parts = [];
  if (d.branch_id) parts.push(branchName(d.branch_id) || "şube");
  if (d.period) parts.push(periodLabel(d.period));
  if (d.lines !== undefined) parts.push(`${d.lines} ürün`);
  if (d.reason) parts.push(`neden: ${d.reason}`);
  if (d.name) parts.push(d.name);
  if (d.added !== undefined) parts.push(`${d.added} ürün eklendi`);
  if (d.after && typeof d.after === "object") parts.push(Object.entries(d.after).map(([k, v]) => `${k}: ${v}`).join(", "));
  if (d.role) parts.push(roleLabel(d.role));
  if (d.email) parts.push(d.email);
  return parts.join(" · ") || "—";
}

async function renderAudit() {
  const PAGE = 50;
  const filter = el("select", { "aria-label": "İşlem türü" },
    [["", "Tüm işlemler"], ["monthly_count.", "Sayım"], ["count_item.", "Ürün listesi"], ["user.", "Kullanıcılar"], ["branch.", "Şubeler"], ["auth.", "Giriş / oturum"]]
      .map(([v, t]) => el("option", { value: v }, t)));
  const body = el("tbody");
  const more = el("button", { type: "button", class: "secondary" }, "Daha fazla");
  let offset = 0;
  async function loadPage(reset) {
    if (reset) { offset = 0; body.replaceChildren(); }
    const q = `limit=${PAGE}&offset=${offset}${filter.value ? `&action=${encodeURIComponent(filter.value)}` : ""}`;
    const r = await api("GET", `/audit-events?${q}`);
    if (!r.ok) { message(r.error); return; }
    for (const e of r.data) {
      body.append(el("tr", {}, el("td", {}, fmtDate(e.at)), el("td", {}, e.user_email || "—"),
        el("td", {}, ACTION_LABEL[e.action] || e.action), el("td", { class: "wrap" }, auditDetail(e))));
    }
    if (!body.children.length) body.append(el("tr", {}, el("td", { colspan: 4, class: "empty" }, "Kayıt yok.")));
    offset += r.data.length;
    more.hidden = r.data.length < PAGE;
  }
  filter.addEventListener("change", () => loadPage(true));
  more.addEventListener("click", () => loadPage(false));
  main().replaceChildren(pageHead("Kayıt Geçmişi", "Kim, ne zaman, ne yaptı. Kayıtlar değiştirilemez ve silinemez.", el("label", {}, "Göster", filter)),
    el("section", { class: "card" }, el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Zaman", "Kullanıcı", "İşlem", "Ayrıntı"].map((h) => el("th", { scope: "col" }, h)))), body)), more));
  await loadPage(true);
}

async function renderAccount() {
  const current = el("input", { type: "password", name: "current", required: true, autocomplete: "current-password" });
  const next = el("input", { type: "password", name: "new", required: true, minlength: 12, autocomplete: "new-password" });
  const again = el("input", { type: "password", name: "again", required: true, minlength: 12, autocomplete: "new-password" });
  const form = el("form", { class: "card login", onsubmit: async (ev) => {
    ev.preventDefault();
    if (next.value !== again.value) { message("Yeni parolalar aynı değil."); return; }
    const res = await api("POST", "/auth/password", { current_password: current.value, new_password: next.value });
    if (!res.ok) { message(res.status === 401 ? "Mevcut parola hatalı." : res.error); return; }
    // Değişiklik bu oturum dahil tüm token'ları iptal etti.
    logout("Parolanız değişti. Yeni parolanızla tekrar giriş yapın.");
  } },
    el("h2", {}, "Parola değiştir"),
    el("label", {}, "Mevcut parola", current),
    el("label", {}, "Yeni parola (en az 12 karakter)", next),
    el("label", {}, "Yeni parola (tekrar)", again),
    el("button", { type: "submit" }, "Parolayı değiştir"));
  const branches = state.me.branch_ids === null ? "Tüm şubeler" : state.allBranches.filter((b) => state.me.branch_ids.includes(b.id)).map((b) => b.name).join(", ") || "—";
  main().replaceChildren(pageHead("Hesabım"),
    el("div", { class: "grid" }, kpi("E-posta", state.me.email), kpi("Rol", state.me.role_label), kpi("Şubeler", branches)),
    form);
}

routes["#/kullanicilar"] = renderUsers;
routes["#/subeler"] = renderBranches;
routes["#/kayitlar"] = renderAudit;
routes["#/hesap"] = renderAccount;
