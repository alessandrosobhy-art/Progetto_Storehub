/* Cruscotto - Analisi Settimanale (v1.6.5)
   Render tabella + KPI + grafici.
*/

(function () {
  const CFG = window.CRUSCOTTO_WEEKLY || {};
  const apiUrl = CFG.apiUrl;
  const storeCode = CFG.storeCode;
  const I18N = CFG.i18n || {};
  const localeMap = { it: 'it-IT', en: 'en-US', fr: 'fr-FR', es: 'es-ES' };
  const configuredLang = String(CFG.locale || 'it').slice(0, 2).toLowerCase();
  const inferredLang = (() => {
    const day = String(I18N.day || '').trim().toLowerCase();
    const weekdays = Array.isArray(I18N.weekdays) ? I18N.weekdays.map((x) => String(x || '').toLowerCase()) : [];
    if (day === 'day' || weekdays.includes('mon')) return 'en';
    if (day === 'jour' || weekdays.includes('lun')) return 'fr';
    if (day === 'dia' || weekdays.includes('jue')) return 'es';
    return configuredLang;
  })();
  const lang = inferredLang || configuredLang || 'it';
  const locale = localeMap[lang] || 'it-IT';
  function tr(key, fallback) {
    return String((I18N && I18N[key]) || fallback || '');
  }

  const els = {
    weekRangeLabel: document.getElementById('weekRangeLabel'),
    err: document.getElementById('weeklyError'),
    yearSelect: document.getElementById('yearSelect'),
    weekNumSelect: document.getElementById('weekNumSelect'),
    btnPrev: document.getElementById('btnPrevWeek'),
    btnThis: document.getElementById('btnThisWeek'),
    btnNext: document.getElementById('btnNextWeek'),
    thead: document.getElementById('weeklyThead'),
    tbody: document.getElementById('weeklyTbody'),
    kpiCards: document.getElementById('kpiCards'),
    chartRevenues: document.getElementById('chartRevenues'),
    chartReceipt: document.getElementById('chartReceipt'),
    chartAvg: document.getElementById('chartAvgReceipt'),
  };

  let weekStart = mondayOf(new Date());
  let charts = { revenues: null, receipt: null, avg: null };

  function mondayOf(d) {
    const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const wd = (x.getDay() + 6) % 7; // Mon=0
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

  // ---- ISO week helpers (Mon-based) ----
  function isoWeek1Monday(year) {
    const jan4 = new Date(year, 0, 4);
    const wd = (jan4.getDay() + 6) % 7; // Mon=0
    const mon = new Date(jan4);
    mon.setDate(jan4.getDate() - wd);
    mon.setHours(0, 0, 0, 0);
    return mon;
  }

  function isoWeekYearOf(d) {
    const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    x.setHours(0, 0, 0, 0);
    // shift to Thursday of current week
    x.setDate(x.getDate() + 3 - ((x.getDay() + 6) % 7));
    const y = x.getFullYear();
    const w1 = isoWeek1Monday(y);
    const week = Math.floor((x - w1) / 86400000 / 7) + 1;
    return { year: y, week };
  }

  function isoWeeksInYear(year) {
    // Dec 28 is always in the last ISO week of the year
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

  function weekdayName(date) {
    const names = Array.isArray(I18N.weekdays) ? I18N.weekdays : [];
    const idx = date instanceof Date && !Number.isNaN(date.getTime()) ? date.getDay() : -1;
    if (names[idx]) return names[idx];
    const fallback = new Intl.DateTimeFormat(locale, { weekday: 'short' }).format(date);
    return fallback ? fallback.charAt(0).toUpperCase() + fallback.slice(1) : '';
  }

  function projectionLabel(value) {
    const raw = String(value || '').trim().toLowerCase();
    if (raw === 'actual') return tr('actual', 'Actual');
    if (raw === 'previsione' || raw === 'forecast') return tr('forecast', 'Forecast');
    return value || '';
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

    // ensure year exists
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

  function fmtMoney(v, decimals = 0) {
    const num = Number.isFinite(Number(v)) ? Number(v) : 0;
    return num.toLocaleString(locale, { style: 'currency', currency: 'EUR', minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  }

  function fmtNum(v, decimals = 0) {
    const num = Number.isFinite(Number(v)) ? Number(v) : 0;
    return num.toLocaleString(locale, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
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

  // Percentuale senza prefisso "+" (utile per Incidenza)
  function fmtPctPlain(p, decimals = 1) {
    if (p === null || p === undefined || !Number.isFinite(Number(p))) {
      return '<span class="pct-zero">—</span>';
    }
    const x = Number(p);
    const s = `${x.toLocaleString(locale, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}%`;
    return `<span class="${pctClass(x)}">${s}</span>`;
  }


  function safeDiv(a, b) {
    const x = Number(a);
    const y = Number(b);
    if (!Number.isFinite(x) || !Number.isFinite(y) || y === 0) return 0;
    return x / y;
  }

  function pctFrom(a, b) {
    const x = Number(a);
    const y = Number(b);
    if (!Number.isFinite(x) || !Number.isFinite(y) || y === 0) return null;
    return (x / y - 1) * 100;
  }

  function setLoading(isLoading) {
    [els.btnPrev, els.btnThis, els.btnNext].forEach((b) => {
      if (b) b.disabled = !!isLoading;
    });
    if (els.yearSelect) els.yearSelect.disabled = !!isLoading;
    if (els.weekNumSelect) els.weekNumSelect.disabled = !!isLoading;
  }

  function showError(msg) {
    if (!els.err) return;
    els.err.textContent = msg || `${tr('error', 'Errore')}.`;
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

  function kpiCard({ label, value, subHtml }) {
    return `
      <div class="cr-kpi">
        <div class="cr-kpi__label">${label}</div>
        <div class="cr-kpi__value">${value}</div>
        ${subHtml ? `<div class="cr-kpi__sub">${subHtml}</div>` : ''}
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
    const revLy = Number(t.revenues_ly || 0);
    const revBudget = Number(t.revenues_budget || 0);

    const receipts = Number(t.receipts_actual || 0);
    const receiptsLy = Number(t.receipts_ly || 0);

    const avg = Number(t.avg_receipt_actual || 0);
    const avgLy = Number(t.avg_receipt_ly || 0);

    const delivery = Number(t.delivery_total || 0);
    const deliveryInc = Number(t.delivery_inc || 0);

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
        label: tr('weekRevenues', 'Revenues (settimana)'),
        value: fmtMoney(rev, 0),
        subHtml: `<span class="cr-badge">${tr('projection', 'Projection')}: A ${fmtNum(projDaysA,0)} / P ${fmtNum(projDaysP,0)}</span>${revActual && revActual !== rev ? `<span class=\"cr-badge\">${tr('actual', 'Actual')}: ${fmtMoney(revActual, 0)}</span>` : ''}<span class="cr-badge">${tr('lastYearShort', 'LY')}: ${fmtMoney(revLy, 0)} ${fmtPct(revVsLy)}</span><span class="cr-badge">${tr('budget', 'Budget')}: ${fmtMoney(revBudget, 0)} ${fmtPct(revVsBudget)}</span>`,
      }),
      kpiCard({
        label: tr('weekReceipt', 'Receipt (settimana)'),
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
        value: fmtMoney(delivery, 0),
        subHtml: `<span class="cr-badge">${tr('inc', 'Inc')}: ${fmtNum(deliveryInc, 1)}%</span>`,
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
        <th colspan="3" class="text-center">${tr('weekReceipt', 'Receipt')}</th>
        <th colspan="3" class="text-center">${tr('averageReceipt', 'Average Receipt')}</th>
        <th colspan="${deliveryCols}" class="text-center">${tr('delivery', 'Delivery')}</th>
        <th colspan="4" class="text-center">${tr('laborCost', 'Costo lavoro')}</th>
      </tr>`,
    ].join('');

    // Header row 2 (columns)
    const r2Parts = [];

    // Revenues
    r2Parts.push(`<th class="num">${tr('actual', 'Actual')}</th>`);
    r2Parts.push(`<th class="num">${tr('previous', 'Prev.')}</th>`);
    r2Parts.push('<th class="num">Δ%</th>');
    r2Parts.push(`<th class="num">${tr('lastYear', 'Last Year')}</th>`);
    r2Parts.push('<th class="num">Δ%</th>');
    r2Parts.push(`<th class="num">${tr('budget', 'Budget')}</th>`);
    r2Parts.push('<th class="num">Δ%</th>');

    // Receipt
    r2Parts.push(`<th class="num">${tr('actual', 'Actual')}</th>`);
    r2Parts.push(`<th class="num">${tr('lastYear', 'Last Year')}</th>`);
    r2Parts.push('<th class="num">Δ%</th>');

    // Avg receipt
    r2Parts.push(`<th class="num">${tr('actual', 'Actual')}</th>`);
    r2Parts.push(`<th class="num">${tr('lastYear', 'Last Year')}</th>`);
    r2Parts.push('<th class="num">Δ%</th>');

    // Delivery (totale + inc)
    r2Parts.push(`<th class="num">${tr('delivery', 'Delivery')}</th>`);
    r2Parts.push(`<th class="num">${tr('inc', 'Inc')}</th>`);

    // Labor
    r2Parts.push(`<th class="num">${tr('totalHours', 'Ore totali')}</th>`);
    r2Parts.push(`<th class="num">${tr('stage', 'Stage')}</th>`);
    r2Parts.push(`<th class="num">${tr('training', 'Training')}</th>`);
    r2Parts.push(`<th class="num">${tr('productivity', 'Produttivita')}</th>`);

    const r2 = `<tr>${r2Parts.join('')}</tr>`;

    // Totals row inside THEAD (requested "in testa alle colonne")
    const totRev = Number(t.revenues_actual || 0);
    const totRevProj = Number(t.revenues_chart || 0);
    const totRevForecast = Number(t.revenues_forecast || 0);
    const totRevLy = Number(t.revenues_ly || 0);
    const totRevBudget = Number(t.revenues_budget || 0);

    const totReceipts = Number(t.receipts_actual || 0);
    const totReceiptsLy = Number(t.receipts_ly || 0);

    const totAvg = Number(t.avg_receipt_actual || 0);
    const totAvgLy = Number(t.avg_receipt_ly || 0);

    const totDelivery = Number(t.delivery_total || 0);

    const totOre = Number(t.ore_totali || 0);
    const totOreStage = Number(t.ore_stage || 0);
    const totOreTraining = Number(t.ore_training || 0);
    const totProd = Number(t.produttivita || 0);

    const totRevVsForecast = pctFrom(totRevProj, totRevForecast);
    const totRevVsLy = pctFrom(totRevProj, totRevLy);
    const totRevVsBudget = pctFrom(totRevProj, totRevBudget);
    const totRecVsLy = pctFrom(totReceipts, totReceiptsLy);
    const totAvgVsLy = pctFrom(totAvg, totAvgLy);

    const totCells = [];
    totCells.push('<th class="sticky-col">Totale</th>');
    const projDaysA = Number(t.projection_days_actual || 0);
    const projDaysP = Number(t.projection_days_forecast || 0);
    totCells.push(`<th class="text-center"><span class="small text-muted">A ${fmtNum(projDaysA,0)} / P ${fmtNum(projDaysP,0)}</span></th>`);

    totCells.push(`<th class="num">${fmtMoney(totRev, 0)}</th>`);
    totCells.push(`<th class="num">${fmtMoney(totRevForecast, 0)}</th>`);
    totCells.push(`<th class="num">${fmtPct(totRevVsForecast)}</th>`);
    totCells.push(`<th class="num">${fmtMoney(totRevLy, 0)}</th>`);
    totCells.push(`<th class="num">${fmtPct(totRevVsLy)}</th>`);
    totCells.push(`<th class="num">${fmtMoney(totRevBudget, 0)}</th>`);
    totCells.push(`<th class="num">${fmtPct(totRevVsBudget)}</th>`);

    totCells.push(`<th class="num">${fmtNum(totReceipts, 0)}</th>`);
    totCells.push(`<th class="num">${fmtNum(totReceiptsLy, 0)}</th>`);
    totCells.push(`<th class="num">${fmtPct(totRecVsLy)}</th>`);

    totCells.push(`<th class="num">${fmtMoney(totAvg, 2)}</th>`);
    totCells.push(`<th class="num">${fmtMoney(totAvgLy, 2)}</th>`);
    totCells.push(`<th class="num">${fmtPct(totAvgVsLy)}</th>`);

    totCells.push(`<th class="num">${fmtMoney(totDelivery, 0)}</th>`);
    const totRevDen = (totRevProj > 0 ? totRevProj : totRevForecast);
    const totDelInc = totRevDen > 0 ? (totDelivery / totRevDen) * 100 : 0;
    totCells.push(`<th class="num">${fmtPctPlain(totDelInc)}</th>`);

    totCells.push(`<th class="num">${fmtNum(totOre, 1)}</th>`);
    totCells.push(`<th class="num">${fmtNum(totOreStage, 1)}</th>`);
    totCells.push(`<th class="num">${fmtNum(totOreTraining, 1)}</th>`);
    totCells.push(`<th class="num">${fmtMoney(totProd, 2)}/h</th>`);

    const rTot = `<tr class="weekly-total-row">${totCells.join('')}</tr>`;

    els.thead.innerHTML = `${r1}${r2}${rTot}`;

    // Body rows
    const fmtDate = new Intl.DateTimeFormat(locale, { day: '2-digit', month: '2-digit' });

    const body = days.map((d) => {
      const dt = new Date(String(d.date || '') + 'T00:00:00');
      const dayLabel = `${weekdayName(dt)} ${fmtDate.format(dt)}`;

      const revA = Number(d.revenues_actual || 0);
      const revF = Number(d.revenues_forecast || 0);
      const revProj = (d && d.revenues_chart !== undefined && d.revenues_chart !== null) ? Number(d.revenues_chart || 0) : (revA ? revA : revF);
      const revLy = Number(d.revenues_ly || 0);
      const revBudget = Number(d.revenues_budget || 0);
      const revVsF = pctFrom(revProj, revF);
      const revVsLy = pctFrom(revProj, revLy);
      const revVsBudget = pctFrom(revProj, revBudget);

      const rcpA = Number(d.receipts_actual || 0);
      const rcpLy = Number(d.receipts_ly || 0);
      const rcpVsLy = pctFrom(rcpA, rcpLy);

      const avgA = Number(d.avg_receipt_actual || 0);
      const avgLy = Number(d.avg_receipt_ly || 0);
      const avgVsLy = pctFrom(avgA, avgLy);

      const delTot = Number(d.delivery_total || 0);

      const oreTot = Number(d.ore_totali || 0);
      const oreStage = Number(d.ore_stage || 0);
      const oreTraining = Number(d.ore_training || 0);
      const prod = Number(d.produttivita || 0);

      const cells = [];
      cells.push(`<th class="sticky-col">${escapeHtml(dayLabel)}</th>`);

      const projSrc = projectionLabel(d.projection_source || (revA > 0 ? 'Actual' : 'Forecast'));
      cells.push(`<td class="text-center"><span class="small text-muted">${escapeHtml(projSrc)}</span></td>`);

      cells.push(`<td class="num">${fmtMoney(revA, 0)}</td>`);
      cells.push(`<td class="num">${fmtMoney(revF, 0)}</td>`);
      cells.push(`<td class="num">${fmtPct(revVsF)}</td>`);
      cells.push(`<td class="num">${fmtMoney(revLy, 0)}</td>`);
      cells.push(`<td class="num">${fmtPct(revVsLy)}</td>`);
      cells.push(`<td class="num">${fmtMoney(revBudget, 0)}</td>`);
      cells.push(`<td class="num">${fmtPct(revVsBudget)}</td>`);

      cells.push(`<td class="num">${fmtNum(rcpA, 0)}</td>`);
      cells.push(`<td class="num">${fmtNum(rcpLy, 0)}</td>`);
      cells.push(`<td class="num">${fmtPct(rcpVsLy)}</td>`);

      cells.push(`<td class="num">${fmtMoney(avgA, 2)}</td>`);
      cells.push(`<td class="num">${fmtMoney(avgLy, 2)}</td>`);
      cells.push(`<td class="num">${fmtPct(avgVsLy)}</td>`);

      cells.push(`<td class="num">${fmtMoney(delTot, 0)}</td>`);

      const revDen = (revProj > 0 ? revProj : revF);
      const delInc = revDen > 0 ? (delTot / revDen) * 100 : 0;
      cells.push(`<td class="num">${fmtPctPlain(delInc)}</td>`);

      cells.push(`<td class="num">${fmtNum(oreTot, 1)}</td>`);
      cells.push(`<td class="num">${fmtNum(oreStage, 1)}</td>`);
      cells.push(`<td class="num">${fmtNum(oreTraining, 1)}</td>`);
      cells.push(`<td class="num">${fmtMoney(prod, 2)}/h</td>`);

      return `<tr>${cells.join('')}</tr>`;
    });

    els.tbody.innerHTML = body.join('');
  }

  function capitalize(s) {
    if (!s) return '';
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  function escapeHtml(str) {
    return String(str || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function ensureChart(canvas, cfg, prev) {
    if (!canvas) return null;
    if (!window.Chart) return null;
    if (prev) {
      try { prev.destroy(); } catch (_) {}
    }
    return new window.Chart(canvas.getContext('2d'), cfg);
  }

  function renderCharts(data) {
    if (!window.Chart) {
      // Non blocca la pagina: solo avviso.
      showError('Chart.js non è disponibile: i grafici non possono essere renderizzati.');
      return;
    }

    const days = data.days || [];
    const labels = days.map((d) => {
      const dt = new Date(String(d.date || '') + 'T00:00:00');
      return `${weekdayName(dt)} ${new Intl.DateTimeFormat(locale, { day: '2-digit' }).format(dt)}`;
    });

    const revA = days.map((d) => {
      if (d && d.revenues_chart !== undefined && d.revenues_chart !== null) {
        return Number(d.revenues_chart || 0);
      }
      const a = Number(d.revenues_actual || 0);
      const f = Number(d.revenues_forecast || 0);
      return a ? a : f;
    });
    const revLy = days.map((d) => Number(d.revenues_ly || 0));
    const revBud = days.map((d) => Number(d.revenues_budget || 0));

    const recA = days.map((d) => Number(d.receipts_actual || 0));
    const recLy = days.map((d) => Number(d.receipts_ly || 0));

    const avgA = days.map((d) => Number(d.avg_receipt_actual || 0));
    const avgLy = days.map((d) => Number(d.avg_receipt_ly || 0));

    const commonOpts = {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'bottom' },
        tooltip: { mode: 'index', intersect: false },
      },
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: { grid: { display: false } },
        y: { ticks: { callback: (v) => v } },
      },
    };

    charts.revenues = ensureChart(
      els.chartRevenues,
      {
        type: 'line',
        data: {
          labels,
          datasets: [
            { label: `${tr('actual', 'Actual')}/${tr('forecast', 'Forecast')}`, data: revA, borderWidth: 2, tension: 0.25 },
            { label: tr('lastYear', 'Last Year'), data: revLy, borderWidth: 2, tension: 0.25 },
            { label: tr('budget', 'Budget'), data: revBud, borderWidth: 2, borderDash: [6, 4], tension: 0.25 },
          ],
        },
        options: {
          ...commonOpts,
          scales: {
            ...commonOpts.scales,
            y: {
              ticks: {
                callback: (v) => Number(v).toLocaleString(locale),
              },
            },
          },
        },
      },
      charts.revenues
    );

    charts.receipt = ensureChart(
      els.chartReceipt,
      {
        type: 'line',
        data: {
          labels,
          datasets: [
            { label: tr('actual', 'Actual'), data: recA, borderWidth: 2, tension: 0.25 },
            { label: tr('lastYear', 'Last Year'), data: recLy, borderWidth: 2, tension: 0.25 },
          ],
        },
        options: commonOpts,
      },
      charts.receipt
    );

    charts.avg = ensureChart(
      els.chartAvg,
      {
        type: 'line',
        data: {
          labels,
          datasets: [
            { label: tr('actual', 'Actual'), data: avgA, borderWidth: 2, tension: 0.25 },
            { label: tr('lastYear', 'Last Year'), data: avgLy, borderWidth: 2, tension: 0.25 },
          ],
        },
        options: {
          ...commonOpts,
          scales: {
            ...commonOpts.scales,
            y: {
              ticks: {
                callback: (v) => Number(v).toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
              },
            },
          },
        },
      },
      charts.avg
    );
  }

  async function loadWeek() {
    clearError();

    if (!apiUrl) {
      showError('Configurazione mancante: apiUrl non disponibile.');
      return;
    }

    if (!storeCode) {
      showError('Seleziona prima uno store.');
      return;
    }

    setLoading(true);

    try {
      const res = await fetch(apiUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ week_start: isoDate(weekStart) }),
      });

      let payload = null;
      try {
        payload = await res.json();
      } catch (_) {
        payload = null;
      }

      if (!res.ok || !payload || !payload.ok) {
        const msg = (payload && payload.error) ? payload.error : `Errore API (${res.status})`;
        throw new Error(msg);
      }

      const data = payload.data || {};
      setWeekLabel(data.week_start, data.week_end);
      // sincronizza weekStart e select
      if (data.week_start) {
        weekStart = mondayOf(new Date(data.week_start + 'T00:00:00'));
        syncSelectorsFromWeekStart();
      }
      renderKpis(data);
      renderTable(data);
      renderCharts(data);
    } catch (e) {
      showError(e && e.message ? e.message : 'Errore caricamento dati.');
      // In caso di errore, svuota solo la tabella per evitare layout "rotto".
      if (els.thead) els.thead.innerHTML = '';
      if (els.tbody) els.tbody.innerHTML = '';
      if (els.kpiCards) els.kpiCards.innerHTML = '';
    } finally {
      setLoading(false);
    }
  }

  function wireEvents() {
    if (els.btnPrev) {
      els.btnPrev.addEventListener('click', () => {
        weekStart = addDays(weekStart, -7);
        syncSelectorsFromWeekStart();
        loadWeek();
      });
    }
    if (els.btnNext) {
      els.btnNext.addEventListener('click', () => {
        weekStart = addDays(weekStart, 7);
        syncSelectorsFromWeekStart();
        loadWeek();
      });
    }
    if (els.btnThis) {
      els.btnThis.addEventListener('click', () => {
        weekStart = mondayOf(new Date());
        syncSelectorsFromWeekStart();
        loadWeek();
      });
    }

    if (els.yearSelect) {
      els.yearSelect.addEventListener('change', () => {
        if (suppressSelectEvents) return;
        const y = Number(els.yearSelect.value || NaN);
        if (!Number.isFinite(y)) return;
        buildWeekNumOptions(y);
        let w = Number(els.weekNumSelect ? els.weekNumSelect.value : NaN);
        const max = isoWeeksInYear(y);
        if (!Number.isFinite(w) || w < 1) w = 1;
        if (w > max) w = max;
        if (els.weekNumSelect) els.weekNumSelect.value = String(w);
        weekStart = mondayFromIsoWeek(y, w);
        loadWeek();
      });
    }

    if (els.weekNumSelect) {
      els.weekNumSelect.addEventListener('change', () => {
        if (suppressSelectEvents) return;
        const y = Number(els.yearSelect ? els.yearSelect.value : NaN);
        const w = Number(els.weekNumSelect.value || NaN);
        if (!Number.isFinite(y) || !Number.isFinite(w)) return;
        weekStart = mondayFromIsoWeek(y, w);
        loadWeek();
      });
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    buildYearOptions();
    syncSelectorsFromWeekStart();
    wireEvents();
    loadWeek();
  });
})();
