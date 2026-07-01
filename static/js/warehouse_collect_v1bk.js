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

  if (!calEl) return;

  const itMonthFmt = new Intl.DateTimeFormat('it-IT', { month: 'long', year: 'numeric' });
  const itCurFmt = new Intl.NumberFormat('it-IT', { style: 'currency', currency: 'EUR' });
  const itIntFmt = new Intl.NumberFormat('it-IT', { maximumFractionDigits: 0 });
  const itPctFmt = new Intl.NumberFormat('it-IT', { minimumFractionDigits: 1, maximumFractionDigits: 1 });

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
      throw new Error('Risposta non JSON (probabile redirect/login/store).');
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
      case 'INV': return 'Inventario';
      case 'DELIVERY': return 'Delivery';
      case 'TXIN': return 'Trasferimenti In';
      case 'TXOUT': return 'Trasferimenti Out';
      case 'WASTE': return 'Waste Crudo';
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
      shownOverdueKey = key;

      if (rcAlertLastDateEl) rcAlertLastDateEl.textContent = lastDisp ? lastDisp : '-';
      if (rcAlertTotalEl) rcAlertTotalEl.textContent = (totalDaVersare === null) ? '-' : fmtEUR(totalDaVersare);
      if (rcAlertDaysEl) rcAlertDaysEl.textContent = (daysSince === null) ? '-' : String(daysSince);

      try {
        const m = bootstrap.Modal.getOrCreateInstance(rcOverdueModalEl);
        m.show();
      } catch (e){
        // no-op
      }
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

      const dayNum = document.createElement('div');
      dayNum.className = 'day-number';
      dayNum.textContent = String(d.getDate());
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
        const giroVal = info ? Number(info.giro || 0) : null;
        const diffVal = info ? Number(info.diff || 0) : null;

        if (info && ((giroVal !== 0) || (diffVal !== 0))){
          const giro = buildRendicontoLine('GA', fmtEUR(giroVal), 'giro');

          const diff = buildRendicontoLine('DC', fmtEUR(diffVal), 'diff' + ((diffVal < 0) ? ' negative' : ''));

          bar.appendChild(giro);
          bar.appendChild(diff);
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
    setStatus('Caricamento...');
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
      setStatus('Errore: ' + (e && e.message ? e.message : String(e)));
    }
  }

  async function refreshRendiconto(){
  resetRendicontoSummary();
  setStatus('Caricamento...');
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
    setStatus('Errore: ' + (e && e.message ? e.message : String(e)));
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
    if (modalTableBody) modalTableBody.innerHTML = `<tr><td colspan="4" class="text-muted">Caricamento...</td></tr>`;

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
          const label = (site2 && name) ? (site2 + ' - ' + name) : (site2 || '(SITE2 non indicato)');

          const gt = (g && g.totals) ? g.totals : {};
          const gFood = Number(gt.foodpaper || 0);
          const gOper = Number(gt.operating || 0);
          const gAll  = Number(gt.all || (gFood + gOper));

          html += `
            <tr class="table-secondary">
              <td colspan="4" class="fw-semibold">
                Destinazione: ${escapeHtml(label)}
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
          if (modalTableBody) modalTableBody.innerHTML = `<tr><td colspan="4" class="text-muted">Nessun dato per questo giorno.</td></tr>`;
          return;
        }

        if (modalTableBody) modalTableBody.innerHTML = html;
        return;
      }

      const rows = Array.isArray(data.rows) ? data.rows : [];
      if (!rows.length){
        if (modalTableBody) modalTableBody.innerHTML = `<tr><td colspan="4" class="text-muted">Nessun dato per questo giorno.</td></tr>`;
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
      if (modalTableBody) modalTableBody.innerHTML = `<tr><td colspan="4" class="text-danger">Errore: ${escapeHtml(e && e.message ? e.message : String(e))}</td></tr>`;
    }
  }

  async function onRendicontoDayClick(ev){
    const cell = ev && ev.currentTarget ? ev.currentTarget : null;
    if (!cell) return;
    const dayIso = String(cell.dataset.date || '').trim();
    if (!dayIso) return;

    const bsModal = (window.bootstrap && rcModalEl) ? bootstrap.Modal.getOrCreateInstance(rcModalEl) : null;

    if (rcTitleEl) rcTitleEl.textContent = `Rendiconto - ${dayIso}`;
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
    if (rcChiusuraTbody) rcChiusuraTbody.innerHTML = `<tr><td colspan="2" class="text-muted">Caricamento...</td></tr>`;

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

      const rows = Array.isArray(data.chiusura_rows) ? data.chiusura_rows : [];
      if (!rows.length){
        if (rcChiusuraTbody) rcChiusuraTbody.innerHTML = `<tr><td colspan="2" class="text-muted">Nessun dato chiusura per questo giorno.</td></tr>`;
        return;
      }

      let html = '';
      rows.forEach((r) => {
        const label = String(r.label || '').trim();
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

    } catch(e){
      console.error(e);
      if (rcChiusuraTbody) rcChiusuraTbody.innerHTML = `<tr><td colspan="2" class="text-danger">Errore: ${escapeHtml(e && e.message ? e.message : String(e))}</td></tr>`;
    }
  }

  // ---- Init ----
  function init(){
    const today = new Date();
    currentYear = today.getFullYear();
    currentMonth = today.getMonth() + 1;
    setMonthLabel(currentYear, currentMonth);

    btnPrev && btnPrev.addEventListener('click', () => {
      currentMonth -= 1;
      if (currentMonth < 1){ currentMonth = 12; currentYear -= 1; }
      refresh();
    });

    btnNext && btnNext.addEventListener('click', () => {
      currentMonth += 1;
      if (currentMonth > 12){ currentMonth = 1; currentYear += 1; }
      refresh();
    });

    btnToday && btnToday.addEventListener('click', () => {
      const t = new Date();
      currentYear = t.getFullYear();
      currentMonth = t.getMonth() + 1;
      refresh();
    });

    dashModeEl && dashModeEl.addEventListener('change', refresh);
    movSelect && movSelect.addEventListener('change', refresh);
    catSelect && catSelect.addEventListener('change', refresh);

    refresh();
  }

  if (document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
