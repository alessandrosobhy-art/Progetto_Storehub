(function () {
  const cfg = window.ESTRAZIONI_VERIFICA_CFG || {};
  const apiUrl = cfg.apiUrl || "/estrazioni/api/verifica-rendiconto";
  const exportUrl = cfg.exportUrl || "/estrazioni/api/verifica-rendiconto/export.xlsx";

  const kindSel = document.getElementById("vrKind");
  const modeSel = document.getElementById("vrMode");
  const btnSearch = document.getElementById("vrBtnSearch");
  const btnExport = document.getElementById("vrBtnExport");
  const elSummary = document.getElementById("vrSummary");
  const elError = document.getElementById("vrError");
  const elWarn = document.getElementById("vrWarn");
  const elSpinner = document.getElementById("vrSpinner");
  const elResults = document.getElementById("vrResults");
  const elCount = document.getElementById("vrCount");
  const elFilterKey = document.getElementById("vrFilterKey");
  const elFilterVal = document.getElementById("vrFilterVal");
  const elFilterTextWrap = document.getElementById("vrFilterTextWrap");
  const elFilterDateWrap = document.getElementById("vrFilterDateWrap");
  const elFilterDateStart = document.getElementById("vrFilterDateStart");
  const elFilterDateEnd = document.getElementById("vrFilterDateEnd");
  const btnFilterClear = document.getElementById("vrFilterClear");

  const elBoxWeek = document.getElementById("vrBoxWeek");
  const elBoxMonth = document.getElementById("vrBoxMonth");
  const elBoxPeriod = document.getElementById("vrBoxPeriod");
  const elWeekAnchor = document.getElementById("vrWeekAnchor");
  const elWeekPrev = document.getElementById("vrWeekPrev");
  const elWeekNext = document.getElementById("vrWeekNext");
  const elWeekLabel = document.getElementById("vrWeekLabel");
  const elMonthPicker = document.getElementById("vrMonthPicker");
  const elMonthPrev = document.getElementById("vrMonthPrev");
  const elMonthNext = document.getElementById("vrMonthNext");
  const elMonthLabel = document.getElementById("vrMonthLabel");
  const elPeriodStart = document.getElementById("vrPeriodStart");
  const elPeriodEnd = document.getElementById("vrPeriodEnd");

  const spesaModalEl = document.getElementById("vrPhotoSpesaModal");
  const spesaImgEl = document.getElementById("vrPhotoSpesaImg");
  const spesaLoadingEl = document.getElementById("vrPhotoSpesaLoading");
  const versModalEl = document.getElementById("vrPhotoVersamentoModal");
  const versImgEl = document.getElementById("vrPhotoVersamentoImg");
  const versLoadingEl = document.getElementById("vrPhotoVersamentoLoading");
  const versErrEl = document.getElementById("vrPhotoVersamentoError");

  let currentKind = "VERSAMENTI";
  let currentRows = [];
  let currentColumns = [];
  let currentFilteredRows = [];

  function escapeHtml(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[c];
    });
  }

  function setError(msg) {
    if (!elError) return;
    elError.textContent = String(msg || "");
    elError.classList.toggle("d-none", !msg);
  }

  function setWarn(list) {
    if (!elWarn) return;
    const arr = Array.isArray(list) ? list.filter(Boolean) : [];
    elWarn.innerHTML = arr.map((x) => "<div>" + escapeHtml(x) + "</div>").join("");
    elWarn.classList.toggle("d-none", arr.length === 0);
  }

  function setLoading(on) {
    if (elSpinner) elSpinner.classList.toggle("d-none", !on);
    if (btnSearch) btnSearch.disabled = !!on;
    if (btnExport) btnExport.disabled = !!on || currentFilteredRows.length === 0;
  }

  function clearResults() {
    currentRows = [];
    currentFilteredRows = [];
    currentColumns = [];
    if (elResults) elResults.innerHTML = "";
    if (elCount) elCount.textContent = "";
    if (elFilterKey) { elFilterKey.innerHTML = '<option value="ALL">Tutti i campi</option>'; elFilterKey.disabled = true; }
    if (elFilterVal) { elFilterVal.value = ""; elFilterVal.disabled = true; }
    if (elFilterDateStart) { elFilterDateStart.value = ""; elFilterDateStart.disabled = true; }
    if (elFilterDateEnd) { elFilterDateEnd.value = ""; elFilterDateEnd.disabled = true; }
    if (btnFilterClear) btnFilterClear.disabled = true;
    if (btnExport) btnExport.disabled = true;
  }

  function toIsoDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return y + "-" + m + "-" + dd;
  }

  function mondayOf(d) {
    const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const wd = (x.getDay() + 6) % 7;
    x.setDate(x.getDate() - wd);
    return x;
  }

  function addDays(d, n) {
    const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    x.setDate(x.getDate() + Number(n || 0));
    return x;
  }

  function monthLabel(v) {
    const m = /^(\d{4})-(\d{2})$/.exec(String(v || ""));
    if (!m) return "";
    const d = new Date(Number(m[1]), Number(m[2]) - 1, 1);
    return d.toLocaleDateString("it-IT", { month: "long", year: "numeric" });
  }

  function rangeLabel(startIso, endIso) {
    return String(startIso || "") + " - " + String(endIso || "");
  }

  function syncModeUi() {
    const mode = String(modeSel && modeSel.value || "MONTH").toUpperCase();
    if (elBoxWeek) elBoxWeek.classList.toggle("d-none", mode !== "WEEK");
    if (elBoxMonth) elBoxMonth.classList.toggle("d-none", mode !== "MONTH");
    if (elBoxPeriod) elBoxPeriod.classList.toggle("d-none", mode !== "PERIOD");
  }

  function setDefaults() {
    const today = new Date();
    if (elWeekAnchor) elWeekAnchor.value = toIsoDate(today);
    if (elMonthPicker) elMonthPicker.value = toIsoDate(today).slice(0, 7);
    if (elPeriodStart) elPeriodStart.value = toIsoDate(new Date(today.getFullYear(), today.getMonth(), 1));
    if (elPeriodEnd) elPeriodEnd.value = toIsoDate(today);
    refreshLabels();
  }

  function refreshLabels() {
    if (elWeekAnchor && elWeekLabel) {
      const d = mondayOf(new Date(elWeekAnchor.value || new Date()));
      const e = addDays(d, 6);
      elWeekLabel.textContent = rangeLabel(toIsoDate(d), toIsoDate(e));
    }
    if (elMonthPicker && elMonthLabel) {
      elMonthLabel.textContent = monthLabel(elMonthPicker.value || "");
    }
  }

  function getRange() {
    const mode = String(modeSel && modeSel.value || "MONTH").toUpperCase();
    if (mode === "WEEK") {
      const a = elWeekAnchor && elWeekAnchor.value ? new Date(elWeekAnchor.value) : new Date();
      const start = mondayOf(a);
      const end = addDays(start, 6);
      return { start: toIsoDate(start), end: toIsoDate(end), label: rangeLabel(toIsoDate(start), toIsoDate(end)) };
    }
    if (mode === "PERIOD") {
      const s = String(elPeriodStart && elPeriodStart.value || "");
      const e = String(elPeriodEnd && elPeriodEnd.value || "");
      if (!s || !e) return null;
      return { start: s, end: e, label: rangeLabel(s, e) };
    }
    const m = String(elMonthPicker && elMonthPicker.value || "");
    const mm = /^(\d{4})-(\d{2})$/.exec(m);
    if (!mm) return null;
    const y = Number(mm[1]);
    const mo = Number(mm[2]) - 1;
    const start = new Date(y, mo, 1);
    const end = new Date(y, mo + 1, 0);
    return { start: toIsoDate(start), end: toIsoDate(end), label: monthLabel(m) };
  }

  function groupRowsByStore(rows) {
    const out = new Map();
    (rows || []).forEach((r) => {
      const key = String(r.store || "");
      if (!out.has(key)) out.set(key, []);
      out.get(key).push(r);
    });
    return Array.from(out.entries());
  }

  function isDateFilterKey(key) {
    return ["data", "data_versamento", "dal", "al"].includes(String(key || "").trim());
  }

  function rowDateIsoField(key) {
    const map = {
      data: "data_iso",
      data_versamento: "data_versamento_iso",
      dal: "dal_iso",
      al: "al_iso",
    };
    return map[String(key || "").trim()] || "";
  }

  function syncFilterInputUi() {
    const key = String(elFilterKey && elFilterKey.value || "ALL");
    const isDate = isDateFilterKey(key);
    if (elFilterTextWrap) elFilterTextWrap.classList.toggle("d-none", isDate);
    if (elFilterDateWrap) elFilterDateWrap.classList.toggle("d-none", !isDate);
    if (elFilterVal) elFilterVal.disabled = (currentRows.length === 0) || isDate;
    if (elFilterDateStart) elFilterDateStart.disabled = (currentRows.length === 0) || !isDate;
    if (elFilterDateEnd) elFilterDateEnd.disabled = (currentRows.length === 0) || !isDate;
  }

  function renderPhotoButtonHtml(kind, url) {
    const u = String(url || "").trim();
    if (!u) return '<span class="text-muted">—</span>';
    if (String(kind).toUpperCase() === "SPESE") {
      return '<button type="button" class="btn btn-sm btn-outline-secondary js-photo-spesa" data-bs-toggle="modal" data-bs-target="#vrPhotoSpesaModal" data-photo-url="' + escapeHtml(u) + '">📷</button>';
    }
    return '<button type="button" class="btn btn-sm btn-outline-secondary js-photo-vers" data-bs-toggle="modal" data-bs-target="#vrPhotoVersamentoModal" data-photo-url="' + escapeHtml(u) + '">📷</button>';
  }

  function renderVerifyCell(row) {
    const checked = row.verificato ? " checked" : "";
    return '<input type="checkbox" class="form-check-input js-verificato" data-record-key="' + escapeHtml(row.record_key || "") + '"' + checked + '>';
  }

  function renderNoteCell(row) {
    return '<input type="text" class="form-control form-control-sm js-nota" data-record-key="' + escapeHtml(row.record_key || "") + '" value="' + escapeHtml(row.nota || "") + '">';
  }

  function renderSaveCell(row) {
    return '<button type="button" class="btn btn-sm btn-primary js-save-row" data-kind="' + escapeHtml(currentKind) + '" data-site="' + escapeHtml(row.site || "") + '" data-record-key="' + escapeHtml(row.record_key || "") + '">Salva</button><div class="small text-muted mt-1 js-save-status"></div>';
  }

  function renderSpeseTable(rows) {
    const table = document.createElement("table");
    table.className = "table table-sm table-striped align-middle mb-0";
    table.innerHTML =
      '<thead><tr>' +
      '<th>Data</th><th>Tipo</th><th>Fornitore / Spesa</th><th>Documento</th><th class="text-end">Importo (EUR)</th><th class="text-center">Foto</th>' +
      '</tr></thead>';
    const tb = document.createElement("tbody");
    (rows || []).forEach((r) => {
      const tr = document.createElement("tr");
      tr.dataset.recordKey = r.record_key || "";
      tr.innerHTML =
        '<td>' + escapeHtml(r.data || "") + '</td>' +
        '<td>' + escapeHtml(r.tipo || "") + '</td>' +
        '<td>' + escapeHtml(r.fornitore || "") + '</td>' +
        '<td>' + escapeHtml(r.documento || "") + '</td>' +
        '<td class="text-end">' + escapeHtml(r.importo || "") + '</td>' +
        '<td class="text-center">' + renderPhotoButtonHtml("SPESE", r.foto_url || "") + '</td>';
      tb.appendChild(tr);
    });
    table.appendChild(tb);
    return table;
  }

  function renderVersamentiTable(rows) {
    const table = document.createElement("table");
    table.className = "table table-sm align-middle mb-0";
    table.innerHTML =
      '<thead><tr>' +
      '<th>Data versamento</th><th>Nome e cognome</th><th class="text-end">Valore</th><th class="text-center">Foto</th>' +
      '</tr></thead>';
    const tb = document.createElement("tbody");
    (rows || []).forEach((r) => {
      const tr = document.createElement("tr");
      tr.dataset.recordKey = r.record_key || "";
      tr.innerHTML =
        '<td>' + escapeHtml(r.data_versamento || "") + '</td>' +
        '<td>' + escapeHtml(r.nome || "") + '</td>' +
        '<td class="text-end">' + escapeHtml(r.valore || "") + ' EUR</td>' +
        '<td class="text-center">' + renderPhotoButtonHtml("VERSAMENTI", r.foto_url || "") + '</td>';
      tb.appendChild(tr);

      const tr2 = document.createElement("tr");
      tr2.className = "table-light";
      tr2.innerHTML =
        '<td colspan="4" class="small">' +
        '<div class="d-flex flex-wrap gap-3">' +
        '<div><span class="text-muted">Periodo:</span> <span class="fw-semibold">' + escapeHtml(r.dal || "") + '</span> - <span class="fw-semibold">' + escapeHtml(r.al || "") + '</span></div>' +
        '<div><span class="text-muted">Tipo:</span> <span class="fw-semibold">' + escapeHtml(r.tipo || "") + '</span></div>' +
        '<div><span class="text-muted">Tessera:</span> <span class="fw-semibold">' + escapeHtml(r.tessera || "") + '</span></div>' +
        '<div><span class="text-muted">Riferimento:</span> <span class="fw-semibold">' + escapeHtml(r.riferimento || "") + '</span></div>' +
        '</div>' +
        '</td>';
      tb.appendChild(tr2);
    });
    table.appendChild(tb);
    return table;
  }

  function renderResults(rows) {
    if (!elResults) return;
    elResults.innerHTML = "";
    const grouped = groupRowsByStore(rows);
    if (!grouped.length) {
      elResults.innerHTML = '<div class="text-muted">Nessun risultato.</div>';
      if (elCount) elCount.textContent = "0 righe";
      return;
    }

    grouped.forEach(([store, list]) => {
      const card = document.createElement("div");
      card.className = "card shadow-sm mb-3";
      const hd = document.createElement("div");
      hd.className = "card-header py-2 d-flex justify-content-between align-items-center";
      hd.innerHTML = '<div class="fw-semibold">' + escapeHtml(store) + '</div><div class="small text-muted">' + list.length + ' righe</div>';
      card.appendChild(hd);
      const bd = document.createElement("div");
      bd.className = "card-body p-0";
      bd.appendChild(currentKind === "SPESE" ? renderSpeseTable(list) : renderVersamentiTable(list));
      card.appendChild(bd);
      elResults.appendChild(card);
    });
    if (elCount) elCount.textContent = rows.length + " righe";
  }

  function populateFilterOptions() {
    if (!elFilterKey) return;
    const keys = new Map();
    (currentColumns || []).forEach((c) => {
      const key = String(c && c.key || "");
      const typ = String(c && c.type || "");
      if (!key || typ === "photo") return;
      if (key === "verificato" || key === "nota") return;
      keys.set(key, String(c && c.label || key));
    });
    elFilterKey.innerHTML = '<option value="ALL">Tutti i campi</option>';
    keys.forEach((label, key) => {
      const opt = document.createElement("option");
      opt.value = key;
      opt.textContent = label;
      elFilterKey.appendChild(opt);
    });
    elFilterKey.disabled = currentRows.length === 0;
    syncFilterInputUi();
    if (btnFilterClear) btnFilterClear.disabled = currentRows.length === 0;
  }

  function applyClientFilter() {
    const key = String(elFilterKey && elFilterKey.value || "ALL");
    const q = String(elFilterVal && elFilterVal.value || "").trim().toLowerCase();
    const dateFrom = String(elFilterDateStart && elFilterDateStart.value || "").trim();
    const dateTo = String(elFilterDateEnd && elFilterDateEnd.value || "").trim();
    if (key !== "ALL" && isDateFilterKey(key)) {
      const isoKey = rowDateIsoField(key);
      if (!dateFrom && !dateTo) {
        currentFilteredRows = currentRows.slice();
        renderResults(currentFilteredRows);
        if (btnExport) btnExport.disabled = currentFilteredRows.length === 0;
        return;
      }
      currentFilteredRows = currentRows.filter((r) => {
        const rv = String(r[isoKey] || "").trim();
        if (!rv) return false;
        if (dateFrom && rv < dateFrom) return false;
        if (dateTo && rv > dateTo) return false;
        return true;
      });
      renderResults(currentFilteredRows);
      if (btnExport) btnExport.disabled = currentFilteredRows.length === 0;
      return;
    }
    if (!q) {
      currentFilteredRows = currentRows.slice();
      renderResults(currentFilteredRows);
      if (btnExport) btnExport.disabled = currentFilteredRows.length === 0;
      return;
    }
    currentFilteredRows = currentRows.filter((r) => {
      if (key !== "ALL") return String(r[key] || "").toLowerCase().includes(q);
      return Object.keys(r || {}).some((k) => {
        if (k === "record_key" || k === "foto_url" || k === "foto_file" || k === "verificato") return false;
        return String(r[k] || "").toLowerCase().includes(q);
      });
    });
    renderResults(currentFilteredRows);
    if (btnExport) btnExport.disabled = currentFilteredRows.length === 0;
  }

  async function doSearch() {
    setError("");
    setWarn([]);
    clearResults();
    const range = getRange();
    if (!range) {
      setError("Seleziona un intervallo valido.");
      return;
    }
    currentKind = String(kindSel && kindSel.value || "VERSAMENTI").toUpperCase();
    if (elSummary) elSummary.textContent = range.label;

    const params = new URLSearchParams();
    params.set("kind", currentKind);
    params.set("start", range.start);
    params.set("end", range.end);

    setLoading(true);
    try {
      const res = await fetch(apiUrl + "?" + params.toString(), { credentials: "same-origin" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data || data.ok === false) {
        throw new Error((data && data.error) || ("Errore ricerca (" + res.status + ")"));
      }
      setWarn(data.warnings || []);
      if (elSummary && data.total_display) elSummary.textContent = range.label + " · Totale: " + data.total_display;
      currentColumns = data.columns || [];
      currentRows = data.rows || [];
      populateFilterOptions();
      applyClientFilter();
    } catch (err) {
      setError(err && err.message ? err.message : "Errore ricerca.");
    } finally {
      setLoading(false);
    }
  }

  async function exportCurrentView() {
    const range = getRange();
    if (!range || !currentFilteredRows.length) return;
    try {
      const res = await fetch(exportUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kind: currentKind,
          start: range.start,
          end: range.end,
          columns: currentColumns,
          rows: currentFilteredRows,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error((data && data.error) || ("Errore export (" + res.status + ")"));
      }
      const blob = await res.blob();
      const cd = String(res.headers.get("Content-Disposition") || "");
      let filename = "verifica_rendiconto.xlsx";
      const m = /filename=([^;]+)/i.exec(cd);
      if (m && m[1]) filename = m[1].replace(/['"]/g, "").trim();
      const a = document.createElement("a");
      const url = URL.createObjectURL(blob);
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err && err.message ? err.message : "Errore export.");
    }
  }

  function bindEvents() {
    if (modeSel) modeSel.addEventListener("change", () => { syncModeUi(); refreshLabels(); });
    if (elWeekAnchor) elWeekAnchor.addEventListener("change", refreshLabels);
    if (elMonthPicker) elMonthPicker.addEventListener("change", refreshLabels);
    if (elWeekPrev) elWeekPrev.addEventListener("click", () => { const d = mondayOf(new Date(elWeekAnchor.value || new Date())); elWeekAnchor.value = toIsoDate(addDays(d, -7)); refreshLabels(); });
    if (elWeekNext) elWeekNext.addEventListener("click", () => { const d = mondayOf(new Date(elWeekAnchor.value || new Date())); elWeekAnchor.value = toIsoDate(addDays(d, 7)); refreshLabels(); });
    if (elMonthPrev) elMonthPrev.addEventListener("click", () => {
      const mm = /^(\d{4})-(\d{2})$/.exec(String(elMonthPicker.value || ""));
      if (!mm) return;
      const d = new Date(Number(mm[1]), Number(mm[2]) - 2, 1);
      elMonthPicker.value = toIsoDate(d).slice(0, 7);
      refreshLabels();
    });
    if (elMonthNext) elMonthNext.addEventListener("click", () => {
      const mm = /^(\d{4})-(\d{2})$/.exec(String(elMonthPicker.value || ""));
      if (!mm) return;
      const d = new Date(Number(mm[1]), Number(mm[2]), 1);
      elMonthPicker.value = toIsoDate(d).slice(0, 7);
      refreshLabels();
    });
    if (btnSearch) btnSearch.addEventListener("click", doSearch);
    if (btnExport) btnExport.addEventListener("click", exportCurrentView);
    if (elFilterKey) elFilterKey.addEventListener("change", () => { syncFilterInputUi(); applyClientFilter(); });
    if (elFilterVal) elFilterVal.addEventListener("input", applyClientFilter);
    if (elFilterDateStart) elFilterDateStart.addEventListener("change", applyClientFilter);
    if (elFilterDateEnd) elFilterDateEnd.addEventListener("change", applyClientFilter);
    if (btnFilterClear) btnFilterClear.addEventListener("click", () => {
      if (elFilterKey) elFilterKey.value = "ALL";
      if (elFilterVal) elFilterVal.value = "";
      if (elFilterDateStart) elFilterDateStart.value = "";
      if (elFilterDateEnd) elFilterDateEnd.value = "";
      syncFilterInputUi();
      applyClientFilter();
    });

    document.addEventListener("click", function (ev) {
      const sp = ev.target && ev.target.closest ? ev.target.closest(".js-photo-spesa") : null;
      if (sp && spesaModalEl) {
        const url = sp.getAttribute("data-photo-url") || "";
        if (spesaLoadingEl) spesaLoadingEl.style.display = "block";
        if (spesaImgEl) {
          spesaImgEl.style.display = "none";
          spesaImgEl.onload = function () { if (spesaLoadingEl) spesaLoadingEl.style.display = "none"; spesaImgEl.style.display = "inline-block"; };
          spesaImgEl.src = url;
        }
        return;
      }
      const vp = ev.target && ev.target.closest ? ev.target.closest(".js-photo-vers") : null;
      if (vp && versModalEl) {
        const url = vp.getAttribute("data-photo-url") || "";
        if (versLoadingEl) versLoadingEl.style.display = "block";
        if (versErrEl) versErrEl.style.display = "none";
        if (versImgEl) {
          versImgEl.style.display = "none";
          versImgEl.onload = function () { if (versLoadingEl) versLoadingEl.style.display = "none"; if (versErrEl) versErrEl.style.display = "none"; versImgEl.style.display = "inline-block"; };
          versImgEl.onerror = function () { if (versLoadingEl) versLoadingEl.style.display = "none"; if (versErrEl) versErrEl.style.display = "block"; versImgEl.style.display = "none"; };
          versImgEl.src = url;
        }
      }
    });
  }

  bindEvents();
  syncModeUi();
  setDefaults();
})();
