"use strict";
// Panel (özet, trend, en çok eksik/zayi) ve ürün analizi. app.js'teki yardımcıları kullanır.

// Sıfır çizgisinin iki yanına çubuk grafik. points: [{ label, value (sayı ya da null), cls, title }]
// W: viewBox genişliği; kartın gerçek genişliğine yakın seçilir ki yazılar ölçeklenip büyümesin.
const compact = new Intl.NumberFormat("tr-TR", { notation: "compact", maximumFractionDigits: 1 });
function barChart(points, { width = 640, height = 190, unit = "" } = {}) {
  const W = width, padL = 8, padR = 8, padT = 18, padB = 24;
  const vals = points.map((p) => p.value).filter((v) => v !== null);
  const max = Math.max(0, ...vals), min = Math.min(0, ...vals);
  const span = max - min || 1;
  const plotH = height - padT - padB;
  const y = (v) => padT + ((max - v) / span) * plotH;
  const slot = (W - padL - padR) / Math.max(points.length, 1);
  const bw = Math.min(42, slot * 0.6);
  const zero = y(0);
  const g = svg("svg", { viewBox: `0 0 ${W} ${height}`, class: "chart", role: "img" });
  g.append(svg("line", { x1: padL, x2: W - padR, y1: zero, y2: zero, class: "zero" }));
  points.forEach((p, i) => {
    const x = padL + slot * i + (slot - bw) / 2;
    const v = p.value === null ? 0 : p.value;
    const top = Math.min(y(v), zero), h = Math.max(Math.abs(y(v) - zero), p.value === null ? 0 : 1);
    g.append(svg("rect", { x, y: top, width: bw, height: h, rx: 2, class: p.value === null ? "bar-none" : p.cls }, svg("title", {}, p.title)));
    if (p.value !== null && p.value !== 0) {
      const ly = v < 0 ? Math.min(top + h + 12, height - padB - 2) : Math.max(top - 4, 11);
      g.append(svg("text", { x: x + bw / 2, y: ly, "text-anchor": "middle", class: "val" }, `${compact.format(p.value)}${unit}`));
    }
    g.append(svg("text", { x: x + bw / 2, y: height - 6, "text-anchor": "middle" }, p.label));
  });
  return g;
}

function signClass(v) { return Number(v) < 0 ? "neg" : Number(v) > 0 ? "pos" : ""; }

async function renderPanel() {
  if (!(await ensureBranch())) return;
  const period = getPeriod();
  const [dash, overview] = await Promise.all([
    api("GET", `/branches/${state.branch}/dashboard?period=${period}`),
    state.branches.length > 1 ? api("GET", `/monthly-counts/overview?period=${period}`) : Promise.resolve(null),
  ]);
  if (!dash.ok) { message(dash.error); return; }
  const d = dash.data;
  const head = pageHead(`Panel — ${periodLabel(period)}`, `${branchName()} · Seçili ayın sayım durumu ve son 12 ayın eğilimi.`,
    d.current ? statusBadge(d.current.status) : statusBadge(null),
    monthInput(period, (p) => { setPeriod(p); renderPanel(); }));
  const parts = [head];

  if (d.current) {
    const t = d.current.totals;
    const progress = el("progress", { max: t.items || 1, value: t.counted, "aria-label": "Sayım ilerlemesi" });
    parts.push(el("div", { class: "grid" },
      el("div", { class: "card kpi" }, el("div", { class: "l" }, "Sayım ilerlemesi"), el("div", { class: "v" }, `${t.counted} / ${t.items}`), progress),
      kpi("Eksik", showMoney(t.eksik_value), `${t.eksik} ürün`, "eksik"),
      kpi("Fazla", showMoney(t.fazla_value), `${t.fazla} ürün`, "fazla"),
      kpi("Zayi", showMoney(t.waste_value), `${t.waste_lines} ürün`, "zayi"),
      kpi("Net fark", showMoney(t.net_difference_value), `${t.priced_items}/${t.items} üründe fiyat var`, signClass(t.net_difference_value) === "neg" ? "eksik" : ""),
      kpi("Sayılan stok değeri", showMoney(t.stock_value), "sayılan × birim fiyat")));
  } else {
    parts.push(el("div", { class: "card" }, el("p", {}, `${periodLabel(period)} için sayım henüz başlatılmadı.`),
      can("count.edit") ? el("button", { type: "button", onclick: () => { location.hash = "#/sayim"; } }, "Sayımı başlat") : null));
  }

  if (d.trend.length) {
    const net = d.trend.map((m) => {
      const v = m.totals.priced_items ? Number(m.totals.net_difference_value) : null;
      return { label: periodShort(m.period), value: v, cls: v < 0 ? "bar-neg" : "bar-pos", title: `${periodLabel(m.period)}: net fark ${showMoney(v)}` };
    });
    const waste = d.trend.map((m) => {
      const v = m.totals.priced_items ? Number(m.totals.waste_value) : null;
      return { label: periodShort(m.period), value: v, cls: "bar-waste", title: `${periodLabel(m.period)}: zayi ${showMoney(v)}` };
    });
    parts.push(el("div", { class: "cols" },
      el("section", { class: "card", "aria-label": "Net fark eğilimi" }, el("h2", {}, "Net fark (₺) — son aylar"), barChart(net),
        el("div", { class: "legend" }, el("span", { class: "neg" }, "eksik ağırlıklı"), el("span", { class: "pos" }, "fazla ağırlıklı"), el("span", {}, "fiyat yok"))),
      el("section", { class: "card", "aria-label": "Zayi eğilimi" }, el("h2", {}, "Zayi (₺) — son aylar"), barChart(waste),
        el("div", { class: "legend" }, el("span", { class: "waste" }, "zayi tutarı")))));
  }

  const focus = d.focus_period ? periodLabel(d.focus_period) : null;
  const lineRow = (ln, qtyKey, valKey) => {
    const tr = el("tr", { class: "clickable", title: "Ürün analizini aç" },
      el("td", { class: "wrap" }, ln.name), el("td", {}, ln.group),
      el("td", { class: `num ${qtyKey === "difference" ? "neg" : ""}` }, `${qtyKey === "difference" ? signedQty(ln[qtyKey]) : showQty(ln[qtyKey])} ${ln.unit}`),
      el("td", { class: "num strong" }, showMoney(ln[valKey])));
    tr.addEventListener("click", () => { save("analysis_item", ln.item_id); location.hash = "#/analiz"; });
    return tr;
  };
  parts.push(el("div", { class: "cols" },
    el("section", { class: "card" }, el("h2", {}, `En çok eksik veren ürünler${focus ? ` · ${focus}` : ""}`),
      table([["Ürün"], ["Grup"], ["Fark", "num"], ["Fark ₺", "num"]], d.top_missing.map((ln) => lineRow(ln, "difference", "difference_value")), "Eksik ürün yok.")),
    el("section", { class: "card" }, el("h2", {}, `En çok zayi olan ürünler${focus ? ` · ${focus}` : ""}`),
      table([["Ürün"], ["Grup"], ["Zayi", "num"], ["Zayi ₺", "num"]], d.top_waste.map((ln) => lineRow(ln, "waste", "waste_value")), "Zayi kaydı yok."))));

  if (d.groups.length) {
    parts.push(el("section", { class: "card" }, el("h2", {}, `Gruplara göre${focus ? ` · ${focus}` : ""}`),
      table([["Grup"], ["Sayılan", "num"], ["Eksik", "num"], ["Fazla", "num"], ["Fark ₺", "num"], ["Zayi ₺", "num"]],
        d.groups.map((g) => [[g.name], [`${g.counted} / ${g.items}`, "num"], [String(g.eksik), "num"], [String(g.fazla), "num"],
          [showMoney(g.difference_value), `num strong ${signClass(g.difference_value)}`], [showMoney(g.waste_value), "num"]]))));
  }

  if (overview && overview.ok && overview.data.length > 1) {
    parts.push(el("section", { class: "card" }, el("h2", {}, `Şubeler · ${periodLabel(period)}`),
      table([["Şube"], ["Durum"], ["Sayımı yapan"], ["Sayılan", "num"], ["Eksik ₺", "num"], ["Zayi ₺", "num"], ["Net fark ₺", "num"]],
        overview.data.map((o) => {
          const t = o.totals;
          const tr = el("tr", { class: "clickable", title: "Bu şubeye geç" },
            el("td", {}, el("strong", {}, o.branch_name)), el("td", {}, statusBadge(o.status === "yok" ? null : o.status)), el("td", {}, o.counted_by_name || "—"),
            el("td", { class: "num" }, t ? `${t.counted} / ${t.items}` : "—"), el("td", { class: "num neg" }, t ? showMoney(t.eksik_value) : "—"),
            el("td", { class: "num" }, t ? showMoney(t.waste_value) : "—"),
            el("td", { class: `num strong ${t ? signClass(t.net_difference_value) : ""}` }, t ? showMoney(t.net_difference_value) : "—"));
          tr.addEventListener("click", () => {
            state.branch = o.branch_id; save("branch", o.branch_id);
            document.getElementById("branch-select").value = o.branch_id; renderPanel();
          });
          return tr;
        }))));
  }
  main().replaceChildren(...parts);
}

async function renderAnalysis() {
  if (!(await ensureBranch())) return;
  const items = await api("GET", "/count-items?include_inactive=true");
  if (!items.ok) { message(items.error); return; }
  if (!items.data.length) { main().replaceChildren(pageHead("Ürün Analizi"), el("p", { class: "empty" }, "Ürün listesi boş.")); return; }
  const byGroup = new Map();
  for (const i of items.data) { if (!byGroup.has(i.group_name)) byGroup.set(i.group_name, []); byGroup.get(i.group_name).push(i); }
  const saved = load("analysis_item");
  const chosen = items.data.find((i) => i.id === saved) ? saved : items.data[0].id;
  const pick = el("select", { "aria-label": "Ürün" }, [...byGroup].map(([g, list]) => el("optgroup", { label: g },
    list.map((i) => el("option", { value: i.id, selected: i.id === chosen }, `${i.name}${i.is_active ? "" : " (pasif)"}`)))));
  const out = el("div");
  pick.addEventListener("change", () => { save("analysis_item", pick.value); draw(); });
  main().replaceChildren(pageHead("Ürün Analizi", `${branchName()} · Bir ürünün ay ay devir, gelen, satılan, zayi ve sayım farkı.`, el("label", {}, "Ürün", pick)), out);

  async function draw() {
    const r = await api("GET", `/branches/${state.branch}/count-items/${pick.value}/history`);
    if (!r.ok) { out.replaceChildren(el("p", {}, r.error)); return; }
    const { item, months } = r.data;
    const u = item.unit;
    if (!months.length) { out.replaceChildren(el("div", { class: "card" }, el("p", { class: "empty" }, "Bu ürün için henüz sayım yok."))); return; }
    const sum = (k) => months.reduce((s, m) => s + (m[k] === null ? 0 : Number(m[k])), 0);
    const counted = months.filter((m) => m.counted !== null);
    const eksikMonths = counted.filter((m) => m.difference_status === "eksik").length;
    const totalDiff = counted.reduce((s, m) => s + Number(m.difference), 0);
    const anyValue = months.some((m) => m.difference_value !== null);
    out.replaceChildren(
      el("div", { class: "grid" },
        kpi("Toplam gelen", `${showQty(sum("incoming"))} ${u}`, `${months.length} ay`),
        kpi("Toplam satılan / kullanılan", `${showQty(sum("used"))} ${u}`),
        kpi("Toplam zayi", `${showQty(sum("waste"))} ${u}`, anyValue ? showMoney(sum("waste_value")) : "fiyat yok", "zayi"),
        kpi("Toplam fark", `${signedQty(totalDiff)} ${u}`, anyValue ? showMoney(sum("difference_value")) : "fiyat yok", totalDiff < 0 ? "eksik" : totalDiff > 0 ? "fazla" : ""),
        kpi("Eksik çıkan ay", `${eksikMonths} / ${counted.length}`, "sayılan aylar içinde")),
      el("section", { class: "card" }, el("h2", {}, `Sayım farkı (${u}) — aylara göre`),
        barChart(months.map((m) => ({ label: periodShort(m.period), value: m.difference === null ? null : Number(m.difference),
          cls: Number(m.difference) < 0 ? "bar-neg" : "bar-pos", title: `${periodLabel(m.period)}: ${signedQty(m.difference)} ${u}` })), { width: 1200, height: 230 }),
        el("div", { class: "legend" }, el("span", { class: "neg" }, "eksik"), el("span", { class: "pos" }, "fazla"), el("span", {}, "sayılmadı"))),
      el("section", { class: "card" }, el("h2", {}, item.name),
        table([["Ay"], ["Durum"], ["Devir", "num"], ["Gelen", "num"], ["Satılan", "num"], ["Zayi", "num"], ["Beklenen", "num"], ["Sayılan", "num"], ["Fark", "num"], ["Fark ₺", "num"], ["Zayi ₺", "num"]],
          [...months].reverse().map((m) => [[periodLabel(m.period)], [statusBadge(m.status)], [showQty(m.opening), "num"], [showQty(m.incoming), "num"], [showQty(m.used), "num"],
            [showQty(m.waste), "num"], [showQty(m.expected), "num"], [showQty(m.counted), "num"], [signedQty(m.difference), `num strong ${m.difference_status}`],
            [showMoney(m.difference_value), "num"], [showMoney(m.waste_value), "num"]]))));
  }
  await draw();
}

routes["#/panel"] = renderPanel;
routes["#/analiz"] = renderAnalysis;
