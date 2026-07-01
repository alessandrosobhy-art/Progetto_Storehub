/* Cruscotto - Analisi KPI Settimanale (v 1.1.0)
   KPI overview + delivery stats + note con tagging @.
*/

(function () {
  const CFG = window.CRUSCOTTO_KPI_WEEKLY || {};
  const apiUrl = CFG.apiUrl;
  const noteGetUrl = CFG.noteGetUrl;
  const noteSaveUrl = CFG.noteSaveUrl;
  const I18N = CFG.i18n || {};
  const localeMap = { it: 'it-IT', en: 'en-US', fr: 'fr-FR', es: 'es-ES' };
  const locale = localeMap[String(CFG.locale || 'it').slice(0, 2)] || 'it-IT';

  function tr(key, fallback) {
    return I18N[key] || fallback;
  }

  const els = {
    weekRangeLabel: document.getElementById('weekRangeLabel'),
    err: document.getElementById('kpiWeeklyError'),
    yearSelect: document.getElementById('yearSelect'),
    weekNumSelect: document.getElementById('weekNumSelect'),
    btnPrev: document.getElementById('btnPrevWeek'),
    btnThis: document.getElementById('btnThisWeek'),
    btnNext: document.getElementById('btnNextWeek'),

    revHero: document.getElementById('revHero'),
    revProjectionHint: document.getElementById('revProjectionHint'),

    deliveryKpis: document.getElementById('deliveryKpis'),
    providerKpis: document.getElementById('providerKpis'),
    refundsHint: document.getElementById('refundsHint'),

    laborHero: document.getElementById('laborHero'),
    laborHint: document.getElementById('laborHint'),

    chartRevCompare: document.getElementById('chartRevCompare'),
    chartRefunds: document.getElementById('chartRefunds'),
    chartLabor: document.getElementById('chartLabor'),

    noteText: document.getElementById('noteText'),
    btnSaveNote: document.getElementById('btnSaveNote'),
    btnReloadNote: document.getElementById('btnReloadNote'),
    noteStatus: document.getElementById('noteStatus'),
    tagSuggest: document.getElementById('tagSuggest'),
  };

  let weekStart = mondayOf(new Date());
  let charts = { rev: null, refunds: null, labor: null };
  let tagItems = []; // [{key,label,insertText}]

  function mondayOf(d) {
    const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const wd = (x.getDay() + 6) % 7;
    x.setDate(x.getDate() - wd);
    x.setHours(0, 0, 0, 0);
    return x;
  }

  function isoDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${dd}`;
  }

  function addDays(d, n) {
    const x = new Date(d.getTime());
    x.setDate(x.getDate() + n);
    return x;
  }

  // ISO week helpers (Mon-based)
  function isoWeek1Monday(year) {
    const jan4 = new Date(year, 0, 4);
    const wd = (jan4.getDay() + 6) % 7;
    const mon = new Date(jan4);
    mon.setDate(jan4.getDate() - wd);
    mon.setHours(0, 0, 0, 0);
    return mon;
  }

  function isoWeekYearOf(d) {
    const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    x.setHours(0, 0, 0, 0);
    x.setDate(x.getDate() + 3 - ((x.getDay() + 6) % 7));
    const y = x.getFullYear();
    const w1 = isoWeek1Monday(y);
    const week = Math.floor((x - w1) / 86400000 / 7) + 1;
    return { year: y, week };
  }

  function isoWeeksInYear(year) {
    const d = new Date(year, 11, 28);
    return isoWeekYearOf(d).week;
  }

  function mondayFromIsoWeek(year, week) {
    const m = isoWeek1Monday(year);
    m.setDate(m.getDate() + (Number(week) - 1) * 7);
    m.setHours(0, 0, 0, 0);
    return m;
  }

  function pad2(n) {
    return String(n).padStart(2, '0');
  }

  let suppressSelectEvents = false;

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

  function buildWeekNumOptions(year) {
    if (!els.weekNumSelect) return;
    const y = Number(year);
    const maxWeeks = isoWeeksInYear(y);
    els.weekNumSelect.innerHTML = '';
    for (let w = 1; w <= maxWeeks; w++) {
      const o = document.createElement('option');
      o.value = String(w);
      o.textContent = `W${pad2(w)}`;
      els.weekNumSelect.appendChild(o);
    }
  }

  function syncSelectorsFromWeekStart() {
    if (!els.yearSelect || !els.weekNumSelect) return;
    const { year, week } = isoWeekYearOf(weekStart);

    suppressSelectEvents = true;

    const years = Array.from(els.yearSelect.options).map((o) => Number(o.value));
    if (!years.includes(year)) {
      const o = document.createElement('option');
      o.value = String(year);
      o.textContent = String(year);
      els.yearSelect.appendChild(o);
    }

    els.yearSelect.value = String(year);
    buildWeekNumOptions(year);
    els.weekNumSelect.value = String(week);

    suppressSelectEvents = false;
  }

  function setWeekStartFromSelectors() {
    if (!els.yearSelect || !els.weekNumSelect) return;
    const y = Number(els.yearSelect.value || new Date().getFullYear());
    const w = Number(els.weekNumSelect.value || 1);
    weekStart = mondayFromIsoWeek(y, w);
  }

  function setLoading(isLoading) {
    [els.btnPrev, els.btnThis, els.btnNext].forEach((b) => {
      if (b) b.disabled = !!isLoading;
    });
    if (els.yearSelect) els.yearSelect.disabled = !!isLoading;
    if (els.weekNumSelect) els.weekNumSelect.disabled = !!isLoading;
    if (els.btnSaveNote) els.btnSaveNote.disabled = !!isLoading;
    if (els.btnReloadNote) els.btnReloadNote.disabled = !!isLoading;
  }

  function showError(msg) {
    if (!els.err) return;
    els.err.textContent = msg || tr('error', 'Errore.');
    els.err.classList.remove('d-none');
  }

  function clearError() {
    if (!els.err) return;
    els.err.classList.add('d-none');
    els.err.textContent = '';
  }

  function setWeekLabel(startIso, endIso) {
    if (!els.weekRangeLabel) return;
    const fmt = new Intl.DateTimeFormat(locale, { weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric' });
    const d1 = new Date(startIso + 'T00:00:00');
    const d2 = new Date(endIso + 'T00:00:00');
    els.weekRangeLabel.textContent = `${fmt.format(d1)} → ${fmt.format(d2)}`;
  }

  function fmtMoney(v, decimals = 0) {
    const num = Number.isFinite(Number(v)) ? Number(v) : 0;
    return num.toLocaleString(locale, { style: 'currency', currency: 'EUR', minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  }

  function fmtNum(v, decimals = 0) {
    const num = Number.isFinite(Number(v)) ? Number(v) : 0;
    return num.toLocaleString(locale, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  }

  function fmtPct(v, decimals = 1) {
    const num = Number(v);
    if (!Number.isFinite(num)) return '—';
    return `${num.toLocaleString(locale, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}%`;
  }

  function fmtSignedEur(v, decimals = 0) {
    const num = Number(v);
    if (!Number.isFinite(num)) return '—';
    const sign = num >= 0 ? '+' : '';
    return sign + fmtMoney(num, decimals);
  }

  function fmtSignedPct(v, decimals = 1) {
    const num = Number(v);
    if (!Number.isFinite(num)) return '—';
    const sign = num >= 0 ? '+' : '';
    return sign + fmtPct(num, decimals);
  }


  function fmtDayMonth(iso) {
    try {
      if (!iso) return '—';
      const d = new Date(String(iso).slice(0, 10) + 'T00:00:00');
      return new Intl.DateTimeFormat(locale, { day: '2-digit', month: '2-digit' }).format(d);
    } catch {
      return String(iso || '—');
    }
  }

  function trendClass(delta) {
    const x = Number(delta);
    if (!Number.isFinite(x) || Math.abs(x) < 0.00001) return 'trend-flat';
    return x >= 0 ? 'trend-up' : 'trend-down';
  }

  function trendIcon(delta) {
    const x = Number(delta);
    if (!Number.isFinite(x) || Math.abs(x) < 0.00001) return '⟷';
    return x >= 0 ? '▲' : '▼';
  }

  function deltaBlock({ title, deltaLabel, deltaValue, deltaPct }) {
    const cls = trendClass(deltaPct);
    const icon = trendIcon(deltaPct);
    const pctTxt = (deltaPct === null || deltaPct === undefined || !Number.isFinite(Number(deltaPct))) ? '—' : `${Number(deltaPct) >= 0 ? '+' : ''}${fmtPct(deltaPct, 1)}`;
    return `
      <div class="kpi-delta">
        <div class="kpi-delta__left">
          <div class="kpi-delta__title">${title}</div>
          <div class="kpi-delta__val">${deltaValue} <span class="text-muted">(${deltaLabel})</span></div>
        </div>
        <div class="kpi-delta__right ${cls}">
          <span class="trend-icon">${icon}</span>
          <span>${pctTxt}</span>
        </div>
      </div>
    `;
  }

  function heroBlock({ label, value, metaHtml, sideHtml }) {
    return `
      <div class="kpi-hero">
        <div class="kpi-hero__main">
          <div class="kpi-hero__label">${label}</div>
          <div class="kpi-hero__value">${value}</div>
          ${metaHtml ? `<div class="kpi-hero__meta">${metaHtml}</div>` : ''}
        </div>
        <div class="kpi-hero__side">${sideHtml || ''}</div>
      </div>
    `;
  }

  function kpiCard({ label, value, subHtml }) {
    return `
      <div class="cr-kpi">
        <div class="cr-kpi__label">${label}</div>
        <div class="cr-kpi__value">${value}</div>
        ${subHtml ? `<div class="cr-kpi__sub">${subHtml}</div>` : ''}
      </div>
    `;
  }

  function safeFetchJson(url, opts) {
    return fetch(url, opts).then(async (r) => {
      const txt = await r.text();
      let j;
      try { j = JSON.parse(txt); } catch { j = null; }
      if (!r.ok) {
        const err = (j && (j.error || j.message)) ? (j.error || j.message) : (txt || r.statusText);
        throw new Error(err);
      }
      return j;
    });
  }

  function destroyChart(ch) {
    if (ch && typeof ch.destroy === 'function') {
      try { ch.destroy(); } catch { /* ignore */ }
    }
  }

  function buildCharts(data) {
    if (typeof Chart === 'undefined') return;

    // Revenues (Week / Week-1 / MTD)
    destroyChart(charts.rev);
    if (els.chartRevCompare) {
      const ctx = els.chartRevCompare.getContext('2d');
      const r = data.revenues || {};
      const rc = data.receipts || {};

      const labels = [tr('week', 'Week'), tr('weekMinus1', 'Week -1'), tr('mtd', 'MTD')];
      const actual = [Number(r.actual) || 0, Number(r.prev_week_actual) || 0, Number(r.mtd_actual) || 0];
      const budget = [Number(r.budget) || 0, Number(r.prev_week_budget) || 0, Number(r.mtd_budget) || 0];
      const lastYear = [Number(r.last_year) || 0, Number(r.prev_week_last_year) || 0, Number(r.mtd_last_year) || 0];
      const receipts = [Number(rc.current) || 0, Number(rc.prev_week) || 0, Number(rc.mtd_current) || 0];

      charts.rev = new Chart(ctx, {
        type: 'bar',
        data: {
          labels,
          datasets: [
            {
              label: 'Actual (€)',
              data: actual,
              borderWidth: 1,
              backgroundColor: 'rgba(54, 162, 235, 0.35)',
              borderColor: 'rgba(54, 162, 235, 0.9)',
            },
            {
              label: 'Budget (€)',
              data: budget,
              borderWidth: 1,
              backgroundColor: 'rgba(255, 159, 64, 0.35)',
              borderColor: 'rgba(255, 159, 64, 0.9)',
            },
            {
              label: 'Last Year (€)',
              data: lastYear,
              borderWidth: 1,
              backgroundColor: 'rgba(75, 192, 192, 0.35)',
              borderColor: 'rgba(75, 192, 192, 0.9)',
            },
            {
              type: 'line',
              label: tr('receipts', 'Scontrini'),
              data: receipts,
              yAxisID: 'y1',
              borderWidth: 2,
              borderColor: 'rgba(153, 102, 255, 0.95)',
              backgroundColor: 'rgba(153, 102, 255, 0.15)',
              pointRadius: 3,
              tension: 0.25,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          plugins: {
            legend: { display: true, position: 'top' },
            tooltip: {
              callbacks: {
                label: (ctx) => {
                  const label = ctx.dataset.label || '';
                  const v = ctx.parsed.y;
                  if (ctx.dataset.yAxisID === 'y1') {
                    return `${label}: ${fmtNum(v, 0)}`;
                  }
                  return `${label}: ${fmtMoney(v, 0)}`;
                }
              }
            }
          },
          scales: {
            y: {
              beginAtZero: true,
              ticks: {
                callback: (v) => {
                  try { return Number(v).toLocaleString(locale); } catch { return v; }
                }
              }
            },
            y1: {
              beginAtZero: true,
              position: 'right',
              grid: { drawOnChartArea: false },
              ticks: {
                callback: (v) => {
                  try { return Number(v).toLocaleString(locale); } catch { return v; }
                }
              }
            }
          }
        }
      });
    }

    // Refunds
    destroyChart(charts.refunds);
    if (els.chartRefunds) {
      const ctx = els.chartRefunds.getContext('2d');
      charts.refunds = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: [tr('refunds', 'Rimborsi'), tr('cancelledRefunds', 'Rimborsi annullati'), tr('netRefunds', 'Rimborsi netti')],
          datasets: [{
            label: '€',
            data: [data.delivery.refunds_value, data.delivery.refunds_cancelled_value, data.delivery.refunds_net],
            borderWidth: 1,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            y: {
              ticks: {
                callback: (v) => {
                  try { return Number(v).toLocaleString(locale); } catch { return v; }
                }
              }
            }
          }
        }
      });
    }

    // Costo del lavoro
    destroyChart(charts.labor);
    if (els.chartLabor) {
      const ctx = els.chartLabor.getContext('2d');
      const p = data.labor_cost || {};
      charts.labor = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: ['Week', 'Week -1', 'MTD'],
          datasets: [
            {
              type: 'bar',
              label: '% su revenues',
              data: [p.pct, p.prev_week_pct, p.mtd_pct],
              borderWidth: 1,
              backgroundColor: 'rgba(54, 162, 235, 0.35)',
              borderColor: 'rgba(54, 162, 235, 1)',
              yAxisID: 'y',
            },
            {
              type: 'line',
              label: 'Costo €',
              data: [p.cost_eur, p.prev_week_cost_eur, p.mtd_cost_eur],
              borderWidth: 2,
              borderColor: 'rgba(255, 159, 64, 1)',
              backgroundColor: 'rgba(255, 159, 64, 0.15)',
              pointRadius: 3,
              tension: 0.25,
              yAxisID: 'y1',
            }
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: true },
            tooltip: {
              callbacks: {
                label: (ctx) => {
                  const label = ctx.dataset.label || '';
                  const v = ctx.parsed.y;
                  if (label.includes('%')) {
                    try { return `${label}: ${Number(v).toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`; } catch { return `${label}: ${v}`; }
                  }
                  try { return `${label}: ${Number(v).toLocaleString(locale)} €`; } catch { return `${label}: ${v}`; }
                }
              }
            }
          },
          scales: {
            y: {
              beginAtZero: true,
              ticks: {
                callback: (v) => {
                  try { return `${Number(v).toLocaleString(locale)}%`; } catch { return v; }
                }
              }
            },
            y1: {
              beginAtZero: true,
              position: 'right',
              grid: { drawOnChartArea: false },
              ticks: {
                callback: (v) => {
                  try { return Number(v).toLocaleString(locale); } catch { return v; }
                }
              }
            }
          }
        }
      });
    }
  }

  function renderRevenue(data) {
    if (!els.revHero) return;
    const r = data.revenues || {};
    const rc = data.receipts || {};

    const weekActual = Number(r.actual) || 0;
    const weekBudget = Number(r.budget) || 0;
    const weekLy = Number(r.last_year) || 0;
    const weekReceipts = Number(rc.current) || 0;

    const prevWeekActual = Number(r.prev_week_actual) || 0;
    const prevWeekBudget = Number(r.prev_week_budget) || 0;
    const prevWeekLy = Number(r.prev_week_last_year) || 0;

    const mtdActual = Number(r.mtd_actual) || 0;
    const mtdBudget = Number(r.mtd_budget) || 0;
    const mtdLy = Number(r.mtd_last_year) || 0;
    const mtdReceipts = Number(rc.mtd_current) || 0;

    const mtdStart = r.mtd_start ? String(r.mtd_start) : null;
    const mtdEnd = r.mtd_end ? String(r.mtd_end) : null;
    const mtdLabel = (mtdStart && mtdEnd) ? `MTD ${fmtDayMonth(mtdStart)}→${fmtDayMonth(mtdEnd)}` : 'Progressivo mese';

    function revRow(label, value, cls = '') {
      return `
        <div class="rev-row">
          <div class="rev-row__label">${label}</div>
          <div class="rev-row__value ${cls}">${value}</div>
        </div>
      `;
    }

    function revDeltaRow(label, deltaVal, deltaPct) {
      // (compat) tenuta per eventuali usi futuri
      const pctCls = trendClass(deltaPct);
      const valTxt = Number.isFinite(Number(deltaVal)) ? fmtSignedEur(deltaVal, 0) : '—';
      const pctTxt = Number.isFinite(Number(deltaPct)) ? fmtSignedPct(deltaPct, 1) : '—';
      return revRow(label, `${valTxt} <span class="rev-row__sub ${pctCls}">${pctTxt}</span>`);
    }

    function revBox(title, subtitle, rowsHtml) {
      return `
        <div class="rev-box">
          <div class="rev-box__head">
            <div class="rev-box__title">${title}</div>
            ${subtitle ? `<div class="rev-box__subtitle">${subtitle}</div>` : ''}
          </div>
          <div class="rev-box__body">${rowsHtml}</div>
        </div>
      `;
    }

    const weekBox = revBox(
      tr('dataWeek', 'Dati week'),
      null,
      [
        revRow(tr('revenues', 'Revenues'), fmtMoney(weekActual, 0)),
        revRow(tr('budget', 'Budget'), fmtMoney(weekBudget, 0)),
        revRow(tr('lastYear', 'Last year'), fmtMoney(weekLy, 0)),
        revRow(tr('receipts', 'Scontrini'), fmtNum(weekReceipts, 0)),
      ].join('')
    );

    const weekDeltaBox = revBox(
      tr('variancesWeek', 'Scostamenti week'),
      null,
      [
        deltaBlock({
          title: tr('vsBudget', 'vs Budget'),
          deltaLabel: fmtMoney(weekBudget, 0),
          deltaValue: fmtSignedEur(r.delta_vs_budget_value, 0),
          deltaPct: r.delta_vs_budget_pct,
        }),
        deltaBlock({
          title: tr('vsLastYear', 'vs Last Year'),
          deltaLabel: fmtMoney(weekLy, 0),
          deltaValue: fmtSignedEur(r.delta_vs_last_year_value, 0),
          deltaPct: r.delta_vs_last_year_pct,
        }),
      ].join('')
    );

    const prevDeltaBudgetVal = (r.prev_week_delta_vs_budget_value !== null && r.prev_week_delta_vs_budget_value !== undefined)
      ? r.prev_week_delta_vs_budget_value
      : (prevWeekActual - prevWeekBudget);
    const prevDeltaBudgetPct = (r.prev_week_delta_vs_budget_pct !== null && r.prev_week_delta_vs_budget_pct !== undefined)
      ? r.prev_week_delta_vs_budget_pct
      : (prevWeekBudget ? ((prevDeltaBudgetVal / prevWeekBudget) * 100.0) : null);

    const prevDeltaLyVal = (r.prev_week_delta_vs_last_year_value !== null && r.prev_week_delta_vs_last_year_value !== undefined)
      ? r.prev_week_delta_vs_last_year_value
      : (prevWeekActual - prevWeekLy);
    const prevDeltaLyPct = (r.prev_week_delta_vs_last_year_pct !== null && r.prev_week_delta_vs_last_year_pct !== undefined)
      ? r.prev_week_delta_vs_last_year_pct
      : (prevWeekLy ? ((prevDeltaLyVal / prevWeekLy) * 100.0) : null);

    const prevWeekDeltaBox = revBox(
      tr('variancesWeekMinus1', 'Scostamenti week -1'),
      null,
      [
        deltaBlock({
          title: tr('vsBudget', 'vs Budget'),
          deltaLabel: fmtMoney(prevWeekBudget, 0),
          deltaValue: fmtSignedEur(prevDeltaBudgetVal, 0),
          deltaPct: prevDeltaBudgetPct,
        }),
        deltaBlock({
          title: tr('vsLastYear', 'vs Last Year'),
          deltaLabel: fmtMoney(prevWeekLy, 0),
          deltaValue: fmtSignedEur(prevDeltaLyVal, 0),
          deltaPct: prevDeltaLyPct,
        }),
      ].join('')
    );

    const mtdBox = revBox(
      tr('monthToDate', 'Progressivo mese'),
      mtdLabel,
      [
        revRow(tr('revenues', 'Revenues'), fmtMoney(mtdActual, 0)),
        revRow(tr('budget', 'Budget'), fmtMoney(mtdBudget, 0)),
        revRow(tr('lastYear', 'Last year'), fmtMoney(mtdLy, 0)),
        revRow(tr('receipts', 'Scontrini'), fmtNum(mtdReceipts, 0)),
      ].join('')
    );

    const mtdDeltaBox = revBox(
      tr('variances', 'Scostamenti'),
      mtdLabel,
      [
        deltaBlock({
          title: tr('vsBudget', 'vs Budget'),
          deltaLabel: fmtMoney(mtdBudget, 0),
          deltaValue: fmtSignedEur(r.mtd_delta_vs_budget_value, 0),
          deltaPct: r.mtd_delta_vs_budget_pct,
        }),
        deltaBlock({
          title: tr('vsLastYear', 'vs Last Year'),
          deltaLabel: fmtMoney(mtdLy, 0),
          deltaValue: fmtSignedEur(r.mtd_delta_vs_last_year_value, 0),
          deltaPct: r.mtd_delta_vs_last_year_pct,
        }),
      ].join('')
    );

    els.revHero.innerHTML = `
      <div class="rev-grid">
        ${weekBox}
        ${weekDeltaBox}
        ${prevWeekDeltaBox}
        ${mtdBox}
        ${mtdDeltaBox}
      </div>
    `;

    if (els.revProjectionHint) {
      const p = r.projection || {};
      const a = Number(p.days_actual) || 0;
      const f = Number(p.days_forecast) || 0;
      const m = Number(p.days_missing) || 0;
      if (a || f || m) {
        els.revProjectionHint.textContent = [
          `${tr('coverageDataWeek', 'Copertura dati settimana')}:`,
          `${tr('actual', 'Actual')} ${a} ${tr('days', 'gg')}`,
          `${tr('forecast', 'Previsione')} ${f} ${tr('days', 'gg')}`,
          `${tr('missing', 'Mancanti')} ${m} ${tr('days', 'gg')}`,
        ].join(' • ');
      } else {
        els.revProjectionHint.textContent = '';
      }
    }
  }

  function renderDelivery(data) {
    if (!els.deliveryKpis) return;
    const d = data.delivery;

    const prev = d.prev_week || {};
    const incOrders = d.orders_incidence_receipts_pct;
    const incOrdersPrev = prev.orders_incidence_receipts_pct;
    const incOrdersDelta = (Number.isFinite(Number(incOrders)) && Number.isFinite(Number(incOrdersPrev))) ? (Number(incOrders) - Number(incOrdersPrev)) : null;

    const delivInc = d.delivery_incidence_pct;
    const delivIncPrev = prev.delivery_incidence_pct;
    const delivIncDelta = (Number.isFinite(Number(delivInc)) && Number.isFinite(Number(delivIncPrev))) ? (Number(delivInc) - Number(delivIncPrev)) : null;

    const refundsInc = d.refunds_incidence_pct;
    const refundsIncPrev = prev.refunds_incidence_pct;
    const refundsIncDelta = (Number.isFinite(Number(refundsInc)) && Number.isFinite(Number(refundsIncPrev))) ? (Number(refundsInc) - Number(refundsIncPrev)) : null;

    const complaintsRate = d.complaints_rate_pct;
    const complaintsRatePrev = prev.complaints_rate_pct;
    const complaintsRateDelta = (Number.isFinite(Number(complaintsRate)) && Number.isFinite(Number(complaintsRatePrev))) ? (Number(complaintsRate) - Number(complaintsRatePrev)) : null;

    const cancelledOrders = Number(d.cancelled_orders) || 0;
    const cancelledOrdersPrev = Number((prev || {}).cancelled_orders) || 0;
    const cancelledOrdersDelta = cancelledOrders - cancelledOrdersPrev;
    const openingWeightedPct = (d.opening_weighted_pct === null || d.opening_weighted_pct === undefined) ? null : Number(d.opening_weighted_pct);
    const openingCoveragePct = (d.opening_calc_coverage_pct === null || d.opening_calc_coverage_pct === undefined) ? null : Number(d.opening_calc_coverage_pct);

    const cards = [];

    cards.push(kpiCard({
      label: tr('deliveryTotal', 'Totale delivery'),
      value: fmtMoney(d.total_delivery, 0),
      subHtml: [
        `<span class="cr-badge">${tr('online', 'Online')}: <b>${fmtMoney(d.total_online, 0)}</b></span>`,
        `<span class="cr-badge">${tr('cash', 'Contanti')}: <b>${fmtMoney(d.total_cash, 0)}</b></span>`,
      ].join('')
    }));

    cards.push(kpiCard({
      label: tr('deliveryIncidence', 'Incidenza delivery'),
      value: fmtPct(delivInc, 1),
      subHtml: [
        `<span class="cr-badge">Δ vs prev: <b class="${trendClass(-delivIncDelta)}">${delivIncDelta === null ? '—' : (delivIncDelta >= 0 ? '+' : '') + fmtPct(delivIncDelta, 1)}</b></span>`,
      ].join('')
    }));

    cards.push(kpiCard({
      label: tr('deliveryOrders', 'Ordini delivery'),
      value: fmtNum(d.total_orders, 0),
      subHtml: [
        `<span class="cr-badge">${tr('avgReceipt', 'Scontrino medio')}: <b>${fmtMoney(d.avg_delivery_receipt, 2)}</b></span>`,
      ].join('')
    }));

    cards.push(kpiCard({
      label: tr('cancelledOrders', 'Ordini cancellati'),
      value: fmtNum(cancelledOrders, 0),
      subHtml: [
        `<span class="cr-badge">Δ vs prev: <b class="${trendClass(-cancelledOrdersDelta)}">${cancelledOrdersDelta === 0 ? '0' : (cancelledOrdersDelta > 0 ? '+' : '') + fmtNum(cancelledOrdersDelta, 0)}</b></span>`,
      ].join('')
    }));

    cards.push(kpiCard({
      label: tr('estimatedOpeningPct', '% Apertura delivery stimata'),
      value: openingWeightedPct === null ? '—' : fmtPct(openingWeightedPct, 2),
      subHtml: [
        `<span class="cr-badge">${tr('calculationCoverage', 'Copertura calcolo')}: <b>${openingCoveragePct === null ? '—' : fmtPct(openingCoveragePct, 1)}</b></span>`,
        `<span class="cr-badge">${tr('estimatedPotential', 'Potenziale stimato')}: <b>${fmtMoney(d.opening_potential_sales_est, 0)}</b></span>`,
      ].join('')
    }));

    cards.push(kpiCard({
      label: tr('estimatedLostSales', 'Vendite perse stimate'),
      value: fmtMoney(d.opening_lost_sales_est, 0),
      subHtml: [
        `<span class="cr-badge">${tr('openingProviderConfig', 'Apertura/chiusura da configurazione provider')}</span>`,
      ].join('')
    }));

    cards.push(kpiCard({
      label: tr('refundsPctOrders', '% Rimborsi su ordini'),
      value: fmtPct(complaintsRate, 2),
      subHtml: [
        `<span class="cr-badge">${tr('netDisputes', 'Netto contestazioni')}: <b>${fmtPct(d.complaints_rate_net_pct, 2)}</b></span>`,
        `<span class="cr-badge">Δ vs prev: <b class="${trendClass(-complaintsRateDelta)}">${complaintsRateDelta === null ? '—' : (complaintsRateDelta >= 0 ? '+' : '') + fmtPct(complaintsRateDelta, 2)}</b></span>`,
      ].join('')
    }));

    cards.push(kpiCard({
      label: tr('refundsNetValue', 'Valore rimborsi netto contestazioni accettate'),
      value: fmtMoney(d.refunds_net, 0),
      subHtml: [
        `<span class="cr-badge">${tr('cancelled', 'Annullati')}: <b>${fmtMoney(d.refunds_cancelled_value, 0)}</b></span>`,
        `<span class="cr-badge">${tr('incidence', 'Incidenza')}: <b>${fmtPct(refundsInc, 2)}</b></span>`,
        `<span class="cr-badge">Δ vs prev: <b class="${trendClass(-refundsIncDelta)}">${refundsIncDelta === null ? '—' : (refundsIncDelta >= 0 ? '+' : '') + fmtPct(refundsIncDelta, 2)}</b></span>`,
      ].join('')
    }));

    cards.push(kpiCard({
      label: tr('deliveryOrdersReceipts', 'Ordini delivery su scontrini'),
      value: fmtPct(incOrders, 1),
      subHtml: [
        `<span class="cr-badge">Δ vs prev: <b class="${trendClass(incOrdersDelta)}">${incOrdersDelta === null ? '—' : (incOrdersDelta >= 0 ? '+' : '') + fmtPct(incOrdersDelta, 1)}</b></span>`,
      ].join('')
    }));

    els.deliveryKpis.innerHTML = cards.join('');

    // KPI per provider (spaccato piattaforme)
    if (els.providerKpis) {
      const rowsProv = (d.platforms || []);
      els.providerKpis.innerHTML = rowsProv.map((p) => {
        const total = Number(p.total) || 0;
        const online = Number(p.total_online) || 0;
        const cash = Number(p.total_cash) || 0;
        const orders = Number(p.orders) || 0;
        const avg = orders ? (total / orders) : 0;

        const complaints = Number(p.complaints_received) || 0;
        const appeals = Number(p.appeals_accepted) || 0;
        const crTxt = orders ? fmtPct((complaints / orders * 100), 2) : '—';
        const crNetTxt = orders ? fmtPct((Math.max(complaints - appeals, 0) / orders * 100), 2) : '—';

        const refunds = Number(p.refund_value) || 0;
        const refundsCancelled = Number(p.refunds_cancelled_value) || 0;
        const refundsNet = refunds - refundsCancelled;
        const refundsIncTxt = total ? fmtPct((refundsNet / total * 100), 2) : '—';

        const openingTxt = (p.opening_pct === null || p.opening_pct === undefined) ? '—' : fmtPct(p.opening_pct, 2);
        const openingModeLabel = String(p.opening_mode || '').toLowerCase() === 'closure' ? tr('reversedClosure', 'Chiusura ribaltata') : tr('opening', 'Apertura');
        const cancelled = Number(p.cancelled_orders) || 0;
        const openingPotentialTxt = (p.opening_potential_sales_est === null || p.opening_potential_sales_est === undefined) ? '—' : fmtMoney(p.opening_potential_sales_est, 0);
        const openingLostTxt = (p.opening_lost_sales_est === null || p.opening_lost_sales_est === undefined) ? '—' : fmtMoney(p.opening_lost_sales_est, 0);

        const ratingTxt = (p.rating_value === null || p.rating_value === undefined) ? '—' : (
          p.rating_unit === 'percent'
            ? fmtPct(p.rating_value, 2)
            : fmtNum(p.rating_value, 2)
        );

        const sub = [
          `<span class=\"cr-badge\">${tr('online', 'Online')}: <b>${fmtMoney(online, 0)}</b></span>`,
          `<span class=\"cr-badge\">${tr('cash', 'Contanti')}: <b>${fmtMoney(cash, 0)}</b></span>`,
          `<span class=\"cr-badge\">${tr('orders', 'Ordini')}: <b>${fmtNum(orders, 0)}</b></span>`,
          `<span class=\"cr-badge\">${tr('cancelledShort', 'Cancellati')}: <b>${fmtNum(cancelled, 0)}</b></span>`,
          `<span class=\"cr-badge\">% ${openingModeLabel}: <b>${openingTxt}</b></span>`,
          `<span class=\"cr-badge\">${tr('lostEstimatedShort', 'Perse stimate')}: <b>${openingLostTxt}</b></span>`,
          `<span class=\"cr-badge\">${tr('potential', 'Potenziale')}: <b>${openingPotentialTxt}</b></span>`,
          `<span class=\"cr-badge\">${tr('receiptShort', 'Scontrino')}: <b>${fmtMoney(avg, 2)}</b></span>`,
          `<span class=\"cr-badge\">${tr('refundsPctOrders', '% Rimborsi su ordini')}: <b>${crTxt}</b> <span class=\"text-muted\">(${tr('netDisputes', 'netto contestazioni')}: ${crNetTxt})</span></span>`,
          `<span class=\"cr-badge\">${tr('refundsValue', 'Valore rimborsi')}: <b>${fmtMoney(refundsNet, 0)}</b> <span class=\"text-muted\">(${refundsIncTxt})</span></span>`,
          `<span class=\"cr-badge\">${tr('rating', 'Rating')}: <b>${ratingTxt}</b></span>`,
        ].join('');

        return kpiCard({ label: p.label || p.platform, value: fmtMoney(total, 0), subHtml: sub });
      }).join('');
    }

    if (els.refundsHint) {
      if (d.total_delivery > 0) {
        const parts = [
          `Incidenza valore rimborsi su totale delivery: ${fmtPct(d.refunds_incidence_pct, 2)}`
        ];
        if (openingWeightedPct !== null) {
          parts.push(`% apertura stimata: ${fmtPct(openingWeightedPct, 2)} (copertura ${openingCoveragePct === null ? '—' : fmtPct(openingCoveragePct, 1)})`);
          parts.push(`Vendite perse stimate: ${fmtMoney(d.opening_lost_sales_est, 0)}`);
        }
        els.refundsHint.textContent = parts.join(' • ');
      } else {
        els.refundsHint.textContent = '';
      }
    }
  }

  function renderLaborCost(data) {
    if (!els.laborHero) return;
    const p = data.labor_cost || {};

    const costW = (p.cost_eur === null || p.cost_eur === undefined) ? null : Number(p.cost_eur);
    const pctW = (p.pct === null || p.pct === undefined) ? null : Number(p.pct);
    const hoursW = (p.hours === null || p.hours === undefined) ? null : Number(p.hours);

    const costW1 = (p.prev_week_cost_eur === null || p.prev_week_cost_eur === undefined) ? null : Number(p.prev_week_cost_eur);
    const pctW1 = (p.prev_week_pct === null || p.prev_week_pct === undefined) ? null : Number(p.prev_week_pct);
    const hoursW1 = (p.prev_week_hours === null || p.prev_week_hours === undefined) ? null : Number(p.prev_week_hours);

    const costW2 = (p.prev2_week_cost_eur === null || p.prev2_week_cost_eur === undefined) ? null : Number(p.prev2_week_cost_eur);
    const pctW2 = (p.prev2_week_pct === null || p.prev2_week_pct === undefined) ? null : Number(p.prev2_week_pct);

    const costLy = (p.last_year_cost_eur === null || p.last_year_cost_eur === undefined) ? null : Number(p.last_year_cost_eur);
    const pctLy = (p.last_year_pct === null || p.last_year_pct === undefined) ? null : Number(p.last_year_pct);

    const costW1Ly = (p.prev_week_last_year_cost_eur === null || p.prev_week_last_year_cost_eur === undefined) ? null : Number(p.prev_week_last_year_cost_eur);
    const pctW1Ly = (p.prev_week_last_year_pct === null || p.prev_week_last_year_pct === undefined) ? null : Number(p.prev_week_last_year_pct);

    const costMtd = (p.mtd_cost_eur === null || p.mtd_cost_eur === undefined) ? null : Number(p.mtd_cost_eur);
    const pctMtd = (p.mtd_pct === null || p.mtd_pct === undefined) ? null : Number(p.mtd_pct);
    const hoursMtd = (p.mtd_hours === null || p.mtd_hours === undefined) ? null : Number(p.mtd_hours);

    const costMtdLy = (p.mtd_last_year_cost_eur === null || p.mtd_last_year_cost_eur === undefined) ? null : Number(p.mtd_last_year_cost_eur);
    const pctMtdLy = (p.mtd_last_year_pct === null || p.mtd_last_year_pct === undefined) ? null : Number(p.mtd_last_year_pct);

    const costMtdPrev = (p.mtd_prev_cost_eur === null || p.mtd_prev_cost_eur === undefined) ? null : Number(p.mtd_prev_cost_eur);
    const pctMtdPrev = (p.mtd_prev_pct === null || p.mtd_prev_pct === undefined) ? null : Number(p.mtd_prev_pct);

    const mtdStart = p.mtd_start || (data.revenues || {}).mtd_start || null;
    const mtdEnd = p.mtd_end || (data.revenues || {}).mtd_end || null;
    const mtdLabel = (mtdStart && mtdEnd) ? `MTD ${fmtDayMonth(mtdStart)}→${fmtDayMonth(mtdEnd)}` : 'Progressivo mese';

    function row(label, value, cls = '') {
      return `
        <div class="rev-row">
          <div class="rev-row__label">${label}</div>
          <div class="rev-row__value ${cls}">${value}</div>
        </div>
      `;
    }

    function box(title, subtitle, bodyHtml) {
      return `
        <div class="rev-box">
          <div class="rev-box__head">
            <div class="rev-box__title">${title}</div>
            ${subtitle ? `<div class="rev-box__subtitle">${subtitle}</div>` : ''}
          </div>
          <div class="rev-box__body">${bodyHtml}</div>
        </div>
      `;
    }

    function fmtSignedPp(v, decimals = 2) {
      const n = Number(v);
      if (!Number.isFinite(n)) return '—';
      const sign = n >= 0 ? '+' : '';
      return `${sign}${n.toLocaleString(locale, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}pp`;
    }

    function baseLabel(baseCost, basePct) {
      const c = (baseCost === null || baseCost === undefined) ? NaN : Number(baseCost);
      const p = (basePct === null || basePct === undefined) ? NaN : Number(basePct);
      const cTxt = Number.isFinite(c) ? fmtMoney(c, 0) : '—';
      const pTxt = Number.isFinite(p) ? fmtPct(p, 2) : '—';
      return `${cTxt} • ${pTxt}`;
    }

    function deltaLine({ title, baseCost, basePct, deltaCost, deltaPp }) {
      const dp = (deltaPp === null || deltaPp === undefined) ? NaN : Number(deltaPp);
      const dc = (deltaCost === null || deltaCost === undefined) ? NaN : Number(deltaCost);
      const trendMetric = Number.isFinite(dp) ? -dp : (Number.isFinite(dc) ? -dc : null);
      const cls = trendClass(trendMetric);
      const icon = trendIcon(trendMetric);

      const leftVal = Number.isFinite(dc) ? fmtSignedEur(dc, 0) : '—';
      const rightVal = fmtSignedPp(dp, 2);

      return `
        <div class="kpi-delta">
          <div class="kpi-delta__left">
            <div class="kpi-delta__title">${title}</div>
            <div class="kpi-delta__val">${leftVal} <span class="text-muted">(${baseLabel(baseCost, basePct)})</span></div>
          </div>
          <div class="kpi-delta__right ${cls}">
            <span class="trend-icon">${icon}</span>
            <span>${rightVal}</span>
          </div>
        </div>
      `;
    }

    function eurPerHour(cost, hours) {
      const c = Number(cost);
      const h = Number(hours);
      if (!Number.isFinite(c) || !Number.isFinite(h) || h <= 0) return '—';
      return fmtMoney(c / h, 2) + '/h';
    }

    const weekBox = box(
      tr('dataWeek', 'Dati week'),
      null,
      [
        row(tr('laborCostShort', 'Costo lavoro'), (costW === null ? '—' : fmtMoney(costW, 0))),
        row(tr('pctOnRevenues', '% su revenues'), (pctW === null ? '—' : fmtPct(pctW, 2))),
        row(tr('hours', 'Ore'), (hoursW === null ? '—' : fmtNum(hoursW, 1))),
        row('€/h', eurPerHour(costW, hoursW)),
      ].join('')
    );

    const weekDeltaBox = box(
      tr('variancesWeek', 'Scostamenti week'),
      null,
      [
        deltaLine({
          title: 'vs Week -1',
          baseCost: costW1,
          basePct: pctW1,
          deltaCost: (costW === null || costW1 === null) ? null : (costW - costW1),
          deltaPp: (pctW === null || pctW1 === null) ? null : (pctW - pctW1),
        }),
        deltaLine({
          title: tr('vsLastYear', 'vs Last year'),
          baseCost: costLy,
          basePct: pctLy,
          deltaCost: p.delta_vs_last_year_cost_eur,
          deltaPp: p.delta_vs_last_year_pp,
        }),
      ].join('')
    );

    const prevWeekDeltaBox = box(
      tr('variancesWeekMinus1', 'Scostamenti week -1'),
      null,
      [
        deltaLine({
          title: 'vs Week -2',
          baseCost: costW2,
          basePct: pctW2,
          deltaCost: p.prev_week_delta_vs_prev_cost_eur,
          deltaPp: p.prev_week_delta_vs_prev_pp,
        }),
        deltaLine({
          title: tr('vsLastYear', 'vs Last year'),
          baseCost: costW1Ly,
          basePct: pctW1Ly,
          deltaCost: p.prev_week_delta_vs_last_year_cost_eur,
          deltaPp: p.prev_week_delta_vs_last_year_pp,
        }),
      ].join('')
    );

    const mtdBox = box(
      tr('monthToDate', 'Progressivo mese'),
      mtdLabel,
      [
        row(tr('laborCostShort', 'Costo lavoro'), (costMtd === null ? '—' : fmtMoney(costMtd, 0))),
        row(tr('pctOnRevenues', '% su revenues'), (pctMtd === null ? '—' : fmtPct(pctMtd, 2))),
        row(tr('hours', 'Ore'), (hoursMtd === null ? '—' : fmtNum(hoursMtd, 1))),
        row('€/h', eurPerHour(costMtd, hoursMtd)),
      ].join('')
    );

    const mtdDeltaBox = box(
      tr('variances', 'Scostamenti'),
      mtdLabel,
      [
        deltaLine({
          title: 'vs Week -1 (MTD)',
          baseCost: costMtdPrev,
          basePct: pctMtdPrev,
          deltaCost: p.mtd_delta_vs_prev_cost_eur,
          deltaPp: p.mtd_delta_vs_prev_pp,
        }),
        deltaLine({
          title: 'vs Last year (MTD)',
          baseCost: costMtdLy,
          basePct: pctMtdLy,
          deltaCost: p.mtd_delta_vs_last_year_cost_eur,
          deltaPp: p.mtd_delta_vs_last_year_pp,
        }),
      ].join('')
    );

    els.laborHero.innerHTML = `
      <div class="rev-grid">
        ${weekBox}
        ${weekDeltaBox}
        ${prevWeekDeltaBox}
        ${mtdBox}
        ${mtdDeltaBox}
      </div>
    `;

    if (els.laborHint) {
      const prevEnd = p.mtd_prev_week_end ? fmtDayMonth(p.mtd_prev_week_end) : null;
      els.laborHint.textContent = (prevEnd && costMtdPrev !== null)
        ? `${tr('untilWeekMinus1', 'MTD fino a week -1')} (${prevEnd}): ${fmtPct(pctMtdPrev, 2)} • ${fmtMoney(costMtdPrev, 0)}`
        : '';
    }
  }

  function updateTags(data) {
    tagItems = (data.tags || []).map((t) => {
      return {
        key: t.key,
        label: t.label,
        insertText: `@${t.key}(${t.value_formatted})`,
        search: `${t.key} ${t.label}`.toLowerCase(),
        valueFormatted: t.value_formatted,
      };
    });
  }

  function hideSuggest() {
    if (!els.tagSuggest) return;
    els.tagSuggest.classList.add('d-none');
    els.tagSuggest.innerHTML = '';
  }

  function showSuggest(items, atIndex, cursorPos) {
    if (!els.tagSuggest) return;
    if (!items || items.length === 0) {
      hideSuggest();
      return;
    }

    els.tagSuggest.innerHTML = items.slice(0, 12).map((it) => {
      return `<div class="tag-suggest__item" data-key="${encodeURIComponent(it.key)}">
        <span class="tag-suggest__k">@${it.key}</span>
        <span class="tag-suggest__v">${it.valueFormatted}</span>
        <span class="tag-suggest__v">• ${it.label}</span>
      </div>`;
    }).join('');

    els.tagSuggest.classList.remove('d-none');

    els.tagSuggest.querySelectorAll('.tag-suggest__item').forEach((el) => {
      el.addEventListener('click', () => {
        const key = decodeURIComponent(el.getAttribute('data-key') || '');
        const it = tagItems.find((x) => x.key === key);
        if (!it || !els.noteText) return;

        const txt = els.noteText.value || '';
        const before = txt.slice(0, atIndex);
        const after = txt.slice(cursorPos);
        const insert = it.insertText;
        els.noteText.value = before + insert + ' ' + after;

        const newPos = (before + insert + ' ').length;
        els.noteText.focus();
        els.noteText.setSelectionRange(newPos, newPos);
        hideSuggest();
      });
    });
  }

  function setupTagging() {
    if (!els.noteText || !els.tagSuggest) return;

    els.noteText.addEventListener('input', () => {
      const txt = els.noteText.value || '';
      const pos = els.noteText.selectionStart || 0;
      const before = txt.slice(0, pos);
      const at = before.lastIndexOf('@');
      if (at < 0) {
        hideSuggest();
        return;
      }

      // se c'è uno spazio tra @ e cursore -> non è un tag attivo
      const segment = before.slice(at + 1);
      if (segment.includes(' ') || segment.includes('\n') || segment.includes('\t')) {
        hideSuggest();
        return;
      }

      const q = segment.trim().toLowerCase();
      const filtered = tagItems.filter((it) => it.search.includes(q));
      showSuggest(filtered, at, pos);
    });

    els.noteText.addEventListener('blur', () => {
      setTimeout(hideSuggest, 150);
    });
  }

  async function loadNote() {
    if (!noteGetUrl || !els.noteText) return;
    try {
      els.noteStatus.textContent = tr('loading', 'Caricamento...');
      const url = `${noteGetUrl}?week_start=${encodeURIComponent(isoDate(weekStart))}`;
      const j = await safeFetchJson(url, { method: 'GET' });
      if (j && j.ok) {
        const t = (j.note && j.note.text) ? String(j.note.text) : '';
        els.noteText.value = t;
        els.noteStatus.textContent = (j.note && j.note.updated_at)
          ? `${tr('lastSave', 'Ultimo salvataggio')}: ${j.note.updated_at}`
          : tr('noWeeklyReport', 'Nessuna relazione salvata per questa settimana.');
      } else {
        els.noteStatus.textContent = '';
      }
    } catch (e) {
      els.noteStatus.textContent = `${tr('reportLoadError', 'Errore caricamento relazione')}: ${e.message || e}`;
    }
  }

  async function saveNote() {
    if (!noteSaveUrl || !els.noteText) return;
    try {
      els.noteStatus.textContent = tr('saving', 'Salvataggio...');
      const payload = {
        week_start: isoDate(weekStart),
        text: els.noteText.value || '',
      };
      const j = await safeFetchJson(noteSaveUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (j && j.ok) {
        els.noteStatus.textContent = tr('reportSaved', 'Relazione salvata.');
        if (j.note && j.note.updated_at) {
          els.noteStatus.textContent = `${tr('reportSaved', 'Relazione salvata.')} • ${j.note.updated_at}`;
        }
      } else {
        els.noteStatus.textContent = tr('saveError', 'Errore salvataggio.');
      }
    } catch (e) {
      els.noteStatus.textContent = `${tr('saveError', 'Errore salvataggio.')}: ${e.message || e}`;
    }
  }

  async function loadData() {
    clearError();
    setLoading(true);

    try {
      const payload = { week_start: isoDate(weekStart) };
      const j = await safeFetchJson(apiUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!j || !j.ok) {
        throw new Error((j && j.error) ? j.error : 'Errore risposta server.');
      }

      const data = j.data;
      setWeekLabel(data.week_start, data.week_end);

      renderRevenue(data);
      renderDelivery(data);
      renderLaborCost(data);
      buildCharts(data);
      updateTags(data);

      await loadNote();

    } catch (e) {
      showError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  function wireEvents() {
    if (els.btnPrev) els.btnPrev.addEventListener('click', () => {
      weekStart = addDays(weekStart, -7);
      syncSelectorsFromWeekStart();
      loadData();
    });

    if (els.btnNext) els.btnNext.addEventListener('click', () => {
      weekStart = addDays(weekStart, 7);
      syncSelectorsFromWeekStart();
      loadData();
    });

    if (els.btnThis) els.btnThis.addEventListener('click', () => {
      weekStart = mondayOf(new Date());
      syncSelectorsFromWeekStart();
      loadData();
    });

    if (els.yearSelect) {
      els.yearSelect.addEventListener('change', () => {
        if (suppressSelectEvents) return;
        buildWeekNumOptions(Number(els.yearSelect.value));
        if (els.weekNumSelect) els.weekNumSelect.value = '1';
        setWeekStartFromSelectors();
        loadData();
      });
    }

    if (els.weekNumSelect) {
      els.weekNumSelect.addEventListener('change', () => {
        if (suppressSelectEvents) return;
        setWeekStartFromSelectors();
        loadData();
      });
    }

    if (els.btnSaveNote) els.btnSaveNote.addEventListener('click', saveNote);
    if (els.btnReloadNote) els.btnReloadNote.addEventListener('click', loadNote);
  }

  function init() {
    // Se la pagina sta usando una versione JS in cache vecchia o la config non è stata iniettata,
    // evitiamo un "silenzio" e mostriamo un errore visibile.
    if (!apiUrl) {
      showError('Configurazione pagina non valida: apiUrl mancante. Forza un refresh (CTRL+F5).');
      return;
    }
    buildYearOptions();
    syncSelectorsFromWeekStart();
    wireEvents();
    setupTagging();
    loadData();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
