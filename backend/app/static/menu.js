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
      const qty = el("input", { inputmode: "decimal", class: "qty", value: l.quantity === "" ? "" : numForInput4(l.quantity), disabled: !manage, "aria-label": `${p.name} miktar ${n}` });
      const unit = el("select", { disabled: !manage, "aria-label": `${p.name} birim ${n}` }, RECIPE_UNITS.map((u) => el("option", { value: u, selected: u === l.unit }, u)));
      const remove = manage ? el("button", { type: "button", class: "danger", onclick: () => { lines.splice(lines.indexOf(rec), 1); tr.remove(); } }, "Sil") : "";
      const tr = el("tr", {}, el("td", {}, item), el("td", { class: "num" }, qty), el("td", {}, unit),
        el("td", { class: "num" }, l.cost === undefined ? "" : showMoney(l.cost)),
        el("td", { class: l.per_item_unit === null ? "warn-cell" : "hint" },
          l.per_item_unit === undefined ? "" : l.per_item_unit === null
            ? `düşülmüyor: ${l.item_unit} ↔ ${l.unit} çevrimi yok — Ürün Listesi'nde paket içeriği girin (1 ${l.item_unit} = ? ${l.unit === "ADET" ? "ADET" : l.unit})`
            : `= ${showQty(l.per_item_unit)} ${l.item_unit} / adet`), el("td", {}, remove));
      const rec = { item, qty, unit };
      lines.push(rec);
      body.append(tr);
    }
    p.recipe.forEach(addLine);
    if (!p.recipe.length && manage) addLine();
    const name = el("input", { value: p.name, maxlength: 200, disabled: !manage, "aria-label": "Menü ürünü adı" });
    const price = el("input", { inputmode: "decimal", class: "qty", value: p.price === null ? "" : numForInput(p.price), disabled: !manage, "aria-label": `${p.name} satış fiyatı` });
    price.addEventListener("change", async () => {
      const v = parseDecimal(price.value, 2, 7);
      price.classList.toggle("invalid", v === undefined);
      if (v !== undefined && (await patch({ price: v }))) renderMenu();
    });
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
      el("div", { class: "toolbar" }, el("label", { class: "grow" }, "Menü ürünü", name), el("label", {}, "Satış fiyatı ₺", price),
        el("div", { class: "kpi" }, el("div", { class: "l" }, "Malzeme maliyeti"), el("div", { class: "v" }, showMoney(p.cost)),
          el("div", { class: "s" }, p.food_cost_pct === null ? "fiyat yok" : `%${showQty(p.food_cost_pct)} maliyet oranı${p.cost_complete ? "" : " (eksik satır var, en az)"}`)),
        el("label", { class: "check" }, active, "Aktif (satış girişinde görünür)")),
      el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["Malzeme", "1 adet için miktar", "Birim", "Maliyet ₺", "", ""].map((h, i) => el("th", { scope: "col", class: i === 1 || i === 3 ? "num" : "" }, h)))), body)),
      manage ? el("div", { class: "toolbar" }, el("button", { type: "button", class: "secondary", onclick: () => addLine() }, "Malzeme ekle"), saveBtn) : null);
  }

  async function loadDefaults(addMissing) {
    const r = await api("POST", `/menu-products/load-defaults${addMissing ? "?add_missing_items=true" : ""}`);
    if (!r.ok) { message(r.error); return; }
    await renderMenu();
    message(`${r.data.added_products.length} menü ürünü, ${r.data.added_lines} reçete satırı${r.data.added_items.length ? `, ${r.data.added_items.length} yeni malzeme` : ""} eklendi.`, "info");
    showReport(r.data);
  }
  function showReport(d) {
    const box = document.getElementById("menu-report");
    if (!box) return;
    const parts = [el("h2", {}, "Yükleme raporu")];
    if (d.missing_items.length) {
      parts.push(el("p", {}, el("strong", {}, `Sayım listesinde olmayan ${d.missing_items.length} malzeme`), " — bu satırlar reçeteye eklenmedi:"),
        table([["Malzeme"], ["Kullanıldığı ürünler"]], d.missing_items.map((m) => [[m.name], [m.products.join(", "), "wrap"]])),
        el("p", {}, el("button", { type: "button", onclick: () => loadDefaults(true) }, `Bu ${d.missing_items.length} malzemeyi Ürün Listesi'ne ekle ve reçetelere bağla`)));
    }
    if (d.unconverted.length) {
      parts.push(el("p", {}, el("strong", {}, `Çevrimi eksik ${d.unconverted.length} malzeme`), " — Ürün Listesi'nde paket içeriği girilene kadar satıştan düşülmez:"),
        table([["Malzeme"], ["Sayım birimi"], ["Reçete birimi"], ["Girilmesi gereken"]], d.unconverted.map((w) => [[w.item_name], [w.item_unit], [w.recipe_units.join(", ")],
          [`1 ${w.item_unit} = ? ${w.recipe_units.includes("ADET") ? "ADET (örn. dilim/adet sayısı)" : "KG veya LT"}`, "wrap"]])));
    }
    parts.push(el("h2", {}, "Varsayımlar (kontrol edin)"), el("ul", {}, d.notes.map((n) => el("li", {}, n))));
    box.replaceChildren(el("section", { class: "card" }, parts));
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
  if (manage) {
    parts.push(el("div", { class: "card toolbar" }, el("span", { class: "grow" }, "Menü panolarındaki 19 burger (et, tavuk, light) ve içerikleri hazır. Var olan ürünler ve sizin girdiğiniz satırlar değiştirilmez."),
      el("button", { type: "button", class: "secondary", onclick: () => loadDefaults(false) }, "Taff Burger menüsünü yükle")));
  }
  parts.push(el("div", { id: "menu-report" }));
  if (prods.data.length) {
    const rows = [...prods.data].sort((a, b) => Number(b.food_cost_pct || 0) - Number(a.food_cost_pct || 0)).map((p) => {
      const missing = p.recipe.filter((l) => l.cost === null).map((l) => l.item_name);
      return [[p.name], [showMoney(p.price), "num"], [showMoney(p.cost), "num"],
        [p.food_cost_pct === null ? "—" : `%${showQty(p.food_cost_pct)}`, `num strong ${Number(p.food_cost_pct) > 35 ? "neg" : ""}`],
        [p.price !== null && p.cost !== null ? showMoney(Number(p.price) - Number(p.cost)) : "—", "num"],
        [missing.length ? `${missing.length}: ${missing.join(", ")}` : "tam", missing.length ? "wrap warn-cell" : "pos"]];
    });
    parts.push(el("section", { class: "card" }, el("h2", {}, "Maliyet özeti"),
      el("p", { class: "hint" }, "Malzeme maliyeti = reçete × Ürün Listesi'ndeki birim fiyat. Fiyatı veya çevrimi eksik satırlar toplama girmez; o ürünlerde oran gerçekte daha yüksektir. Satış fiyatı menü panosundan, KDV dahil varsayıldı; tedarikçi fiyatlarının KDV durumu bilinmiyor."),
      table([["Ürün"], ["Satış ₺", "num"], ["Maliyet ₺", "num"], ["Oran", "num"], ["Brüt kâr ₺", "num"], ["Maliyeti eksik malzeme"]], rows)));
  }
  if (!prods.data.length) parts.push(el("p", { class: "empty" }, "Henüz menü ürünü yok."));
  parts.push(...prods.data.map(productCard));
  main().replaceChildren(...parts);
}

routes["#/receteler"] = renderMenu;
