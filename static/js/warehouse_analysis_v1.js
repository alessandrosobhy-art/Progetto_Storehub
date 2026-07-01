(function () {
  "use strict";

  const cfg = window.WH_ANALYSIS || {};
  const apiUrl = cfg.apiUrl || "/magazzino/api/analysis/summary";
  const I18N = cfg.i18n || {};
  const localeMap = { it: "it-IT", en: "en-US", fr: "fr-FR", es: "es-ES" };
  const locale = localeMap[String(cfg.locale || "it").slice(0, 2)] || "it-IT";

  function t(key, fallback) {
    return I18N[key] || fallback;
  }

  const modeSel = document.getElementById("anaMode");
  const boxWeek = document.getElementById("anaModeWeek");
  const boxMonth = document.getElementById("anaModeMonth");
  const boxPeriod = document.getElementById("anaModePeriod");
  const weekAnchor = document.getElementById("weekAnchor");
  const weekRangeLabel = document.getElementById("weekRangeLabel");
  const monthPicker = document.getElementById("monthPicker");
  const monthRangeLabel = document.getElementById("monthRangeLabel");
  const periodStart = document.getElementById("periodStart");
  const periodEnd = document.getElementById("periodEnd");

  const btnApplyWeek = document.getElementById("applyRange");
  const btnApplyMonth = document.getElementById("applyRange2");
  const btnApplyPeriod = document.getElementById("applyRange3");
  const btnWeekPrev = document.getElementById("weekPrev");
  const btnWeekNext = document.getElementById("weekNext");
  const btnMonthPrev = document.getElementById("monthPrev");
  const btnMonthNext = document.getElementById("monthNext");

  const summaryPeriodLabel = document.getElementById("summaryPeriodLabel");
  const summaryRevenues = document.getElementById("summaryRevenues");
  const summaryTableWrap = document.getElementById("summaryTableWrap");
  const summaryError = document.getElementById("summaryError");
  const summaryWarnings = document.getElementById("summaryWarnings");

  const supplierSelect = document.getElementById("supplierSelect");
  const supplierPeriodLabel = document.getElementById("supplierPeriodLabel");
  const supplierTableWrap = document.getElementById("supplierTableWrap");
  const supplierError = document.getElementById("supplierError");
  const supplierWarnings = document.getElementById("supplierWarnings");

  if (!modeSel || !summaryTableWrap) return;

  const moneyFmt = new Intl.NumberFormat(locale, { style: "currency", currency: "EUR" });
  const pctFmt = new Intl.NumberFormat(locale, { maximumFractionDigits: 2, minimumFractionDigits: 2 });

  function toISODate(d) {
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return `${yyyy}-${mm}-${dd}`;
  }

  function fromISODate(iso) {
    if (!iso) return null;
    const s = String(iso).slice(0, 10);
    const parts = s.split("-");
    if (parts.length !== 3) return null;
    const y = parseInt(parts[0], 10);
    const m = parseInt(parts[1], 10);
    const d = parseInt(parts[2], 10);
    if (!y || !m || !d) return null;
    return new Date(y, m - 1, d);
  }

  function fmtDate(iso) {
    const d = fromISODate(iso);
    if (!d) return "-";
    return new Intl.DateTimeFormat(locale).format(d);
  }

  function startOfWeek(d) {
    const dd = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const day = (dd.getDay() + 6) % 7;
    dd.setDate(dd.getDate() - day);
    return dd;
  }

  function endOfWeek(d) {
    const s = startOfWeek(d);
    const e = new Date(s);
    e.setDate(e.getDate() + 6);
    return e;
  }

  function startOfMonth(d) {
    return new Date(d.getFullYear(), d.getMonth(), 1);
  }

  function endOfMonth(d) {
    return new Date(d.getFullYear(), d.getMonth() + 1, 0);
  }

  function setVisible(el, visible) {
    if (!el) return;
    el.classList.toggle("d-none", !visible);
  }

  let currentStart = null;
  let currentEnd = null;

  function setError(el, msg) {
    if (!el) return;
    if (!msg) {
      el.classList.add("d-none");
      el.textContent = "";
      return;
    }
    el.textContent = msg;
    el.classList.remove("d-none");
  }

  function setWarnings(el, warnings) {
    if (!el) return;
    const list = (warnings || []).map(w => String(w || "").trim()).filter(Boolean);
    const uniq = Array.from(new Set(list));
    if (!uniq.length) {
      el.classList.add("d-none");
      el.textContent = "";
      return;
    }
    el.textContent = uniq.join(" - ");
    el.classList.remove("d-none");
  }

  function formatValue(v, type) {
    const num = (typeof v === "number" && isFinite(v)) ? v : 0;
    if (type === "pct") {
      return `${pctFmt.format(num)}%`;
    }
    return moneyFmt.format(num);
  }

  function renderTable(container, data) {
    if (!container) return;
    const buckets = (data && data.buckets) ? data.buckets : {};
    const fp = buckets.FoodPaper || {};
    const op = buckets.Operating || {};
    const invInitDate = data && data.period ? data.period.inv_initial_date : null;
    const endDate = data && data.period ? data.period.end : null;

    const rows = [
      { label: t("initialInventory", "Inventario iniziale"), hint: invInitDate ? `INV ${fmtDate(invInitDate)}` : "", key: "inv_initial", type: "money" },
      { label: t("delivery", "Delivery"), hint: "", key: "delivery", type: "money" },
      { label: t("transfersIn", "Trasferimenti In"), hint: "", key: "tx_in", type: "money" },
      { label: t("transfersOut", "Trasferimenti Out"), hint: "", key: "tx_out", type: "money" },
      { label: t("finalInventory", "Inventario finale"), hint: endDate ? `INV ${fmtDate(endDate)}` : "", key: "inv_final", type: "money" },
      { label: t("rawWaste", "Waste (crudo)"), hint: "", key: "waste", type: "money" },
      { label: t("consumption", "Consumo"), hint: "", key: "consumption", type: "money", strong: true },
      { label: t("consumptionPct", "Consumo %"), hint: "", key: "consumption_pct", type: "pct" },
      { label: t("wastePct", "Waste %"), hint: "", key: "waste_pct", type: "pct" },
    ];

    const table = document.createElement("table");
    table.className = "table table-sm table-hover align-middle table-kpi";

    const thead = document.createElement("thead");
    thead.innerHTML = `
      <tr>
        <th style="width: 50%">${t("item", "Voce")}</th>
        <th class="text-end">${t("foodpaper", "FoodPaper")}</th>
        <th class="text-end">${t("operating", "Operating")}</th>
      </tr>
    `;
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    rows.forEach(r => {
      const tr = document.createElement("tr");
      if (r.strong) tr.classList.add("kpi-row-total");

      const td0 = document.createElement("td");
      td0.innerHTML = `<div class="${r.strong ? "kpi-strong" : ""}">${r.label}</div>` +
        (r.hint ? `<div class="small kpi-muted">${r.hint}</div>` : "");

      const td1 = document.createElement("td");
      td1.className = "value";
      td1.textContent = formatValue(fp[r.key], r.type);

      const td2 = document.createElement("td");
      td2.className = "value";
      td2.textContent = formatValue(op[r.key], r.type);

      tr.appendChild(td0);
      tr.appendChild(td1);
      tr.appendChild(td2);
      tbody.appendChild(tr);
    });

    table.appendChild(tbody);
    container.innerHTML = "";
    container.appendChild(table);
  }

  async function fetchSummary(startIso, endIso, supplier) {
    const u = new URL(apiUrl, window.location.origin);
    u.searchParams.set("start", startIso);
    u.searchParams.set("end", endIso);
    if (supplier) u.searchParams.set("supplier", supplier);

    const res = await fetch(u.toString(), { credentials: "same-origin" });
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (_) {
      data = null;
    }
    if (!res.ok) {
      const msg = (data && data.error) ? data.error : (`HTTP ${res.status}`);
      throw new Error(msg);
    }
    return data;
  }

  function updatePeriodLabels(startIso, endIso) {
    const label = `${fmtDate(startIso)} -> ${fmtDate(endIso)}`;
    if (summaryPeriodLabel) summaryPeriodLabel.textContent = label;
    if (supplierPeriodLabel) supplierPeriodLabel.textContent = label;
  }

  async function refreshAll() {
    if (!currentStart || !currentEnd) return;
    const startIso = toISODate(currentStart);
    const endIso = toISODate(currentEnd);
    updatePeriodLabels(startIso, endIso);

    setError(summaryError, "");
    setWarnings(summaryWarnings, []);
    summaryTableWrap.innerHTML = `<div class="text-muted small">${t("loading", "Caricamento...")}</div>`;
    summaryRevenues.textContent = "-";

    try {
      const data = await fetchSummary(startIso, endIso, "");
      const rev = (data && typeof data.revenues_net === "number") ? data.revenues_net : 0;
      summaryRevenues.textContent = moneyFmt.format(rev);
      setWarnings(summaryWarnings, data.warnings || []);
      renderTable(summaryTableWrap, data);
    } catch (e) {
      summaryTableWrap.innerHTML = "";
      setError(summaryError, `${t("error", "Errore")}: ${e && e.message ? e.message : e}`);
    }

    if (!supplierSelect || !supplierTableWrap) return;
    const supplier = (supplierSelect.value || "").trim();
    setError(supplierError, "");
    setWarnings(supplierWarnings, []);

    if (!supplier) {
      supplierTableWrap.innerHTML = `<div class="text-muted small">${t("selectSupplierHelp", "Seleziona un fornitore per vedere i KPI dedicati.")}</div>`;
      return;
    }

    supplierTableWrap.innerHTML = `<div class="text-muted small">${t("loading", "Caricamento...")}</div>`;
    try {
      const data2 = await fetchSummary(startIso, endIso, supplier);
      setWarnings(supplierWarnings, data2.warnings || []);
      renderTable(supplierTableWrap, data2);
    } catch (e) {
      supplierTableWrap.innerHTML = "";
      setError(supplierError, `${t("error", "Errore")}: ${e && e.message ? e.message : e}`);
    }
  }

  function computeWeekRange() {
    const anchor = fromISODate(weekAnchor.value) || new Date();
    const s = startOfWeek(anchor);
    const e = endOfWeek(anchor);
    weekRangeLabel.textContent = `${fmtDate(toISODate(s))} -> ${fmtDate(toISODate(e))}`;
    return { start: s, end: e };
  }

  function computeMonthRange() {
    const raw = String(monthPicker.value || "");
    let d = null;
    if (raw && raw.includes("-")) {
      const parts = raw.split("-");
      const y = parseInt(parts[0], 10);
      const m = parseInt(parts[1], 10);
      if (y && m) d = new Date(y, m - 1, 1);
    }
    if (!d) d = new Date();
    const s = startOfMonth(d);
    const e = endOfMonth(d);
    monthRangeLabel.textContent = `${fmtDate(toISODate(s))} -> ${fmtDate(toISODate(e))}`;
    return { start: s, end: e };
  }

  function computePeriodRange() {
    const s = fromISODate(periodStart.value);
    const e = fromISODate(periodEnd.value);
    if (!s || !e) return null;
    return { start: s, end: e };
  }

  function applyRange(range) {
    if (!range || !range.start || !range.end) return;
    currentStart = range.start;
    currentEnd = range.end;
    refreshAll();
  }

  function updateModeUI() {
    const mode = modeSel.value;
    setVisible(boxWeek, mode === "week");
    setVisible(boxMonth, mode === "month");
    setVisible(boxPeriod, mode === "period");
  }

  modeSel.addEventListener("change", updateModeUI);
  if (weekAnchor) weekAnchor.addEventListener("change", computeWeekRange);
  if (monthPicker) monthPicker.addEventListener("change", computeMonthRange);
  if (btnApplyWeek) btnApplyWeek.addEventListener("click", () => applyRange(computeWeekRange()));
  if (btnApplyMonth) btnApplyMonth.addEventListener("click", () => applyRange(computeMonthRange()));
  if (btnApplyPeriod) btnApplyPeriod.addEventListener("click", () => applyRange(computePeriodRange()));

  if (btnWeekPrev) btnWeekPrev.addEventListener("click", () => {
    const anchor = fromISODate(weekAnchor.value) || new Date();
    anchor.setDate(anchor.getDate() - 7);
    weekAnchor.value = toISODate(anchor);
    computeWeekRange();
  });
  if (btnWeekNext) btnWeekNext.addEventListener("click", () => {
    const anchor = fromISODate(weekAnchor.value) || new Date();
    anchor.setDate(anchor.getDate() + 7);
    weekAnchor.value = toISODate(anchor);
    computeWeekRange();
  });

  if (btnMonthPrev) btnMonthPrev.addEventListener("click", () => {
    const raw = String(monthPicker.value || "");
    let y = new Date().getFullYear();
    let m = new Date().getMonth();
    if (raw && raw.includes("-")) {
      const p = raw.split("-");
      y = parseInt(p[0], 10) || y;
      m = (parseInt(p[1], 10) || (m + 1)) - 1;
    }
    const d = new Date(y, m - 1, 1);
    monthPicker.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    computeMonthRange();
  });
  if (btnMonthNext) btnMonthNext.addEventListener("click", () => {
    const raw = String(monthPicker.value || "");
    let y = new Date().getFullYear();
    let m = new Date().getMonth();
    if (raw && raw.includes("-")) {
      const p = raw.split("-");
      y = parseInt(p[0], 10) || y;
      m = (parseInt(p[1], 10) || (m + 1)) - 1;
    }
    const d = new Date(y, m + 1, 1);
    monthPicker.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    computeMonthRange();
  });

  if (supplierSelect) supplierSelect.addEventListener("change", refreshAll);

  const today = new Date();
  if (weekAnchor) weekAnchor.value = toISODate(today);
  if (periodStart) periodStart.value = toISODate(today);
  if (periodEnd) periodEnd.value = toISODate(today);
  if (monthPicker) {
    monthPicker.value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}`;
  }

  updateModeUI();
  computeWeekRange();
  computeMonthRange();

  if (summaryTableWrap) summaryTableWrap.innerHTML = `<div class="text-muted small">${t("selectPeriod", "Seleziona un periodo e premi \"Applica\".")}</div>`;
  if (supplierTableWrap) supplierTableWrap.innerHTML = `<div class="text-muted small">${t("selectPeriod", "Seleziona un periodo e premi \"Applica\".")}</div>`;
})();
