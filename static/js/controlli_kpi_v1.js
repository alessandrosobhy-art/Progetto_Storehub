(function () {
  const cfg = window.__KpiCfg || {};
  const apiUrl = cfg.apiUrl || '';

  const elYear = document.getElementById('kpiYear');
  const elFrom = document.getElementById('kpiMonthFrom');
  const elTo = document.getElementById('kpiMonthTo');
  const elApply = document.getElementById('kpiApply');

  const elCards = document.getElementById('kpiCards');
  const elError = document.getElementById('kpiError');
  const elHint = document.getElementById('kpiHint');
  const elPeriodLabel = document.getElementById('kpiPeriodLabel');

  const elStoresLabel = document.getElementById('kpiStoresLabel');
  const elStoresModal = document.getElementById('kpiStoresModal');
  const elStoresSearch = document.getElementById('kpiStoresSearch');
  const elStoresList = document.getElementById('kpiStoresList');
  const elStoresAll = document.getElementById('kpiStoresAll');
  const elStoresNone = document.getElementById('kpiStoresNone');
  const elStoresSave = document.getElementById('kpiStoresSave');

  const LS_KEY = 'kpi_filters_v1';

  let storesLoaded = false;
  let storesByCode = {};
  let selectedStores = [];

  function safeJsonParse(s) {
    try { return JSON.parse(s); } catch { return null; }
  }

  function monthLabel(m) {
    const labels = ['Gennaio', 'Febbraio', 'Marzo', 'Aprile', 'Maggio', 'Giugno', 'Luglio', 'Agosto', 'Settembre', 'Ottobre', 'Novembre', 'Dicembre'];
    const i = Number(m) - 1;
    return labels[i] || String(m);
  }

  function populateYearSelect(sel, value) {
    if (!sel) return;
    const now = new Date();
    const currentYear = now.getFullYear();
    const saved = safeJsonParse(localStorage.getItem(LS_KEY) || '');
    const savedYear = saved && saved.year ? Number(saved.year) : 0;
    const baseYear = Number(cfg.defaultYear || currentYear);
    const top = Math.max(currentYear, baseYear, savedYear) + 1; // include next year
    const bottom = top - 7; // 7 years window
    const opts = [];
    for (let y = top; y >= bottom; y--) {
      opts.push(`<option value="${y}">${y}</option>`);
    }
    sel.innerHTML = opts.join('');
    if (value) sel.value = String(value);
  }

  function populateMonthSelect(sel, value) {
    if (!sel) return;
    const opts = [];
    for (let m = 1; m <= 12; m++) {
      opts.push(`<option value="${m}">${monthLabel(m)}</option>`);
    }
    sel.innerHTML = opts.join('');
    if (value) sel.value = String(value);
  }

  function currentFilters() {
    const year = Number(elYear ? elYear.value : cfg.defaultYear || new Date().getFullYear());
    const month_from = Number(elFrom ? elFrom.value : cfg.defaultMonth || new Date().getMonth() + 1);
    const month_to = Number(elTo ? elTo.value : month_from);
    return { year, month_from, month_to };
  }

  function saveFilters() {
    const f = currentFilters();
    localStorage.setItem(LS_KEY, JSON.stringify({
      year: f.year,
      month_from: f.month_from,
      month_to: f.month_to,
      stores: selectedStores.slice(),
    }));
  }

  function loadFilters() {
    const saved = safeJsonParse(localStorage.getItem(LS_KEY) || '');
    const year = saved && saved.year ? Number(saved.year) : Number(cfg.defaultYear || new Date().getFullYear());
    const monthFrom = saved && saved.month_from ? Number(saved.month_from) : Number(cfg.defaultMonth || new Date().getMonth() + 1);
    const monthTo = saved && saved.month_to ? Number(saved.month_to) : monthFrom;
    const stores = saved && Array.isArray(saved.stores) ? saved.stores : null;

    populateYearSelect(elYear, year);
    populateMonthSelect(elFrom, monthFrom);
    populateMonthSelect(elTo, monthTo);

    if (stores && stores.length) selectedStores = stores.slice();
    else if (cfg.storeCode) {
      selectedStores = String(cfg.storeCode).split(',').map(s => s.trim()).filter(Boolean);
    }
  }

  async function ensureStoresLoaded() {
    if (storesLoaded || !cfg.storesJsonUrl) return;
    try {
      const res = await fetch(cfg.storesJsonUrl, { credentials: 'same-origin' });
      const data = await res.json();
      const list = Array.isArray(data) ? data : (Array.isArray(data.stores) ? data.stores : []);
      storesByCode = {};
      list.forEach(it => {
        const code = String(it.code || it.Site || it.site || it.store_code || it.value || '').trim();
        if (!code) return;
        const name = String(it.name || it.Nome || it.store_name || it.text || code).trim();
        storesByCode[code] = { code, name };
      });
      storesLoaded = true;
    } catch {
      storesLoaded = true;
      storesByCode = {};
    }
  }

  function getStoresLabel() {
    if (!selectedStores || !selectedStores.length) return 'Nessuno store selezionato';
    const parts = selectedStores.map(c => {
      const meta = storesByCode[c];
      return meta ? `${meta.code} - ${meta.name}` : c;
    });
    if (parts.length <= 2) return parts.join(', ');
    return `${parts[0]}, ${parts[1]} +${parts.length - 2}`;
  }

  function updateStoresLabel() {
    if (!elStoresLabel) return;
    elStoresLabel.value = getStoresLabel();
  }

  function renderStoresList(filterText) {
    if (!elStoresList) return;
    const ft = String(filterText || '').toLowerCase().trim();

    const codes = Object.keys(storesByCode).sort();
    const items = codes
      .map(c => storesByCode[c])
      .filter(s => !ft || (`${s.code} ${s.name}`.toLowerCase().includes(ft)));

    if (!items.length) {
      elStoresList.innerHTML = `<div class="text-muted small">Nessun risultato</div>`;
      return;
    }

    elStoresList.innerHTML = items.map(s => {
      const checked = selectedStores.includes(s.code) ? 'checked' : '';
      return `
        <label class="kpi-store-item">
          <input class="form-check-input me-2" type="checkbox" value="${s.code}" ${checked}>
          <span class="kpi-store-code">${s.code}</span>
          <span class="text-muted">—</span>
          <span class="kpi-store-name">${escapeHtml(s.name)}</span>
        </label>
      `;
    }).join('');

    // checkbox change
    elStoresList.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', () => {
        const code = cb.value;
        if (cb.checked) {
          if (!selectedStores.includes(code)) selectedStores.push(code);
        } else {
          selectedStores = selectedStores.filter(x => x !== code);
        }
      });
    });
  }

  function escapeHtml(s) {
    return String(s || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function statusBadge(status) {
    if (status === 'green') return `<span class="badge text-bg-success">OK</span>`;
    if (status === 'yellow') return `<span class="badge text-bg-warning">ATT</span>`;
    if (status === 'red') return `<span class="badge text-bg-danger">KO</span>`;
    return `<span class="badge text-bg-secondary">N/A</span>`;
  }

  function formatEuro(v) {
    const n = Number(v || 0);
    return n.toLocaleString('it-IT', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
  }

  function formatPct(v, digits = 1) {
    const n = Number(v || 0) * 100;
    return n.toLocaleString('it-IT', { minimumFractionDigits: digits, maximumFractionDigits: digits }) + '%';
  }

  function formatPp(v, digits = 1) {
    const n = Number(v || 0) * 100;
    const sign = n > 0 ? '+' : '';
    return sign + n.toLocaleString('it-IT', { minimumFractionDigits: digits, maximumFractionDigits: digits }) + ' pp';
  }

  function renderCard(it) {
    const kpi = it.kpi || '';
    const st = it.status || 'na';

    let left = '';
    let mid = '';
    let right = '';

    if (it.missing) {
      left = `<div class="kpi-metric"><div class="kpi-metric-label">Budget</div><div class="kpi-metric-val">—</div></div>`;
      mid = `<div class="kpi-metric"><div class="kpi-metric-label">Actual</div><div class="kpi-metric-val">—</div></div>`;
      right = `<div class="kpi-metric"><div class="kpi-metric-label">Δ</div><div class="kpi-metric-val">—</div></div>`;
    } else if (kpi === 'REVENUES') {
      const budget = formatEuro(it.budget);
      const actual = formatEuro(it.actual);
      const diff = formatEuro(it.diff);
      left = `<div class="kpi-metric"><div class="kpi-metric-label">Budget</div><div class="kpi-metric-val">${budget}</div></div>`;
      mid = `<div class="kpi-metric"><div class="kpi-metric-label">Actual</div><div class="kpi-metric-val">${actual}</div></div>`;
      right = `<div class="kpi-metric"><div class="kpi-metric-label">Δ</div><div class="kpi-metric-val">${diff}</div></div>`;
    } else {
      const b = formatPct(it.budget_pct, 1);
      const a = formatPct(it.actual_pct, 1);
      const d = formatPp(it.delta_pp, 1);
      left = `<div class="kpi-metric"><div class="kpi-metric-label">Budget</div><div class="kpi-metric-val">${b}</div></div>`;
      mid = `<div class="kpi-metric"><div class="kpi-metric-label">Actual</div><div class="kpi-metric-val">${a}</div></div>`;
      right = `<div class="kpi-metric"><div class="kpi-metric-label">Δ</div><div class="kpi-metric-val">${d}</div></div>`;
    }

    return `
      <div class="col-12 col-md-6 col-xl-3">
        <div class="card shadow-sm kpi-card kpi-${st}">
          <div class="card-body">
            <div class="d-flex align-items-start justify-content-between gap-2">
              <div>
                <div class="kpi-title">${escapeHtml(kpi)}</div>
                <div class="kpi-subtitle">vs Budget</div>
              </div>
              <div class="kpi-status">
                ${statusBadge(st)}
              </div>
            </div>

            <div class="kpi-grid mt-3">
              ${left}
              ${mid}
              ${right}
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function renderSection(title, subtitle, items, sectionClass = '') {
    const cards = (items || []).map(renderCard).join('');
    return `
      <div class="col-12">
        <div class="kpi-section ${sectionClass}">
          <div class="kpi-section-head d-flex align-items-start justify-content-between gap-2 flex-wrap">
            <div>
              <div class="kpi-section-title">${escapeHtml(title || '')}</div>
              ${subtitle ? `<div class="kpi-section-subtitle">${escapeHtml(subtitle)}</div>` : ''}
            </div>
          </div>
          <div class="row g-3 mt-1">
            ${cards}
          </div>
        </div>
      </div>
    `;
  }

  function render(data) {
    if (!elCards) return;

    const aggregateItems = (data && Array.isArray(data.items)) ? data.items : [];
    const breakdown = (data && Array.isArray(data.store_breakdown)) ? data.store_breakdown : [];
    const html = [];

    html.push(renderSection(
      breakdown.length ? 'Aggregato store selezionati' : 'KPI store selezionato',
      breakdown.length ? 'Totale combinato dei negozi selezionati' : '',
      aggregateItems,
      'kpi-section-aggregate'
    ));

    if (breakdown.length) {
      breakdown.forEach(entry => {
        const code = String((entry && entry.store_code) || '').trim();
        const meta = storesByCode[code];
        const title = meta ? `${meta.code} - ${meta.name}` : code;
        html.push(renderSection(title, 'Dettaglio store', entry.items || [], 'kpi-section-store'));
      });
    }

    elCards.innerHTML = html.join('');
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

  async function loadData() {
    setError('');
    if (elCards) elCards.innerHTML = '';
    if (elHint) elHint.classList.remove('d-none');

    const f = currentFilters();
    const params = new URLSearchParams();
    params.set('year', String(f.year));
    params.set('month_from', String(f.month_from));
    params.set('month_to', String(f.month_to));
    if (selectedStores && selectedStores.length) params.set('stores', selectedStores.join(','));

    if (elPeriodLabel) {
      const label = f.month_from === f.month_to ? `${monthLabel(f.month_from)} ${f.year}` : `${monthLabel(f.month_from)}-${monthLabel(f.month_to)} ${f.year}`;
      elPeriodLabel.textContent = label;
    }

    const url = apiUrl + '?' + params.toString();
    const res = await fetch(url, { credentials: 'same-origin' });
    const data = await res.json();
    if (!res.ok) throw new Error((data && data.error) ? data.error : 'Errore caricamento KPI');

    if (data && data.months_label && elPeriodLabel) elPeriodLabel.textContent = data.months_label;

    render(data || {});
  }

  async function apply() {
    saveFilters();
    await loadData();
  }

  async function initStoresModal() {
    if (!elStoresModal) return;

    elStoresModal.addEventListener('show.bs.modal', async () => {
      await ensureStoresLoaded();
      renderStoresList(elStoresSearch ? elStoresSearch.value : '');
      if (elStoresSearch) setTimeout(() => elStoresSearch.focus(), 150);
    });

    if (elStoresSearch) {
      elStoresSearch.addEventListener('input', () => renderStoresList(elStoresSearch.value));
    }

    if (elStoresAll) {
      elStoresAll.addEventListener('click', async () => {
        await ensureStoresLoaded();
        selectedStores = Object.keys(storesByCode).sort();
        renderStoresList(elStoresSearch ? elStoresSearch.value : '');
      });
    }

    if (elStoresNone) {
      elStoresNone.addEventListener('click', () => {
        selectedStores = [];
        renderStoresList(elStoresSearch ? elStoresSearch.value : '');
      });
    }

    if (elStoresSave) {
      elStoresSave.addEventListener('click', () => {
        updateStoresLabel();
        saveFilters();
      });
    }
  }

  async function init() {
    loadFilters();
    await ensureStoresLoaded();
    updateStoresLabel();

    await initStoresModal();

    if (elApply) elApply.addEventListener('click', () => apply());

    // initial load
    try {
      await loadData();
    } catch (e) {
      setError(e && e.message ? e.message : String(e));
    }
  }

  init();
})();
