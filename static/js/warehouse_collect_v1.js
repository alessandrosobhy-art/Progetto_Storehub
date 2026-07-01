(function(){
  'use strict';

  // ---- DOM ----
  const calEl = document.getElementById('collectCalendar');
  const monthLabelEl = document.getElementById('monthLabel');
  const monthTotalEl = document.getElementById('monthTotal');
  const statusEl = document.getElementById('collectStatus');

  const btnPrev = document.getElementById('btnPrev');
  const btnNext = document.getElementById('btnNext');
  const btnToday = document.getElementById('btnToday');

  const dashModeEl = document.getElementById('dashMode');
  const warehouseFiltersEl = document.getElementById('warehouseFilters');

  const movSelect = document.getElementById('movSelect');
  const catSelect = document.getElementById('catSelect');

  // Modal Magazzino
  const modalEl = document.getElementById('collectBreakdownModal');
  const modalTitleEl = document.getElementById('collectModalTitle');
  const modalFilteredTotalEl = document.getElementById('modalFilteredTotal');
  const modalFilterLabelEl = document.getElementById('modalFilterLabel');
  const modalTotalFoodEl = document.getElementById('modalTotalFood');
  const modalTotalOperEl = document.getElementById('modalTotalOper');
  const modalTotalAllEl = document.getElementById('modalTotalAll');
  const modalTableBody = document.querySelector('#modalTable tbody');

  // Modal Rendiconto
  const rcModalEl = document.getElementById('rendicontoDayModal');
  const rcTitleEl = document.getElementById('rcModalTitle');
  const rcGiroEl = document.getElementById('rcGiro');
  const rcDiffEl = document.getElementById('rcDiff');
  const rcDistinteEl = document.getElementById('rcDistinte');
  const rcTicketSiEl = document.getElementById('rcTicketSi');
  const rcDeliveryOnlineEl = document.getElementById('rcDeliveryOnline');
  const rcDeliveryContantiEl = document.getElementById('rcDeliveryContanti');
  const rcCouponSiEl = document.getElementById('rcCouponSi');
  const rcSpeseNetEl = document.getElementById('rcSpeseNet');
  const rcSpeseTotalEl = document.getElementById('rcSpeseTotal');
  const rcNoteCreditoEl = document.getElementById('rcNoteCredito');
  const rcChiusuraTbody = document.querySelector('#rcChiusuraTable tbody');
  const rcCustomSectionsWrap = document.getElementById('rcCustomSectionsWrap');
  const rcCustomSectionsEl = document.getElementById('rcCustomSections');
  const rcPhotoBtnEl = document.getElementById('rcPhotoBtn');
  const distintaModalEl = document.getElementById('photoDistintaModal');
  const distintaImgEl = document.getElementById('photoDistintaImg');
  const distintaLoadingEl = document.getElementById('photoDistintaLoading');
  const distintaErrEl = document.getElementById('photoDistintaError');

  // Riepilogo mensile Magazzino
  const whSummaryEl = document.getElementById('warehouseSummary');
  const whMonthDdtEl = document.getElementById('whMonthDdt');
  const whMonthTxInEl = document.getElementById('whMonthTxIn');
  const whMonthTxOutEl = document.getElementById('whMonthTxOut');
  const whMonthWasteEl = document.getElementById('whMonthWaste');
  const whMonthWastePctEl = document.getElementById('whMonthWastePct');

  // Riepilogo mensile Rendiconto
  const rcSummaryEl = document.getElementById('rendicontoSummary');
  const rcMonthGiroEl = document.getElementById('rcMonthGiro');
  const rcMonthScontriniEl = document.getElementById('rcMonthScontrini');
  const rcMonthPosEl = document.getElementById('rcMonthPos');
  const rcMonthDistinteEl = document.getElementById('rcMonthDistinte');
  const rcMonthAnnullatiEl = document.getElementById('rcMonthAnnullati');
  const rcMonthDiffEl = document.getElementById('rcMonthDiff');

  const rcLastVersDateEl = document.getElementById('rcLastVersDate');
  const rcDaysNonVersatiEl = document.getElementById('rcDaysNonVersati');
  const rcDistinteDaVersareEl = document.getElementById('rcDistinteDaVersare');

  // Modal alert versamenti (overdue)
  const rcOverdueModalEl = document.getElementById('rcOverdueModal');
  const rcAlertLastDateEl = document.getElementById('rcAlertLastDate');
  const rcAlertTotalEl = document.getElementById('rcAlertTotal');
  const rcAlertDaysEl = document.getElementById('rcAlertDays');

  const validateCfg = window.__RENDICONTO_VALIDATE__ || {};
  const rendicontoValidationPanelEl = document.getElementById('rendicontoValidationPanel');
  const rendicontoValidatedListPanelEl = document.getElementById('rendicontoValidatedListPanel');
  const rvDalEl = document.getElementById('rvDal');
  const rvAlEl = document.getElementById('rvAl');
  const rvPreviewBtn = document.getElementById('rvPreviewBtn');
  const rvModalEl = document.getElementById('rendicontoValidateModal');
  const rvModalRangeEl = document.getElementById('rvModalRange');
  const rvModalTotalDiffEl = document.getElementById('rvModalTotalDiff');
  const rvModalBodyEl = document.getElementById('rvModalBody');
  const rvModalErrorEl = document.getElementById('rvModalError');
  const rvConfirmBtn = document.getElementById('rvConfirmBtn');


// Modal riepilogo giornata (post-login)
const dsModalEl = document.getElementById('dailySummaryModal');
const dsStoreNameEl = document.getElementById('dsStoreName');
const dsBudgetEl = document.getElementById('dsBudget');
const dsLyRevenuesEl = document.getElementById('dsLyRevenues');
const dsForecastEl = document.getElementById('dsForecast');
const dsLyDateRowEl = document.getElementById('dsLyDateRow');
const dsLyDateEl = document.getElementById('dsLyDate');
const dsCashCustomWrapEl = document.getElementById('dsCashCustomWrap');
const dsCashCustomListEl = document.getElementById('dsCashCustomList');

  if (!calEl) return;

  const I18N = window.__DASHBOARD_I18N__ || {};
  function t(key, fallback){
    return String((I18N && I18N[key]) || fallback || '');
  }
  function translatedClosingLabel(label) {
    const raw = String(label || '').trim();
    const key = raw.toUpperCase();
    const map = (I18N && I18N.closingLabels) || {};
    return String(map[key] || raw);
  }
  const locale = String(I18N.locale || 'it').toLowerCase();
  const browserLocale = locale === 'en' ? 'en-US' : (locale === 'fr' ? 'fr-FR' : (locale === 'es' ? 'es-ES' : 'it-IT'));
  const itMonthFmt = new Intl.DateTimeFormat(browserLocale, { month: 'long', year: 'numeric' });
  const itCurFmt = new Intl.NumberFormat(browserLocale, { style: 'currency', currency: 'EUR' });
  const itIntFmt = new Intl.NumberFormat(browserLocale, { maximumFractionDigits: 0 });
  const itPctFmt = new Intl.NumberFormat(browserLocale, { minimumFractionDigits: 1, maximumFractionDigits: 1 });

  function fmtEUR(v){
    try { return itCurFmt.format(Number(v || 0)); } catch(e){ return '€ 0,00'; }
  }

  function fmtINT(v){
    try { return itIntFmt.format(Number(v || 0)); } catch(e){ return '0'; }
  }

  function fmtPCT(v){
    const n = Number(v);
    if (!isFinite(n)) return '-';
    try { return itPctFmt.format(n) + '%'; } catch(e){ return String(n) + '%'; }
  }


  function openDistintaPhoto(url){
    if (!distintaModalEl || !distintaImgEl || !url) return;
    if (distintaErrEl) distintaErrEl.style.display = 'none';
    if (distintaLoadingEl) distintaLoadingEl.style.display = '';
    distintaImgEl.style.display = 'none';
    distintaImgEl.onload = function(){
      if (distintaLoadingEl) distintaLoadingEl.style.display = 'none';
      if (distintaErrEl) distintaErrEl.style.display = 'none';
      distintaImgEl.style.display = '';
    };
    distintaImgEl.onerror = function(){
      if (distintaLoadingEl) distintaLoadingEl.style.display = 'none';
      distintaImgEl.style.display = 'none';
      if (distintaErrEl) distintaErrEl.style.display = '';
    };
    distintaImgEl.src = url;
  }

  function buildRendicontoLine(label, valueText, extraClass){
    const div = document.createElement('div');
    div.className = 'metric-line ' + (extraClass || '');

    const lab = document.createElement('span');
    lab.className = 'metric-label';
    lab.textContent = String(label || '');

    const val = document.createElement('span');
    val.className = 'metric-value';
    val.textContent = String(valueText || '');

    div.appendChild(lab);
    div.appendChild(val);
    return div;
  }

  function setStatus(msg){
    if (!statusEl) return;
    statusEl.textContent = msg || '\u00A0';
  }

  async function fetchJSON(url){
    const res = await fetch(url, {
      headers: {
        'Accept': 'application/json'
      },
      credentials: 'same-origin'
    });

    const ct = (res.headers.get('content-type') || '').toLowerCase();
    if (!ct.includes('application/json')) {
      throw new Error(t('nonJsonResponse', 'Risposta non JSON (probabile redirect/login/store).'));
    }
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data && (data.error || data.message) ? (data.error || data.message) : ('HTTP ' + res.status));
    }
    if (data && data.error) {
      throw new Error(String(data.error));
    }
    return data;
  }


  // Cache: store code -> name (per label SITE2 nei trasferimenti)
  let storeMapPromise = null;
  async function getStoreMap(){
    if (storeMapPromise) return storeMapPromise;
    storeMapPromise = fetchJSON('/magazzino/stores-json')
      .then((d) => {
        const map = {};
        const stores = (d && d.stores) ? d.stores : [];
        stores.forEach((s) => {
          const code = String((s && s.code) || '').trim();
          if (!code) return;
          map[code] = String((s && s.name) || '').trim();
        });
        return map;
      })
      .catch(() => ({}));
    return storeMapPromise;
  }

  function movementChipClass(movement){
    switch(String(movement || '').toUpperCase()){
      case 'INV': return 'chip-inv';
      case 'DELIVERY': return 'chip-delivery';
      case 'TXIN': return 'chip-txin';
      case 'TXOUT': return 'chip-txout';
      case 'WASTE': return 'chip-waste';
      default: return 'chip-inv';
    }
  }

  function movementLabel(movement){
    switch(String(movement || '').toUpperCase()){
      case 'INV': return t('inventory', 'Inventario');
      case 'DELIVERY': return t('delivery', 'Delivery');
      case 'TXIN': return t('transfersIn', 'Trasferimenti In');
      case 'TXOUT': return t('transfersOut', 'Trasferimenti Out');
      case 'WASTE': return t('wasteRaw', 'Waste Crudo');
      default: return movement;
    }
  }

  // Monday = 0 ... Sunday = 6
  function mondayIndex(jsDay){
    // JS: Sunday=0..Saturday=6
    return (jsDay + 6) % 7;
  }

  function buildMonthGrid(year, month){
    // month: 1..12
    const first = new Date(year, month - 1, 1);
    const startOffset = mondayIndex(first.getDay());
    const gridStart = new Date(year, month - 1, 1 - startOffset);
    const cells = [];
    for (let i = 0; i < 42; i++){
      const d = new Date(gridStart);
      d.setDate(gridStart.getDate() + i);
      cells.push(d);
    }
    return cells;
  }

  function isoDate(d){
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2,'0');
    const day = String(d.getDate()).padStart(2,'0');
    return `${y}-${m}-${day}`;
  }

  function setMonthLabel(year, month){
    const dt = new Date(year, month - 1, 1);
    const txt = itMonthFmt.format(dt);
    monthLabelEl.textContent = txt.charAt(0).toUpperCase() + txt.slice(1);
  }

  function setMonthTotal(total){
    if (!monthTotalEl) return;
    monthTotalEl.textContent = fmtEUR(total);
  }

  function escapeHtml(str){
    return String(str || '')
      .replaceAll('&','&amp;')
      .replaceAll('<','&lt;')
      .replaceAll('>','&gt;')
      .replaceAll('"','&quot;')
      .replaceAll("'",'&#39;');
  }

  // ---- Mode ----
  function getMode(){
    const v = String(dashModeEl?.value || 'MAGAZZINO').toUpperCase();
    return (v === 'RENDICONTO') ? 'RENDICONTO' : 'MAGAZZINO';
  }

  function applyModeUI(){
    const mode = getMode();
    if (warehouseFiltersEl){
      // Usa d-none (bootstrap) per forzare display:none !important, anche se qualche CSS imposta display:flex !important
      warehouseFiltersEl.classList.toggle('d-none', mode !== 'MAGAZZINO');
    }
    if (whSummaryEl){
      whSummaryEl.classList.toggle('d-none', mode !== 'MAGAZZINO');
    }
    if (rcSummaryEl){
      rcSummaryEl.classList.toggle('d-none', mode !== 'RENDICONTO');
    }
    if (rendicontoValidationPanelEl){
      rendicontoValidationPanelEl.classList.toggle('d-none', !(mode === 'RENDICONTO' && !!validateCfg.canValidate));
    }
    if (rendicontoValidatedListPanelEl){
      rendicontoValidatedListPanelEl.classList.toggle('d-none', mode !== 'RENDICONTO');
    }
  }


  function resetWarehouseSummary(){
    if (whMonthDdtEl) whMonthDdtEl.textContent = '-';
    if (whMonthTxInEl) whMonthTxInEl.textContent = '-';
    if (whMonthTxOutEl) whMonthTxOutEl.textContent = '-';
    if (whMonthWasteEl) whMonthWasteEl.textContent = '-';
    if (whMonthWastePctEl) whMonthWastePctEl.textContent = '-';
  }

  function setWarehouseSummary(summary){
    if (!summary){ resetWarehouseSummary(); return; }
    const s = summary || {};
    const ddt = Number(s.ddt || 0);
    const txin = Number(s.txin || 0);
    const txout = Number(s.txout || 0);
    const waste = Number(s.waste || 0);
    const wastePct = s.waste_pct;

    if (whMonthDdtEl) whMonthDdtEl.textContent = fmtEUR(ddt);
    if (whMonthTxInEl) whMonthTxInEl.textContent = fmtEUR(txin);
    if (whMonthTxOutEl) whMonthTxOutEl.textContent = fmtEUR(txout);
    if (whMonthWasteEl) whMonthWasteEl.textContent = fmtEUR(waste);
    if (whMonthWastePctEl){
      if (wastePct === null || wastePct === undefined || wastePct === "") {
        whMonthWastePctEl.textContent = '-';
      } else {
        const n = Number(wastePct);
        whMonthWastePctEl.textContent = isFinite(n) ? fmtPCT(n) : '-';
      }
    }
  }

function resetRendicontoSummary(){
  if (rcMonthGiroEl) rcMonthGiroEl.textContent = '-';
  if (rcMonthScontriniEl) rcMonthScontriniEl.textContent = '-';
  if (rcMonthPosEl) rcMonthPosEl.textContent = '-';
  if (rcMonthDistinteEl) rcMonthDistinteEl.textContent = '-';
  if (rcMonthAnnullatiEl) rcMonthAnnullatiEl.textContent = '-';
  if (rcMonthDiffEl){
    rcMonthDiffEl.textContent = '-';
    rcMonthDiffEl.classList.remove('rc-negative');
  }
  if (rcLastVersDateEl) rcLastVersDateEl.textContent = '-';
  if (rcDaysNonVersatiEl) rcDaysNonVersatiEl.textContent = '-';
  if (rcDistinteDaVersareEl) rcDistinteDaVersareEl.textContent = '-';
}

function setRendicontoSummary(summary){
  const s = summary || {};
  const giro = Number(s.giro || 0);
  const scontrini = Number(s.scontrini || 0);
  const pos = Number(s.pos || 0);
  const distinte = Number(s.distinte || 0);
  const annullati = Number(s.annullati || 0);
  const diff = Number(s.diff || 0);

  if (rcMonthGiroEl) rcMonthGiroEl.textContent = fmtEUR(giro);
  if (rcMonthScontriniEl) rcMonthScontriniEl.textContent = fmtINT(scontrini);
  if (rcMonthPosEl) rcMonthPosEl.textContent = fmtEUR(pos);
  if (rcMonthDistinteEl) rcMonthDistinteEl.textContent = fmtEUR(distinte);
  if (rcMonthAnnullatiEl) rcMonthAnnullatiEl.textContent = fmtEUR(annullati);

  if (rcMonthDiffEl){
    rcMonthDiffEl.textContent = fmtEUR(diff);
    if (diff < 0) rcMonthDiffEl.classList.add('rc-negative');
    else rcMonthDiffEl.classList.remove('rc-negative');
  }
}

function setRendicontoVersamentiStatus(status, year, month){
  const st = status || {};
  const lastDisp = (st.ultimo_al_disp || '').toString().trim();

  let daysSince = null;
  if (st.giorni_da_ultimo !== null && st.giorni_da_ultimo !== undefined && st.giorni_da_ultimo !== ''){
    const n = Number(st.giorni_da_ultimo);
    if (isFinite(n)) daysSince = n;
  }

  let totalDaVersare = null;
  if (st.distinte_non_versate !== null && st.distinte_non_versate !== undefined && st.distinte_non_versate !== ''){
    const n = Number(st.distinte_non_versate);
    if (isFinite(n)) totalDaVersare = n;
  }

  if (rcLastVersDateEl) rcLastVersDateEl.textContent = lastDisp ? lastDisp : '-';

  if (rcDaysNonVersatiEl){
    rcDaysNonVersatiEl.textContent = (daysSince === null) ? '-' : String(daysSince);
  }

  if (rcDistinteDaVersareEl){
    rcDistinteDaVersareEl.textContent = (totalDaVersare === null) ? '-' : fmtEUR(totalDaVersare);
  }


// Popup alert se > 7 giorni non versati (solo quando backend lo richiede)
if (st && st.should_alert && rcOverdueModalEl && window.bootstrap){
  const key = `${String(year || '')}-${String(month || '')}`;
  if (shownOverdueKey !== key){
    const payload = {
      key,
      lastDisp: lastDisp ? String(lastDisp) : '',
      totalDaVersare: (totalDaVersare === null || totalDaVersare === undefined) ? null : Number(totalDaVersare),
      daysSince: (daysSince === null || daysSince === undefined) ? null : Number(daysSince)
    };

    if (dailySummaryPending){
      pendingOverduePayload = payload;
      return;
    }

    showOverdueModal(payload);
  }
}

}



  // ---- Render: Magazzino ----
  function renderCalendarWarehouse(cells, currentYear, currentMonth, totalsByDay, movement){
    calEl.innerHTML = '';

    const now = new Date();
    const todayIso = isoDate(now);

    cells.forEach(d => {
      const inMonth = (d.getFullYear() === currentYear && (d.getMonth() + 1) === currentMonth);
      const cell = document.createElement('div');
      cell.className = 'day-cell' + (inMonth ? '' : ' muted');
      const dayNum = document.createElement('div');
      dayNum.className = 'day-number';
      dayNum.textContent = String(d.getDate());
      cell.appendChild(dayNum);

      const bar = document.createElement('div');
      bar.className = 'occ-bar';

      if (inMonth){
        const id = isoDate(d);
        const v = (totalsByDay && typeof totalsByDay[id] !== 'undefined') ? totalsByDay[id] : 0;

        const chip = document.createElement('span');
        chip.className = `metric-chip ${movementChipClass(movement)}` + ((Number(v) || 0) === 0 ? ' is-zero' : '');
        chip.setAttribute('role','button');
        chip.setAttribute('tabindex','0');
        chip.dataset.date = id;
        chip.dataset.movement = String(movement || '').toUpperCase();
        chip.textContent = fmtEUR(v);

        chip.addEventListener('click', onChipClick);
        chip.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter' || ev.key === ' ') {
            ev.preventDefault();
            onChipClick({ currentTarget: chip });
          }
        });

        bar.appendChild(chip);
      }

      cell.appendChild(bar);

      const cellIso = isoDate(d);
      if (cellIso === todayIso) cell.classList.add('today');

      calEl.appendChild(cell);
    });
  }

  // ---- Render: Rendiconto ----
  function renderCalendarRendiconto(cells, currentYear, currentMonth, daysMap){
    calEl.innerHTML = '';

    const now = new Date();
    const todayIso = isoDate(now);

    cells.forEach(d => {
      const inMonth = (d.getFullYear() === currentYear && (d.getMonth() + 1) === currentMonth);
      const cell = document.createElement('div');
      cell.className = 'day-cell' + (inMonth ? '' : ' muted');
      const dayNum = document.createElement(inMonth ? 'a' : 'div');
      dayNum.className = 'day-number' + (inMonth ? ' day-link' : '');
      dayNum.textContent = String(d.getDate());
      if (inMonth){
        const idForLink = isoDate(d);
        dayNum.href = `/rendiconto/distinta-cassa?d=${encodeURIComponent(idForLink)}`;
        dayNum.setAttribute('aria-label', `Apri distinta di cassa ${idForLink}`);
        dayNum.addEventListener('click', (ev) => {
          ev.stopPropagation();
        });
        dayNum.addEventListener('keydown', (ev) => {
          if (ev.key === ' '){
            ev.preventDefault();
            window.location.href = dayNum.href;
          }
        });
      }
      cell.appendChild(dayNum);

      const bar = document.createElement('div');
      bar.className = 'occ-bar rendiconto';

      if (inMonth){
        const id = isoDate(d);
        cell.dataset.date = id;
        cell.classList.add('rendiconto-cell');
        cell.setAttribute('role','button');
        cell.setAttribute('tabindex','0');

        const info = (daysMap && daysMap[id]) ? daysMap[id] : null;
        if (info && info.validated) cell.classList.add('validated-period');
        const giroVal = info ? Number(info.giro || 0) : null;
        const diffVal = info ? Number(info.diff || 0) : null;

        if (info && ((giroVal !== 0) || (diffVal !== 0) || info.has_photo)){
          const giro = buildRendicontoLine('GA', fmtEUR(giroVal), 'giro');
          const diff = buildRendicontoLine('DC', fmtEUR(diffVal), 'diff' + ((diffVal < 0) ? ' negative' : ''));
          bar.appendChild(giro);
          bar.appendChild(diff);
          if (info.has_photo && info.photo_url){
            const photoBtn = document.createElement('button');
            photoBtn.type = 'button';
            photoBtn.className = 'day-photo-btn';
            photoBtn.innerHTML = '&#128247;';
            photoBtn.setAttribute('aria-label', `Apri foto ${id}`);
            photoBtn.setAttribute('title', 'Apri foto');
            photoBtn.addEventListener('click', (ev) => {
              ev.preventDefault();
              ev.stopPropagation();
              openDistintaPhoto(String(info.photo_url || ''));
              if (window.bootstrap && distintaModalEl) bootstrap.Modal.getOrCreateInstance(distintaModalEl).show();
            });
            cell.appendChild(photoBtn);
          }
        }

        cell.addEventListener('click', onRendicontoDayClick);
        cell.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter' || ev.key === ' ') {
            ev.preventDefault();
            onRendicontoDayClick({ currentTarget: cell });
          }
        });
      }

      cell.appendChild(bar);

      const cellIso = isoDate(d);
      if (cellIso === todayIso) cell.classList.add('today');

      calEl.appendChild(cell);
    });
  }

  let currentYear;
  let currentMonth;

  let shownOverdueKey = null;
  let validatePreviewState = null;

let dailySummaryPending = false;
let pendingOverduePayload = null;

function showDailySummaryIfAny(){
  const data = (window.__DAILY_SUMMARY__ || null);
  if (!data || !dsModalEl || !window.bootstrap) return false;

  // Fill
  try {
    if (dsStoreNameEl) dsStoreNameEl.textContent = String(data.store_name || data.store_code || '-');
    if (dsBudgetEl) dsBudgetEl.textContent = fmtEUR(Number(data.budget_net || 0));
    if (dsLyRevenuesEl) dsLyRevenuesEl.textContent = fmtEUR(Number(data.ly_revenues_net || 0));

    const f = data.forecast_net;
    if (dsForecastEl){
      if (f === null || f === undefined) dsForecastEl.textContent = '-';
      else dsForecastEl.textContent = fmtEUR(Number(f || 0));
    }

    const lyDate = data.ly_date;
    if (dsLyDateEl) dsLyDateEl.textContent = lyDate ? String(lyDate) : '-';
    if (dsLyDateRowEl) dsLyDateRowEl.style.display = lyDate ? '' : 'none';
    renderDailySummaryCustomizations(data.cash_statement_customizations);
  } catch (e){
    // no-op
  }

  dailySummaryPending = true;

  try {
    dsModalEl.addEventListener('hidden.bs.modal', () => {
      dailySummaryPending = false;
      if (pendingOverduePayload){
        const p = pendingOverduePayload;
        pendingOverduePayload = null;
        showOverdueModal(p);
      }
    }, { once: true });

    const m = bootstrap.Modal.getOrCreateInstance(dsModalEl);
    m.show();
    return true;
  } catch (e){
    dailySummaryPending = false;
    return false;
  }
}

function renderDailySummaryCustomizations(sections){
  if (!dsCashCustomWrapEl || !dsCashCustomListEl) return;
  const list = Array.isArray(sections) ? sections : [];
  if (!list.length){
    dsCashCustomWrapEl.classList.add('d-none');
    dsCashCustomListEl.innerHTML = '';
    return;
  }
  let html = '';
  list.forEach((section) => {
    const fields = Array.isArray(section && section.fields) ? section.fields : [];
    if (!fields.length) return;
    const title = escapeHtml(String(section.label || 'Personalizzazione').trim());
    const labels = fields.map((f) => escapeHtml(String(f.label || '').trim())).filter(Boolean).join(', ');
    html += `<div><span class="fw-semibold">${title}</span>${labels ? ': ' + labels : ''}</div>`;
  });
  if (!html){
    dsCashCustomWrapEl.classList.add('d-none');
    dsCashCustomListEl.innerHTML = '';
    return;
  }
  dsCashCustomListEl.innerHTML = html;
  dsCashCustomWrapEl.classList.remove('d-none');
}

function showOverdueModal(payload){
  if (!payload || !rcOverdueModalEl || !window.bootstrap) return;

  const key = payload.key || null;
  if (key && shownOverdueKey === key) return;

  // mark as shown
  if (key) shownOverdueKey = key;

  const lastDisp = payload.lastDisp;
  const totalDaVersare = payload.totalDaVersare;
  const daysSince = payload.daysSince;

  if (rcAlertLastDateEl) rcAlertLastDateEl.textContent = lastDisp ? String(lastDisp) : '-';
  if (rcAlertTotalEl) rcAlertTotalEl.textContent = (totalDaVersare === null || totalDaVersare === undefined) ? '-' : fmtEUR(totalDaVersare);
  if (rcAlertDaysEl) rcAlertDaysEl.textContent = (daysSince === null || daysSince === undefined) ? '-' : String(daysSince);

  try {
    const m = bootstrap.Modal.getOrCreateInstance(rcOverdueModalEl);
    m.show();
  } catch (e){
    // no-op
  }
}

function setValidationMonthRange(){
  if (!rvDalEl || !rvAlEl) return;
  rvDalEl.value = `${String(currentYear).padStart(4, '0')}-${String(currentMonth).padStart(2, '0')}-01`;
  const last = new Date(currentYear, currentMonth, 0);
  rvAlEl.value = isoDate(last);
}


  function parseMovementAndCategory(){
    return {
      movement: String(movSelect?.value || 'INV').toUpperCase(),
      category: String(catSelect?.value || 'FoodPaper')
    };
  }

  async function refreshMagazzino(){
    const movement = String(movSelect?.value || 'INV').toUpperCase();
    const category = String(catSelect?.value || 'FoodPaper');

    resetWarehouseSummary();
    setStatus(t('loading', 'Caricamento...'));
    setMonthTotal(0);

    const cells = buildMonthGrid(currentYear, currentMonth);
    renderCalendarWarehouse(cells, currentYear, currentMonth, {}, movement);

    const url = `/magazzino/api/collect/month?year=${encodeURIComponent(currentYear)}&month=${encodeURIComponent(currentMonth)}&movement=${encodeURIComponent(movement)}&category=${encodeURIComponent(category)}`;
    try {
      const data = await fetchJSON(url);
      const days = data.days || {};
      setMonthLabel(currentYear, currentMonth);
      setMonthTotal(data.total || 0);
      setWarehouseSummary(data.summary);
      renderCalendarWarehouse(cells, currentYear, currentMonth, days, movement);
      setStatus('');
    } catch (e){
      console.error(e);
      setStatus(t('error', 'Errore') + ': ' + (e && e.message ? e.message : String(e)));
    }
  }

  async function refreshRendiconto(){
  resetRendicontoSummary();
  setStatus(t('loading', 'Caricamento...'));
  setMonthTotal(0);

  const cells = buildMonthGrid(currentYear, currentMonth);
  renderCalendarRendiconto(cells, currentYear, currentMonth, {});

  const url = `/rendiconto/api/dashboard/month?year=${encodeURIComponent(currentYear)}&month=${encodeURIComponent(currentMonth)}`;
  try {
    const data = await fetchJSON(url);
    const days = data.days || {};
    setMonthLabel(currentYear, currentMonth);
    setRendicontoSummary(data.summary || null);
    setRendicontoVersamentiStatus(data.versamenti_status || null, data.year, data.month);
    renderCalendarRendiconto(cells, currentYear, currentMonth, days);
    setStatus('');
  } catch (e){
    console.error(e);
    resetRendicontoSummary();
    setStatus(t('error', 'Errore') + ': ' + (e && e.message ? e.message : String(e)));
  }
}


  async function refresh(){
    applyModeUI();
    const mode = getMode();
    if (mode === 'RENDICONTO'){
      return refreshRendiconto();
    }
    return refreshMagazzino();
  }

  function setValidateError(msg){
    if (!rvModalErrorEl) return;
    rvModalErrorEl.textContent = msg || '';
    rvModalErrorEl.classList.toggle('d-none', !msg);
  }

  function renderValidatePreview(rows){
    if (!rvModalBodyEl) return;
    const data = Array.isArray(rows) ? rows : [];
    if (!data.length){
      rvModalBodyEl.innerHTML = '<tr><td colspan="2" class="text-muted">' + escapeHtml(t('noDaysInPeriod', 'Nessun giorno nel periodo selezionato.')) + '</td></tr>';
      return;
    }
    rvModalBodyEl.innerHTML = data.map((row) => `
      <tr>
        <td>${escapeHtml(String(row.date || ''))}</td>
        <td class="text-end">${escapeHtml(fmtEUR(Number(row.diff || 0)))}</td>
      </tr>
    `).join('');
  }

  async function previewValidatePeriod(){
    if (!validateCfg.canValidate) return;
    const dal = String(rvDalEl?.value || '').trim();
    const al = String(rvAlEl?.value || '').trim();
    if (!dal || !al){
      window.alert(t('selectValidPeriod', 'Seleziona un periodo valido.'));
      return;
    }

    const modal = (window.bootstrap && rvModalEl) ? bootstrap.Modal.getOrCreateInstance(rvModalEl) : null;
    setValidateError('');
    validatePreviewState = null;
    if (rvModalRangeEl) rvModalRangeEl.textContent = `${dal} → ${al}`;
    if (rvModalTotalDiffEl) rvModalTotalDiffEl.textContent = '-';
    if (rvModalBodyEl) rvModalBodyEl.innerHTML = '<tr><td colspan="2" class="text-muted">' + escapeHtml(t('loading', 'Caricamento...')) + '</td></tr>';
    modal && modal.show();

    try {
      const url = new URL(String(validateCfg.previewUrl || ''), window.location.origin);
      url.searchParams.set('dal', dal);
      url.searchParams.set('al', al);
      const data = await fetchJSON(url.toString());
      validatePreviewState = data;
      if (rvModalRangeEl) rvModalRangeEl.textContent = `${String(data.dal || dal)} → ${String(data.al || al)}`;
      if (rvModalTotalDiffEl) rvModalTotalDiffEl.textContent = fmtEUR(Number(data.total_diff || 0));
      renderValidatePreview(data.days || []);
    } catch (e){
      setValidateError((e && e.message) ? e.message : t('previewLoadError', 'Errore caricamento anteprima.'));
      if (rvModalBodyEl) rvModalBodyEl.innerHTML = '<tr><td colspan="2" class="text-muted">' + escapeHtml(t('previewUnavailable', 'Anteprima non disponibile.')) + '</td></tr>';
    }
  }

  async function confirmValidatePeriod(){
    if (!validateCfg.canValidate || !validatePreviewState) return;
    if (rvConfirmBtn) rvConfirmBtn.disabled = true;
    setValidateError('');
    try {
      const res = await fetch(String(validateCfg.confirmUrl || ''), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          dal: validatePreviewState.dal,
          al: validatePreviewState.al,
          total_diff: Number(validatePreviewState.total_diff || 0)
        })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || (data && data.error)){
        throw new Error((data && (data.error || data.message)) ? (data.error || data.message) : ('HTTP ' + res.status));
      }
      if (window.bootstrap && rvModalEl){
        bootstrap.Modal.getOrCreateInstance(rvModalEl).hide();
      }
      await refresh();
    } catch (e){
      setValidateError((e && e.message) ? e.message : t('validationError', 'Errore convalida periodo.'));
    } finally {
      if (rvConfirmBtn) rvConfirmBtn.disabled = false;
    }
  }

  // ---- Modal handlers ----

  async function onChipClick(ev){
    const chip = ev && ev.currentTarget ? ev.currentTarget : null;
    if (!chip) return;
    const dayIso = chip.dataset.date;
    const movement = String(chip.dataset.movement || '').toUpperCase();
    const { category } = parseMovementAndCategory();

    // Modal init
    const bsModal = (window.bootstrap && modalEl) ? bootstrap.Modal.getOrCreateInstance(modalEl) : null;
    if (modalTitleEl) modalTitleEl.textContent = `${movementLabel(movement)} - ${dayIso}`;
    if (modalFilterLabelEl) modalFilterLabelEl.textContent = category;
    if (modalFilteredTotalEl) modalFilteredTotalEl.textContent = '-';
    if (modalTotalFoodEl) modalTotalFoodEl.textContent = '-';
    if (modalTotalOperEl) modalTotalOperEl.textContent = '-';
    if (modalTotalAllEl) modalTotalAllEl.textContent = '-';
    if (modalTableBody) modalTableBody.innerHTML = `<tr><td colspan="4" class="text-muted">${escapeHtml(t('loading', 'Caricamento...'))}</td></tr>`;

    bsModal && bsModal.show();

    try {
      const url = `/magazzino/api/collect/breakdown?date=${encodeURIComponent(dayIso)}&movement=${encodeURIComponent(movement)}`;
      const data = await fetchJSON(url);

      const totals = (data.totals || {});
      const tFood = Number(totals.foodpaper || 0);
      const tOper = Number(totals.operating || 0);
      const tAll = Number(totals.all || (tFood + tOper));

      if (modalTotalFoodEl) modalTotalFoodEl.textContent = fmtEUR(tFood);
      if (modalTotalOperEl) modalTotalOperEl.textContent = fmtEUR(tOper);
      if (modalTotalAllEl) modalTotalAllEl.textContent = fmtEUR(tAll);

      const filtered = (category === 'Operating') ? tOper : tFood;
      if (modalFilteredTotalEl) modalFilteredTotalEl.textContent = fmtEUR(filtered);

      const highlightFood = (category === 'FoodPaper');
      const highlightOper = (category === 'Operating');

      const site2Groups = Array.isArray(data.site2_groups) ? data.site2_groups : null;
      if (site2Groups && site2Groups.length){
        const storeMap = await getStoreMap();

        let html = '';
        let hasAny = false;

        site2Groups.forEach((g) => {
          const site2 = String((g && g.site2) || '').trim();
          const name = site2 ? (storeMap[site2] || '') : '';
          const label = (site2 && name) ? (site2 + ' - ' + name) : (site2 || '(' + t('site2Missing', 'SITE2 non indicato') + ')');

          const gt = (g && g.totals) ? g.totals : {};
          const gFood = Number(gt.foodpaper || 0);
          const gOper = Number(gt.operating || 0);
          const gAll  = Number(gt.all || (gFood + gOper));

          html += `
            <tr class="table-secondary">
              <td colspan="4" class="fw-semibold">
                ${escapeHtml(t('destination', 'Destinazione'))}: ${escapeHtml(label)}
                <span class="text-muted fw-normal ms-2">(${fmtEUR(gFood)} · ${fmtEUR(gOper)} · ${fmtEUR(gAll)})</span>
              </td>
            </tr>
          `;

          const rows = Array.isArray(g && g.rows) ? g.rows : [];
          rows.forEach((r) => {
            hasAny = true;
            const s = (r.supplier || '').toString();
            const fp = Number(r.foodpaper || 0);
            const op = Number(r.operating || 0);
            const tt = Number(r.total || (fp + op));
            html += `
              <tr>
                <td>${escapeHtml(s)}</td>
                <td class="text-end ${highlightFood ? 'highlight' : ''}">${fmtEUR(fp)}</td>
                <td class="text-end ${highlightOper ? 'highlight' : ''}">${fmtEUR(op)}</td>
                <td class="text-end fw-semibold">${fmtEUR(tt)}</td>
              </tr>
            `;
          });
        });

        if (!hasAny){
          if (modalTableBody) modalTableBody.innerHTML = `<tr><td colspan="4" class="text-muted">${escapeHtml(t('noDataForDay', 'Nessun dato per questo giorno.'))}</td></tr>`;
          return;
        }

        if (modalTableBody) modalTableBody.innerHTML = html;
        return;
      }

      const rows = Array.isArray(data.rows) ? data.rows : [];
      if (!rows.length){
        if (modalTableBody) modalTableBody.innerHTML = `<tr><td colspan="4" class="text-muted">${escapeHtml(t('noDataForDay', 'Nessun dato per questo giorno.'))}</td></tr>`;
        return;
      }

      let html = '';
      rows.forEach(r => {
        const s = (r.supplier || '').toString();
        const fp = Number(r.foodpaper || 0);
        const op = Number(r.operating || 0);
        const tt = Number(r.total || (fp + op));
        html += `
          <tr>
            <td>${escapeHtml(s)}</td>
            <td class="text-end ${highlightFood ? 'highlight' : ''}">${fmtEUR(fp)}</td>
            <td class="text-end ${highlightOper ? 'highlight' : ''}">${fmtEUR(op)}</td>
            <td class="text-end fw-semibold">${fmtEUR(tt)}</td>
          </tr>
        `;
      });

      if (modalTableBody) modalTableBody.innerHTML = html;

    } catch(e){
      console.error(e);
      if (modalTableBody) modalTableBody.innerHTML = `<tr><td colspan="4" class="text-danger">${escapeHtml(t('error', 'Errore'))}: ${escapeHtml(e && e.message ? e.message : String(e))}</td></tr>`;
    }
  }

  async function onRendicontoDayClick(ev){
    const cell = ev && ev.currentTarget ? ev.currentTarget : null;
    if (!cell) return;

    // Se il click parte dal numero giorno (link), non aprire il popup
    try {
      const tgt = ev && ev.target ? ev.target : null;
      if (tgt && typeof tgt.closest === 'function' && tgt.closest('.day-link')) return;
    } catch (e){ /* no-op */ }
    const dayIso = String(cell.dataset.date || '').trim();
    if (!dayIso) return;

    const bsModal = (window.bootstrap && rcModalEl) ? bootstrap.Modal.getOrCreateInstance(rcModalEl) : null;

    if (rcTitleEl) rcTitleEl.textContent = `${t('cashReport', 'Rendiconto')} - ${dayIso}`;
    if (rcGiroEl) rcGiroEl.textContent = '-';
    if (rcDiffEl) {
      rcDiffEl.textContent = '-';
      rcDiffEl.classList.remove('text-danger');
    }
    if (rcDistinteEl) rcDistinteEl.textContent = '-';
    if (rcTicketSiEl) rcTicketSiEl.textContent = '-';
    if (rcDeliveryOnlineEl) rcDeliveryOnlineEl.textContent = '-';
    if (rcDeliveryContantiEl) rcDeliveryContantiEl.textContent = '-';
    if (rcCouponSiEl) rcCouponSiEl.textContent = '-';
    if (rcSpeseNetEl) rcSpeseNetEl.textContent = '-';
    if (rcSpeseTotalEl) rcSpeseTotalEl.textContent = '-';
    if (rcNoteCreditoEl) rcNoteCreditoEl.textContent = '-';
    if (rcChiusuraTbody) rcChiusuraTbody.innerHTML = `<tr><td colspan="2" class="text-muted">${escapeHtml(t('loading', 'Caricamento...'))}</td></tr>`;
    if (rcCustomSectionsWrap) rcCustomSectionsWrap.classList.add('d-none');
    if (rcCustomSectionsEl) rcCustomSectionsEl.innerHTML = '';
    if (rcPhotoBtnEl){
      rcPhotoBtnEl.classList.add('d-none');
      rcPhotoBtnEl.onclick = null;
    }

    bsModal && bsModal.show();

    try {
      const url = `/rendiconto/api/dashboard/day?date=${encodeURIComponent(dayIso)}`;
      const data = await fetchJSON(url);

      const giro = Number(data.giro || 0);
      const diff = Number(data.diff || 0);

      if (rcGiroEl) rcGiroEl.textContent = fmtEUR(giro);
      if (rcDiffEl) {
        rcDiffEl.textContent = fmtEUR(diff);
        if (diff < 0) rcDiffEl.classList.add('text-danger');
        else rcDiffEl.classList.remove('text-danger');
      }

      if (rcDistinteEl) rcDistinteEl.textContent = fmtEUR(Number(data.distinte || 0));
      if (rcTicketSiEl) rcTicketSiEl.textContent = fmtEUR(Number(data.ticket_si || 0));
      if (rcDeliveryOnlineEl) rcDeliveryOnlineEl.textContent = fmtEUR(Number(data.delivery_si || 0));
      if (rcDeliveryContantiEl) rcDeliveryContantiEl.textContent = fmtEUR(Number(data.delivery_no || 0));
      if (rcCouponSiEl) rcCouponSiEl.textContent = fmtEUR(Number(data.coupon_si || 0));

      const spnet = Number(data.spese_net || 0);
      const sptot = Number(data.spese_total || 0);
      const nc = Number(data.note_credito || 0);

      if (rcSpeseNetEl) rcSpeseNetEl.textContent = fmtEUR(spnet);
      if (rcSpeseTotalEl) rcSpeseTotalEl.textContent = fmtEUR(sptot);
      if (rcNoteCreditoEl) rcNoteCreditoEl.textContent = fmtEUR(nc);

      if (rcPhotoBtnEl && data.has_photo && data.photo_url){
        rcPhotoBtnEl.classList.remove('d-none');
        rcPhotoBtnEl.onclick = () => openDistintaPhoto(String(data.photo_url || ''));
      }

      const rows = Array.isArray(data.chiusura_rows) ? data.chiusura_rows : [];
      if (!rows.length){
        if (rcChiusuraTbody) rcChiusuraTbody.innerHTML = `<tr><td colspan="2" class="text-muted">${escapeHtml(t('noClosingData', 'Nessun dato chiusura per questo giorno.'))}</td></tr>`;
        renderRendicontoCustomSections(data.custom_sections);
        return;
      }

      let html = '';
      rows.forEach((r) => {
        const label = translatedClosingLabel(r.label);
        const type = String(r.type || 'money').toLowerCase();
        const value = r.value;

        let txt = '-';
        if (type === 'int') txt = fmtINT(value);
        else txt = fmtEUR(value);

        html += `
          <tr>
            <td>${escapeHtml(label)}</td>
            <td class="text-end">${escapeHtml(txt)}</td>
          </tr>
        `;
      });

      if (rcChiusuraTbody) rcChiusuraTbody.innerHTML = html;
      renderRendicontoCustomSections(data.custom_sections);

    } catch(e){
      console.error(e);
      if (rcChiusuraTbody) rcChiusuraTbody.innerHTML = `<tr><td colspan="2" class="text-danger">${escapeHtml(t('error', 'Errore'))}: ${escapeHtml(e && e.message ? e.message : String(e))}</td></tr>`;
    }
  }

  function renderRendicontoCustomSections(sections){
    if (!rcCustomSectionsWrap || !rcCustomSectionsEl) return;
    const list = Array.isArray(sections) ? sections : [];
    if (!list.length){
      rcCustomSectionsWrap.classList.add('d-none');
      rcCustomSectionsEl.innerHTML = '';
      return;
    }
    let html = '';
    list.forEach((section) => {
      const title = String(section && section.label ? section.label : t('customSection', 'Personalizzazione')).trim();
      const rows = Array.isArray(section && section.rows) ? section.rows : [];
      if (!rows.length) return;
      let body = '';
      rows.forEach((r) => {
        const type = String(r.type || 'money').toLowerCase();
        const value = type === 'int' ? fmtINT(r.value) : fmtEUR(r.value);
        body += `
          <tr>
            <td>${escapeHtml(String(r.label || '').trim())}</td>
            <td class="text-end">${escapeHtml(value)}</td>
          </tr>
        `;
      });
      html += `
        <div class="border rounded mb-2 overflow-hidden">
          <div class="px-2 py-1 bg-light fw-semibold small">${escapeHtml(title)}</div>
          <div class="table-responsive">
            <table class="table table-sm align-middle mb-0">
              <tbody>${body}</tbody>
            </table>
          </div>
        </div>
      `;
    });
    if (!html){
      rcCustomSectionsWrap.classList.add('d-none');
      rcCustomSectionsEl.innerHTML = '';
      return;
    }
    rcCustomSectionsEl.innerHTML = html;
    rcCustomSectionsWrap.classList.remove('d-none');
  }

// ---- Init ----
function init(){
  const today = new Date();
  currentYear = today.getFullYear();
  currentMonth = today.getMonth() + 1;
  setMonthLabel(currentYear, currentMonth);
  setValidationMonthRange();

  btnPrev && btnPrev.addEventListener('click', () => {
    currentMonth -= 1;
    if (currentMonth < 1){ currentMonth = 12; currentYear -= 1; }
    setValidationMonthRange();
    refresh();
  });

  btnNext && btnNext.addEventListener('click', () => {
    currentMonth += 1;
    if (currentMonth > 12){ currentMonth = 1; currentYear += 1; }
    setValidationMonthRange();
    refresh();
  });

  btnToday && btnToday.addEventListener('click', () => {
    const t = new Date();
    currentYear = t.getFullYear();
    currentMonth = t.getMonth() + 1;
    setValidationMonthRange();
    refresh();
  });

  dashModeEl && dashModeEl.addEventListener('change', refresh);
  movSelect && movSelect.addEventListener('change', refresh);
  catSelect && catSelect.addEventListener('change', refresh);
  rvPreviewBtn && rvPreviewBtn.addEventListener('click', previewValidatePeriod);
  rvConfirmBtn && rvConfirmBtn.addEventListener('click', confirmValidatePeriod);

  // Pop-up riepilogo giornata (se presente) prima dell'eventuale alert versamenti
  showDailySummaryIfAny();

  refresh();
}

if (document.readyState === 'loading'){
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

})();
