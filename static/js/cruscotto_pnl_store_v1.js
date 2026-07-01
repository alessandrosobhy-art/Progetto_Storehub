(function () {
  const cfg = window.__PnlStoreCfg || {};
  const MONTHS = ['Gennaio', 'Febbraio', 'Marzo', 'Aprile', 'Maggio', 'Giugno', 'Luglio', 'Agosto', 'Settembre', 'Ottobre', 'Novembre', 'Dicembre'];
  const LS_KEY = 'cruscotto_pnl_store_filters_v1';

  const elYear = document.getElementById('pnlStoreYear');
  const elFrom = document.getElementById('pnlStoreMonthFrom');
  const elTo = document.getElementById('pnlStoreMonthTo');
  const elApply = document.getElementById('pnlStoreApply');
  const elError = document.getElementById('pnlStoreError');
  const elHint = document.getElementById('pnlStoreHint');
  const elPeriod = document.getElementById('pnlStorePeriodLabel');
  const elCards = document.getElementById('pnlStoreKpiCards');
  const elTbody = document.querySelector('#pnlTable tbody');
  const elMobile = document.getElementById('pnlStoreMobileList');

  const elStoresLabel = document.getElementById('pnlStoreStoresLabel');
  const elStoresModal = document.getElementById('pnlStoreStoresModal');
  const elStoresSearch = document.getElementById('pnlStoreStoresSearch');
  const elStoresList = document.getElementById('pnlStoreStoresList');
  const elStoresAll = document.getElementById('pnlStoreStoresAll');
  const elStoresNone = document.getElementById('pnlStoreStoresNone');
  const elStoresSave = document.getElementById('pnlStoreStoresSave');

  let stores = [];
  let storesByCode = {};
  let visibleMonths = [];
  let selectedStores = [];

  const MACRO = new Set(['REVENUES', 'COGS', 'MARGINE DI CONTRIBUZIONE', 'LABOUR COST', 'DELIVERY FEES']);

  function escapeHtml(s) {
    return String(s || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function parseJson(s) {
    try { return JSON.parse(s); } catch { return null; }
  }

  function toNum(v) {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  }

  function fmtEuro(v) {
    return toNum(v).toLocaleString('it-IT', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
  }

  function fmtPct(v) {
    if (v === null || v === undefined) return '';
    const n = Number(v);
    if (!Number.isFinite(n)) return '';
    return n.toLocaleString('it-IT', { style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1 });
  }

  function fmtPp(v) {
    const n = toNum(v) * 100;
    const sign = n > 0 ? '+' : '';
    return sign + n.toLocaleString('it-IT', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + ' pp';
  }

  function setError(msg) {
    if (!elError) return;
    if (!msg) {
      elError.classList.add('d-none');
      elError.textContent = '';
      return;
    }
    elError.classList.remove('d-none');
    elError.textContent = msg;
  }

  function setLoading(text) {
    if (elTbody) elTbody.innerHTML = `<tr><td colspan="11" class="text-muted">${escapeHtml(text || 'Caricamento...')}</td></tr>`;
    if (elMobile) elMobile.innerHTML = `<div class="text-muted">${escapeHtml(text || 'Caricamento...')}</div>`;
  }

  function visibleByYear(year) {
    return visibleMonths.filter(m => Number(m.year) === Number(year)).map(m => Number(m.month));
  }

  function populateYears(selectedYear) {
    const years = Array.from(new Set(visibleMonths.map(m => Number(m.year)))).sort((a, b) => b - a);
    if (!years.length) {
      years.push(Number(cfg.defaultYear || new Date().getFullYear()));
    }
    elYear.innerHTML = years.map(y => `<option value="${y}">${y}</option>`).join('');
    if (years.includes(Number(selectedYear))) elYear.value = String(selectedYear);
  }

  function populateMonthSelect(el, selected) {
    const year = Number(elYear.value || cfg.defaultYear);
    const visible = new Set(visibleByYear(year));
    const fallback = visible.size ? Array.from(visible)[0] : Number(cfg.defaultMonth || 1);
    el.innerHTML = MONTHS.map((name, idx) => {
      const month = idx + 1;
      const disabled = visible.size && !visible.has(month) ? 'disabled' : '';
      return `<option value="${month}" ${disabled}>${name}</option>`;
    }).join('');
    const wanted = visible.has(Number(selected)) ? Number(selected) : fallback;
    el.value = String(wanted);
  }

  function currentFilters() {
    let mf = Number(elFrom.value || cfg.defaultMonth || 1);
    let mt = Number(elTo.value || mf);
    if (mf > mt) [mf, mt] = [mt, mf];
    return { year: Number(elYear.value || cfg.defaultYear), month_from: mf, month_to: mt };
  }

  function saveFilters() {
    const f = currentFilters();
    localStorage.setItem(LS_KEY, JSON.stringify({ year: f.year, month_from: f.month_from, month_to: f.month_to, stores: selectedStores }));
  }

  function storeLabel() {
    if (!selectedStores.length) return 'Tutti gli store visibili';
    const names = selectedStores.map(code => {
      const s = storesByCode[code] || { code, name: code };
      return `${s.code} - ${s.name}`;
    });
    if (names.length <= 2) return names.join(', ');
    return `${names[0]}, ${names[1]} +${names.length - 2}`;
  }

  function updateStoreLabel() {
    if (elStoresLabel) elStoresLabel.value = storeLabel();
  }

  function renderStoresList() {
    if (!elStoresList) return;
    const term = String(elStoresSearch ? elStoresSearch.value : '').toLowerCase().trim();
    const rows = stores.filter(s => !term || `${s.code} ${s.name}`.toLowerCase().includes(term));
    if (!rows.length) {
      elStoresList.innerHTML = '<div class="text-muted small">Nessun risultato</div>';
      return;
    }
    elStoresList.innerHTML = rows.map(s => `
      <label class="kpi-store-item">
        <input class="form-check-input me-2" type="checkbox" value="${escapeHtml(s.code)}" ${selectedStores.includes(s.code) ? 'checked' : ''}>
        <span class="kpi-store-code">${escapeHtml(s.code)}</span>
        <span class="text-muted">-</span>
        <span class="kpi-store-name">${escapeHtml(s.name)}</span>
      </label>
    `).join('');
    elStoresList.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', () => {
        const code = cb.value;
        if (cb.checked && !selectedStores.includes(code)) selectedStores.push(code);
        if (!cb.checked) selectedStores = selectedStores.filter(x => x !== code);
      });
    });
  }

  function statusBadge(status) {
    if (status === 'green') return '<span class="badge text-bg-success">OK</span>';
    if (status === 'yellow') return '<span class="badge text-bg-warning">ATT</span>';
    if (status === 'red') return '<span class="badge text-bg-danger">KO</span>';
    return '<span class="badge text-bg-secondary">N/A</span>';
  }

  function renderKpiCard(item) {
    const kpi = item.kpi || '';
    const status = item.status || 'na';
    let metrics = '';
    if (item.missing) {
      metrics = ['Budget', 'Actual', 'Delta'].map(x => `<div class="kpi-metric"><div class="kpi-metric-label">${x}</div><div class="kpi-metric-val">-</div></div>`).join('');
    } else if (kpi === 'REVENUES') {
      metrics = `
        <div class="kpi-metric"><div class="kpi-metric-label">Budget</div><div class="kpi-metric-val">${fmtEuro(item.budget)}</div></div>
        <div class="kpi-metric"><div class="kpi-metric-label">Actual</div><div class="kpi-metric-val">${fmtEuro(item.actual)}</div></div>
        <div class="kpi-metric"><div class="kpi-metric-label">Delta</div><div class="kpi-metric-val">${fmtEuro(item.diff)}</div></div>
      `;
    } else {
      metrics = `
        <div class="kpi-metric"><div class="kpi-metric-label">Budget</div><div class="kpi-metric-val">${fmtPct(item.budget_pct)}</div></div>
        <div class="kpi-metric"><div class="kpi-metric-label">Actual</div><div class="kpi-metric-val">${fmtPct(item.actual_pct)}</div></div>
        <div class="kpi-metric"><div class="kpi-metric-label">Delta</div><div class="kpi-metric-val">${fmtPp(item.delta_pp)}</div></div>
      `;
    }
    return `
      <div class="col-12 col-md-6 col-xl-3">
        <div class="card shadow-sm kpi-card kpi-${escapeHtml(status)}">
          <div class="card-body">
            <div class="d-flex justify-content-between gap-2">
              <div><div class="kpi-title">${escapeHtml(kpi)}</div><div class="kpi-subtitle">vs Budget</div></div>
              <div>${statusBadge(status)}</div>
            </div>
            <div class="kpi-grid mt-3">${metrics}</div>
          </div>
        </div>
      </div>
    `;
  }

  function rowClass(row) {
    const voice = String(row.voice || '').trim().toUpperCase();
    const classes = [];
    if (MACRO.has(voice)) classes.push('pnl-macrovoice');
    if (voice === 'REVENUES') classes.push('pnl-sticky-row');
    return classes.join(' ');
  }

  function numClass(v) {
    return Number(v) < 0 ? 'pnl-neg' : '';
  }

  function renderRows(rows) {
    if (!rows.length) {
      setLoading('Nessun dato.');
      return;
    }
    if (elTbody) {
      elTbody.innerHTML = rows.map(r => `
        <tr class="${rowClass(r)}">
          <td class="pnl-col-voice">${escapeHtml(r.voice)}</td>
          <td class="text-end pnl-num pnl-col-group-budget ${numClass(r.budget)}">${fmtEuro(r.budget)}</td>
          <td class="text-end pnl-num pnl-col-group-budget">${fmtPct(r.budget_pct)}</td>
          <td class="text-end pnl-num pnl-col-group-actual ${numClass(r.actual)}">${fmtEuro(r.actual)}</td>
          <td class="text-end pnl-num pnl-col-group-actual">${fmtPct(r.actual_pct)}</td>
          <td class="text-end pnl-num pnl-col-group-delta ${numClass(r.diff)}">${fmtEuro(r.diff)}</td>
          <td class="text-end pnl-num pnl-col-group-delta">${fmtPct(r.diff_pct)}</td>
          <td class="text-end pnl-num pnl-col-prev pnl-col-group-ly ${numClass(r.last_year)}">${fmtEuro(r.last_year)}</td>
          <td class="text-end pnl-num pnl-col-prev pnl-col-group-ly">${fmtPct(r.last_year_pct)}</td>
          <td class="text-end pnl-num pnl-col-prev pnl-col-group-delta-ly ${numClass(r.diff_last_year)}">${fmtEuro(r.diff_last_year)}</td>
          <td class="text-end pnl-num pnl-col-prev pnl-col-group-delta-ly">${fmtPct(r.diff_last_year_pct)}</td>
        </tr>
      `).join('');
    }
    if (elMobile) {
      const macroRows = rows.filter(r => MACRO.has(String(r.voice || '').trim().toUpperCase()));
      elMobile.innerHTML = macroRows.map(r => `
        <div class="pnl-macro-card mb-2">
          <div class="pnl-macro-top">
            <div class="pnl-macro-name">${escapeHtml(r.voice)}</div>
            <div class="pnl-macro-delta ${numClass(r.diff)}">${fmtEuro(r.diff)}</div>
          </div>
          <div class="pnl-macro-grid">
            <div class="pnl-macro-box"><div class="pnl-macro-label">Budget</div><div class="pnl-macro-value">${fmtEuro(r.budget)}</div></div>
            <div class="pnl-macro-box"><div class="pnl-macro-label">Actual</div><div class="pnl-macro-value">${fmtEuro(r.actual)}</div></div>
            <div class="pnl-macro-box"><div class="pnl-macro-label">Delta %</div><div class="pnl-macro-value">${fmtPct(r.diff_pct)}</div></div>
          </div>
        </div>
      `).join('');
    }
  }

  function render(data) {
    if (elPeriod) elPeriod.textContent = data.months_label || '-';
    if (elCards) elCards.innerHTML = (data.kpi_items || []).map(renderKpiCard).join('');
    renderRows(data.rows || []);
  }

  async function loadOptions() {
    const res = await fetch(cfg.optionsUrl, { credentials: 'same-origin' });
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || 'Errore caricamento opzioni');
    stores = Array.isArray(data.stores) ? data.stores : [];
    storesByCode = {};
    stores.forEach(s => { storesByCode[String(s.code)] = { code: String(s.code), name: String(s.name || s.code) }; });
    visibleMonths = Array.isArray(data.visible_months) ? data.visible_months : [];
  }

  async function loadData() {
    setError('');
    setLoading('Caricamento P&L store...');
    const f = currentFilters();
    const params = new URLSearchParams();
    params.set('year', String(f.year));
    params.set('month_from', String(f.month_from));
    params.set('month_to', String(f.month_to));
    if (selectedStores.length) params.set('stores', selectedStores.join(','));
    const res = await fetch(`${cfg.apiUrl}?${params.toString()}`, { credentials: 'same-origin' });
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || 'Errore caricamento dati');
    render(data);
  }

  function initModal() {
    if (!elStoresModal) return;
    elStoresModal.addEventListener('show.bs.modal', renderStoresList);
    if (elStoresSearch) elStoresSearch.addEventListener('input', renderStoresList);
    if (elStoresAll) elStoresAll.addEventListener('click', () => { selectedStores = stores.map(s => String(s.code)); renderStoresList(); });
    if (elStoresNone) elStoresNone.addEventListener('click', () => { selectedStores = []; renderStoresList(); });
    if (elStoresSave) elStoresSave.addEventListener('click', () => { updateStoreLabel(); saveFilters(); });
  }

  async function init() {
    try {
      await loadOptions();
      const saved = parseJson(localStorage.getItem(LS_KEY) || '{}') || {};
      const firstVisible = visibleMonths[0] || {};
      const year = Number(saved.year || firstVisible.year || cfg.defaultYear || new Date().getFullYear());
      populateYears(year);
      populateMonthSelect(elFrom, saved.month_from || firstVisible.month || cfg.defaultMonth);
      populateMonthSelect(elTo, saved.month_to || saved.month_from || firstVisible.month || cfg.defaultMonth);
      selectedStores = Array.isArray(saved.stores) ? saved.stores.filter(code => storesByCode[code]) : [];
      updateStoreLabel();
      initModal();

      if (elYear) elYear.addEventListener('change', () => {
        populateMonthSelect(elFrom, elFrom.value);
        populateMonthSelect(elTo, elTo.value);
        saveFilters();
      });
      if (elApply) elApply.addEventListener('click', async () => {
        saveFilters();
        try { await loadData(); } catch (e) { setError(e.message || String(e)); }
      });

      if (!visibleMonths.length) {
        if (elHint) elHint.textContent = 'Nessun mese abilitato. Un admin deve abilitarlo dalla manutenzione.';
        setLoading('Nessun mese abilitato.');
        return;
      }

      if (elHint) elHint.textContent = 'Seleziona store e periodo, poi premi Applica.';
      setLoading('Seleziona store e periodo, poi premi Applica.');
    } catch (e) {
      setError(e.message || String(e));
      setLoading('Errore.');
    }
  }

  init();
})();
