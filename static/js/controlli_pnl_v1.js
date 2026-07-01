(function () {
  const cfg = window.__PnlCfg || {};
  const apiUrl = cfg.apiUrl || '';

  const elYear = document.getElementById('pnlYear');
  const elFrom = document.getElementById('pnlMonthFrom');
  const elTo = document.getElementById('pnlMonthTo');
  const elApply = document.getElementById('pnlApply');
  const elTogglePrev = document.getElementById('pnlTogglePrev');

  const elTable = document.getElementById('pnlTable');
  const elTbody = elTable ? elTable.querySelector('tbody') : null;
  const elTableWrap = document.getElementById('pnlTableWrap');

  const elMobileWrap = document.getElementById('pnlMobileWrap');
  const elMobileList = document.getElementById('pnlMobileList');

  const elError = document.getElementById('pnlError');
  const elHint = document.getElementById('pnlHint');
  const elPeriodLabel = document.getElementById('pnlPeriodLabel');

  const elStoresLabel = document.getElementById('pnlStoresLabel');
  const elStoresModal = document.getElementById('pnlStoresModal');
  const elStoresSearch = document.getElementById('pnlStoresSearch');
  const elStoresList = document.getElementById('pnlStoresList');
  const elStoresAll = document.getElementById('pnlStoresAll');
  const elStoresNone = document.getElementById('pnlStoresNone');
  const elStoresConfirm = document.getElementById('pnlStoresConfirm');

  const elDetailModal = document.getElementById('pnlDetailModal');
  const elDetailTitle = document.getElementById('pnlDetailTitle');
  const elDetailBody = document.getElementById('pnlDetailBody');

  const LS_KEY = 'pnl_filters_v1';
  let storesByCode = {};
  let storesLoaded = false;
  let selectedStores = [];
  let lastDataRows = [];

  // ---- Voice definitions ----
  const SUB_OTHER_GA = new Set([
    'Casse e HiTec',
    'Altri servizi esterni',
    'Commissioni Ticket',
    'Piccole attrezzaure - Cancelleria',
    'Costi assicurativi',
    'Affitto attrezzature',
    'Altro',
  ]);

  const MACRO_ORDER = [
    'REVENUES',
    'COGS',
    'MARGINE DI CONTRIBUZIONE',
    'LABOUR COST',
    'DELIVERY FEES',
    'G&A STORE',
    'TOTALE COSTI CONTROLLABILI',
    'STORE EBITDA',
    'EBITDA',
  ];

  const DETAIL_MAP = {
    'COGS': ['Magazzino Iniziale', 'Acquistato', 'Trasferimenti', 'Magazzino Finale', 'Waste'],
    'LABOUR COST': ['Labour fixed', 'Stage', 'External Labour', 'Trasferimento', 'Costo formazione', 'Other cost'],
    'DELIVERY FEES': ['Variable fees', 'Other delivery fees'],
    'G&A STORE': [
      'Rent',
      'Spese Condominiali',
      'Utilities',
      'Cleaning+Security',
      'Marketing',
      'Maintenance',
      'Spese Trasporto',
      'Other G&A',
      'Casse e HiTec',
      'Altri servizi esterni',
      'Commissioni Ticket',
      'Piccole attrezzaure - Cancelleria',
      'Costi assicurativi',
      'Affitto attrezzature',
      'Altro',
    ],
    'MARGINE DI CONTRIBUZIONE': ['REVENUES', 'COGS'],
    'TOTALE COSTI CONTROLLABILI': ['COGS', 'LABOUR COST', 'DELIVERY FEES', 'G&A STORE'],
    'STORE EBITDA': ['REVENUES', 'TOTALE COSTI CONTROLLABILI'],
    'EBITDA': ['STORE EBITDA', 'Other personnel cost', 'Bank commissions', 'Consultancies', 'Other taxes', 'Other revenues'],
  };

  function isMobile() {
    return window.matchMedia && window.matchMedia('(max-width: 767.98px)').matches;
  }

  function safeJsonParse(text) {
    try { return JSON.parse(text); } catch (e) { return null; }
  }

  function setError(msg) {
    if (!elError) return;
    if (!msg) {
      elError.classList.add('d-none');
      elError.textContent = '';
      return;
    }
    elError.textContent = String(msg);
    elError.classList.remove('d-none');
  }

  function setLoadingRow(text) {
    if (!elTbody) return;
    elTbody.innerHTML = `<tr><td colspan="11" class="text-muted">${escapeHtml(text || 'Caricamento...')}</td></tr>`;
  }

  function toNum(v) {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  }

  function fmtEuro(v) {
    const n = toNum(v);
    if (!Number.isFinite(n)) return '';
    return n.toLocaleString('it-IT', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
  }

  function fmtPct(v) {
    if (v === null || v === undefined) return '';
    const n = Number(v);
    if (!Number.isFinite(n)) return '';
    return (n).toLocaleString('it-IT', { style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1 });
  }

  // Compatibilità chiavi API: alcune versioni restituiscono diff/diff_pct,
  // altre diff_budget/diff_budget_pct.
  function getDiffBudget(r) {
    if (!r) return { d: 0, dp: null };
    const d = (r.diff_budget ?? r.diff ?? 0);
    const dp = (r.diff_budget_pct ?? r.diff_pct ?? null);
    return { d, dp };
  }

  function numClassEuro(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return '';
    return n < 0 ? 'pnl-neg' : '';
  }

  // Δ% rules:
  // - REVENUES, MARGINE DI CONTRIBUZIONE, LABOUR COST, STORE EBITDA: red if negative
  // - all others: red if positive
  function numClassDeltaPct(voice, v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return '';
    const vv = String(voice || '').trim().toUpperCase();
    const redIfNeg = (vv === 'REVENUES' || vv === 'MARGINE DI CONTRIBUZIONE' || vv === 'LABOUR COST' || vv === 'STORE EBITDA');
    if (redIfNeg) return n < 0 ? 'pnl-red' : '';
    return n > 0 ? 'pnl-red' : '';
  }

  // Store EBITDA incidence %: red if negative
  function numClassStoreEbitdaPct(voice, v) {
    const vv = String(voice || '').trim().toUpperCase();
    if (vv !== 'STORE EBITDA') return '';
    const n = Number(v);
    if (!Number.isFinite(n)) return '';
    return n < 0 ? 'pnl-red' : '';
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function monthLabel(m) {
    const labels = ['Gennaio', 'Febbraio', 'Marzo', 'Aprile', 'Maggio', 'Giugno', 'Luglio', 'Agosto', 'Settembre', 'Ottobre', 'Novembre', 'Dicembre'];
    const i = Number(m) - 1;
    return labels[i] || String(m);
  }

  function populateYearSelect(sel, year) {
    if (!sel) return;

    const nowY = new Date().getFullYear();
    const defY = Number(cfg.defaultYear) || nowY;
    const selectedY = Number(year) || defY;

    // Base year: include current/default year even if localStorage contains an older year
    const baseY = Math.max(nowY, defY, selectedY);

    // Show a wide range + next year (budget often exists for next year)
    const maxY = baseY + 1;
    const minY = baseY - 12;

    const years = [];
    for (let y = maxY; y >= minY; y--) years.push(y);

    // Ensure the selected year is always present
    if (!years.includes(selectedY)) {
      years.push(selectedY);
      years.sort((a, b) => b - a);
    }

    sel.innerHTML = years.map(v => `<option value="${v}">${v}</option>`).join('');
    sel.value = String(selectedY);
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

  function saveFilters() {
    const f = currentFilters();
    localStorage.setItem(LS_KEY, JSON.stringify({
      year: f.year,
      month_from: f.month_from,
      month_to: f.month_to,
      stores: selectedStores.slice(),
    }));
  }

  function currentFilters() {
    const year = Number(elYear ? elYear.value : cfg.defaultYear || new Date().getFullYear());
    const month_from = Number(elFrom ? elFrom.value : cfg.defaultMonth || new Date().getMonth() + 1);
    const month_to = Number(elTo ? elTo.value : month_from);
    return { year, month_from, month_to };
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
      updateStoresLabel();
    } catch (e) {
      // leave empty; still usable with codes
      storesLoaded = true;
    }
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
      const id = `pnl_store_${s.code.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
      return `
        <label class="list-group-item d-flex align-items-center gap-2" for="${id}">
          <input class="form-check-input me-1" type="checkbox" value="${escapeHtml(s.code)}" id="${id}" ${checked}>
          <span class="flex-grow-1">${escapeHtml(s.code)} <span class="text-muted">- ${escapeHtml(s.name)}</span></span>
        </label>
      `;
    }).join('');
  }

  function getCheckedStores() {
    if (!elStoresList) return [];
    const inputs = elStoresList.querySelectorAll('input[type="checkbox"]');
    const arr = [];
    inputs.forEach(i => {
      if (i.checked) arr.push(String(i.value));
    });
    return arr;
  }

  async function fetchData() {
    setError('');
    const f = currentFilters();
    setLoadingRow('Caricamento P&L...');
    if (elMobileList) elMobileList.innerHTML = '';

    const params = new URLSearchParams();
    params.set('year', String(f.year));
    params.set('month_from', String(f.month_from));
    params.set('month_to', String(f.month_to));
    if (selectedStores && selectedStores.length) params.set('stores', selectedStores.join(','));

    const url = apiUrl + '?' + params.toString();
    const res = await fetch(url, { credentials: 'same-origin' });
    const data = await res.json();
    if (!res.ok) throw new Error((data && data.error) ? data.error : 'Errore');
    return data;
  }

  function buildRowHtml(r) {
    const voice = r.voice || r.Voce || '';
    const vtrim = String(voice).trim();
    const vup = vtrim.toUpperCase();

    const isMacro = MACRO_ORDER.includes(vtrim) || MACRO_ORDER.includes(vup) || (DETAIL_MAP[vtrim] || DETAIL_MAP[vup]);
    const isSub = SUB_OTHER_GA.has(vtrim);

    const trClass = [
      isMacro ? 'pnl-macrovoice' : '',
      isSub ? 'pnl-subvoice' : '',
      vup === 'REVENUES' ? 'pnl-sticky-row' : ''
    ].filter(Boolean).join(' ');

    const b = r.budget;
    const bp = r.budget_pct;
    const a = r.actual;
    const ap = r.actual_pct;
    const { d, dp } = getDiffBudget(r);
    const ly = r.last_year;
    const lyp = r.last_year_pct;
    const dly = r.diff_last_year;
    const dlyp = r.diff_last_year_pct;

    return `
      <tr class="${trClass}">
        <td class="pnl-col-voice">${escapeHtml(vtrim)}</td>
        <td class="text-end pnl-num pnl-col-group-budget ${numClassEuro(b)}">${fmtEuro(b)}</td>
        <td class="text-end pnl-num pnl-col-group-budget ${numClassStoreEbitdaPct(vtrim, bp)}">${fmtPct(bp)}</td>

        <td class="text-end pnl-num pnl-col-group-actual ${numClassEuro(a)}">${fmtEuro(a)}</td>
        <td class="text-end pnl-num pnl-col-group-actual ${numClassStoreEbitdaPct(vtrim, ap)}">${fmtPct(ap)}</td>

        <td class="text-end pnl-num pnl-col-group-delta ${numClassEuro(d)}">${fmtEuro(d)}</td>
        <td class="text-end pnl-num pnl-col-group-delta ${numClassDeltaPct(vtrim, dp)}">${fmtPct(dp)}</td>

        <td class="text-end pnl-num pnl-col-prev pnl-col-group-ly ${numClassEuro(ly)}">${fmtEuro(ly)}</td>
        <td class="text-end pnl-num pnl-col-prev pnl-col-group-ly ${numClassStoreEbitdaPct(vtrim, lyp)}">${fmtPct(lyp)}</td>

        <td class="text-end pnl-num pnl-col-prev pnl-col-group-delta-ly ${numClassEuro(dly)}">${fmtEuro(dly)}</td>
        <td class="text-end pnl-num pnl-col-prev pnl-col-group-delta-ly ${numClassDeltaPct(vtrim, dlyp)}">${fmtPct(dlyp)}</td>
      </tr>
    `;
  }

  function renderDesktop(rows) {
    if (!elTbody) return;
    if (!rows || !rows.length) {
      elTbody.innerHTML = `<tr><td colspan="11" class="text-muted">Nessun dato.</td></tr>`;
      return;
    }
    elTbody.innerHTML = rows.map(buildRowHtml).join('');
    updateStickyOffsets();
  }

  function macroCardHtml(voice, r) {
    const b = r.budget;
    const a = r.actual;
    const { d, dp } = getDiffBudget(r);

    const deltaClass = numClassEuro(d);
    const dpClass = numClassDeltaPct(voice, dp);
    const bClass = numClassEuro(b);
    const aClass = numClassEuro(a);

    return `
      <div class="pnl-macro-card" role="button" tabindex="0" data-voice="${escapeHtml(voice)}">
        <div class="pnl-macro-top">
          <div class="pnl-macro-name">${escapeHtml(voice)}</div>
          <div class="pnl-macro-delta ${deltaClass}">${fmtEuro(d)}</div>
        </div>
        <div class="pnl-macro-grid">
          <div class="pnl-macro-box">
            <div class="pnl-macro-label">Budget</div>
            <div class="pnl-macro-value ${bClass}">${fmtEuro(b)}</div>
          </div>
          <div class="pnl-macro-box">
            <div class="pnl-macro-label">Actual</div>
            <div class="pnl-macro-value ${aClass}">${fmtEuro(a)}</div>
          </div>
          <div class="pnl-macro-box">
            <div class="pnl-macro-label">Δ %</div>
            <div class="pnl-macro-value ${dpClass}">${fmtPct(dp)}</div>
          </div>
        </div>
      </div>
    `;
  }

  function renderMobile(rows) {
    if (!elMobileWrap || !elMobileList) return;

    if (!isMobile()) {
      elMobileWrap.classList.add('d-md-none');
      return;
    }

    const map = {};
    (rows || []).forEach(r => {
      const v = String(r.voice || r.Voce || '').trim();
      if (v) map[v.toUpperCase()] = r;
    });

    const cards = [];
    MACRO_ORDER.forEach(v => {
      const r = map[v.toUpperCase()];
      if (!r) return;
      cards.push(macroCardHtml(v, r));
    });

    if (!cards.length) {
      elMobileList.innerHTML = `<div class="text-muted">Nessun dato.</div>`;
      return;
    }
    elMobileList.innerHTML = cards.join('');

    // click handlers
    elMobileList.querySelectorAll('.pnl-macro-card').forEach(card => {
      const open = () => {
        const v = card.getAttribute('data-voice');
        if (v) openDetail(v, rows);
      };
      card.addEventListener('click', open);
      card.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
      });
    });
  }

  function buildDetailTable(voice, rows, showPrev) {
    const map = {};
    rows.forEach(r => {
      const v = String(r.voice || r.Voce || '').trim();
      if (v) map[v.toUpperCase()] = r;
    });

    const vup = String(voice).trim().toUpperCase();
    const comps = DETAIL_MAP[vup] || [];
    const list = [];
    comps.forEach(c => {
      const rr = map[String(c).trim().toUpperCase()];
      if (rr) list.push({ voice: c, row: rr });
    });

    // add total line if present
    const totalRow = map[vup];
    if (totalRow) list.push({ voice: voice, row: totalRow, isTotal: true });

    const cols = showPrev
      ? ['Voce', 'Budget', 'Actual', 'Δ €', 'Δ %', 'A.P.', 'Δ €', 'Δ %']
      : ['Voce', 'Budget', 'Actual', 'Δ €', 'Δ %'];

    const thead = `<thead><tr>${cols.map(c => `<th class="${c === 'Voce' ? '' : 'text-end'}">${escapeHtml(c)}</th>`).join('')}</tr></thead>`;

    const tbody = list.map(item => {
      const r = item.row;
      const vv = item.voice;
      const isSub = SUB_OTHER_GA.has(vv);
      const trClass = [
        item.isTotal ? 'pnl-macrovoice' : '',
        isSub ? 'pnl-subvoice' : ''
      ].filter(Boolean).join(' ');

      const b = r.budget;
      const a = r.actual;
      const { d, dp } = getDiffBudget(r);

      const tds = [];
      tds.push(`<td class="pnl-col-voice">${escapeHtml(vv)}</td>`);
      tds.push(`<td class="text-end pnl-num ${numClassEuro(b)}">${fmtEuro(b)}</td>`);
      tds.push(`<td class="text-end pnl-num ${numClassEuro(a)}">${fmtEuro(a)}</td>`);
      tds.push(`<td class="text-end pnl-num ${numClassEuro(d)}">${fmtEuro(d)}</td>`);
      tds.push(`<td class="text-end pnl-num ${numClassDeltaPct(vv, dp)}">${fmtPct(dp)}</td>`);

      if (showPrev) {
        const ly = r.last_year;
        const dly = r.diff_last_year;
        const dlyp = r.diff_last_year_pct;
        tds.push(`<td class="text-end pnl-num ${numClassEuro(ly)}">${fmtEuro(ly)}</td>`);
        tds.push(`<td class="text-end pnl-num ${numClassEuro(dly)}">${fmtEuro(dly)}</td>`);
        tds.push(`<td class="text-end pnl-num ${numClassDeltaPct(vv, dlyp)}">${fmtPct(dlyp)}</td>`);
      }

      return `<tr class="${trClass}">${tds.join('')}</tr>`;
    }).join('');

    return `<div class="table-responsive"><table class="table table-sm align-middle mb-0">${thead}<tbody>${tbody}</tbody></table></div>`;
  }

  function openDetail(voice, rows) {
    if (!elDetailModal || !elDetailBody) return;
    if (elDetailTitle) elDetailTitle.textContent = voice;

    const showPrev = !(elTableWrap && elTableWrap.classList.contains('pnl-hide-prev'));
    const html = buildDetailTable(voice, rows, showPrev);
    elDetailBody.innerHTML = html + `<div class="pnl-detail-note">Tocca una riga per copiarla: non modificabile da qui.</div>`;

    const modal = bootstrap.Modal.getOrCreateInstance(elDetailModal);
    modal.show();
  }

  function render(data) {
    const rows = (data && data.rows) ? data.rows : [];
    lastDataRows = rows;

    const monthsLabel = (data && data.months_label) ? String(data.months_label) : '';
    if (elPeriodLabel) elPeriodLabel.textContent = monthsLabel || '—';

    if (elHint) {
      // Hidden: source/store hint removed from filters area (per UX request).
      elHint.textContent = '';
      elHint.style.display = 'none';
    }


    renderDesktop(rows);
    if (elMobileWrap && elMobileList) renderMobile(rows);
  }

  function updateStickyOffsets() {
    if (!elTableWrap || !elTable) return;
    const thead = elTable.querySelector('thead');
    if (!thead) return;
    const h = Math.ceil(thead.getBoundingClientRect().height || 0);
    if (h > 0) elTableWrap.style.setProperty('--pnl-sticky-top', h + 'px');
  }

  function togglePrev() {
    if (!elTableWrap) return;
    elTableWrap.classList.toggle('pnl-hide-prev');
    if (elTogglePrev) elTogglePrev.classList.toggle('active');
    // refresh mobile detail columns if needed
  }

  async function apply() {
    try {
      saveFilters();
      updateStoresLabel();
      const data = await fetchData();
      setError('');
      render(data);
    } catch (e) {
      const msg = (e && e.message) ? e.message : 'Errore';
      setError(msg);
      setLoadingRow('Errore');
    }
  }

  function initStoreModal() {
    if (!elStoresModal) return;
    elStoresModal.addEventListener('show.bs.modal', () => {
      ensureStoresLoaded().then(() => {
        renderStoresList(elStoresSearch ? elStoresSearch.value : '');
      });
    });

    if (elStoresSearch) {
      elStoresSearch.addEventListener('input', () => {
        renderStoresList(elStoresSearch.value);
      });
    }

    if (elStoresAll) {
      elStoresAll.addEventListener('click', () => {
        // check all visible
        if (!elStoresList) return;
        elStoresList.querySelectorAll('input[type="checkbox"]').forEach(i => { i.checked = true; });
      });
    }
    if (elStoresNone) {
      elStoresNone.addEventListener('click', () => {
        if (!elStoresList) return;
        elStoresList.querySelectorAll('input[type="checkbox"]').forEach(i => { i.checked = false; });
      });
    }
    if (elStoresConfirm) {
      elStoresConfirm.addEventListener('click', () => {
        selectedStores = getCheckedStores();
        updateStoresLabel();
        saveFilters();
      });
    }
  }

  function init() {
    loadFilters();
    ensureStoresLoaded();
    updateStoresLabel();
    initStoreModal();

    if (elApply) elApply.addEventListener('click', apply);
    if (elTogglePrev) elTogglePrev.addEventListener('click', togglePrev);

    // keep sticky correct on resize
    window.addEventListener('resize', () => {
      updateStickyOffsets();
      if (lastDataRows && lastDataRows.length && elMobileWrap && elMobileList) {
        renderMobile(lastDataRows);
      }
    });
  }

  init();
})();