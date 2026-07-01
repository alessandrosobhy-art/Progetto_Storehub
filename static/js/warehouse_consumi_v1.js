(function () {
  "use strict";

  const cfg = window.WH_CONSUMI || {};
  const apiUrl = cfg.apiUrl || "/magazzino/api/consumi/table";
  const I18N = cfg.i18n || {};

  function t(key, fallback) {
    const value = Object.prototype.hasOwnProperty.call(I18N, key) ? I18N[key] : "";
    return value == null || value === "" ? fallback : String(value);
  }

  const modeSel = document.getElementById("conMode");
  const boxWeek = document.getElementById("conModeWeek");
  const boxMonth = document.getElementById("conModeMonth");
  const boxPeriod = document.getElementById("conModePeriod");
  const weekAnchor = document.getElementById("conWeekAnchor");
  const weekRangeLabel = document.getElementById("conWeekRangeLabel");
  const monthPicker = document.getElementById("conMonthPicker");
  const monthRangeLabel = document.getElementById("conMonthRangeLabel");
  const periodStart = document.getElementById("conPeriodStart");
  const periodEnd = document.getElementById("conPeriodEnd");
  const btnApplyWeek = document.getElementById("conApplyWeek");
  const btnApplyMonth = document.getElementById("conApplyMonth");
  const btnApplyPeriod = document.getElementById("conApplyPeriod");
  const btnWeekPrev = document.getElementById("conWeekPrev");
  const btnWeekNext = document.getElementById("conWeekNext");
  const btnMonthPrev = document.getElementById("conMonthPrev");
  const btnMonthNext = document.getElementById("conMonthNext");
  const supplierSel = document.getElementById("conSupplier");
  const listinoSel = document.getElementById("conListino");
  const revenuesEl = document.getElementById("conRevenues");
  const periodLabel = document.getElementById("conPeriodLabel");
  const metaEl = document.getElementById("conMeta");
  const errorEl = document.getElementById("conError");
  const warningsEl = document.getElementById("conWarnings");
  const tableWrap = document.getElementById("conTableWrap");
  const toggleDetailsBtn = document.getElementById("conToggleDetails");
  const modalEl = document.getElementById("conDetailModal");
  const modalTitleEl = document.getElementById("conModalTitle");
  const modalSubtitleEl = document.getElementById("conModalSubtitle");
  const modalBodyEl = document.getElementById("conModalBody");

  if (!modeSel || !tableWrap) return;

  let detailsShown = false;
  let detailModal = null;
  let currentStart = null;
  let currentEnd = null;
  let lastData = null;

  const mql = window.matchMedia ? window.matchMedia("(max-width: 991.98px)") : null;
  let isMobile = !!(mql && mql.matches);

  const moneyFmt = new Intl.NumberFormat("it-IT", { style: "currency", currency: "EUR" });
  const numFmt0 = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 0 });
  const numFmt2 = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 2, minimumFractionDigits: 2 });

  function ensureDetailModal() {
    if (detailModal) return true;
    if (!modalEl) return false;
    const bs = window.bootstrap;
    if (!bs || !bs.Modal) return false;
    detailModal = new bs.Modal(modalEl, { backdrop: true, keyboard: true });
    return true;
  }

  function onViewportChange() {
    isMobile = !!(mql && mql.matches);
    if (lastData) render(lastData);
  }

  if (mql) {
    if (typeof mql.addEventListener === "function") mql.addEventListener("change", onViewportChange);
    else if (typeof mql.addListener === "function") mql.addListener(onViewportChange);
  }

  function applyDetailsVisibility() {
    if (!tableWrap || isMobile) return;
    tableWrap.querySelectorAll(".con-detail-col").forEach(n => {
      n.classList.toggle("d-none", !detailsShown);
    });
    if (toggleDetailsBtn) {
      toggleDetailsBtn.textContent = detailsShown ? t("hideDetails", "Nascondi dettagli") : t("showDetails", "Mostra dettagli");
    }
  }

  if (toggleDetailsBtn) {
    toggleDetailsBtn.addEventListener("click", () => {
      detailsShown = !detailsShown;
      applyDetailsVisibility();
    });
  }

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

  function fmtItDate(iso) {
    const d = fromISODate(iso);
    if (!d) return "-";
    const dd = String(d.getDate()).padStart(2, "0");
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const yyyy = d.getFullYear();
    return `${dd}/${mm}/${yyyy}`;
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

  function setError(msg) {
    if (!errorEl) return;
    if (!msg) {
      errorEl.classList.add("d-none");
      errorEl.textContent = "";
      return;
    }
    errorEl.textContent = msg;
    errorEl.classList.remove("d-none");
  }

  function setWarnings(warnings) {
    if (!warningsEl) return;
    const list = (warnings || []).map(w => String(w || "").trim()).filter(Boolean);
    const uniq = Array.from(new Set(list));
    if (!uniq.length) {
      warningsEl.classList.add("d-none");
      warningsEl.textContent = "";
      return;
    }
    warningsEl.textContent = uniq.join(" - ");
    warningsEl.classList.remove("d-none");
  }

  function computeWeekRange() {
    const anchor = fromISODate(weekAnchor.value) || new Date();
    const s = startOfWeek(anchor);
    const e = endOfWeek(anchor);
    if (weekRangeLabel) weekRangeLabel.textContent = `${fmtItDate(toISODate(s))} -> ${fmtItDate(toISODate(e))}`;
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
    if (monthRangeLabel) monthRangeLabel.textContent = `${fmtItDate(toISODate(s))} -> ${fmtItDate(toISODate(e))}`;
    return { start: s, end: e };
  }

  function computePeriodRange() {
    const s = fromISODate(periodStart.value);
    const e = fromISODate(periodEnd.value);
    if (!s || !e) return null;
    return { start: s, end: e };
  }

  function updateModeUI() {
    const mode = modeSel.value;
    setVisible(boxWeek, mode === "week");
    setVisible(boxMonth, mode === "month");
    setVisible(boxPeriod, mode === "period");
  }

  async function fetchTable(startIso, endIso, supplier, listino) {
    const u = new URL(apiUrl, window.location.origin);
    u.searchParams.set("start", startIso);
    u.searchParams.set("end", endIso);
    u.searchParams.set("supplier", supplier);
    u.searchParams.set("listino", listino);

    const res = await fetch(u.toString(), { credentials: "same-origin" });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = null; }
    if (!res.ok) {
      const msg = data && data.error ? data.error : `HTTP ${res.status}`;
      throw new Error(msg);
    }
    return data;
  }

  function num(v) {
    const n = typeof v === "number" && isFinite(v) ? v : parseFloat(v);
    return isFinite(n) ? n : 0;
  }

  function fmtInt(v) {
    return numFmt0.format(Math.round(num(v)));
  }

  function fmt2(v) {
    return numFmt2.format(num(v));
  }

  function escapeHtml(s) {
    return String(s || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function updatePeriodLabels(startIso, endIso) {
    if (periodLabel) periodLabel.textContent = `${fmtItDate(startIso)} -> ${fmtItDate(endIso)}`;
  }

  function computeTotals(rows) {
    const totals = {
      inv_initial_pz: 0,
      delivery_pz: 0,
      tx_in_pz: 0,
      tx_out_pz: 0,
      inv_final_pz: 0,
      consumption_pz: 0,
    };
    (rows || []).forEach(r => {
      totals.inv_initial_pz += num(r.inv_initial_pz);
      totals.delivery_pz += num(r.delivery_pz);
      totals.tx_in_pz += num(r.tx_in_pz);
      totals.tx_out_pz += num(r.tx_out_pz);
      totals.inv_final_pz += num(r.inv_final_pz);
      totals.consumption_pz += num(r.consumption_pz);
    });
    return totals;
  }

  function renderTable(data) {
    const rows = data && data.rows ? data.rows : [];
    const revenues = data && typeof data.revenues_net === "number" ? data.revenues_net : 0;
    const totals = computeTotals(rows);
    const table = document.createElement("table");
    table.className = "table table-sm table-hover table-striped align-middle table-consumi";

    table.innerHTML = `
      <thead>
        <tr>
          <th class="con-sticky" style="min-width: 320px;">${t("description", "Descrizione")}</th>
          <th class="text-end con-detail-col d-none" style="min-width: 110px;" title="${t("initialInventory", "Inventario iniziale")} (pz)">INV</th>
          <th class="text-end con-detail-col d-none" style="min-width: 110px;" title="${t("delivery", "Delivery")} (pz)">DELIVERY</th>
          <th class="text-end con-detail-col d-none" style="min-width: 110px;" title="${t("transfersIn", "Trasferimenti In")} (pz)">TX IN</th>
          <th class="text-end con-detail-col d-none" style="min-width: 110px;" title="${t("transfersOut", "Trasferimenti Out")} (pz)">TX OUT</th>
          <th class="text-end con-detail-col d-none" style="min-width: 110px;" title="${t("finalInventory", "Inventario finale")} (pz)">INV</th>
          <th class="text-end" style="min-width: 120px;">${t("consumption", "Consumo").toUpperCase()}</th>
          <th class="text-end" style="min-width: 130px;">PZ/1000 EUR</th>
        </tr>
      </thead>
    `;

    const tbody = document.createElement("tbody");
    if (!rows.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 8;
      td.className = "text-muted small";
      td.textContent = t("noData", "Nessun dato per i filtri selezionati.");
      tr.appendChild(td);
      tbody.appendChild(tr);
    } else {
      rows.forEach(r => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td class="con-sticky">
            <div class="fw-semibold">${escapeHtml(r.desc || "")}</div>
            <div class="small text-muted">${escapeHtml(r.code || "")}</div>
          </td>
          <td class="text-end con-detail-col d-none">${fmtInt(r.inv_initial_pz)}</td>
          <td class="text-end con-detail-col d-none">${fmtInt(r.delivery_pz)}</td>
          <td class="text-end con-detail-col d-none">${fmtInt(r.tx_in_pz)}</td>
          <td class="text-end con-detail-col d-none">${fmtInt(r.tx_out_pz)}</td>
          <td class="text-end con-detail-col d-none">${fmtInt(r.inv_final_pz)}</td>
          <td class="text-end fw-semibold">${fmtInt(r.consumption_pz)}</td>
          <td class="text-end">${fmt2(r.consumption_per_1000)}</td>
        `;
        tbody.appendChild(tr);
      });
    }

    table.appendChild(tbody);

    const tfoot = document.createElement("tfoot");
    const trTot = document.createElement("tr");
    trTot.className = "table-total";
    const totalPer1000 = revenues > 0 ? (num(totals.consumption_pz) / revenues) * 1000 : 0;
    trTot.innerHTML = `
      <td class="fw-semibold con-sticky">${t("total", "Totale")}</td>
      <td class="text-end con-detail-col d-none">${fmtInt(totals.inv_initial_pz)}</td>
      <td class="text-end con-detail-col d-none">${fmtInt(totals.delivery_pz)}</td>
      <td class="text-end con-detail-col d-none">${fmtInt(totals.tx_in_pz)}</td>
      <td class="text-end con-detail-col d-none">${fmtInt(totals.tx_out_pz)}</td>
      <td class="text-end con-detail-col d-none">${fmtInt(totals.inv_final_pz)}</td>
      <td class="text-end fw-semibold">${fmtInt(totals.consumption_pz)}</td>
      <td class="text-end">${fmt2(totalPer1000)}</td>
    `;
    tfoot.appendChild(trTot);
    table.appendChild(tfoot);

    tableWrap.innerHTML = "";
    tableWrap.classList.add("table-responsive");
    tableWrap.appendChild(table);
    applyDetailsVisibility();
  }

  function setModalKV(container, label, value) {
    const k = document.createElement("div");
    k.className = "k";
    k.textContent = label;
    const v = document.createElement("div");
    v.className = "v";
    v.textContent = value;
    container.appendChild(k);
    container.appendChild(v);
  }

  function openDetailModal(row, subtitleParts) {
    if (!modalTitleEl || !modalBodyEl) return;
    if (!ensureDetailModal()) return;
    modalTitleEl.textContent = row && row._title ? row._title : (row && row.desc ? row.desc : t("detail", "Dettaglio"));
    if (modalSubtitleEl) {
      const s = (subtitleParts || []).map(x => String(x || "").trim()).filter(Boolean);
      modalSubtitleEl.textContent = s.join(" - ") || "-";
    }

    const kv = document.createElement("div");
    kv.className = "con-modal-kv";
    setModalKV(kv, `${t("initialInventory", "Inventario iniziale")} (pz)`, fmtInt(row.inv_initial_pz));
    setModalKV(kv, `${t("delivery", "Delivery")} (pz)`, fmtInt(row.delivery_pz));
    setModalKV(kv, `${t("transfersIn", "Trasferimenti In")} (pz)`, fmtInt(row.tx_in_pz));
    setModalKV(kv, `${t("transfersOut", "Trasferimenti Out")} (pz)`, fmtInt(row.tx_out_pz));
    setModalKV(kv, `${t("finalInventory", "Inventario finale")} (pz)`, fmtInt(row.inv_final_pz));
    setModalKV(kv, `${t("consumption", "Consumo")} (pz)`, fmtInt(row.consumption_pz));
    setModalKV(kv, "PZ/1000 EUR", fmt2(row.consumption_per_1000));

    modalBodyEl.innerHTML = "";
    modalBodyEl.appendChild(kv);
    detailModal.show();
  }

  function renderMobileList(data) {
    const rows = data && data.rows ? data.rows : [];
    const revenues = data && typeof data.revenues_net === "number" ? data.revenues_net : 0;
    const totals = computeTotals(rows);
    const totalPer1000 = revenues > 0 ? (num(totals.consumption_pz) / revenues) * 1000 : 0;
    const list = document.createElement("div");
    list.className = "list-group con-mobile-list";

    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "text-muted small";
      empty.textContent = t("noData", "Nessun dato per i filtri selezionati.");
      tableWrap.innerHTML = "";
      tableWrap.classList.remove("table-responsive");
      tableWrap.appendChild(empty);
      return;
    }

    const subtitleBase = [];
    if (metaEl && metaEl.textContent) subtitleBase.push(metaEl.textContent);
    if (periodLabel && periodLabel.textContent) subtitleBase.push(periodLabel.textContent);

    rows.forEach((r, idx) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "list-group-item list-group-item-action con-mobile-item";
      btn.dataset.idx = String(idx);
      btn.innerHTML = `
        <div class="con-mobile-desc">${escapeHtml(r.desc || "")}</div>
        <div class="small mt-1 con-mobile-metrics">
          <span class="metric"><span class="label">${t("consumption", "Consumo")}:</span> <span class="value">${fmtInt(r.consumption_pz)}</span></span>
          <span class="metric"><span class="label">PZ/1000 EUR:</span> <span class="value">${fmt2(r.consumption_per_1000)}</span></span>
        </div>
      `;
      btn.addEventListener("click", () => {
        const subtitle = subtitleBase.slice();
        if (r.code) subtitle.unshift(r.code);
        openDetailModal(r, subtitle);
      });
      list.appendChild(btn);
    });

    const btnTot = document.createElement("button");
    btnTot.type = "button";
    btnTot.className = "list-group-item list-group-item-action con-mobile-item con-mobile-total";
    btnTot.innerHTML = `
      <div class="con-mobile-desc">${t("total", "Totale")}</div>
      <div class="small mt-1 con-mobile-metrics">
        <span class="metric"><span class="label">${t("consumption", "Consumo")}:</span> <span class="value">${fmtInt(totals.consumption_pz)}</span></span>
        <span class="metric"><span class="label">PZ/1000 EUR:</span> <span class="value">${fmt2(totalPer1000)}</span></span>
      </div>
    `;
    btnTot.addEventListener("click", () => {
      openDetailModal({
        _title: t("total", "Totale"),
        inv_initial_pz: totals.inv_initial_pz,
        delivery_pz: totals.delivery_pz,
        tx_in_pz: totals.tx_in_pz,
        tx_out_pz: totals.tx_out_pz,
        inv_final_pz: totals.inv_final_pz,
        consumption_pz: totals.consumption_pz,
        consumption_per_1000: totalPer1000,
      }, subtitleBase);
    });
    list.appendChild(btnTot);

    tableWrap.innerHTML = "";
    tableWrap.classList.remove("table-responsive");
    tableWrap.appendChild(list);
  }

  function render(data) {
    if (isMobile) renderMobileList(data);
    else renderTable(data);
  }

  async function refresh() {
    if (!currentStart || !currentEnd) return;
    const supplier = supplierSel ? supplierSel.value.trim() : "";
    const listino = (listinoSel ? listinoSel.value : "FoodPaper").trim() || "FoodPaper";
    const startIso = toISODate(currentStart);
    const endIso = toISODate(currentEnd);
    updatePeriodLabels(startIso, endIso);

    setError("");
    setWarnings([]);
    if (revenuesEl) revenuesEl.textContent = "-";
    if (metaEl) metaEl.textContent = "-";

    if (!supplier) {
      tableWrap.innerHTML = `<div class="text-muted small">${t("supplierPrompt", 'Seleziona un fornitore e premi "Applica".')}</div>`;
      return;
    }

    tableWrap.innerHTML = `<div class="text-muted small">${t("loading", "Caricamento...")}</div>`;

    try {
      const data = await fetchTable(startIso, endIso, supplier, listino);
      if (data && data.error) throw new Error(String(data.error));

      const rev = data && typeof data.revenues_net === "number" ? data.revenues_net : 0;
      if (revenuesEl) revenuesEl.textContent = moneyFmt.format(rev);

      const supText = supplierSel ? (supplierSel.options[supplierSel.selectedIndex]?.textContent || supplier) : supplier;
      if (metaEl) metaEl.textContent = `${supText} - ${listino}`;

      setWarnings(data.warnings || []);
      lastData = data;
      render(data);
    } catch (e) {
      tableWrap.innerHTML = "";
      setError(`${t("error", "Errore")}: ${e && e.message ? e.message : e}`);
    }
  }

  function applyRange(range) {
    if (!range || !range.start || !range.end) return;
    currentStart = range.start;
    currentEnd = range.end;
    refresh();
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

  const today = new Date();
  if (weekAnchor) weekAnchor.value = toISODate(today);
  if (periodStart) periodStart.value = toISODate(today);
  if (periodEnd) periodEnd.value = toISODate(today);
  if (monthPicker) monthPicker.value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}`;

  updateModeUI();
  computeWeekRange();
  computeMonthRange();
  tableWrap.innerHTML = `<div class="text-muted small">${t("selectPrompt", 'Seleziona un periodo, un fornitore e premi "Applica".')}</div>`;
})();
