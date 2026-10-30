"use strict";
// Menü ürünleri ve reçeteler. Satılan adet × reçete = sayım formundaki "Satıştan (reçete)".

const RECIPE_UNITS = ["GR", "KG", "ML", "LT", "ADET", "PKT", "KOLİ"];

async function renderMenu() {
  const [prods, items] = await Promise.all([api("GET", "/menu-products"), api("GET", "/count-items")]);
  if (!prods.ok) { message(prods.error); return; }
  if (!items.ok) { message(items.error); return; }
  const manage = can("item.manage");
  const byGroup = new Map();
  for (const i of items.data) { if (!byGroup.has(i.group_name)) byGroup.set(i.group_name, []); byGroup.get(i.group_name).push(i); }
  const itemSelect = (selected, label) => el("select", { "aria-label": label, disabled: !manage },
    el("option", { value: "" }, "Malzeme seçin…"),
    [...byGroup].map(([g, list]) => el("optgroup", { label: g }, list.map((i) => el("option", { value: i.id, selected: i.id === selected }, `${i.name} (${i.unit})`)))));

  function productCard(p) {
    const body = el("tbody");
    const lines = [];
    function addLine(l = { item_id: "", quantity: "", unit: "GR" }) {
      const n = lines.length + 1;
      const item = itemSelect(l.item_id, `${p.name} malzeme ${n}`);
      const qty = el("input", { inputmode: "decimal", class: "qty", value: l.quantity === "" ? "" : numForInput(l.quantity), disabled: !manage, "aria-label": `${p.name} miktar ${n}` });
      const unit = el("select", { disabled: !manage, "aria-label": `${p.name} birim ${n}` }, RECIPE_UNITS.map((u) => el("option", { value: u, selected: u === l.unit }, u)));
      const remove = manage ? el("button", { type: "button", class: "danger", onclick: () => { lines.splice(lines.indexOf(rec), 1); tr.remove(); } }, "Sil") : "";
      const tr = el("tr", {}, el("td", {}, item), el("td", { class: "num" }, qty), el("td", {}, unit),
        el("td", { class: "hint" }, l.per_item_unit !== undefined && l.per_item_unit !== null ? `= ${showQty(l.per_item_unit)} ${l.item_unit} / adet` : ""), el("td", {}, remove));
      const rec = { item, qty, unit };
      lines.push(rec);
      body.append(tr);
    }
    p.recipe.forEach(addLine);
    if (!p.recipe.length && manage) addLine();
    const name = el("input", { value: p.name, maxlength: 200, disabled: !manage, "aria-label": "Menü ürünü adı" });
    const active = el("input", { type: "checkbox", checked: p.is_active, disabled: !manage, "aria-label": `${p.name} aktif` });
    const patch = async (fields) => {
      const r = await api("PATCH", `/menu-products/${p.id}`, fields);
      if (!r.ok) { message(r.error); return false; }
      Object.assign(p, r.data); message(`${p.name} güncellendi.`, "info"); return true;
    };
    name.addEventListener("change", async () => { if (!name.value.trim() || !(await patch({ name: name.value.trim() }))) name.value = p.name; });
    active.addEventListener("change", async () => { if (!(await patch({ is_active: active.checked }))) active.checked = !active.checked; });
    const saveBtn = el("button", { type: "button", onclick: async () => {
      const out = [];
      for (const l of lines) {
        if (!l.item.value && !l.qty.value.trim()) continue;
        const q = parseDecimal(l.qty.value, 4, 7);
        if (!l.item.value || !q || Number(q) <= 0) { l.qty.classList.add("invalid"); message("Her satırda malzeme ve 0'dan büyük miktar olmalı."); return; }
        l.qty.classList.remove("invalid");
        out.push({ item_id: l.item.value, quantity: q, unit: l.unit.value });
      }
      const r = await api("PUT", `/menu-products/${p.id}/recipe`, { lines: out });
      if (!r.ok) { message(r.error); return; }
      await renderMenu();
      message(`${p.name} reçetesi kaydedildi (${out.length} malzeme). Taslak aylar yeniden hesaplanır.`, "info");
    } }, "Reçeteyi kaydet");
    return el("section", { class: "card recipe-card" },
      el("div", { class: "toolbar" }, el("label", { class: "grow" }, "Menü ürünü", name), el("label", { class: "check" }, active, "Aktif (satış girişinde görünür)")),
      el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["Malzeme", "1 adet için miktar", "Birim", "", ""].map((h, i) => el("th", { scope: "col", class: i === 1 ? "num" : "" }, h)))), body)),
      manage ? el("div", { class: "toolbar" }, el("button", { type: "button", class: "secondary", onclick: () => addLine() }, "Malzeme ekle"), saveBtn) : null);
  }

  const parts = [pageHead("Menü & Reçeteler", "Her menü ürününün 1 adedinde kullanılan malzemeler. Sayım formunda ayın satış adetleri girilince, örneğin bir sos 3 farklı burgerde kullanılıyorsa toplam kullanım (adet × gram) otomatik hesaplanır ve beklenenden düşülür. Adetle sayılan ürünlerde (kova, şişe) gram yazabilmek için Ürün Listesi'nde paket içeriğini girin.")];
  if (manage) {
    const n = el("input", { name: "name", required: true, maxlength: 200, placeholder: "Örn. Klasik Burger" });
    parts.push(el("form", { class: "card row", onsubmit: async (ev) => {
      ev.preventDefault();
      const r = await api("POST", "/menu-products", { name: n.value.trim() });
      if (!r.ok) { message(r.error); return; }
      await renderMenu(); message(`${r.data.name} eklendi; şimdi reçetesini girin.`, "info");
    } }, el("label", { class: "grow" }, "Yeni menü ürünü", n), el("button", { type: "submit" }, "Ekle")));
  }
  if (!prods.data.length) parts.push(el("p", { class: "empty" }, "Henüz menü ürünü yok."));
  parts.push(...prods.data.map(productCard));
  main().replaceChildren(...parts);
}

routes["#/receteler"] = renderMenu;
