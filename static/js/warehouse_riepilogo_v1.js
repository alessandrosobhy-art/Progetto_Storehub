(function () {
  "use strict";

  const cfg = window.WH_RIEPILOGO || {};
  const apiUrl = cfg.apiUrl || "/magazzino/api/riepilogo/mensile";
  const exportUrl = cfg.exportUrl || "/magazzino/riepilogo/export";
  const I18N = cfg.i18n || {};

  function t(key, fallback) {
    const value = Object.prototype.hasOwnProperty.call(I18N, key) ? I18N[key] : "";
    return value == null || value === "" ? fallback : String(value);
  }

  const monthPicker = document.getElementById("riepMonthPicker");
  const monthRangeLabel = document.getElementById("riepMonthRangeLabel");
  const btnPrev = document.getElementById("riepMonthPrev");
  const btnNext = document.getElementById("riepMonthNext");
  const btnApply = document.getElementById("riepApply");
  const bucketSel = document.getElementById("riepBucket");
  const btnExport = document.getElementById("riepExportBtn");
  const periodLabel = document.getElementById("riepPeriodLabel");
  const revenuesTotalEl = document.getElementById("riepRevenuesTotal");
  const tableWrap = document.getElementById("riepTableWrap");
  const errorEl = document.getElementById("riepError");
  const warningsEl = document.getElementById("riepWarnings");

  if (!monthPicker || !tableWrap || !bucketSel) return;

  const moneyFmt = new Intl.NumberFormat("it-IT", { style: "currency", currency: "EUR" });
  const pctFmt = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 2, minimumFractionDigits: 2 });

  function syncExportHref() {
    if (!btnExport) return;
    const month = String(monthPicker.value || "").trim();
    const bucket = String(bucketSel.value || "FoodPaper").trim() || "FoodPaper";
    const u = new URL(exportUrl, window.location.origin);
    if (month) u.searchParams.set("month", month);
    if (bucket) u.searchParams.set("bucket", bucket);
    btnExport.href = u.toString();
  }

  function fmtItDate(iso) {
    if (!iso) return "-";
    const s = String(iso).slice(0, 10);
    const p = s.split("-");
    if (p.length !== 3) return "-";
    return `${p[2]}/${p[1]}/${p[0]}`;
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

  function setWarnings(list) {
    if (!warningsEl) return;
    const arr = (list || []).map(x => String(x || "").trim()).filter(Boolean);
    const uniq = Array.from(new Set(arr));
    if (!uniq.length) {
      warningsEl.classList.add("d-none");
      warningsEl.textContent = "";
      return;
    }
    warningsEl.textContent = uniq.join(" - ");
    warningsEl.classList.remove("d-none");
  }

  function monthToRange(yyyyMm) {
    let y = new Date().getFullYear();
    let m = new Date().getMonth() + 1;
    const raw = String(yyyyMm || "");
    if (raw && raw.includes("-")) {
      const p = raw.split("-");
      const y2 = parseInt(p[0], 10);
      const m2 = parseInt(p[1], 10);
      if (y2 && m2 >= 1 && m2 <= 12) {
        y = y2;
        m = m2;
      }
    }
    const start = new Date(y, m - 1, 1);
    const end = new Date(y, m, 0);
    const startIso = `${start.getFullYear()}-${String(start.getMonth() + 1).padStart(2, "0")}-${String(start.getDate()).padStart(2, "0")}`;
    const endIso = `${end.getFullYear()}-${String(end.getMonth() + 1).padStart(2, "0")}-${String(end.getDate()).padStart(2, "0")}`;
    return { startIso, endIso };
  }

  function updateRangeLabel() {
    const r = monthToRange(String(monthPicker.value || ""));
    if (monthRangeLabel) monthRangeLabel.textContent = `${fmtItDate(r.startIso)} -> ${fmtItDate(r.endIso)}`;
    return r;
  }

  function fmtMoney(v) {
    const n = typeof v === "number" && isFinite(v) ? v : 0;
    return moneyFmt.format(n);
  }

  function fmtPct(v) {
    const n = typeof v === "number" && isFinite(v) ? v : 0;
    return `${pctFmt.format(n)}%`;
  }

  function renderTable(rows) {
    const table = document.createElement("table");
    table.className = "table table-sm table-hover align-middle table-riepilogo";

    const thead = document.createElement("thead");
    thead.innerHTML = `
      <tr>
        <th>${t("store", "STORE").toUpperCase()}</th>
        <th class="text-center th-num">REV</th>
        <th class="text-center th-num">INV</th>
        <th class="text-center th-num">TX IN</th>
        <th class="text-center th-num">TX OUT</th>
        <th class="text-center th-num">DEL</th>
        <th class="text-center th-num">INV</th>
        <th class="text-center th-num">CONS</th>
        <th class="text-center th-num">%</th>
        <th class="text-center th-num">WASTE</th>
        <th class="text-center th-num">%</th>
      </tr>
    `;
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    (rows || []).forEach(r => {
      const tr = document.createElement("tr");
      const code = String(r.store_code || "").trim();
      const name = String(r.store_name || "").trim();

      const tdStore = document.createElement("td");
      tdStore.className = "store";
      if (r.error) {
        tdStore.innerHTML = `<div class="fw-semibold">${code} ${name ? "- " + name : ""}</div><div class="small text-danger">${String(r.error)}</div>`;
      } else {
        tdStore.innerHTML = `<div class="fw-semibold">${name}</div>${name && name !== code ? `<div class="small muted">${code}</div>` : ""}`;
      }
      tr.appendChild(tdStore);

      function addNumCell(text) {
        const td = document.createElement("td");
        td.className = "num";
        td.textContent = text;
        tr.appendChild(td);
      }

      if (r.error) {
        for (let i = 0; i < 10; i++) addNumCell("-");
      } else {
        addNumCell(fmtMoney(r.revenues_net));
        addNumCell(fmtMoney(r.inv_initial));
        addNumCell(fmtMoney(r.tx_in));
        addNumCell(fmtMoney(r.tx_out));
        addNumCell(fmtMoney(r.delivery));
        addNumCell(fmtMoney(r.inv_final));
        addNumCell(fmtMoney(r.consumption));
        addNumCell(fmtPct(r.consumption_pct));
        addNumCell(fmtMoney(r.waste));
        addNumCell(fmtPct(r.waste_pct));
      }

      tbody.appendChild(tr);
    });

    table.appendChild(tbody);
    tableWrap.innerHTML = "";
    tableWrap.appendChild(table);
  }

  async function fetchMonthly(month, bucket) {
    const u = new URL(apiUrl, window.location.origin);
    u.searchParams.set("month", month);
    u.searchParams.set("bucket", bucket);

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

  async function refresh() {
    const month = String(monthPicker.value || "").trim();
    const bucket = String(bucketSel.value || "FoodPaper").trim() || "FoodPaper";

    setError("");
    setWarnings([]);
    tableWrap.innerHTML = `<div class="text-muted small">${t("loading", "Caricamento...")}</div>`;
    if (revenuesTotalEl) revenuesTotalEl.textContent = "-";

    try {
      const data = await fetchMonthly(month, bucket);
      const startIso = data && data.period ? data.period.start : "";
      const endIso = data && data.period ? data.period.end : "";
      if (periodLabel) periodLabel.textContent = `${fmtItDate(startIso)} -> ${fmtItDate(endIso)} - ${bucket}`;

      const rows = data && data.rows ? data.rows : [];
      renderTable(rows);

      const revTot = rows.reduce((acc, r) => {
        if (!r || r.error) return acc;
        const v = typeof r.revenues_net === "number" && isFinite(r.revenues_net) ? r.revenues_net : 0;
        return acc + v;
      }, 0);
      if (revenuesTotalEl) revenuesTotalEl.textContent = fmtMoney(revTot);
      setWarnings(data.warnings || []);
      syncExportHref();
    } catch (e) {
      tableWrap.innerHTML = "";
      setError(`${t("error", "Errore")}: ${e && e.message ? e.message : e}`);
    }
  }

  monthPicker.addEventListener("change", () => { updateRangeLabel(); syncExportHref(); });
  bucketSel.addEventListener("change", syncExportHref);
  if (btnApply) btnApply.addEventListener("click", refresh);
  if (btnExport) btnExport.addEventListener("click", syncExportHref);

  function shiftMonth(delta) {
    const raw = String(monthPicker.value || "");
    let y = new Date().getFullYear();
    let m = new Date().getMonth() + 1;
    if (raw && raw.includes("-")) {
      const p = raw.split("-");
      y = parseInt(p[0], 10) || y;
      m = parseInt(p[1], 10) || m;
    }
    const d = new Date(y, (m - 1) + delta, 1);
    monthPicker.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    updateRangeLabel();
    syncExportHref();
  }

  if (btnPrev) btnPrev.addEventListener("click", () => shiftMonth(-1));
  if (btnNext) btnNext.addEventListener("click", () => shiftMonth(1));

  const today = new Date();
  monthPicker.value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}`;
  updateRangeLabel();
  syncExportHref();
  tableWrap.innerHTML = `<div class="text-muted small">${t("selectPrompt", 'Seleziona mese e listino, poi premi "Applica".')}</div>`;
})();
