(function () {
  const cfg = window.__TrendCfg || {};
  const apiUrl = cfg.apiUrl || '';

  const elYear = document.getElementById('trendYear');
  const elFrom = document.getElementById('trendMonthFrom');
  const elTo = document.getElementById('trendMonthTo');
  const elApply = document.getElementById('trendApply');

  const elError = document.getElementById('trendError');
  const elLoading = document.getElementById('trendLoading');
  const elChartsWrap = document.getElementById('trendChartsWrap');
  const elPeriodLabel = document.getElementById('trendPeriodLabel');

  const elStoresLabel = document.getElementById('trendStoresLabel');
  const elStoresList = document.getElementById('trendStoresList');
  const elStoresSearch = document.getElementById('trendStoresSearch');
  const elStoresAll = document.getElementById('trendStoresAll');
  const elStoresNone = document.getElementById('trendStoresNone');
  const elStoresApply = document.getElementById('trendStoresApply');

  const LS_KEY = 'pnl_filters_v1'; // condiviso con P&L

  // code -> { code, name }
  let storesByCode = {};
  let storesLoaded = false;
  let selectedStores = [];
  let charts = [];

  const MONTHS = {
    1: 'Gennaio', 2: 'Febbraio', 3: 'Marzo', 4: 'Aprile', 5: 'Maggio', 6: 'Giugno',
    7: 'Luglio', 8: 'Agosto', 9: 'Settembre', 10: 'Ottobre', 11: 'Novembre', 12: 'Dicembre'
  };

  const fmtEUR = new Intl.NumberFormat('it-IT', {
    style: 'currency',
    currency: 'EUR',
    maximumFractionDigits: 0
  });

  const fmtPCT = new Intl.NumberFormat('it-IT', {
    style: 'percent',
    maximumFractionDigits: 1
  });

  function setVisible(el, v) {
    if (!el) return;
    el.classList.toggle('d-none', !v);
  }

  function setError(msg) {
    if (!elError) return;
    if (!msg) {
      elError.textContent = '';
      setVisible(elError, false);
    } else {
      elError.textContent = msg;
      setVisible(elError, true);
    }
  }

  function destroyCharts() {
    charts.forEach(c => {
      try { c.destroy(); } catch (e) {}
    });
    charts = [];
  }

  function fillYearSelect() {
    if (!elYear) return;
    const now = new Date();
    const y = now.getFullYear();
    const years = [y - 1, y, y + 1];
    elYear.innerHTML = '';
    years.forEach(v => {
      const opt = document.createElement('option');
      opt.value = String(v);
      opt.textContent = String(v);
      elYear.appendChild(opt);
    });
  }

  function fillMonthSelect(el) {
    if (!el) return;
    el.innerHTML = '';
    for (let m = 1; m <= 12; m++) {
      const opt = document.createElement('option');
      opt.value = String(m);
      opt.textContent = MONTHS[m] || String(m);
      el.appendChild(opt);
    }
  }

  function updatePeriodLabel() {
    if (!elPeriodLabel) return;
    const y = elYear ? Number(elYear.value) : NaN;
    const mf = elFrom ? Number(elFrom.value) : NaN;
    const mt = elTo ? Number(elTo.value) : NaN;
    if (!y || !mf || !mt) {
      elPeriodLabel.textContent = '—';
      return;
    }
    const s = (mf === mt)
      ? `${MONTHS[mf] || mf} ${y}`
      : `${MONTHS[mf] || mf}-${MONTHS[mt] || mt} ${y}`;
    elPeriodLabel.textContent = s;
  }

  function saveFilters() {
    try {
      const obj = {
        year: elYear ? Number(elYear.value) : undefined,
        month_from: elFrom ? Number(elFrom.value) : undefined,
        month_to: elTo ? Number(elTo.value) : undefined,
        stores: selectedStores
      };
      localStorage.setItem(LS_KEY, JSON.stringify(obj));
    } catch (e) {}
  }

  function loadSaved() {
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (!raw) return;
      const obj = JSON.parse(raw);
      if (elYear && obj.year) elYear.value = String(obj.year);
      if (elFrom && obj.month_from) elFrom.value = String(obj.month_from);
      if (elTo && obj.month_to) elTo.value = String(obj.month_to);
      if (Array.isArray(obj.stores)) selectedStores = obj.stores.slice();
    } catch (e) {}

    if (!selectedStores.length && cfg.storeCode) {
      selectedStores = String(cfg.storeCode).split(',').map(s => s.trim()).filter(Boolean);
    }
  }

  function getStoresLabel() {
    if (!selectedStores || !selectedStores.length) return 'Nessuno store selezionato';

    const parts = selectedStores.map(code => {
      const meta = storesByCode && storesByCode[code];
      if (!meta) return code;
      const name = String(meta.name || '').trim();
      return name ? `${meta.code} - ${name}` : meta.code;
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
      (list || []).forEach(it => {
        const code = String(it.code || it.Site || it.site || it.store_code || it.value || '').trim();
        if (!code) return;
        const name = String(it.name || it.Nome || it.store_name || it.text || code).trim();
        storesByCode[code] = { code, name };
      });

      storesLoaded = true;
      updateStoresLabel();
    } catch (e) {
      // fallback: lascia selezione per codice
      storesLoaded = true;
    }
  }

  function storeLabel(code) {
    const meta = storesByCode && storesByCode[code];
    if (!meta) return code;
    return meta.name ? `${meta.code} – ${meta.name}` : meta.code;
  }

  function renderStoresList(filterText) {
    if (!elStoresList) return;
    const q = (filterText || '').toLowerCase().trim();
    const codes = Object.keys(storesByCode || {}).sort();

    elStoresList.innerHTML = '';
    codes.forEach(code => {
      const label = storeLabel(code);
      if (q && !label.toLowerCase().includes(q) && !code.toLowerCase().includes(q)) return;

      const item = document.createElement('label');
      item.className = 'list-group-item d-flex align-items-center gap-2';

      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'form-check-input m-0';
      cb.value = code;
      cb.checked = selectedStores.includes(code);

      const span = document.createElement('span');
      span.className = 'flex-grow-1';
      span.textContent = label;

      item.appendChild(cb);
      item.appendChild(span);
      elStoresList.appendChild(item);
    });
  }

  function selectAllStores(on) {
    if (!storesByCode) return;
    selectedStores = on ? Object.keys(storesByCode) : [];
    renderStoresList(elStoresSearch ? elStoresSearch.value : '');
  }

  function applyStoresSelection() {
    if (!elStoresList) return;
    const cbs = elStoresList.querySelectorAll('input[type="checkbox"]');
    const sel = [];
    cbs.forEach(cb => {
      if (cb.checked) sel.push(cb.value);
    });
    selectedStores = sel;
    updateStoresLabel();
    saveFilters();
  }

  async function fetchTrend() {
    const y = elYear ? Number(elYear.value) : cfg.defaultYear;
    const mf = elFrom ? Number(elFrom.value) : cfg.defaultMonth;
    const mt = elTo ? Number(elTo.value) : mf;

    const params = new URLSearchParams();
    params.set('year', String(y));
    params.set('month_from', String(mf));
    params.set('month_to', String(mt));
    params.set('stores', selectedStores.join(','));

    const res = await fetch(`${apiUrl}?${params.toString()}`, { credentials: 'same-origin' });
    const data = await res.json();
    if (!res.ok) throw new Error(data && data.error ? data.error : 'Errore');
    if (data && data.error) throw new Error(data.error);
    return data;
  }

  function buildCard(title) {
    const col = document.createElement('div');
    col.className = 'trend-card card shadow-sm';

    const header = document.createElement('div');
    header.className = 'card-header bg-white';
    header.innerHTML = `<div class="fw-semibold">${title}</div>`;

    const body = document.createElement('div');
    body.className = 'card-body';
    const canvas = document.createElement('canvas');
    canvas.className = 'trend-canvas';
    body.appendChild(canvas);

    col.appendChild(header);
    col.appendChild(body);
    return { col, canvas };
  }

  function render(data) {
    destroyCharts();
    if (!elChartsWrap) return;
    elChartsWrap.innerHTML = '';

    const months = Array.isArray(data.months) ? data.months : [];
    const labels = months.map(m => m.label || String(m.month || ''));

    const cards = Array.isArray(data.cards) ? data.cards : null;

    // fallback (vecchia struttura)
    const fallbackCards = [];
    if (!cards && data && data.series && data.macro_order) {
      (data.macro_order || []).forEach((v, idx) => {
        const s = data.series[v] || {};
        fallbackCards.push({
          id: `f${idx+1}`,
          voice: v,
          metric: 'eur',
          title: `${v} (€)`,
          budget: s.budget || [],
          actual: s.actual || [],
          last: s.last || []
        });
      });
    }

    const list = cards || fallbackCards;
    if (!list.length) return;

    list.forEach(card => {
      const metric = card.metric || 'eur';
      const title = card.title || card.voice || 'Grafico';

      const b = Array.isArray(card.budget) ? card.budget : [];
      const a = Array.isArray(card.actual) ? card.actual : [];
      const l = Array.isArray(card.last) ? card.last : [];

      const { col, canvas } = buildCard(title);
      elChartsWrap.appendChild(col);

      const ctx = canvas.getContext('2d');
      const isPct = metric === 'pct';

      const chart = new Chart(ctx, {
        type: 'line',
        data: {
          labels,
          datasets: [
            {
              label: 'Budget',
              data: b,
              borderColor: '#6c757d',
              backgroundColor: 'rgba(108,117,125,.08)',
              borderDash: [6, 4],
              tension: 0.25,
              pointRadius: 2,
            },
            {
              label: 'Actual',
              data: a,
              borderColor: '#0d6efd',
              backgroundColor: 'rgba(13,110,253,.08)',
              tension: 0.25,
              pointRadius: 2,
            },
            {
              label: 'Anno precedente',
              data: l,
              borderColor: '#198754',
              backgroundColor: 'rgba(25,135,84,.08)',
              borderDash: [3, 3],
              tension: 0.25,
              pointRadius: 2,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: function (ctx) {
                  const val = ctx.parsed.y;
                  const f = isPct ? fmtPCT : fmtEUR;
                  return `${ctx.dataset.label}: ${f.format(val || 0)}`;
                }
              }
            }
          },
          scales: {
            y: {
              ticks: {
                callback: function (v) {
                  const f = isPct ? fmtPCT : fmtEUR;
                  return f.format(v || 0);
                }
              }
            }
          }
        }
      });

      charts.push(chart);
    });
  }

  async function apply() {
    setError('');
    updatePeriodLabel();
    saveFilters();

    if (!selectedStores.length) {
      setError('Seleziona almeno uno store.');
      return;
    }

    setVisible(elLoading, true);
    try {
      const data = await fetchTrend();
      render(data);
    } catch (e) {
      setError(e && e.message ? e.message : 'Errore');
      if (elChartsWrap) elChartsWrap.innerHTML = '';
    } finally {
      setVisible(elLoading, false);
    }
  }

  async function initStoresModal() {
    await ensureStoresLoaded();
    updateStoresLabel();
    renderStoresList('');

    if (elStoresSearch) {
      elStoresSearch.addEventListener('input', () => renderStoresList(elStoresSearch.value));
    }
    if (elStoresAll) {
      elStoresAll.addEventListener('click', () => selectAllStores(true));
    }
    if (elStoresNone) {
      elStoresNone.addEventListener('click', () => selectAllStores(false));
    }
    if (elStoresApply) {
      elStoresApply.addEventListener('click', () => applyStoresSelection());
    }

    const modalEl = document.getElementById('trendStoresModal');
    if (modalEl) {
      modalEl.addEventListener('show.bs.modal', async () => {
        await ensureStoresLoaded();
        renderStoresList(elStoresSearch ? elStoresSearch.value : '');
      });
    }
  }

  function init() {
    fillYearSelect();
    fillMonthSelect(elFrom);
    fillMonthSelect(elTo);

    loadSaved();

    const now = new Date();
    if (elYear && !elYear.value) elYear.value = String(cfg.defaultYear || now.getFullYear());
    if (elFrom && !elFrom.value) elFrom.value = String(cfg.defaultMonth || (now.getMonth() + 1));
    if (elTo && !elTo.value) elTo.value = elFrom ? elFrom.value : String(cfg.defaultMonth || (now.getMonth() + 1));

    updatePeriodLabel();
    updateStoresLabel();

    if (elApply) elApply.addEventListener('click', apply);

    if (elFrom && elTo) {
      elFrom.addEventListener('change', () => {
        if (Number(elTo.value) < Number(elFrom.value)) elTo.value = elFrom.value;
      });
      elTo.addEventListener('change', () => {
        if (Number(elTo.value) < Number(elFrom.value)) elFrom.value = elTo.value;
      });
    }

    initStoresModal();

    apply();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
