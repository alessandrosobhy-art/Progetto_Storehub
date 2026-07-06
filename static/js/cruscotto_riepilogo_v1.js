(function () {
  "use strict";

  const cfg = window.CR_RIEPILOGO || {};
  const apiUrl = cfg.apiUrl || "/rendiconto/api/riepilogo/mensile";
  const exportUrl = cfg.exportUrl || "/rendiconto/api/riepilogo/mensile.xlsx";
  const exportDailyUrl = cfg.exportDailyUrl || "/rendiconto/api/riepilogo/giornaliero.xlsx";
  const timeoutMs = (typeof cfg.timeoutMs === "number" && isFinite(cfg.timeoutMs)) ? cfg.timeoutMs : 45000;

  const monthPicker = document.getElementById("crRiepMonthPicker");
  const monthRangeLabel = document.getElementById("crRiepMonthRangeLabel");
  const btnPrev = document.getElementById("crRiepMonthPrev");
  const btnNext = document.getElementById("crRiepMonthNext");
  const btnApply = document.getElementById("crRiepApply");
  const btnExcel = document.getElementById("crRiepExcel");
  const btnExcelDaily = document.getElementById("crRiepExcelDaily");

  const periodLabel = document.getElementById("crRiepPeriodLabel");
  const giroTotalEl = document.getElementById("crRiepGiroTotal");
  const tableWrap = document.getElementById("crRiepTableWrap");
  const errorEl = document.getElementById("crRiepError");
  const warningsEl = document.getElementById("crRiepWarnings");

  if (!monthPicker || !tableWrap) return;

  // Cache (per mantenere il riepilogo quando si cambia pagina e si torna indietro)
  const STORAGE_PREFIX = "rendiconto_riepilogo:";
  const KEY_LAST_MONTH = STORAGE_PREFIX + "last_month";
  const keyForMonth = (ym) => STORAGE_PREFIX + "data:" + String(ym || "").trim();

  function saveCache(ym, data) {
    try {
      const month = String(ym || "").trim();
      if (!month) return;
      const payload = { ts: Date.now(), month, data: data || null };
      sessionStorage.setItem(KEY_LAST_MONTH, month);
      sessionStorage.setItem(keyForMonth(month), JSON.stringify(payload));
    } catch (_) {
      // ignore
    }
  }

  function loadCache(ym) {
    try {
      const month = String(ym || "").trim();
      if (!month) return null;
      const raw = sessionStorage.getItem(keyForMonth(month));
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || !parsed.data) return null;
      return parsed;
    } catch (_) {
      return null;
    }
  }

  const moneyFmt = new Intl.NumberFormat("it-IT", { style: "currency", currency: "EUR" });
  const intFmt = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 0 });

  let activeController = null;
  let isLoading = false;

  function setBusy(busy) {
    isLoading = !!busy;
    if (btnApply) btnApply.disabled = isLoading;
    if (btnPrev) btnPrev.disabled = isLoading;
    if (btnNext) btnNext.disabled = isLoading;
    if (monthPicker) monthPicker.disabled = isLoading;
    if (btnExcel) btnExcel.disabled = isLoading;
    if (btnExcelDaily) btnExcelDaily.disabled = isLoading;
  }

  function fmtItDate(iso) {
    if (!iso) return "—";
    const s = String(iso).slice(0, 10);
    const p = s.split("-");
    if (p.length !== 3) return "—";
    return `${p[2]}/${p[1]}/${p[0]}`;
  }

  function monthToRange(yyyyMm) {
    let y = new Date().getFullYear();
    let m = new Date().getMonth() + 1;
    try {
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
    } catch (_) {
      // ignore
    }
    const start = new Date(y, m - 1, 1);
    const end = new Date(y, m, 0);
    const startIso = `${start.getFullYear()}-${String(start.getMonth() + 1).padStart(2, "0")}-${String(start.getDate()).padStart(2, "0")}`;
    const endIso = `${end.getFullYear()}-${String(end.getMonth() + 1).padStart(2, "0")}-${String(end.getDate()).padStart(2, "0")}`;
    return { startIso, endIso };
  }

  function updateRangeLabel() {
    const raw = String(monthPicker.value || "");
    const r = monthToRange(raw);
    if (monthRangeLabel) monthRangeLabel.textContent = `${fmtItDate(r.startIso)} → ${fmtItDate(r.endIso)}`;
    return r;
  }

  function fmtMoney(v) {
    const n = (typeof v === "number" && isFinite(v)) ? v : 0;
    return moneyFmt.format(n);
  }

  function fmtInt(v) {
    const n = (typeof v === "number" && isFinite(v)) ? v : 0;
    return intFmt.format(Math.round(n));
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
    warningsEl.textContent = uniq.join(" · ");
    warningsEl.classList.remove("d-none");
  }

  function gotoRendiconto(storeCode, month, kind) {
    const form = document.getElementById("storeModalForm");
    const hiddenCode = document.getElementById("storeModalFormCode");
    const hiddenNext = document.getElementById("storeModalFormNext");
    if (!form || !hiddenCode || !hiddenNext) return;

    const ym = String(month || "").trim();
    const next = `/rendiconto/${encodeURIComponent(kind)}?ym=${encodeURIComponent(ym)}`;

    hiddenCode.value = String(storeCode || "").trim();
    hiddenNext.value = next;
    form.submit();
  }

  function renderTable(rows, month) {
    const table = document.createElement("table");
    table.className = "table table-sm table-hover align-middle table-riepilogo";

    function num(v) {
      return (typeof v === "number" && isFinite(v)) ? v : 0;
    }

    const thead = document.createElement("thead");
    thead.innerHTML = `
      <tr>
        <th>STORE</th>
        <th class="text-center th-num">GIRO AFFARI</th>
        <th class="text-center th-num">SCONTRINI</th>
        <th class="text-center th-num">DIFF. CASSA</th>
        <th class="text-center th-num">DISTINTE</th>
        <th class="text-center th-num">CONTANTI IPRATICO</th>
        <th class="text-center th-num">POS</th>
        <th class="text-center th-num">SPESE</th>
        <th class="text-center th-num">VERSAMENTI</th>
        <th class="text-center th-num">GIORNI NON VERSATI</th>
        <th class="text-center th-num">TICKET</th>
        <th class="text-center th-num">DELIVERY</th>
        <th class="text-center th-num">COUPON</th>
      </tr>
    `;
    table.appendChild(thead);

    const tbody = document.createElement("tbody");

    function detailItems(row, key) {
      return Array.isArray(row && row[key]) ? row[key].filter(x => x && Number(x.valore || 0) !== 0) : [];
    }

    function renderDetailBlock(title, items) {
      if (!items.length) return "";
      const body = items.map((it) => {
        const label = escapeHtml(String(it.label || it.voce || ""));
        return `<div class="d-flex justify-content-between gap-3 border-bottom py-1"><span>${label}</span><span class="fw-semibold text-nowrap">${fmtMoney(num(Number(it.valore || 0)))}</span></div>`;
      }).join("");
      return `<div class="col-12 col-lg-4"><div class="small fw-semibold text-muted mb-1">${escapeHtml(title)}</div>${body}</div>`;
    }

    function escapeHtml(v) {
      return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
        return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[c];
      });
    }

    (rows || []).forEach((r, idx) => {
      const tr = document.createElement("tr");
      const code = String(r.store_code || "").trim();
      const name = String(r.store_name || "").trim();
      const detailId = `riep-detail-${idx}-${code.replace(/[^a-zA-Z0-9_-]/g, "") || "store"}`;

      const tdStore = document.createElement("td");
      tdStore.className = "store";

      const warns = (r.warnings || []).map(x => String(x || "").trim()).filter(Boolean);
      const warnHtml = warns.length ? `<div class="small text-warning">${warns.join(" · ")}</div>` : "";
      tdStore.innerHTML = `<div class="fw-semibold">${name}</div>${name && name !== code ? `<div class="small muted">${code}</div>` : ""}${warnHtml}`;
      tr.appendChild(tdStore);

      function addNumCell(text) {
        const td = document.createElement("td");
        td.className = "num";
        td.textContent = text;
        tr.appendChild(td);
      }

      function addIntCell(value) {
        const td = document.createElement("td");
        td.className = "num";
        td.textContent = fmtInt(value);
        tr.appendChild(td);
      }

      function addLinkCell(kind, value) {
        const td = document.createElement("td");
        td.className = "num";
        const a = document.createElement("a");
        a.href = "#";
        a.className = "riep-link";
        a.textContent = fmtMoney(value);
        a.addEventListener("click", (ev) => {
          ev.preventDefault();
          gotoRendiconto(code, month, kind);
        });
        td.appendChild(a);
        tr.appendChild(td);
      }

      function addExpandableCell(value) {
        const td = document.createElement("td");
        td.className = "num";
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-link btn-sm p-0 riep-link js-riep-detail-toggle";
        btn.textContent = fmtMoney(value);
        btn.setAttribute("aria-expanded", "false");
        btn.setAttribute("aria-controls", detailId);
        btn.addEventListener("click", () => {
          const detailRow = document.getElementById(detailId);
          if (!detailRow) return;
          const show = detailRow.classList.contains("d-none");
          detailRow.classList.toggle("d-none", !show);
          btn.setAttribute("aria-expanded", show ? "true" : "false");
        });
        td.appendChild(btn);
        tr.appendChild(td);
      }

      addNumCell(fmtMoney(r.giro_affari));
      addIntCell(r.scontrini);
      const diffCassa = (r && typeof r.diff_cassa === "number" && isFinite(r.diff_cassa))
        ? r.diff_cassa
        : (num(r.distinte) + num(r.ticket_si) + num(r.delivery_si) + num(r.coupon_si) + num(r.pos) + num(r.spese) - num(r.giro_affari));
      // Differenza cassa: se negativa va in rosso
      (function addDiffCell() {
        const td = document.createElement("td");
        td.className = "num";
        td.textContent = fmtMoney(diffCassa);
        if (diffCassa < 0) {
          td.classList.add("text-danger", "fw-semibold");
        }
        tr.appendChild(td);
      })();
      addNumCell(fmtMoney(r.distinte));
      addNumCell(fmtMoney(r.contanti_ipratico));
      addNumCell(fmtMoney(r.pos));
      addLinkCell("spese", r.spese);
      addLinkCell("versamenti", r.versamenti);
      addIntCell(r.giorni_non_versati);
      addExpandableCell(num(r.ticket_si) + num(r.ticket_no));
      addExpandableCell(num(r.delivery_si) + num(r.delivery_no));
      addExpandableCell(num(r.coupon_si) + num(r.coupon_no));

      tbody.appendChild(tr);

      const ticketDetail = detailItems(r, "ticket_detail");
      const deliveryDetail = detailItems(r, "delivery_detail");
      const couponDetail = detailItems(r, "coupon_detail");
      if (ticketDetail.length || deliveryDetail.length || couponDetail.length) {
        const detailTr = document.createElement("tr");
        detailTr.id = detailId;
        detailTr.className = "table-light d-none";
        detailTr.innerHTML = `
          <td colspan="13">
            <div class="row g-3 p-2">
              ${renderDetailBlock("Ticket", ticketDetail)}
              ${renderDetailBlock("Delivery", deliveryDetail)}
              ${renderDetailBlock("Coupon", couponDetail)}
            </div>
          </td>
        `;
        tbody.appendChild(detailTr);
      }
    });

    table.appendChild(tbody);
    tableWrap.innerHTML = "";
    tableWrap.appendChild(table);
  }

  async function fetchMonthly(month, signal) {
    const u = new URL(apiUrl, window.location.origin);
    u.searchParams.set("month", month);
    const res = await fetch(u.toString(), { credentials: "same-origin", signal });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = null; }
    if (!res.ok) {
      const msg = (data && data.error) ? data.error : (`HTTP ${res.status}`);
      throw new Error(msg);
    }
    return data;
  }

  function applyData(data, monthFallback) {
    const monthUsed = (data && data.month) ? data.month : String(monthFallback || "").trim();
    const startIso = data && data.period ? data.period.start : "";
    const endIso = data && data.period ? data.period.end : "";
    if (periodLabel) periodLabel.textContent = `${fmtItDate(startIso)} → ${fmtItDate(endIso)}`;

    const rows = (data && data.rows) ? data.rows : [];
    renderTable(rows, monthUsed);

    const giroTot = rows.reduce((acc, r) => {
      const v = (r && typeof r.giro_affari === "number" && isFinite(r.giro_affari)) ? r.giro_affari : 0;
      return acc + v;
    }, 0);
    if (giroTotalEl) giroTotalEl.textContent = fmtMoney(giroTot);

    setWarnings((data && data.warnings) ? data.warnings : []);
    setError("");
  }

  function clearView() {
    if (periodLabel) periodLabel.textContent = "—";
    if (giroTotalEl) giroTotalEl.textContent = "—";
    setWarnings([]);
    setError("");
    tableWrap.innerHTML = '<div class="text-muted small">Seleziona il mese e premi “Carica”.</div>';
  }

  function showFromCacheOrHint() {
    const month = String(monthPicker.value || "").trim();
    const cached = loadCache(month);
    if (cached && cached.data) {
      applyData(cached.data, month);
    } else {
      clearView();
    }
  }

  async function refresh() {
    const month = String(monthPicker.value || "").trim();

    if (activeController) {
      try { activeController.abort(); } catch (_) { /* ignore */ }
    }
    activeController = new AbortController();
    const { signal } = activeController;

    const t = setTimeout(() => {
      try { activeController && activeController.abort(); } catch (_) { /* ignore */ }
    }, timeoutMs);

    setError("");
    setWarnings([]);
    tableWrap.innerHTML = '<div class="text-muted small">Caricamento…</div>';
    if (giroTotalEl) giroTotalEl.textContent = "—";
    if (periodLabel) periodLabel.textContent = "—";

    setBusy(true);

    try {
      const data = await fetchMonthly(month, signal);
      applyData(data, month);
      saveCache((data && data.month) ? data.month : month, data);
    } catch (e) {
      tableWrap.innerHTML = "";
      const msg = (e && (e.name === "AbortError")) ? "Timeout o nuova richiesta: riprova." : (e && e.message ? e.message : e);
      setError(`Errore: ${msg}`);
    } finally {
      clearTimeout(t);
      setBusy(false);
    }
  }

  function triggerDownload(url) {
    // Scarica via <a download> invece di window.location: non cambia pagina e
    // l'overlay di caricamento lo ignora (niente spinner appeso sui download).
    const a = document.createElement("a");
    a.href = url;
    a.setAttribute("download", "");
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    window.setTimeout(function () {
      try { document.body.removeChild(a); } catch (_) {}
    }, 0);
  }

  function downloadExcel() {
    const month = String(monthPicker.value || "").trim();
    if (!month) return;
    const u = new URL(exportUrl, window.location.origin);
    u.searchParams.set("month", month);
    triggerDownload(u.toString());
  }

  function downloadExcelDaily() {
    const month = String(monthPicker.value || "").trim();
    if (!month) return;
    const u = new URL(exportDailyUrl, window.location.origin);
    u.searchParams.set("month", month);
    triggerDownload(u.toString());
  }

  function onMonthChanged() {
    const month = String(monthPicker.value || "").trim();
    try { sessionStorage.setItem(KEY_LAST_MONTH, month); } catch (_) {}
    updateRangeLabel();
    showFromCacheOrHint();
  }

  function shiftMonth(delta) {
    const raw = String(monthPicker.value || "");
    let y = new Date().getFullYear();
    let m = new Date().getMonth() + 1;
    try {
      if (raw && raw.includes("-")) {
        const p = raw.split("-");
        y = parseInt(p[0], 10);
        m = parseInt(p[1], 10);
      }
    } catch (_) {
      // ignore
    }
    const d = new Date(y, m - 1 + delta, 1);
    monthPicker.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    onMonthChanged();
  }

  // init month picker: prefer last month in sessionStorage, else current month
  const storedMonth = (function () {
    try { return String(sessionStorage.getItem(KEY_LAST_MONTH) || "").trim(); } catch (_) { return ""; }
  })();

  if (!monthPicker.value) {
    if (storedMonth) {
      monthPicker.value = storedMonth;
    } else {
      const now = new Date();
      monthPicker.value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
    }
  }

  monthPicker.addEventListener("change", onMonthChanged);
  if (btnPrev) btnPrev.addEventListener("click", () => shiftMonth(-1));
  if (btnNext) btnNext.addEventListener("click", () => shiftMonth(1));
  if (btnApply) btnApply.addEventListener("click", refresh);
  if (btnExcel) btnExcel.addEventListener("click", downloadExcel);
  if (btnExcelDaily) btnExcelDaily.addEventListener("click", downloadExcelDaily);

  // first render: mostra cache se presente, altrimenti resta in attesa del click su “Carica”
  onMonthChanged();
})();
