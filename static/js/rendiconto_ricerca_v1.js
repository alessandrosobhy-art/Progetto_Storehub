(function () {
  "use strict";

  // Build: v1.3.3 (UI hotfix: better diagnostics + cache-busting via template)

  const cfg = window.REND_SEARCH_CFG || {};
  const I18N = cfg.i18n || {};
  function t(key, fallback) {
    const v = I18N && Object.prototype.hasOwnProperty.call(I18N, key) ? I18N[key] : "";
    return String(v || fallback || "");
  }

  function _showFatal(err) {
    try {
      const el = document.getElementById("rsError");
      if (!el) return;
      const msg = (err && err.message) ? err.message : String(err || "");
      el.textContent = t("uiError", "Errore UI") + ": " + msg;
      el.classList.remove("d-none");
    } catch (_) {
      // ignore
    }
  }

  window.addEventListener("error", function (ev) {
    if (ev && ev.error) _showFatal(ev.error);
    else if (ev && ev.message) _showFatal(ev.message);
  });

  window.addEventListener("unhandledrejection", function (ev) {
    const r = ev && ev.reason ? ev.reason : ev;
    _showFatal(r);
  });

  const apiUrl = cfg.apiUrl || "/rendiconto/api/ricerca";
  const exportUrl = cfg.exportUrl || "/rendiconto/api/ricerca/export.xlsx";

  // Elements
  const kindSel = document.getElementById("rsKind");
  const modeSel = document.getElementById("rsMode");

  const boxWeek = document.getElementById("rsBoxWeek");
  const boxMonth = document.getElementById("rsBoxMonth");
  const boxPeriod = document.getElementById("rsBoxPeriod");

  const weekAnchor = document.getElementById("rsWeekAnchor");
  const weekPrev = document.getElementById("rsWeekPrev");
  const weekNext = document.getElementById("rsWeekNext");
  const weekLabel = document.getElementById("rsWeekLabel");

  const monthPicker = document.getElementById("rsMonthPicker");
  const monthPrev = document.getElementById("rsMonthPrev");
  const monthNext = document.getElementById("rsMonthNext");
  const monthLabel = document.getElementById("rsMonthLabel");

  const periodStart = document.getElementById("rsPeriodStart");
  const periodEnd = document.getElementById("rsPeriodEnd");

  const btnSearch = document.getElementById("rsBtnSearch");
  const btnExport = document.getElementById("rsBtnExport");

  const filterKey = document.getElementById("rsFilterKey");
  const filterVal = document.getElementById("rsFilterVal");
  const filterClear = document.getElementById("rsFilterClear");

  const elSummary = document.getElementById("rsSummary");
  const elCount = document.getElementById("rsCount");
  const elSpinner = document.getElementById("rsSpinner");
  const elError = document.getElementById("rsError");
  const elWarn = document.getElementById("rsWarn");
  const elHint = document.getElementById("rsHint");

  const elResults = document.getElementById("rsResults");

  // Photo modals (same ids used in Spese/Versamenti pages)
  const spesaModalEl = document.getElementById("photoSpesaModal");
  const spesaImgEl = document.getElementById("photoSpesaImg");
  const spesaLoadingEl = document.getElementById("photoSpesaLoading");

  const versModalEl = document.getElementById("photoVersamentoModal");
  const versImgEl = document.getElementById("photoVersamentoImg");
  const versLoadingEl = document.getElementById("photoVersamentoLoading");
  const versErrEl = document.getElementById("photoVersamentoError");

  // State
  let currentKind = "VERSAMENTI";
  let currentColumns = [];
  let currentRows = [];
  let filteredRows = [];

  // -------------------------
  // Helpers
  // -------------------------
  function pad2(n) {
    return String(n).padStart(2, "0");
  }

  function toISO(d) {
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  function parseISO(s) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(s || "").trim());
    if (!m) return null;
    const y = Number(m[1]), mo = Number(m[2]) - 1, da = Number(m[3]);
    const d = new Date(y, mo, da);
    if (isNaN(d.getTime())) return null;
    return d;
  }

  function formatIT(iso) {
    const d = parseISO(iso);
    if (!d) return String(iso || "");
    return pad2(d.getDate()) + "/" + pad2(d.getMonth() + 1) + "/" + d.getFullYear();
  }

  function addDays(d, days) {
    const x = new Date(d.getTime());
    x.setDate(x.getDate() + days);
    return x;
  }

  function addMonths(d, months) {
    const x = new Date(d.getTime());
    x.setMonth(x.getMonth() + months);
    return x;
  }

  // ISO-week starting Monday
  function startOfWeek(d) {
    const x = new Date(d.getTime());
    const day = (x.getDay() + 6) % 7; // Mon=0..Sun=6
    x.setDate(x.getDate() - day);
    return x;
  }

  function endOfWeek(d) {
    return addDays(startOfWeek(d), 6);
  }

  function startOfMonth(d) {
    return new Date(d.getFullYear(), d.getMonth(), 1);
  }

  function endOfMonth(d) {
    return new Date(d.getFullYear(), d.getMonth() + 1, 0);
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function setLoading(on) {
    if (elSpinner) elSpinner.classList.toggle("d-none", !on);
    btnSearch.disabled = !!on;
    kindSel.disabled = !!on;
    modeSel.disabled = !!on;
  }

  function setError(msg) {
    if (!elError) return;
    const s = String(msg || "").trim();
    if (!s) {
      elError.classList.add("d-none");
      elError.textContent = "";
      return;
    }
    elError.textContent = s;
    elError.classList.remove("d-none");
  }

  function setWarn(list) {
    if (!elWarn) return;
    const arr = (list || []).map((x) => String(x || "").trim()).filter(Boolean);
    const uniq = Array.from(new Set(arr));
    if (!uniq.length) {
      elWarn.classList.add("d-none");
      elWarn.textContent = "";
      return;
    }
    elWarn.textContent = uniq.join(" · ");
    elWarn.classList.remove("d-none");
  }

  function showMode(mode) {
    boxWeek.classList.toggle("d-none", mode !== "WEEK");
    boxMonth.classList.toggle("d-none", mode !== "MONTH");
    boxPeriod.classList.toggle("d-none", mode !== "PERIOD");
  }

  function getRange() {
    const mode = String(modeSel.value || "MONTH").toUpperCase();

    if (mode === "WEEK") {
      const anchor = parseISO(weekAnchor.value) || new Date();
      const s = startOfWeek(anchor);
      const e = endOfWeek(anchor);
      return {
        start: toISO(s),
        end: toISO(e),
        label: t("weekPrefix", "Settimana") + ": " + formatIT(toISO(s)) + " - " + formatIT(toISO(e)),
      };
    }

    if (mode === "PERIOD") {
      const s = parseISO(periodStart.value);
      const e = parseISO(periodEnd.value);
      if (!s || !e) return null;
      return {
        start: toISO(s),
        end: toISO(e),
        label: t("periodPrefix", "Periodo") + ": " + formatIT(toISO(s)) + " - " + formatIT(toISO(e)),
      };
    }

    // MONTH
    const mp = String(monthPicker.value || "").trim();
    const mm = /^(\d{4})-(\d{2})$/.exec(mp);
    let d = new Date();
    if (mm) d = new Date(Number(mm[1]), Number(mm[2]) - 1, 1);
    const s = startOfMonth(d);
    const e = endOfMonth(d);
    return {
      start: toISO(s),
      end: toISO(e),
      label: t("monthPrefix", "Mese") + ": " + formatIT(toISO(s)) + " - " + formatIT(toISO(e)),
    };
  }

  function clearResults() {
    currentColumns = [];
    currentRows = [];
    filteredRows = [];
    if (elResults) elResults.innerHTML = "";
    if (elCount) elCount.textContent = "";
    btnExport.disabled = true;
    setFilterEnabled(false);
  }

  function setFilterEnabled(on) {
    if (filterKey) filterKey.disabled = !on;
    if (filterVal) filterVal.disabled = !on;
    if (filterClear) filterClear.disabled = !on;
    if (elHint) elHint.style.display = on ? "" : "none";
  }

  function buildFilterOptions() {
    if (!filterKey) return;
    const opts = [];
    // Always include ALL
    opts.push({ value: "ALL", label: t("allFields", "Tutti i campi") });

    // Use columns from API (exclude photo)
    (currentColumns || []).forEach((c) => {
      const key = String((c || {}).key || "").trim();
      const lbl = String((c || {}).label || key).trim();
      const typ = String((c || {}).type || "").trim().toLowerCase();
      if (!key) return;
      if (typ === "photo") return;
      opts.push({ value: key, label: lbl });
    });

    // Deduplicate keeping first
    const seen = new Set();
    const uniq = [];
    for (const o of opts) {
      if (!seen.has(o.value)) {
        seen.add(o.value);
        uniq.push(o);
      }
    }

    filterKey.innerHTML = "";
    uniq.forEach((o) => {
      const opt = document.createElement("option");
      opt.value = o.value;
      opt.textContent = o.label;
      filterKey.appendChild(opt);
    });

    filterKey.value = "ALL";
  }

  function getFilterableKeys() {
    const keys = [];
    (currentColumns || []).forEach((c) => {
      const key = String((c || {}).key || "").trim();
      const typ = String((c || {}).type || "").trim().toLowerCase();
      if (!key) return;
      if (typ === "photo") return;
      keys.push(key);
    });
    return keys;
  }

  function applyFilterAndRender() {
    const q = String((filterVal && filterVal.value) || "").trim().toLowerCase();
    const k = String((filterKey && filterKey.value) || "ALL").trim();

    if (!q) {
      filteredRows = currentRows.slice();
    } else {
      const keys = getFilterableKeys();
      filteredRows = (currentRows || []).filter((r) => {
        if (!r) return false;
        if (k && k !== "ALL") {
          const v = r[k] !== undefined && r[k] !== null ? String(r[k]) : "";
          return v.toLowerCase().includes(q);
        }
        // ALL
        for (const kk of keys) {
          const v = r[kk] !== undefined && r[kk] !== null ? String(r[kk]) : "";
          if (v.toLowerCase().includes(q)) return true;
        }
        return false;
      });
    }

    renderResults(filteredRows);
    btnExport.disabled = filteredRows.length === 0;

    if (elCount) {
      const total = (currentRows || []).length;
      const shown = (filteredRows || []).length;
      elCount.textContent = total === shown
        ? (t("rows", "Righe") + ": " + shown)
        : (t("rows", "Righe") + ": " + shown + " (" + t("of", "su") + " " + total + ")");
    }
  }

  function groupRowsByStore(rows) {
    const m = new Map();
    (rows || []).forEach((r) => {
      const store = String((r && r.store) || "").trim() || "(" + t("storeUnavailable", "store non disponibile") + ")";
      if (!m.has(store)) m.set(store, []);
      m.get(store).push(r);
    });

    // Order stores: numeric site first if possible
    const entries = Array.from(m.entries());
    entries.sort((a, b) => {
      const as = String(a[0] || "");
      const bs = String(b[0] || "");
      const aSite = (as.split("-")[0] || "").trim();
      const bSite = (bs.split("-")[0] || "").trim();
      const an = Number(aSite);
      const bn = Number(bSite);
      const aIsNum = !isNaN(an);
      const bIsNum = !isNaN(bn);
      if (aIsNum && bIsNum) return an - bn;
      if (aIsNum) return -1;
      if (bIsNum) return 1;
      return as.localeCompare(bs);
    });

    return entries;
  }

  function renderResults(rows) {
    if (!elResults) return;
    elResults.innerHTML = "";

    const entries = groupRowsByStore(rows);
    if (!entries.length) {
      elResults.innerHTML = '<div class="text-muted">' + escapeHtml(t("noResults", "Nessun risultato.")) + '</div>';
      return;
    }

    const kind = String(currentKind || "VERSAMENTI").toUpperCase();

    entries.forEach(([storeDisp, storeRows]) => {
      const card = document.createElement("div");
      card.className = "card mb-3";

      const header = document.createElement("div");
      header.className = "card-header d-flex align-items-center justify-content-between flex-wrap gap-2";
      header.innerHTML = '<span>' + escapeHtml(t("store", "Store")) + ': <strong>' + escapeHtml(storeDisp) + '</strong></span>' +
        '<span class="text-muted small">' + escapeHtml(t("rows", "Righe")) + ': <strong>' + (storeRows ? storeRows.length : 0) + '</strong></span>';
      card.appendChild(header);

      const body = document.createElement("div");
      body.className = "card-body p-0";

      const wrap = document.createElement("div");
      wrap.className = "table-responsive";

      if (kind === "SPESE") {
        wrap.appendChild(renderSpeseTable(storeRows || []));
      } else {
        wrap.appendChild(renderVersamentiTable(storeRows || []));
      }

      body.appendChild(wrap);
      card.appendChild(body);
      elResults.appendChild(card);
    });
  }

  function renderSpeseTable(rows) {
    const table = document.createElement("table");
    table.className = "table table-sm table-striped align-middle mb-0";

    table.innerHTML =
      '<thead><tr>' +
      '<th style="white-space:nowrap;">' + escapeHtml(t("date", "Data")) + '</th>' +
      '<th style="white-space:nowrap;">' + escapeHtml(t("type", "Tipo")) + '</th>' +
      '<th>' + escapeHtml(t("supplierExpense", "Fornitore / Spesa")) + '</th>' +
      '<th style="white-space:nowrap;">' + escapeHtml(t("document", "Scontrino / Fattura")) + '</th>' +
      '<th class="text-end" style="white-space:nowrap;">Importo (€)</th>' +
      '<th class="text-center" style="white-space:nowrap;">' + escapeHtml(t("photo", "Foto")) + '</th>' +
      '</tr></thead>';

    const tb = document.createElement("tbody");
    (rows || []).forEach((r) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        '<td style="white-space:nowrap;">' + escapeHtml(r.data || "") + '</td>' +
        '<td style="white-space:nowrap;">' + escapeHtml(r.tipo || "") + '</td>' +
        '<td>' + escapeHtml(r.fornitore || "") + '</td>' +
        '<td style="white-space:nowrap;">' + escapeHtml(r.documento || "") + '</td>' +
        '<td class="text-end" style="white-space:nowrap;">' + escapeHtml(r.importo || "") + '</td>' +
        '<td class="text-center" style="white-space:nowrap;">' + renderPhotoButtonHtml("SPESE", r.foto_url || "") + '</td>';
      tb.appendChild(tr);
    });

    table.appendChild(tb);
    return table;
  }

  function renderVersamentiTable(rows) {
    const table = document.createElement("table");
    table.className = "table table-sm align-middle mb-0 versamenti-table";

    table.innerHTML =
      '<thead><tr>' +
      '<th style="white-space:nowrap;">' + escapeHtml(t("depositDate", "Data versamento")) + '</th>' +
      '<th>' + escapeHtml(t("fullName", "Nome e cognome")) + '</th>' +
      '<th class="text-end" style="white-space:nowrap;">' + escapeHtml(t("value", "Valore")) + '</th>' +
      '<th class="text-center" style="white-space:nowrap;">' + escapeHtml(t("photo", "Foto")) + '</th>' +
      '</tr></thead>';

    const tb = document.createElement("tbody");
    (rows || []).forEach((r) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        '<td style="white-space:nowrap;">' + escapeHtml(r.data_versamento || "") + '</td>' +
        '<td>' + escapeHtml(r.nome || "") + '</td>' +
        '<td class="text-end" style="white-space:nowrap;">' + escapeHtml((r.valore || "")) + ' €</td>' +
        '<td class="text-center" style="white-space:nowrap;">' + renderPhotoButtonHtml("VERSAMENTI", r.foto_url || "") + '</td>';
      tb.appendChild(tr);

      // Details row (come subrow nella pagina Versamenti)
      const tr2 = document.createElement("tr");
      tr2.className = "table-light";
      tr2.innerHTML =
        '<td colspan="4" class="small">' +
        '<div class="d-flex flex-wrap gap-3">' +
        '<div style="white-space:nowrap;"><span class="text-muted">' + escapeHtml(t("period", "Periodo")) + ':</span> <span class="fw-semibold">' + escapeHtml(r.dal || "") + '</span> - <span class="fw-semibold">' + escapeHtml(r.al || "") + '</span></div>' +
        '<div style="white-space:nowrap;"><span class="text-muted">' + escapeHtml(t("type", "Tipo")) + ':</span> <span class="fw-semibold">' + escapeHtml(r.tipo || "") + '</span></div>' +
        '<div style="white-space:nowrap;"><span class="text-muted">' + escapeHtml(t("card", "Tessera")) + ':</span> <span class="fw-semibold">' + escapeHtml(r.tessera || "") + '</span></div>' +
        '<div style="white-space:nowrap;"><span class="text-muted">' + escapeHtml(t("reference", "Riferimento")) + ':</span> <span class="fw-semibold">' + escapeHtml(r.riferimento || "") + '</span></div>' +
        '</div>' +
        '</td>';
      tb.appendChild(tr2);
    });

    table.appendChild(tb);
    return table;
  }

  function renderPhotoButtonHtml(kind, url) {
    const u = String(url || "").trim();
    if (!u) return '<span class="text-muted">—</span>';
    if (String(kind).toUpperCase() === "SPESE") {
      return (
        '<button type="button" class="btn btn-sm btn-outline-secondary photo-btn" ' +
        'data-bs-toggle="modal" data-bs-target="#photoSpesaModal" ' +
        'data-photo-url="' + escapeHtml(u) + '" aria-label="' + escapeHtml(t("viewPhoto", "Vedi foto")) + '">📷</button>'
      );
    }
    return (
      '<button type="button" class="btn btn-sm btn-outline-secondary js-photo-btn" ' +
      'data-bs-toggle="modal" data-bs-target="#photoVersamentoModal" ' +
      'data-photo-url="' + escapeHtml(u) + '" title="' + escapeHtml(t("viewPhoto", "Vedi foto")) + '">📷</button>'
    );
  }

  async function doSearch() {
    setError("");
    setWarn([]);
    clearResults();

    const range = getRange();
    if (!range) {
      setError(t("invalidRange", "Seleziona un intervallo valido."));
      return;
    }

    const kind = String(kindSel.value || "VERSAMENTI").toUpperCase();
    currentKind = kind;
    if (elSummary) elSummary.textContent = range.label;

    const params = new URLSearchParams();
    params.set("kind", kind);
    params.set("start", range.start);
    params.set("end", range.end);
    const url = apiUrl + "?" + params.toString();

    setLoading(true);
    try {
      const res = await fetch(url, { credentials: "same-origin" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data || data.ok === false) {
        const msg = data && data.error ? data.error : t("searchError", "Errore ricerca") + " (" + res.status + ")";
        throw new Error(msg);
      }

      setWarn(data.warnings || []);
      if (elSummary && data.total_display) {
        elSummary.textContent = range.label + " · Totale: " + data.total_display;
      }

      currentColumns = data.columns || [];
      currentRows = data.rows || [];

      buildFilterOptions();
      setFilterEnabled(true);
      if (filterVal) filterVal.value = "";

      // Initial render
      filteredRows = currentRows.slice();
      renderResults(filteredRows);
      btnExport.disabled = filteredRows.length === 0;
      if (elCount) elCount.textContent = t("rows", "Righe") + ": " + filteredRows.length;
    } catch (e) {
      setError(e && e.message ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  function getVisibleRowsForExport() {
    return (filteredRows || []).map((r) => {
      const out = {};
      (currentColumns || []).forEach((col) => {
        const k = String((col || {}).key || "");
        const typ = String((col || {}).type || "").toLowerCase();
        if (!k) return;
        if (typ === "photo") {
          out[k + "_url"] = r && r[k + "_url"] ? r[k + "_url"] : (r && r[k] ? r[k] : "");
          out[k + "_file"] = r && r[k + "_file"] ? r[k + "_file"] : "";
        } else {
          out[k] = r && r[k] !== undefined && r[k] !== null ? String(r[k]) : "";
        }
      });
      return out;
    });
  }

  async function doExport() {
    setError("");

    const range = getRange();
    if (!range) {
      setError(t("invalidRange", "Intervallo non valido."));
      return;
    }

    const payload = {
      kind: String(kindSel.value || "VERSAMENTI").toUpperCase(),
      start: range.start,
      end: range.end,
      columns: currentColumns,
      rows: getVisibleRowsForExport(),
    };

    try {
      btnExport.disabled = true;
      const res = await fetch(exportUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const msg = data && data.error ? data.error : t("exportError", "Errore export") + " (" + res.status + ")";
        throw new Error(msg);
      }

      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);

      let filename = "";
      const cd = res.headers.get("Content-Disposition") || "";
      const m = /filename\*=UTF-8''([^;]+)|filename="?([^"]+)"?/i.exec(cd);
      if (m) filename = decodeURIComponent(m[1] || m[2] || "");
      if (!filename) {
        filename =
          "rendiconto_ricerca_" +
          payload.kind.toLowerCase() +
          "_" +
          payload.start +
          "_" +
          payload.end +
          ".xlsx";
      }

      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      setError(e && e.message ? e.message : String(e));
    } finally {
      btnExport.disabled = filteredRows.length === 0;
    }
  }

  function initDefaults() {
    const today = new Date();
    // Month picker default
    monthPicker.value = today.getFullYear() + "-" + pad2(today.getMonth() + 1);
    const ms = startOfMonth(today);
    const me = endOfMonth(today);
    if (monthLabel) monthLabel.textContent = formatIT(toISO(ms)) + " - " + formatIT(toISO(me));

    // Week anchor default today
    weekAnchor.value = toISO(today);
    const ws = startOfWeek(today);
    const we = endOfWeek(today);
    if (weekLabel) weekLabel.textContent = formatIT(toISO(ws)) + " - " + formatIT(toISO(we));

    // Period default: current month
    periodStart.value = toISO(ms);
    periodEnd.value = toISO(me);

    setFilterEnabled(false);
  }

  // -------------------------
  // Photo modal handlers
  // -------------------------
  function initPhotoModals() {
    if (spesaModalEl && spesaImgEl && spesaLoadingEl) {
      spesaModalEl.addEventListener("show.bs.modal", function (event) {
        const btn = event.relatedTarget;
        const url = btn ? btn.getAttribute("data-photo-url") || "" : "";
        if (!url) {
          spesaImgEl.src = "";
          spesaLoadingEl.style.display = "none";
          return;
        }

        spesaLoadingEl.textContent = t("loading", "Caricamento...");
        spesaLoadingEl.style.display = "";
        spesaImgEl.onload = function () {
          spesaLoadingEl.style.display = "none";
        };
        spesaImgEl.onerror = function () {
          spesaLoadingEl.textContent = t("photoLoadError", "Impossibile caricare la foto.");
        };

        const sep = url.includes("?") ? "&" : "?";
        spesaImgEl.src = url + sep + "v=" + Date.now();
      });

      spesaModalEl.addEventListener("hidden.bs.modal", function () {
        spesaImgEl.src = "";
        spesaLoadingEl.style.display = "none";
      });
    }

    if (versModalEl && versImgEl && versLoadingEl && versErrEl) {
      versModalEl.addEventListener("show.bs.modal", function (event) {
        const btn = event.relatedTarget;
        const url = btn ? btn.getAttribute("data-photo-url") || "" : "";
        versErrEl.style.display = "none";
        versImgEl.style.display = "none";

        if (!url) {
          versImgEl.src = "";
          versLoadingEl.style.display = "none";
          return;
        }

        versLoadingEl.style.display = "";
        versLoadingEl.textContent = t("loading", "Caricamento...");

        versImgEl.onload = function () {
          versLoadingEl.style.display = "none";
          versErrEl.style.display = "none";
          versImgEl.style.display = "";
        };
        versImgEl.onerror = function () {
          versLoadingEl.style.display = "none";
          versImgEl.style.display = "none";
          versErrEl.style.display = "";
        };

        const sep = url.includes("?") ? "&" : "?";
        versImgEl.src = url + sep + "v=" + Date.now();
      });

      versModalEl.addEventListener("hidden.bs.modal", function () {
        versImgEl.src = "";
        versLoadingEl.style.display = "none";
        versErrEl.style.display = "none";
        versImgEl.style.display = "none";
      });
    }
  }

  // -------------------------
  // Events
  // -------------------------
  modeSel.addEventListener("change", function () {
    const mode = String(modeSel.value || "MONTH").toUpperCase();
    showMode(mode);
  });

  weekPrev.addEventListener("click", function () {
    const d = parseISO(weekAnchor.value) || new Date();
    const x = addDays(d, -7);
    weekAnchor.value = toISO(x);
    const ws = startOfWeek(x);
    const we = endOfWeek(x);
    weekLabel.textContent = formatIT(toISO(ws)) + " - " + formatIT(toISO(we));
  });

  weekNext.addEventListener("click", function () {
    const d = parseISO(weekAnchor.value) || new Date();
    const x = addDays(d, 7);
    weekAnchor.value = toISO(x);
    const ws = startOfWeek(x);
    const we = endOfWeek(x);
    weekLabel.textContent = formatIT(toISO(ws)) + " - " + formatIT(toISO(we));
  });

  weekAnchor.addEventListener("change", function () {
    const d = parseISO(weekAnchor.value) || new Date();
    const ws = startOfWeek(d);
    const we = endOfWeek(d);
    weekLabel.textContent = formatIT(toISO(ws)) + " - " + formatIT(toISO(we));
  });

  monthPrev.addEventListener("click", function () {
    const mp = String(monthPicker.value || "").trim();
    const mm = /^(\d{4})-(\d{2})$/.exec(mp);
    let d = new Date();
    if (mm) d = new Date(Number(mm[1]), Number(mm[2]) - 1, 1);
    d = addMonths(d, -1);
    monthPicker.value = d.getFullYear() + "-" + pad2(d.getMonth() + 1);
    const ms = startOfMonth(d);
    const me = endOfMonth(d);
    monthLabel.textContent = formatIT(toISO(ms)) + " - " + formatIT(toISO(me));
  });

  monthNext.addEventListener("click", function () {
    const mp = String(monthPicker.value || "").trim();
    const mm = /^(\d{4})-(\d{2})$/.exec(mp);
    let d = new Date();
    if (mm) d = new Date(Number(mm[1]), Number(mm[2]) - 1, 1);
    d = addMonths(d, 1);
    monthPicker.value = d.getFullYear() + "-" + pad2(d.getMonth() + 1);
    const ms = startOfMonth(d);
    const me = endOfMonth(d);
    monthLabel.textContent = formatIT(toISO(ms)) + " - " + formatIT(toISO(me));
  });

  monthPicker.addEventListener("change", function () {
    const mp = String(monthPicker.value || "").trim();
    const mm = /^(\d{4})-(\d{2})$/.exec(mp);
    if (!mm) return;
    const d = new Date(Number(mm[1]), Number(mm[2]) - 1, 1);
    const ms = startOfMonth(d);
    const me = endOfMonth(d);
    monthLabel.textContent = formatIT(toISO(ms)) + " - " + formatIT(toISO(me));
  });

  btnSearch.addEventListener("click", doSearch);
  btnExport.addEventListener("click", doExport);

  if (filterKey) {
    filterKey.addEventListener("change", applyFilterAndRender);
  }
  if (filterVal) {
    filterVal.addEventListener("input", applyFilterAndRender);
  }
  if (filterClear) {
    filterClear.addEventListener("click", function () {
      if (filterVal) filterVal.value = "";
      if (filterKey) filterKey.value = "ALL";
      applyFilterAndRender();
    });
  }

  // init
  initDefaults();
  showMode(String(modeSel.value || "MONTH").toUpperCase());
  initPhotoModals();
})();
