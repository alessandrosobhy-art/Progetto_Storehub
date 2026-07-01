/* Cruscotto - Analisi Mensile (v1.0.0)
   Stessi principi dell'analisi settimanale: Actual dove presente, altrimenti Previsione.
   Il mese viene mostrato completo se ci sono dati per tutti i giorni, altrimenti fino all'ultimo dato (Actual/Previsione).
*/

(function () {
  const CFG = window.CRUSCOTTO_MONTHLY || {};
  const apiUrl = CFG.apiUrl;
  const storeCode = CFG.storeCode;
  const I18N = CFG.i18n || {};
  const localeMap = { it: 'it-IT', en: 'en-US', fr: 'fr-FR', es: 'es-ES' };
  const locale = localeMap[String(CFG.locale || 'it').slice(0, 2)] || 'it-IT';
  function tr(key, fallback) { return I18N[key] || fallback; }

  const els = {
    monthRangeLabel: document.getElementById('monthRangeLabel'),
    err: document.getElementById('monthlyError'),
    yearSelect: document.getElementById('yearSelect'),
    monthSelect: document.getElementById('monthSelect'),
    btnPrev: document.getElementById('btnPrevMonth'),
    btnThis: document.getElementById('btnThisMonth'),
    btnNext: document.getElementById('btnNextMonth'),
    thead: document.getElementById('monthlyThead'),
    tbody: document.getElementById('monthlyTbody'),
    kpiCards: document.getElementById('kpiCards'),
    chartRevenues: document.getElementById('chartRevenues'),
    chartReceipt: document.getElementById('chartReceipt'),
    chartAvg: document.getElementById('chartAvgReceipt'),
  };

  let monthStart = firstOfMonth(new Date());
  let charts = { revenues: null, receipt: null, avg: null };
  let suppressSelectEvents = false;

  function firstOfMonth(d) {
    return new Date(d.getFullYear(), d.getMonth(), 1);
  }

  function addMonths(d, delta) {
    const x = new Date(d.getFullYear(), d.getMonth(), 1);
    x.setMonth(x.getMonth() + delta);
    return firstOfMonth(x);
  }

  function isoDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${dd}`;
  }

  function capitalize(s) {
    const t = String(s || '');
    return t ? t.charAt(0).toUpperCase() + t.slice(1) : t;
  }

  function weekdayName(date) {
    const names = Array.isArray(I18N.weekdays) ? I18N.weekdays : [];
    const idx = date instanceof Date && !Number.isNaN(date.getTime()) ? date.getDay() : -1;
    return names[idx] || capitalize(new Intl.DateTimeFormat(locale, { weekday: 'short' }).format(date));
  }

  function projectionLabel(value) {
    const raw = String(value || '').trim().toLowerCase();
    if (raw === 'actual') return tr('actual', 'Actual');
    if (raw === 'previsione' || raw === 'forecast') return tr('forecast', 'Forecast');
    return value || '';
  }

  function fmtMoney(v, dec) {
    const n = Number(v || 0);
    return n.toLocaleString(locale, { minimumFractionDigits: dec, maximumFractionDigits: dec });
  }

  function fmtNum(v, dec) {
    const n = Number(v || 0);
    return n.toLocaleString(locale, { minimumFractionDigits: dec, maximumFractionDigits: dec });
  }

  function pctFrom(a, b) {
    const A = Number(a || 0);
    const B = Number(b || 0);
    if (!B) return 0;
    return (A / B - 1) * 100;
  }

  function pctClass(p) {
    const x = Number(p);
    if (!Number.isFinite(x) || Math.abs(x) < 0.00001) return 'pct-zero';
    return x >= 0 ? 'pct-pos' : 'pct-neg';
  }

  function fmtPct(p, decimals = 1) {
    if (p === null || p === undefined || !Number.isFinite(Number(p))) {
      return '<span class="pct-zero">—</span>';
    }
    const x = Number(p);
    const s = `${x >= 0 ? '+' : ''}${x.toLocaleString(locale, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}%`;
    return `<span class="${pctClass(x)}">${s}</span>`;
  }

  function showError(msg) {
    if (!els.err) return;
    els.err.textContent = msg || tr('error', 'Errore');
    els.err.classList.remove('d-none');
  }

  function hideError() {
    if (!els.err) return;
    els.err.classList.add('d-none');
    els.err.textContent = '';
  }

  function buildYearOptions() {
    if (!els.yearSelect) return;
    const now = new Date();
    const yNow = now.getFullYear();
    const years = [];
    for (let y = yNow - 2; y <= yNow + 2; y++) years.push(y);

    els.yearSelect.innerHTML = '';
    years.forEach((y) => {
      const o = document.createElement('option');
      o.value = String(y);
      o.textContent = String(y);
      els.yearSelect.appendChild(o);
    });
  }

  function buildMonthOptions() {
    if (!els.monthSelect) return;
    const fmt = new Intl.DateTimeFormat(locale, { month: 'long' });
    els.monthSelect.innerHTML = '';
    for (let m = 0; m < 12; m++) {
      const o = document.createElement('option');
      o.value = String(m + 1);
      o.textContent = capitalize(fmt.format(new Date(2020, m, 1)));
      els.monthSelect.appendChild(o);
    }
  }

  function syncSelectorsFromMonthStart() {
    if (!els.yearSelect || !els.monthSelect) return;
    suppressSelectEvents = true;
    try {
      els.yearSelect.value = String(monthStart.getFullYear());
      els.monthSelect.value = String(monthStart.getMonth() + 1);
    } finally {
      suppressSelectEvents = false;
    }
  }

  function setMonthLabel(monthStartIso, displayEndIso, monthEndIso) {
    if (!els.monthRangeLabel) return;
    const dStart = new Date(String(monthStartIso || '') + 'T00:00:00');
    const dEnd = new Date(String(displayEndIso || '') + 'T00:00:00');
    const dMonthEnd = new Date(String(monthEndIso || '') + 'T00:00:00');

    const fmtMonth = new Intl.DateTimeFormat(locale, { month: 'long', year: 'numeric' });
    const fmtDay = new Intl.DateTimeFormat(locale, { day: '2-digit', month: '2-digit', year: 'numeric' });

    const labelMonth = capitalize(fmtMonth.format(dStart));
    const range = `${fmtDay.format(dStart)} → ${fmtDay.format(dEnd)}`;
    const isFull = dEnd.getTime() === dMonthEnd.getTime();

    els.monthRangeLabel.textContent = isFull ? `${labelMonth} • ${range}` : `${labelMonth} • ${range} (${tr('partial', 'partial')})`;
  }

  function kpiCard({ label, value, subHtml }) {
    return `
      <div class="cr-kpi">
        <div class="cr-kpi__label">${label}</div>
        <div class="cr-kpi__value">${value}</div>
        <div class="cr-kpi__sub">${subHtml || ''}</div>
      </div>
    `;
  }

  function renderKpis(data) {
    if (!els.kpiCards) return;
    const t = data.totals || {};

    const revActual = Number(t.revenues_actual || 0);
    const rev = Number(t.revenues_chart || 0); // Proiezione: Actual (se presente) o Previsione
    const projDaysA = Number(t.projection_days_actual || 0);
    const projDaysP = Number(t.projection_days_forecast || 0);
    const projDaysND = Number(t.projection_days_missing || 0);
    const revLy = Number(t.revenues_ly || 0);
    const revBudget = Number(t.revenues_budget || 0);

    const receipts = Number(t.receipts_actual || 0);
    const receiptsLy = Number(t.receipts_ly || 0);

    const avg = Number(t.avg_receipt_actual || 0);
    const avgLy = Number(t.avg_receipt_ly || 0);

    const del = Number(t.delivery_total || 0);
    const delInc = Number(t.delivery_inc || 0);

    const ore = Number(t.ore_totali || 0);
    const oreStage = Number(t.ore_stage || 0);
    const oreTraining = Number(t.ore_training || 0);
    const prod = Number(t.produttivita || 0);

    const revVsLy = pctFrom(rev, revLy);
    const revVsBudget = pctFrom(rev, revBudget);
    const recVsLy = pctFrom(receipts, receiptsLy);
    const avgVsLy = pctFrom(avg, avgLy);

        const html = [
      kpiCard({
        label: tr('monthRevenues', 'Revenues (mese)'),
        value: fmtMoney(rev, 0),
        subHtml: `<span class="cr-badge">${tr('projection', 'Projection')}: A ${fmtNum(projDaysA, 0)} / P ${fmtNum(projDaysP, 0)}</span>${revActual ? `<span class="cr-badge cr-badge--muted">${tr('actual', 'Actual')}: ${fmtMoney(revActual, 0)}</span>` : ''}<span class="cr-badge">${tr('lastYearShort', 'LY')}: ${fmtMoney(revLy, 0)} ${fmtPct(revVsLy)}</span><span class="cr-badge">${tr('budget', 'Budget')}: ${fmtMoney(revBudget, 0)} ${fmtPct(revVsBudget)}</span>`,
      }),
      kpiCard({
        label: tr('monthReceipt', 'Receipt (mese)'),
        value: fmtNum(receipts, 0),
        subHtml: `<span class="cr-badge">${tr('lastYearShort', 'LY')}: ${fmtNum(receiptsLy, 0)} ${fmtPct(recVsLy)}</span>`,
      }),
      kpiCard({
        label: tr('averageReceipt', 'Average Receipt'),
        value: fmtMoney(avg, 2),
        subHtml: `<span class="cr-badge">${tr('lastYearShort', 'LY')}: ${fmtMoney(avgLy, 2)} ${fmtPct(avgVsLy)}</span>`,
      }),
      kpiCard({
        label: tr('laborCost', 'Costo Lavoro'),
        value: `${fmtNum(ore, 1)} h`,
        subHtml: `<span class="cr-badge">${tr('stage', 'Stage')}: ${fmtNum(oreStage, 1)} h</span><span class="cr-badge">${tr('training', 'Training')}: ${fmtNum(oreTraining, 1)} h</span><span class="cr-badge">${tr('productivity', 'Prod')}: ${fmtMoney(prod, 2)}/h</span>`,
      }),
      kpiCard({
        label: tr('delivery', 'Delivery'),
        value: fmtMoney(del, 0),
        subHtml: `<span class="cr-badge">${tr('inc', 'Inc')}: ${fmtNum(delInc, 1)}%</span>`,
      }),
    ].join('');

    els.kpiCards.innerHTML = html;
  }

  function renderTable(data) {
    if (!els.thead || !els.tbody) return;

    const days = data.days || [];
    const t = data.totals || {};

    // Tabella: per Delivery mostriamo SOLO Totale e Inc (niente dettaglio voci).
    const deliveryCols = 2;

    // Header row 1 (groups)
    const r1 = [
      `<tr>
        <th class="sticky-col" rowspan="2">${tr('day', 'Giorno')}</th>
        <th rowspan="2" class="text-center">${tr('projection', 'Projection')}</th>
        <th colspan="7" class="text-center">${tr('revenues', 'Revenues')}</th>
        <th colspan="3" class="text-center">${tr('monthReceipt', 'Receipt')}</th>
        <th colspan="3" class="text-center">${tr('averageReceipt', 'Average Receipt')}</th>
        <th colspan="${deliveryCols}" class="text-center">${tr('delivery', 'Delivery')}</th>
        <th colspan="4" class="text-center">${tr('hours', 'Ore')}</th>
        <th rowspan="2" class="text-center">${tr('productivity', 'Prod')}</th>
      </tr>`,
    ];

    // Header row 2 (columns)
    const r2 = [
      `<tr>
        <th class="text-end">${tr('actual', 'Actual')}</th>
        <th class="text-end">${tr('previous', 'Prev')}</th>
        <th class="text-end">${tr('projectionShort', 'Proj')}</th>
        <th class="text-end">${tr('lastYearShort', 'LY')}</th>
        <th class="text-end">Δ%</th>
        <th class="text-end">${tr('budget', 'Budget')}</th>
        <th class="text-end">Δ%</th>

        <th class="text-end">${tr('actual', 'Actual')}</th>
        <th class="text-end">${tr('lastYearShort', 'LY')}</th>
        <th class="text-end">Δ%</th>

        <th class="text-end">${tr('actual', 'Actual')}</th>
        <th class="text-end">${tr('lastYearShort', 'LY')}</th>
        <th class="text-end">Δ%</th>

        <th class="text-end">Tot</th>
        <th class="text-end">Inc%</th>

        <th class="text-end">Tot</th>
        <th class="text-end">${tr('stage', 'Stage')}</th>
        <th class="text-end">${tr('training', 'Training')}</th>
        <th class="text-end">%</th>
      </tr>`,
    ];

    els.thead.innerHTML = r1.join('') + r2.join('');

    const fmtDate = new Intl.DateTimeFormat(locale, { day: '2-digit', month: '2-digit' });

    const body = days.map((d) => {
      const dt = new Date(String(d.date || '') + 'T00:00:00');
      const dayLabel = `${weekdayName(dt)} ${fmtDate.format(dt)}`;

      const revA = Number(d.revenues_actual || 0);
      const revF = Number(d.revenues_forecast || 0);
      const revProj = Number(d.revenues_chart || 0);
      const revLy = Number(d.revenues_ly || 0);
      const revBud = Number(d.revenues_budget || 0);

      const revVsLy = pctFrom(revProj, revLy);
      const revVsBud = pctFrom(revProj, revBud);

      const recA = Number(d.receipts_actual || 0);
      const recLy = Number(d.receipts_ly || 0);
      const recVsLy = pctFrom(recA, recLy);

      const avgA = Number(d.avg_receipt_actual || 0);
      const avgLy = Number(d.avg_receipt_ly || 0);
      const avgVsLy = pctFrom(avgA, avgLy);

      const del = Number(d.delivery_total || 0);
      const delInc = Number(d.delivery_inc || 0);

      const ore = Number(d.ore_totali || 0);
      const oreStage = Number(d.ore_stage || 0);
      const oreTraining = Number(d.ore_training || 0);
      const orePctStage = ore ? (oreStage / ore) * 100 : 0;

      const prod = Number(d.produttivita || 0);

      const proj = projectionLabel(d.projection_source || '');

      return `
        <tr>
          <td class="sticky-col">${dayLabel}</td>
          <td class="text-center">${proj ? `<span class="cr-pill">${proj}</span>` : ''}</td>

          <td class="text-end">${fmtMoney(revA, 0)}</td>
          <td class="text-end">${fmtMoney(revF, 0)}</td>
          <td class="text-end fw-semibold">${fmtMoney(revProj, 0)}</td>
          <td class="text-end">${fmtMoney(revLy, 0)}</td>
          <td class="text-end ${revVsLy >= 0 ? 'text-success' : 'text-danger'}">${fmtNum(revVsLy, 1)}%</td>
          <td class="text-end">${fmtMoney(revBud, 0)}</td>
          <td class="text-end ${revVsBud >= 0 ? 'text-success' : 'text-danger'}">${fmtNum(revVsBud, 1)}%</td>

          <td class="text-end">${fmtNum(recA, 0)}</td>
          <td class="text-end">${fmtNum(recLy, 0)}</td>
          <td class="text-end ${recVsLy >= 0 ? 'text-success' : 'text-danger'}">${fmtNum(recVsLy, 1)}%</td>

          <td class="text-end">${fmtMoney(avgA, 2)}</td>
          <td class="text-end">${fmtMoney(avgLy, 2)}</td>
          <td class="text-end ${avgVsLy >= 0 ? 'text-success' : 'text-danger'}">${fmtNum(avgVsLy, 1)}%</td>

          <td class="text-end">${fmtMoney(del, 0)}</td>
          <td class="text-end">${fmtNum(delInc, 1)}%</td>

          <td class="text-end">${fmtNum(ore, 1)}</td>
          <td class="text-end">${fmtNum(oreStage, 1)}</td>
          <td class="text-end">${fmtNum(oreTraining, 1)}</td>
          <td class="text-end">${fmtNum(orePctStage, 1)}%</td>

          <td class="text-end fw-semibold">${fmtMoney(prod, 2)}</td>
        </tr>
      `;
    });

    // Totals row
    const revActual = Number(t.revenues_actual || 0);
    const revF = Number(t.revenues_forecast || 0);
    const revProj = Number(t.revenues_chart || 0);
    const revLy = Number(t.revenues_ly || 0);
    const revBud = Number(t.revenues_budget || 0);
    const revVsLy = pctFrom(revProj, revLy);
    const revVsBud = pctFrom(revProj, revBud);

    const recA = Number(t.receipts_actual || 0);
    const recLy = Number(t.receipts_ly || 0);
    const recVsLy = pctFrom(recA, recLy);

    const avgA = Number(t.avg_receipt_actual || 0);
    const avgLy = Number(t.avg_receipt_ly || 0);
    const avgVsLy = pctFrom(avgA, avgLy);

    const del = Number(t.delivery_total || 0);
    const delInc = Number(t.delivery_inc || 0);

    const ore = Number(t.ore_totali || 0);
    const oreStage = Number(t.ore_stage || 0);
    const oreTraining = Number(t.ore_training || 0);
    const orePctStage = ore ? (oreStage / ore) * 100 : 0;

    const prod = Number(t.produttivita || 0);

    body.push(`
      <tr class="table-light fw-semibold">
        <td class="sticky-col">Totale</td>
        <td class="text-center">—</td>

        <td class="text-end">${fmtMoney(revActual, 0)}</td>
        <td class="text-end">${fmtMoney(revF, 0)}</td>
        <td class="text-end">${fmtMoney(revProj, 0)}</td>
        <td class="text-end">${fmtMoney(revLy, 0)}</td>
        <td class="text-end ${revVsLy >= 0 ? 'text-success' : 'text-danger'}">${fmtNum(revVsLy, 1)}%</td>
        <td class="text-end">${fmtMoney(revBud, 0)}</td>
        <td class="text-end ${revVsBud >= 0 ? 'text-success' : 'text-danger'}">${fmtNum(revVsBud, 1)}%</td>

        <td class="text-end">${fmtNum(recA, 0)}</td>
        <td class="text-end">${fmtNum(recLy, 0)}</td>
        <td class="text-end ${recVsLy >= 0 ? 'text-success' : 'text-danger'}">${fmtNum(recVsLy, 1)}%</td>

        <td class="text-end">${fmtMoney(avgA, 2)}</td>
        <td class="text-end">${fmtMoney(avgLy, 2)}</td>
        <td class="text-end ${avgVsLy >= 0 ? 'text-success' : 'text-danger'}">${fmtNum(avgVsLy, 1)}%</td>

        <td class="text-end">${fmtMoney(del, 0)}</td>
        <td class="text-end">${fmtNum(delInc, 1)}%</td>

        <td class="text-end">${fmtNum(ore, 1)}</td>
        <td class="text-end">${fmtNum(oreStage, 1)}</td>
        <td class="text-end">${fmtNum(oreTraining, 1)}</td>
        <td class="text-end">${fmtNum(orePctStage, 1)}%</td>

        <td class="text-end">${fmtMoney(prod, 2)}</td>
      </tr>
    `);

    els.tbody.innerHTML = body.join('');
  }

  function destroyCharts() {
    try { charts.revenues && charts.revenues.destroy(); } catch (_) {}
    try { charts.receipt && charts.receipt.destroy(); } catch (_) {}
    try { charts.avg && charts.avg.destroy(); } catch (_) {}
    charts = { revenues: null, receipt: null, avg: null };
  }

  function renderCharts(data) {
    const days = data.days || [];
    if (!window.Chart || !els.chartRevenues || !els.chartReceipt || !els.chartAvg) return;

    destroyCharts();

    const labels = days.map((d) => {
      const dt = new Date(String(d.date || '') + 'T00:00:00');
      const fmt = new Intl.DateTimeFormat(locale, { day: '2-digit', month: '2-digit' });
      return fmt.format(dt);
    });

    const seriesRevProj = days.map((d) => Number(d.revenues_chart || 0));
    const seriesRevLy = days.map((d) => Number(d.revenues_ly || 0));
    const seriesRevBud = days.map((d) => Number(d.revenues_budget || 0));

    const seriesRecA = days.map((d) => Number(d.receipts_actual || 0));
    const seriesRecLy = days.map((d) => Number(d.receipts_ly || 0));

    const seriesAvgA = days.map((d) => Number(d.avg_receipt_actual || 0));
    const seriesAvgLy = days.map((d) => Number(d.avg_receipt_ly || 0));

    charts.revenues = new Chart(els.chartRevenues.getContext('2d'), {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: tr('projectionShort', 'Proj'), data: seriesRevProj },
          { label: tr('lastYearShort', 'LY'), data: seriesRevLy },
          { label: tr('budget', 'Budget'), data: seriesRevBud },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: true } },
        scales: {
          y: { ticks: { callback: (v) => fmtMoney(v, 0) } },
        },
      },
    });

    charts.receipt = new Chart(els.chartReceipt.getContext('2d'), {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: tr('actual', 'Actual'), data: seriesRecA },
          { label: tr('lastYearShort', 'LY'), data: seriesRecLy },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: true } },
      },
    });

    charts.avg = new Chart(els.chartAvg.getContext('2d'), {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: tr('actual', 'Actual'), data: seriesAvgA },
          { label: tr('lastYearShort', 'LY'), data: seriesAvgLy },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: true } },
        scales: {
          y: { ticks: { callback: (v) => fmtMoney(v, 2) } },
        },
      },
    });
  }

  async function loadMonth() {
    hideError();
    if (!apiUrl) {
      showError('Config mancante (apiUrl).');
      return;
    }
    if (!storeCode) {
      showError('Seleziona prima uno store.');
      return;
    }

    try {
      const res = await fetch(apiUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ month_start: isoDate(monthStart) }),
      });

      let payload = null;
      try {
        payload = await res.json();
      } catch (_) {
        payload = null;
      }

      if (!res.ok || !payload || !payload.ok) {
        const msg = (payload && payload.error) ? payload.error : `Errore (${res.status})`;
        showError(msg);
        return;
      }

      const data = payload.data || {};
      setMonthLabel(data.month_start, data.display_end || data.month_end, data.month_end);
      renderKpis(data);
      renderTable(data);
      renderCharts(data);
    } catch (e) {
      showError(String(e || 'Errore'));
    }
  }

  function wireEvents() {
    if (els.btnPrev) els.btnPrev.addEventListener('click', () => {
      monthStart = addMonths(monthStart, -1);
      syncSelectorsFromMonthStart();
      loadMonth();
    });

    if (els.btnNext) els.btnNext.addEventListener('click', () => {
      monthStart = addMonths(monthStart, +1);
      syncSelectorsFromMonthStart();
      loadMonth();
    });

    if (els.btnThis) els.btnThis.addEventListener('click', () => {
      monthStart = firstOfMonth(new Date());
      syncSelectorsFromMonthStart();
      loadMonth();
    });

    if (els.yearSelect) {
      els.yearSelect.addEventListener('change', () => {
        if (suppressSelectEvents) return;
        const y = Number(els.yearSelect.value || NaN);
        if (!Number.isFinite(y)) return;
        monthStart = new Date(y, monthStart.getMonth(), 1);
        syncSelectorsFromMonthStart();
        loadMonth();
      });
    }

    if (els.monthSelect) {
      els.monthSelect.addEventListener('change', () => {
        if (suppressSelectEvents) return;
        const m = Number(els.monthSelect.value || NaN);
        if (!Number.isFinite(m)) return;
        monthStart = new Date(monthStart.getFullYear(), m - 1, 1);
        syncSelectorsFromMonthStart();
        loadMonth();
      });
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    buildYearOptions();
    buildMonthOptions();
    syncSelectorsFromMonthStart();
    wireEvents();
    loadMonth();
  });
})();
