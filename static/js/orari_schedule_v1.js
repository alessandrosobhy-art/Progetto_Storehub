(function () {
  const boot = window.ORARI_BOOT || {};
  const I18N = (boot && boot.i18n) || {};
  function t(key, fallback) {
    return I18N[key] || fallback;
  }
  function translateDayLabel(label) {
    const raw = String(label || '');
    const m = raw.match(/^([A-ZÀ-Ú]{3})(\b|\s|$)(.*)$/i);
    if (!m) return raw;
    const map = {
      LUN: t('weekdayMon', 'LUN'),
      MAR: t('weekdayTue', 'MAR'),
      MER: t('weekdayWed', 'MER'),
      GIO: t('weekdayThu', 'GIO'),
      VEN: t('weekdayFri', 'VEN'),
      SAB: t('weekdaySat', 'SAB'),
      DOM: t('weekdaySun', 'DOM')
    };
    const prefix = String(m[1] || '').toUpperCase();
    return (map[prefix] || m[1]) + (m[2] || '') + (m[3] || '');
  }
  let staffAll = Array.isArray(boot.staff) ? boot.staff : [];
  const orariConfig = (boot && boot.orari_config) || {};
  const orariCausali = Array.isArray(orariConfig.causali) ? orariConfig.causali : [];
  const orariInquadramenti = Array.isArray(orariConfig.inquadramenti) ? orariConfig.inquadramenti : [];

  const elStaffBtn = document.getElementById('staffDropBtn');
  const elStaffSearch = document.getElementById('staffSearch');
  const elStaffList = document.getElementById('staffCheckList');

  const elBody = document.getElementById('schedBody');
  const elSalesRow = document.getElementById('schedSalesRow');
  const elPrevYearRow = document.getElementById('schedPrevYearRow');
  const elDaysRow = document.getElementById('schedDaysRow');
  const elWeekLabel = document.getElementById('weekLabel');
  const elStatus = document.getElementById('schedStatus');

  const elMobileSales = document.getElementById('schedMobileSales');
  const elMobile = document.getElementById('schedMobile');
  const MOBILE_MQ = (window.matchMedia ? window.matchMedia('(max-width: 991.98px)') : null);

  const elStatSales = document.getElementById('statSales');
  const elStatHours = document.getElementById('statHours');
  const elStatProd = document.getElementById('statProd');

  const btnPrev = document.getElementById('btnPrevWeek');
  const btnNext = document.getElementById('btnNextWeek');
  const btnSave = document.getElementById('btnSave');
  const btnPdf = document.getElementById('btnPdf');
  const btnLinear = document.getElementById('btnLinear');
  const pdfForm = document.getElementById('pdfForm');
  const pdfWeekStart = document.getElementById('pdfWeekStart');
  const pdfNames = document.getElementById('pdfNames');
  const btnSelectAll = document.getElementById('btnSelectAll');
  const btnClearSel = document.getElementById('btnClearSel');
  const btnToday = document.getElementById('btnToday');
  const btnImportWeek = document.getElementById('btnImportWeek');

  const elImportWeekModal = document.getElementById('orariImportWeekModal');
  const elImportWeekDate = document.getElementById('importWeekDate');
  const btnImportWeekDo = document.getElementById('btnImportWeekDo');

  const elCellMenu = document.getElementById('orariCellMenu');

  const elLinearModal = document.getElementById('orariLinearModal');
  const elLinearDay = document.getElementById('linearDaySelect');
  const elLinearWrap = document.getElementById('linearTableWrap');

  const COLOR_SWATCHES = [
    { code: 0, value: '' },
    { code: 1, value: '#f8f9fa' },
    { code: 2, value: '#e9ecef' },
    { code: 3, value: '#fff3cd' },
    { code: 4, value: '#d1e7dd' },
    { code: 5, value: '#cff4fc' },
    { code: 6, value: '#f8d7da' },
    { code: 7, value: '#ffe5d0' },
    { code: 8, value: '#e2d9f3' },
    { code: 9, value: '#d2f4ea' },
    { code: 10, value: '#f7d6e6' }
  ];

  let storesAll = [];
  let weekStart = parseISO(boot.week_start) || mondayOf(new Date());
  let daysMeta = [];
  let salesCache = {};
  let prevYearCache = {};

  let lastTurni = [];

  let cache = new Map();
  let selectedSet = new Set();
  let selectionMode = 'auto';
  let loadTimer = null;
  let dirty = false;
  let statsRaf = null;
  let mobileSalesExpanded = false;
  let mobileExpandedStaff = new Set();

  let cellClipboard = null; // { week: 'YYYY-MM-DD', data: {...}, srcKey: 'n|d' }
  let cellMenuTarget = null;
  let longPressTimer = null;
  let longPressPos = null;

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[c]));
  }


  function cssEsc(s) {
    const v = String(s ?? '');
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(v);
    return v.replace(/[^a-zA-Z0-9_\-]/g, (ch) => `\\${ch}`);
  }

  function isMobileView() {
    if (MOBILE_MQ) return !!MOBILE_MQ.matches;
    return (window.innerWidth || 0) <= 992;
  }

  function findRowContainer(el) {
    if (!el || !el.closest) return null;
    return el.closest('tr') || el.closest('[data-row="1"]');
  }

  function getRowContainers() {
    if (isMobileView()) {
      return Array.from((elMobile || document).querySelectorAll('#schedMobile [data-row="1"]'));
    }
    return Array.from((elBody || document).querySelectorAll('#schedBody tr'));
  }

  function showModal(title, message) {
    const t = document.getElementById('orariModalTitle');
    const b = document.getElementById('orariModalBody');
    if (t) t.textContent = title || 'Attenzione';
    if (b) b.textContent = message || '';
    const el = document.getElementById('orariModal');
    if (el && window.bootstrap && window.bootstrap.Modal) {
      const inst = window.bootstrap.Modal.getOrCreateInstance(el);
      inst.show();
      return;
    }
    window.alert((title ? (title + '\n\n') : '') + (message || ''));
  }



// Validazione ore contrattuali (salvataggio)
function _normRuleKey(v) {
  return String(v || '').trim().toLowerCase().replace(/[^a-z0-9]/g, '');
}

function _causaleRule(v) {
  const key = _normRuleKey(v);
  if (!key) return null;
  return (orariCausali || []).find(r => _normRuleKey(r && r.name) === key) || null;
}

function _inquadramentoRule(v) {
  const key = _normRuleKey(v);
  if (!key) return null;
  return (orariInquadramenti || []).find(r => _normRuleKey(r && r.name) === key) || null;
}

function _boolRule(rule, key, fallback) {
  if (!rule || !(key in rule)) return !!fallback;
  return !!rule[key];
}

const _CONTRACT_CAUSALI_DEFICIT = (orariCausali || [])
  .filter(r => !!(r && r.justifies_contract_hours))
  .map(r => String(r.name || '').trim())
  .filter(Boolean)
  .join(' / ') || 'Ferie / Permesso / Allattamento / Malattia';

function _normCaus(v) {
  return String(v || '').trim().toLowerCase();
}

function _isExtraCaus(v) {
  return _normCaus(v) === 'extra';
}

function _isAutoExtraEligibleCaus(v) {
  const low = _normCaus(v);
  if (!low) return true;
  const rule = _causaleRule(v);
  if (rule) return !!rule.auto_extra_eligible;
  return low === 'prestito' || low === 'training' || low === 'extra';
}

function _dateSortKey(v) {
  const s = String(v || '').trim();
  if (!s) return '9999-99-99';
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
  const m = s.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  if (m) return `${m[3]}-${m[2]}-${m[1]}`;
  return s;
}

function buildAutoExtraSummary(rows) {
  const perName = new Map();

  function ensureRec(name) {
    let rec = perName.get(name);
    if (!rec) {
      const st = staffByName(name);
      rec = {
        nominativo: name,
        contract_mins: st ? (Number(st.ore_contrattuali || 0) * 60) : 0,
        ruolo: String((st && (st.ruolo || st.inquadramento)) || '').trim().toUpperCase(),
        total_mins: 0,
        explicit_extra_mins: 0,
        auto_extra_mins: 0,
        regular_mins: 0,
        unallocatable_extra_mins: 0,
        segments: []
      };
      perName.set(name, rec);
    }
    return rec;
  }

  (rows || []).forEach(r0 => {
    const name = String(r0.nominativo || '').trim();
    if (!name) return;
    const rec = ensureRec(name);
    const dayKey = _dateSortKey(r0.data);

    [
      { slot: 1, causale: r0.causale, inizio: r0.inizio_1, fine: r0.fine_1 },
      { slot: 2, causale: r0.causale2, inizio: r0.inizio_2, fine: r0.fine_2 }
    ].forEach(seg => {
      const r = duration(seg.inizio, seg.fine, { overnightStartMin: 12 * 60, overnightEndMax: 8 * 60 });
      const mins = (r && !r.err) ? (r.mins || 0) : 0;
      if (mins <= 0) return;

      const isExplicitExtra = _isExtraCaus(seg.causale);
      rec.total_mins += mins;
      if (isExplicitExtra) rec.explicit_extra_mins += mins;

      rec.segments.push({
        day_key: dayKey,
        slot: seg.slot,
        mins,
        auto_eligible: _isAutoExtraEligibleCaus(seg.causale) && !isExplicitExtra
      });
    });
  });

  perName.forEach(rec => {
    rec.segments.sort((a, b) => {
      if (a.day_key !== b.day_key) return a.day_key.localeCompare(b.day_key);
      return a.slot - b.slot;
    });

    const contract = Number(rec.contract_mins || 0);
    if (contract > 0) {
      const targetExtra = Math.max(0, rec.total_mins - contract);
      let remainingAuto = Math.max(0, targetExtra - rec.explicit_extra_mins);

      for (const seg of rec.segments) {
        if (!remainingAuto) break;
        if (!seg.auto_eligible) continue;
        const take = Math.min(seg.mins, remainingAuto);
        remainingAuto -= take;
        rec.auto_extra_mins += take;
      }

      rec.unallocatable_extra_mins = Math.max(0, remainingAuto);
    }

    rec.regular_mins = Math.max(0, rec.total_mins - rec.explicit_extra_mins - rec.auto_extra_mins);
  });

  return perName;
}

function _contractRowElByName(name) {
  const n = String(name || '').trim();
  if (!n) return null;

  // Desktop
  let el = null;
  try {
    el = document.querySelector(`#schedBody tr[data-nominativo="${cssEsc(n)}"]`);
  } catch (e) {
    el = null;
  }
  if (el) return el;

  // Mobile
  try {
    el = document.querySelector(`#schedMobile [data-row="1"][data-nominativo="${cssEsc(n)}"]`);
  } catch (e) {
    el = null;
  }
  return el || null;
}

function _clearContractMarks() {
  try {
    getRowContainers().forEach(r => r.classList.remove('orari-contract-err', 'orari-contract-err-pulse'));
  } catch (e) { }
}

function _markContractErr(name) {
  const el = _contractRowElByName(name);
  if (el) el.classList.add('orari-contract-err');
}

function _scrollToContractErr(name) {
  const el = _contractRowElByName(name);
  if (!el) return;
  try {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  } catch (e) {
    try { el.scrollIntoView(); } catch (e2) { }
  }
  el.classList.add('orari-contract-err-pulse');
  setTimeout(() => { try { el.classList.remove('orari-contract-err-pulse'); } catch (e) { } }, 1200);
}

function showContractValidationModal(anomalies) {
  const modalEl = document.getElementById('orariValidationModal');
  const bodyEl = document.getElementById('orariValidationBody');

  if (!bodyEl) {
    // fallback
    showModal('Anomalie ore contrattuali', (anomalies || []).map(a => `${a.nominativo}: ${a.action}`).join('\n'));
    return;
  }

  _clearContractMarks();
  (anomalies || []).forEach(a => _markContractErr(a.nominativo));

  const rowsHtml = (anomalies || []).map(a => {
    const diff = (a.base_mins || 0) - (a.contract_mins || 0);
    const diffTxt = fmtHM(diff);
    const diffCls = diff < 0 ? 'text-danger' : 'text-warning';
    return `
      <tr>
        <td class="fw-semibold">${esc(a.nominativo || '')}</td>
        <td class="text-end">${fmtHM(a.contract_mins || 0)}</td>
        <td class="text-end">${fmtHM(a.base_mins || 0)}</td>
        <td class="text-end">${fmtHM(a.extra_mins || 0)}</td>
        <td class="text-end ${diffCls} fw-semibold">${esc(diffTxt)}</td>
        <td>${esc(a.action || '')}</td>
        <td class="text-end">
          <button type="button" class="btn btn-sm btn-outline-secondary" data-act="goto-contract" data-name="${esc(a.nominativo || '')}">Vai</button>
        </td>
      </tr>
    `;
  }).join('');

  bodyEl.innerHTML = `
    <div class="small text-muted mb-2">
      Le <b>ore pianificate (al netto dell'Extra automatico)</b> devono coincidere con le <b>ore contrattuali</b>.
      Le ore <b>in eccedenza</b> vengono allocate automaticamente come <b>Extra</b> partendo dal luned?.
      L'auto-assegnazione usa solo turni <b>senza causale</b> oppure con causale <b>Prestito</b>/<b>Training</b>. Le ore <b>mancanti</b> vanno coperte con: <b>${_CONTRACT_CAUSALI_DEFICIT}</b>.
    </div>
    <div class="table-responsive">
      <table class="table table-sm align-middle mb-0">
        <thead>
          <tr class="small text-muted">
            <th>Dipendente</th>
            <th class="text-end">Contratto</th>
            <th class="text-end">Pianificate</th>
            <th class="text-end">Extra</th>
            <th class="text-end">Diff.</th>
            <th>Segnalazione / Suggerimento</th>
            <th></th>
          </tr>
        </thead>
        <tbody>${rowsHtml}</tbody>
      </table>
    </div>
  `;

  if (modalEl && window.bootstrap && window.bootstrap.Modal) {
    const inst = window.bootstrap.Modal.getOrCreateInstance(modalEl);
    inst.show();
    return;
  }

  // fallback
  showModal('Anomalie ore contrattuali', (anomalies || []).map(a => `${a.nominativo}: ${a.action}`).join('\n'));
}


  function showCopyHint(msg) {
      if (!msg) return;
      setStatus(msg);
    }

    function hideCellMenu() {
      if (elCellMenu) elCellMenu.style.display = 'none';
      cellMenuTarget = null;
    }

    function showCellMenuAt(cell, x, y) {
      if (!elCellMenu) return;
      cellMenuTarget = cell || null;

      // enable/disable paste based on clipboard and week
      const pasteBtn = elCellMenu.querySelector('[data-act="paste"]');
      const clearBtn = elCellMenu.querySelector('[data-act="clear"]');
      const canPaste = !!(cellClipboard && cellClipboard.week === toISO(weekStart));
      if (pasteBtn) pasteBtn.classList.toggle('disabled', !canPaste);
      if (clearBtn) clearBtn.classList.toggle('disabled', !cellClipboard);
      const selectedColor = Number((cell && cell.dataset && cell.dataset.colore) || 0);
      elCellMenu.querySelectorAll('[data-color-code]').forEach(function (sw) {
        sw.classList.toggle('sel', Number(sw.dataset.colorCode || 0) === selectedColor);
      });

      const vw = window.innerWidth || 0;
      const vh = window.innerHeight || 0;

      elCellMenu.style.display = 'block';
      elCellMenu.style.left = '0px';
      elCellMenu.style.top = '0px';

      const rect = elCellMenu.getBoundingClientRect();
      let left = Math.max(8, Math.min(x, vw - rect.width - 8));
      let top = Math.max(8, Math.min(y, vh - rect.height - 8));
      elCellMenu.style.left = left + 'px';
      elCellMenu.style.top = top + 'px';
    }

    function getCellData(cellBox) {
      const v = (k) => (cellBox.querySelector(`input[data-k="${k}"]`)?.value || '');
      const sv = (k) => (cellBox.querySelector(`select[data-k="${k}"]`)?.value || '');
      return {
        causale: sv('causale'),
        causale2: sv('causale2'),
        s_prestito: sv('s_prestito'),
        s_prestito2: sv('s_prestito2'),
        inizio_1: v('inizio_1'),
        fine_1: v('fine_1'),
        inizio_2: v('inizio_2'),
        fine_2: v('fine_2'),
        colore: Number(cellBox.dataset.colore || 0)
      };
    }

    function applyCellData(cellBox, data) {
      if (!cellBox || !data) return;

      const setTime = (k, val) => {
        const el = cellBox.querySelector(`input[data-k="${k}"]`);
        if (!el) return;
        el.value = normalizeTime(val) || '';
        clearTimeInvalid(el);
      };

      const setSel = (k, val) => {
        const el = cellBox.querySelector(`select[data-k="${k}"]`);
        if (!el) return;
        el.value = String(val || '');
        // trigger refresh handlers
        try { el.dispatchEvent(new Event('change', { bubbles: true })); } catch (e) { }
      };

      // causale first (refresh will handle prestito visibility/options)
      setSel('causale', data.causale || '');
      setSel('causale2', data.causale2 || '');

      // times
      setTime('inizio_1', data.inizio_1 || '');
      setTime('fine_1', data.fine_1 || '');
      setTime('inizio_2', data.inizio_2 || '');
      setTime('fine_2', data.fine_2 || '');

      // prestito values only if causale is prestito
      if (String(data.causale || '').trim().toLowerCase() === 'prestito') setSel('s_prestito', data.s_prestito || '');
      else setSel('s_prestito', '');
      if (String(data.causale2 || '').trim().toLowerCase() === 'prestito') setSel('s_prestito2', data.s_prestito2 || '');
      else setSel('s_prestito2', '');

      applyCellColor(cellBox, Number(data.colore || 0));

      computeCellTotals(cellBox);
      updateRowTotals(findRowContainer(cellBox));
    }

    function clearClipboardHighlight() {
      document.querySelectorAll('.cell-box.copy-src').forEach(el => el.classList.remove('copy-src'));
    }

    function setClipboardFromCell(cellBox) {
      if (!cellBox) return;
      clearClipboardHighlight();
      cellBox.classList.add('copy-src');
      cellClipboard = {
        week: toISO(weekStart),
        data: getCellData(cellBox),
        srcKey: `${String(cellBox.dataset.nominativo || '').trim()}|${String(cellBox.dataset.data || '').trim()}`
      };
      showCopyHint('Giornata copiata. Per incollare: tasto destro/pressione lunga oppure SHIFT+click sulla cella.');
    }

    function pasteClipboardToCell(cellBox) {
      if (!cellBox || !cellClipboard || cellClipboard.week !== toISO(weekStart)) {
        showModal('Copia non disponibile', 'La copia è valida solo per la settimana corrente.');
        return;
      }
      applyCellData(cellBox, cellClipboard.data);
      markDirty();
      cellBox.classList.add('pasted');
      setTimeout(() => { try { cellBox.classList.remove('pasted'); } catch (e) { } }, 600);
    }

  function setDirty(v) {
    dirty = !!v;
  }

  function markDirty() {
    dirty = true;
  }

  function confirmLoseChanges() {
    if (!dirty) return true;
    return window.confirm('Modifiche non salvate. Proseguendo le perderai. Continuare?');
  }

  function parseISO(s) {
    if (!s) return null;
    const p = String(s).split('-').map(x => parseInt(x, 10));
    if (p.length < 3 || !p[0] || !p[1] || !p[2]) return null;
    return new Date(p[0], p[1] - 1, p[2]);
  }

  function toISO(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${dd}`;
  }

  function mondayOf(d) {
    const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const day = x.getDay() || 7;
    x.setDate(x.getDate() - (day - 1));
    x.setHours(0, 0, 0, 0);
    return x;
  }

  function addDays(d, n) {
    const x = new Date(d.getTime());
    x.setDate(x.getDate() + n);
    return x;
  }

  function fmtRange(monday) {
    const sun = addDays(monday, 6);
    return `${toISO(monday)} → ${toISO(sun)}`;
  }

  function setStatus(s) {
    if (elStatus) elStatus.textContent = s || '';
  }

  function staffByName(name) {
    const n = String(name || '').trim();
    return staffAll.find(x => String((x || {}).nome_cognome || '').trim() === n) || null;
  }

  function staffById(staffId) {
    const sid = String(staffId || '').trim();
    if (!sid) return null;
    return staffAll.find(x => String((x || {}).id || '').trim() === sid) || null;
  }

  function selectedNames() {
    return Array.from(selectedSet).map(x => String(x || '').trim()).filter(Boolean).sort((a, b) => a.localeCompare(b));
  }

  function updateStaffButton() {
    if (!elStaffBtn) return;
    const noms = selectedNames();
    if (!noms.length) {
      elStaffBtn.textContent = t('people', 'Persone');
      return;
    }
    if (noms.length === 1) {
      elStaffBtn.textContent = noms[0];
      return;
    }
    elStaffBtn.textContent = `${t('peopleCount', 'Persone')} (${noms.length})`;
  }

  function renderStaffList(filterText) {
    if (!elStaffList) return;
    const f = String(filterText || '').trim().toLowerCase();
    elStaffList.innerHTML = '';
    const items = (staffAll || []).slice().sort((a, b) => {
      const na = String((a || {}).nome_cognome || '').toLowerCase();
      const nb = String((b || {}).nome_cognome || '').toLowerCase();
      return na.localeCompare(nb);
    });

    items.forEach((s, i) => {
      const name = String((s || {}).nome_cognome || '').trim();
      if (!name) return;
      if (f && !name.toLowerCase().includes(f)) return;

      const id = `st_${i}_${Math.random().toString(16).slice(2)}`;
      const wrap = document.createElement('div');
      wrap.className = 'form-check';
      const cb = document.createElement('input');
      cb.className = 'form-check-input';
      cb.type = 'checkbox';
      cb.value = name;
      cb.id = id;
      cb.dataset.staff = '1';
      cb.checked = selectedSet.has(name);

      const lab = document.createElement('label');
      lab.className = 'form-check-label w-100';
      lab.htmlFor = id;
      lab.innerHTML = `<div>${esc(name)}</div>`;

      wrap.appendChild(cb);
      wrap.appendChild(lab);
      elStaffList.appendChild(wrap);
    });
  }


  async function loadStaffActive() {
    try {
      const r = await fetch('/orari/api/staff', {
        method: 'GET',
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json' }
      });
      const j = await r.json().catch(() => null);
      if (r.ok && j && j.ok && Array.isArray(j.staff)) {
        staffAll = j.staff;
      }
    } catch (e) {
    }
  }

  function setAllChecked(v) {
    // Interazione esplicita utente: forza modalità manuale (anche per "Nessuno")
    selectionMode = 'manual';
    if (v) {
      selectedSet = new Set((staffAll || []).map(s => String((s || {}).nome_cognome || '').trim()).filter(Boolean));
    } else {
      selectedSet = new Set();
    }
    renderStaffList(elStaffSearch ? (elStaffSearch.value || '') : '');
    updateStaffButton();
    scheduleLoadWeek();
  }

  function scheduleLoadWeek() {
    if (loadTimer) clearTimeout(loadTimer);
    loadTimer = setTimeout(() => loadWeek(), 150);
  }

  async function loadStoresAll() {
    async function tryFetch(url) {
      const r = await fetch(url, { credentials: 'same-origin' });
      if (!r.ok) return null;
      const j = await r.json();
      return Array.isArray(j) ? j : (Array.isArray(j.stores) ? j.stores : null);
    }
    let s = null;
    // Preferisci endpoint dedicato Orari (sempre elenco completo, non filtrato)
    try { s = await tryFetch('/orari/api/stores-all'); } catch (e) { s = null; }
    if (!s) {
      // Fallback: endpoint magazzino completo
      try { s = await tryFetch('/magazzino/stores-json-all'); } catch (e) { s = null; }
    }
    if (!s) {
      // Ultimo fallback: elenco store filtrato per utente
      try { s = await tryFetch('/magazzino/stores-json'); } catch (e) { s = null; }
    }
    storesAll = s || [];
    document.querySelectorAll('select[data-k="s_prestito"], select[data-k="s_prestito2"]').forEach(sel => {
      const keep = sel.value || '';
      while (sel.options.length > 1) sel.remove(1);
      (storesAll || []).forEach(st => {
        let code = '';
        let label = '';
        if (typeof st === 'string') { code = st; label = st; }
        else if (st) {
          code = st.site || st.code || st.value || st.id || st.store || st.codice || '';
          label = st.label || st.name || st.nome || st.descrizione || st.title || code;
        }
        code = String(code || '').trim();
        if (!code) return;
        const opt = document.createElement('option');
        opt.value = code;
        opt.textContent = String(label || code);
        sel.appendChild(opt);
      });
      sel.value = keep;
    });
  }

  function closeAllColorPops() {
    document.querySelectorAll('.color-pop.show').forEach(p => p.classList.remove('show'));
    document.querySelectorAll('.row-color-pop.show').forEach(p => p.classList.remove('show'));
  }

  function closeAllEditingCells(exceptCell) {
    document.querySelectorAll('.cell-box.is-editing').forEach(cell => {
      if (exceptCell && cell === exceptCell) return;
      if (cell.querySelector('.is-invalid')) return;
      cell.classList.remove('is-editing');
      cell.classList.add('is-compact');
    });
  }

  function closeAllCausalePops(exceptWrap) {
    document.querySelectorAll('.causale-block.is-open').forEach(pop => {
      if (exceptWrap && pop === exceptWrap) return;
      pop.classList.remove('is-open');
      pop.style.display = 'none';
    });
  }

  function applyCellColor(cellBox, code) {
    const c = Number(code || 0);
    const item = COLOR_SWATCHES.find(x => x.code === c) || COLOR_SWATCHES[0];
    const val = item && item.value ? item.value : '';
    cellBox.style.background = val;
    cellBox.dataset.colore = String(c || 0);

    const sws = cellBox.querySelectorAll('.color-pop .color-swatch');
    sws.forEach(sw => sw.classList.toggle('sel', Number(sw.dataset.code || 0) === c));
  }

  function getRowCellsByName(nominativo) {
    const n = String(nominativo || '').trim();
    if (!n) return [];
    try {
      return Array.from(document.querySelectorAll(`.cell-box[data-nominativo="${cssEsc(n)}"]`));
    } catch (e) {
      return [];
    }
  }

  function applyRowColor(nominativo, code) {
    const cells = getRowCellsByName(nominativo);
    cells.forEach(cell => applyCellColor(cell, code));
    if (cells.length) {
      markDirty();
      const row = findRowContainer(cells[0]);
      if (row) updateRowTotals(row);
    }
  }

  function buildRowColorControl(nominativo) {
    const wrap = document.createElement('div');
    wrap.className = 'row-color-wrap mt-1';

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-outline-secondary btn-sm row-color-btn';
    btn.textContent = t('rowColor', 'Colore riga');

    const pop = document.createElement('div');
    pop.className = 'row-color-pop';

    const grid = document.createElement('div');
    grid.className = 'color-grid';

    {
      const s0 = document.createElement('div');
      s0.className = 'color-swatch reset';
      s0.dataset.code = '0';
      grid.appendChild(s0);
    }

    COLOR_SWATCHES.filter(x => x.code !== 0).forEach(sw => {
      const s = document.createElement('div');
      s.className = 'color-swatch';
      s.style.background = sw.value;
      s.dataset.code = String(sw.code);
      grid.appendChild(s);
    });

    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      closeAllColorPops();
      pop.classList.toggle('show');
    });

    pop.addEventListener('click', (e) => {
      e.stopPropagation();
      const sw = e.target && e.target.closest ? e.target.closest('.color-swatch') : null;
      if (!sw) return;
      const code = Number(sw.dataset && sw.dataset.code != null ? sw.dataset.code : 0);
      applyRowColor(nominativo, code);
      pop.classList.remove('show');
    });

    wrap.appendChild(btn);
    pop.appendChild(grid);
    wrap.appendChild(pop);
    return wrap;
  }

  function parseMoney(v) {
    const raw = String(v || '').trim();
    if (!raw) return null;
    let s = raw.replace(/€/g, '').replace(/\s/g, '');
    // Italian format: 1.234,56
    if (s.includes(',') && s.includes('.')) {
      s = s.replace(/\./g, '').replace(',', '.');
    } else if (s.includes(',') && !s.includes('.')) {
      s = s.replace(',', '.');
    }
    // keep only digits, dot, minus
    s = s.replace(/[^0-9.\-]/g, '');
    if (!s) return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  }

  function formatMoneyForInput(v) {
    const n = parseMoney(v);
    if (n == null) return '';
    const fixed = n.toFixed(2);
    return fixed.replace('.', ',');
  }

function normalizeTime(v) {
    const raw = String(v || '').trim();
    if (!raw) return '';
    const digits = raw.replace(/[^\d]/g, '');
    if (!digits) return '';
    let d = digits;
    if (d.length === 3) d = '0' + d;
    if (d.length !== 4) return '';
    const hh = parseInt(d.slice(0, 2), 10);
    const mm = parseInt(d.slice(2, 4), 10);
    if (!Number.isFinite(hh) || !Number.isFinite(mm)) return '';
    if (hh < 0 || hh > 23 || mm < 0 || mm > 59) return '';
    return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
  }

  
  function validateTimeRaw(raw) {
    const t = String(raw || '').trim();
    if (!t) return { norm: '', err: null };
    const norm = normalizeTime(t);
    if (!norm) return { norm: '', err: 'Orario non valido. Usa hh:mm.' };
    const mm = parseInt(norm.split(':')[1], 10);
    if (![0, 15, 30, 45].includes(mm)) return { norm, err: 'Minuti ammessi: 00, 15, 30, 45.' };
    return { norm, err: null };
  }

  // Valida il vincolo "fine non può essere prima dell'inizio".
  // Applica l'errore SOLO in seguito a blur (invocato da wireTimeInput),
  // così non compaiono errori durante la digitazione.
  function validateTimePairInCell(cell, pairKey) {
    if (!cell || !pairKey) return;
    const key = String(pairKey || '');
    const map = {
      'inizio_1': ['inizio_1', 'fine_1'],
      'fine_1': ['inizio_1', 'fine_1'],
      'inizio_2': ['inizio_2', 'fine_2'],
      'fine_2': ['inizio_2', 'fine_2'],
    };
    const pair = map[key];
    if (!pair) return;

    const inEl = cell.querySelector(`input[data-k="${pair[0]}"]`);
    const fiEl = cell.querySelector(`input[data-k="${pair[1]}"]`);
    if (!inEl || !fiEl) return;

    const vIn = validateTimeRaw(inEl.value);
    const vFi = validateTimeRaw(fiEl.value);

    // Se mancano i valori o ci sono errori di formato, non forziamo l'errore di coppia.
    // Se era rimasto un errore "fine prima dell'inizio" lo rimuoviamo.
    if (vIn.err || vFi.err || !vIn.norm || !vFi.norm) {
      const prev = String(fiEl.dataset.err || '');
      if (prev && prev.toLowerCase().includes('fine non può essere prima')) {
        clearTimeInvalid(fiEl);
      }
      return;
    }

    const r = duration(vIn.norm, vFi.norm, { overnightStartMin: 12 * 60, overnightEndMax: 8 * 60 });
    if (r.err) setTimeInvalid(fiEl, r.err);
    else clearTimeInvalid(fiEl);
  }

  function setTimeInvalid(inp, msg) {
    if (!inp) return;
    inp.classList.add('is-invalid');
    inp.title = msg || '';
    inp.dataset.err = msg || '1';
  }

  function clearTimeInvalid(inp) {
    if (!inp) return;
    inp.classList.remove('is-invalid');
    inp.title = '';
    delete inp.dataset.err;
  }

function minsFromTime(t) {
    const s = normalizeTime(t);
    if (!s) return null;
    const p = s.split(':');
    if (p.length < 2) return null;
    const h = parseInt(p[0], 10);
    const m = parseInt(p[1], 10);
    if (!Number.isFinite(h) || !Number.isFinite(m)) return null;
    return h * 60 + m;
  }

  function fmtHM(mins) {
    const sign = mins < 0 ? '-' : '';
    const a = Math.abs(mins);
    const h = Math.floor(a / 60);
    const m = a % 60;
    return `${sign}${h}:${String(m).padStart(2, '0')}`;
  }

  function fmtEuro(n) {
    if (n == null) return '';
    const v = Number(n);
    if (!Number.isFinite(v)) return '0,00';
    try {
      return v.toLocaleString('it-IT', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    } catch (e) {
      return v.toFixed(2).replace('.', ',');
    }
  }

  function scheduleStats() {
    if (statsRaf) cancelAnimationFrame(statsRaf);
    statsRaf = requestAnimationFrame(() => {
      statsRaf = null;
      updateGlobalStats();
    });
  }

  function updateGlobalStats() {
    let salesTot = 0;
    (daysMeta || []).forEach(d => {
      const inp = document.querySelector(`input.sales-inp[data-date="${d.date}"]`);
      const n = parseMoney(inp ? inp.value : '');
      if (n != null) salesTot += n;
    });
    let weekMins = 0;
    getRowContainers().forEach(tr => {
      const v = Number(tr.dataset.weekMinsProd || 0);
      if (Number.isFinite(v)) weekMins += v;
    });

    const hoursDec = weekMins > 0 ? (weekMins / 60) : 0;
    const prod = hoursDec > 0 ? (salesTot / hoursDec) : 0;

    if (elStatSales) elStatSales.textContent = `€ ${fmtEuro(salesTot) || '0,00'}`;
    if (elStatHours) elStatHours.textContent = fmtHM(weekMins);
    if (elStatProd) elStatProd.textContent = `€ ${fmtEuro(prod) || '0,00'}`;
  }

  function duration(inizio, fine, opts) {
    const a = minsFromTime(inizio);
    const b = minsFromTime(fine);
    if (a === null || b === null) return { mins: 0, err: null };
    if (b === a) return { mins: 0, err: null };
    if (b > a) return { mins: b - a, err: null };

    // b < a => possibile attraversamento mezzanotte
    const startMin = (opts && Number.isFinite(opts.overnightStartMin)) ? opts.overnightStartMin : (12 * 60);
    const endMax = (opts && Number.isFinite(opts.overnightEndMax)) ? opts.overnightEndMax : (8 * 60);
    if (a >= startMin && b <= endMax) {
      return { mins: (24 * 60 - a) + b, err: null };
    }
    return { mins: 0, err: "La fine non può essere prima dell'inizio." };
  }

  function _isProdShift(causaleValue) {
    const low = String(causaleValue || '').trim().toLowerCase();
    if (!low) return true;
    const rule = _causaleRule(causaleValue);
    if (rule) return !!rule.counts_productivity;
    const fallbackNonProd = new Set(['ferie', 'permesso', 'allattamento', 'off', 'prestito', 'malattia', 'training', 'riposo festivo']);
    return !fallbackNonProd.has(low);
  }

  
function computeCellTotals(cell) {
  const in1el = cell.querySelector('input[data-k="inizio_1"]');
  const fi1el = cell.querySelector('input[data-k="fine_1"]');
  const in2el = cell.querySelector('input[data-k="inizio_2"]');
  const fi2el = cell.querySelector('input[data-k="fine_2"]');

  const rawIn1 = in1el?.value || '';
  const rawFi1 = fi1el?.value || '';
  const rawIn2 = in2el?.value || '';
  const rawFi2 = fi2el?.value || '';

  const vIn1 = validateTimeRaw(rawIn1);
  const vFi1 = validateTimeRaw(rawFi1);
  const vIn2 = validateTimeRaw(rawIn2);
  const vFi2 = validateTimeRaw(rawFi2);

  let r1 = { mins: 0, err: null };
  let r2 = { mins: 0, err: null };

  if (!vIn1.err && !vFi1.err) r1 = duration(vIn1.norm, vFi1.norm, { overnightStartMin: 12*60, overnightEndMax: 8*60 });
  if (!vIn2.err && !vFi2.err) r2 = duration(vIn2.norm, vFi2.norm, { overnightStartMin: 12*60, overnightEndMax: 8*60 });

  const d1 = (r1.err || vIn1.err || vFi1.err) ? 0 : (r1.mins || 0);
  const d2 = (r2.err || vIn2.err || vFi2.err) ? 0 : (r2.mins || 0);
  const tot = d1 + d2;

  const elP1 = cell.querySelector('[data-k="parz_1"]');
  const elP2 = cell.querySelector('[data-k="parz_2"]');

  if (elP1) elP1.textContent = fmtHM(d1);
  if (elP2) elP2.textContent = fmtHM(d2);

  return tot;
}


function computeCellProdTotals(cell) {
  if (!cell) return 0;

  // Turno 1
  const caus1 = (cell.querySelector('select[data-k="causale"]')?.value || '').trim();
  const isProd1 = _isProdShift(caus1);
  const in1 = cell.querySelector('input[data-k="inizio_1"]')?.value || '';
  const fi1 = cell.querySelector('input[data-k="fine_1"]')?.value || '';
  const r1 = duration(in1, fi1, { allowCrossMidnight: true });
  const d1 = (isProd1 ? (r1.mins || 0) : 0);

  // Turno 2
  const caus2 = (cell.querySelector('select[data-k="causale2"]')?.value || '').trim();
  const isProd2 = _isProdShift(caus2);
  const in2 = cell.querySelector('input[data-k="inizio_2"]')?.value || '';
  const fi2 = cell.querySelector('input[data-k="fine_2"]')?.value || '';
  const r2 = duration(in2, fi2, { allowCrossMidnight: true });
  const d2 = (isProd2 ? (r2.mins || 0) : 0);

  return d1 + d2;
}


  function updateRowTotals(tr) {
    if (!tr) return;
    const cells = Array.from(tr.querySelectorAll('.cell-box'));
    let weekMins = 0;
    let weekMinsProd = 0;
    cells.forEach(c => {
      weekMins += computeCellTotals(c);
      weekMinsProd += computeCellProdTotals(c);
    });

    tr.dataset.weekMins = String(weekMins || 0);

    

    tr.dataset.weekMinsProd = String(weekMinsProd || 0);
const name = tr.dataset.nominativo || '';
    const st = staffByName(name);
    const contr = st ? (Number(st.ore_contrattuali || 0) * 60) : 0;

    const elContr = tr.querySelector('[data-k="contr"]');
    const elTot = tr.querySelector('[data-k="week_tot"]');
    const elDiff = tr.querySelector('[data-k="week_diff"]');

    if (elContr) elContr.textContent = contr ? fmtHM(contr) : '0:00';
    if (elTot) elTot.textContent = fmtHM(weekMins);

    const diff = weekMins - contr;
    if (elDiff) {
      elDiff.textContent = fmtHM(diff);
      elDiff.classList.toggle('text-danger', diff < 0);
    }

    scheduleStats();
  }

  function renderMobileSales() {
    if (!elMobileSales) return;
    elMobileSales.innerHTML = '';
    if (!daysMeta || !daysMeta.length) return;

    const card = document.createElement('div');
    card.className = 'card orari-mobile-sales';

    let salesTot = 0;
    (daysMeta || []).forEach(d => {
      const v = (salesCache && salesCache[d.date] != null) ? Number(salesCache[d.date]) : null;
      if (Number.isFinite(v)) salesTot += v;
    });

    const hdr = document.createElement('div');
    hdr.className = 'card-header py-2 orari-mobile-toggle';
    hdr.setAttribute('aria-expanded', mobileSalesExpanded ? 'true' : 'false');
    hdr.setAttribute('role', 'button');
    hdr.tabIndex = 0;
    hdr.innerHTML = `
      <div class="orari-mobile-toggle__main">
        <div class="fw-semibold">${esc(t('forecasts', 'Previsioni'))}</div>
        <div class="small text-muted">${esc(t('netWeekForecast', 'Previsione netta settimana'))}</div>
      </div>
      <div class="orari-mobile-toggle__side">
        <div class="orari-mobile-toggle__value">€ ${fmtEuro(salesTot)}</div>
        <div class="orari-mobile-toggle__chev">${mobileSalesExpanded ? '−' : '+'}</div>
      </div>
    `;
    card.appendChild(hdr);

    const body = document.createElement('div');
    body.className = 'card-body py-2';
    body.style.display = mobileSalesExpanded ? '' : 'none';

    (daysMeta || []).forEach(d => {
      const row = document.createElement('div');
      row.className = 'day-row';

      const lbl = document.createElement('div');
      const cls = ['day-lbl'];
      if (d.is_holiday) cls.push('holiday');
      if (d.is_sunday) cls.push('sunday');
      lbl.className = cls.join(' ');
      lbl.textContent = translateDayLabel(d.label || d.date);

      const ig = document.createElement('div');
      ig.className = 'input-group input-group-sm';
      ig.style.maxWidth = '220px';
      ig.innerHTML = `<span class="input-group-text">€</span><input type="text" class="form-control form-control-sm sales-inp sales-inp-mobile" data-date="${esc(d.date)}" inputmode="decimal" autocomplete="off">`;

      const inp = ig.querySelector('input.sales-inp');
      const val = (salesCache && salesCache[d.date] != null) ? String(salesCache[d.date]) : '';
      if (inp) {
        inp.value = formatMoneyForInput(val);
        inp.addEventListener('input', () => {
          markDirty();
          inp.classList.remove('is-invalid');
          scheduleStats();
        });
        inp.addEventListener('blur', () => {
          const n = parseMoney(inp.value);
          inp.value = (n == null) ? '' : formatMoneyForInput(String(n));
          scheduleStats();
        });
      }

      const right = document.createElement('div');
      right.className = 'd-flex flex-column align-items-end gap-1';

      const prev = document.createElement('div');
      prev.className = 'small text-muted text-end';
      const info = (prevYearCache && typeof prevYearCache === 'object') ? prevYearCache[d.date] : null;
      const net = info && (info.net != null) ? Number(info.net) : null;
      const ad = info && info.aligned_date ? String(info.aligned_date) : '';
      prev.title = ad ? (t('alignedTo', 'Allineato a') + ' ' + ad) : '';
      prev.textContent = (net == null || !Number.isFinite(net)) ? '' : (t('previousYearShort', 'Anno prec.') + ': € ' + fmtEuro(net));

      right.appendChild(ig);
      if (prev.textContent) right.appendChild(prev);

      row.appendChild(lbl);
      row.appendChild(right);
      body.appendChild(row);
    });

    hdr.addEventListener('click', function () {
      mobileSalesExpanded = !mobileSalesExpanded;
      renderHeaders();
      scheduleStats();
    });
    hdr.addEventListener('keydown', function (e) {
      if (!e || (e.key !== 'Enter' && e.key !== ' ')) return;
      e.preventDefault();
      hdr.click();
    });

    card.appendChild(body);
    elMobileSales.appendChild(card);
  }

  function renderHeaders() {
    if (!elDaysRow || !elSalesRow || !elPrevYearRow) return;

    if (isMobileView()) {
      // clear desktop header rows to avoid duplicate inputs
      elSalesRow.innerHTML = '';
      elPrevYearRow.innerHTML = '';
      elDaysRow.innerHTML = '';
      renderMobileSales();
      return;
    }

    if (elMobileSales) elMobileSales.innerHTML = '';

    // Sales row
    elSalesRow.innerHTML = `<th class="sticky-col name-col sales-head"><div class="fw-semibold small">${esc(t('netForecast', 'Previsione netta'))}</div></th>`;
    daysMeta.forEach(d => {
      const th = document.createElement('th');
      th.className = 'sales-head day-col';
      const val = (salesCache && salesCache[d.date] != null) ? String(salesCache[d.date]) : '';
      th.innerHTML = `<div class="input-group input-group-sm sales-ig"><span class="input-group-text">€</span><input type="text" class="form-control form-control-sm sales-inp" data-date="${esc(d.date)}" inputmode="decimal" autocomplete="off"></div>`;
      elSalesRow.appendChild(th);
      const inp = th.querySelector('input.sales-inp');
      if (inp) {
        inp.value = formatMoneyForInput(val);
        inp.addEventListener('input', () => {
          markDirty();
          inp.classList.remove('is-invalid');
          scheduleStats();
        });
        inp.addEventListener('blur', () => {
          const n = parseMoney(inp.value);
          inp.value = (n == null) ? '' : formatMoneyForInput(String(n));
          scheduleStats();
        });
      }
    });

    // Prev year row
    elPrevYearRow.innerHTML = `<th class="sticky-col name-col sales-head"><div class="small text-muted fw-semibold">${esc(t('previousYearNet', 'Anno precedente netto'))}</div></th>`;
    daysMeta.forEach(d => {
      const th = document.createElement('th');
      th.className = 'sales-head day-col';
      const info = (prevYearCache && typeof prevYearCache === 'object') ? prevYearCache[d.date] : null;
      const net = info && (info.net != null) ? Number(info.net) : null;
      const ad = info && info.aligned_date ? String(info.aligned_date) : '';
      const txt = (net == null || !Number.isFinite(net)) ? '' : `€ ${fmtEuro(net)}`;
      th.innerHTML = `<div class="small text-muted text-end" title="${esc(ad ? (t('alignedTo', 'Allineato a') + ' ' + ad) : '')}">${esc(txt)}</div>`;
      elPrevYearRow.appendChild(th);
    });

    // Days row
    elDaysRow.innerHTML = `<th class="sticky-col name-col">${esc(t('personName', 'Nominativo'))}</th>`;
    daysMeta.forEach(d => {
      const th = document.createElement('th');
      const cls = ['day-head', 'day-col'];
      if (d.is_holiday) cls.push('holiday');
      if (d.is_sunday) cls.push('sunday');
      th.className = cls.join(' ');
      th.textContent = translateDayLabel(d.label);
      elDaysRow.appendChild(th);
    });
  }


  function buildColorPicker(cellBox, selectedCode) {
    const pop = document.createElement('div');
    pop.className = 'color-pop';

    const grid = document.createElement('div');
    grid.className = 'color-grid';


    // reset (code 0)
    {
      const s0 = document.createElement('div');
      s0.className = 'color-swatch reset' + (Number(selectedCode || 0) === 0 ? ' sel' : '');
      s0.dataset.code = '0';
      grid.appendChild(s0);
    }

    COLOR_SWATCHES.filter(x => x.code !== 0).forEach(sw => {
      const s = document.createElement('div');
      s.className = 'color-swatch' + (Number(selectedCode) === sw.code ? ' sel' : '');
      s.style.background = sw.value;
      s.dataset.code = String(sw.code);
      grid.appendChild(s);
    });

    pop.appendChild(grid);

    pop.addEventListener('click', (e) => {
      e.stopPropagation();
      const sw = e.target && e.target.closest ? e.target.closest('.color-swatch') : null;
      if (!sw) return;
      const code = Number(sw.dataset && sw.dataset.code != null ? sw.dataset.code : 0);
      applyCellColor(cellBox, code);
      pop.classList.remove('show');
      markDirty();
      updateRowTotals(findRowContainer(cellBox));
    });

    cellBox.appendChild(pop);
    applyCellColor(cellBox, selectedCode || 0);
  }

  function buildTimeInput(key, value) {
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.className = 'form-control form-control-sm t-inp';
    inp.dataset.k = key;
    // no placeholder
    inp.inputMode = 'numeric';
    inp.autocomplete = 'off';
    inp.value = normalizeTime(value) || '';
    return inp;
  }

  function wireTimeInput(inp, onChanged) {
    inp.addEventListener('input', () => {
      markDirty();
      // Durante la digitazione NON mostriamo errori.
      // Se c'era un errore, lo rimuoviamo così l'utente non vede il bordo rosso mentre completa l'orario.
      clearTimeInvalid(inp);
      onChanged();
    });
    inp.addEventListener('blur', () => {
      const res = validateTimeRaw(inp.value);
      inp.value = res.norm || '';
      if (res.err) setTimeInvalid(inp, res.err);
      else clearTimeInvalid(inp);

      // Validazione coppia inizio/fine: errore solo dopo blur.
      const cell = inp.closest ? inp.closest('.cell-box') : null;
      if (cell) validateTimePairInCell(cell, inp.dataset.k);
      onChanged();
    });
    inp.addEventListener('keydown', (ev) => {
      if (!ev) return;
      if (ev.key === 'Enter') {
        ev.preventDefault();
        try { inp.blur(); } catch (e) {}
      }
    });
  }


  
function buildCell(nominativo, dayIso, preset) {
  const box = document.createElement('div');
  box.className = 'cell-box is-compact';
  box.dataset.nominativo = nominativo;
  box.dataset.data = dayIso;
  box.dataset.colore = String(Number(preset.colore || 0));

  buildColorPicker(box, Number(preset.colore || 0));

  function fillStoreOptions(sel) {
    // keep first empty option
    while (sel.options.length > 1) sel.remove(1);
    (storesAll || []).forEach(s => {
      let code = '';
      let label = '';
      if (typeof s === 'string') {
        code = s;
        label = s;
      } else if (s) {
        code = s.site || s.code || s.value || s.id || s.store || s.codice || '';
        label = s.label || s.name || s.nome || s.descrizione || s.title || code;
      }
      code = String(code || '').trim();
      if (!code) return;
      const opt = document.createElement('option');
      opt.value = code;
      opt.textContent = String(label || code);
      sel.appendChild(opt);
    });
  }

  function applyStoreFit(sel) {
    const idx = sel.selectedIndex;
    const txt = (idx >= 0 ? (sel.options[idx]?.textContent || '') : '');
    const t = String(txt || '').trim();
    sel.title = t;
    sel.classList.toggle('store-small', t.length > 18);
    sel.classList.toggle('store-xsmall', t.length > 28);
  }

  function buildCausalePrestitoBlock(opts) {
    const causKey = opts.causKey;
    const prestKey = opts.prestKey;
    const causValue = opts.causValue || '';
    const prestValue = opts.prestValue || '';
    const causPlaceholder = opts.causPlaceholder || '';
    const prestPlaceholder = opts.prestPlaceholder || '';

    let presetApplied = false;
    let userOpened = false;
    let toggleEl = null;

    const wrap = document.createElement('div');
    wrap.className = 'causale-block causale-popup';
    wrap.style.display = 'none';

    const content = document.createElement('div');
    content.className = 'causale-content';

    const selC = document.createElement('select');
    selC.className = 'form-select form-select-sm';
    selC.dataset.k = causKey;

    const o0 = document.createElement('option');
    o0.value = '';
    o0.textContent = causPlaceholder;
    selC.appendChild(o0);

    const causaliOptions = (orariCausali && orariCausali.length)
      ? orariCausali.map(r => String((r && r.name) || '').trim()).filter(Boolean)
      : ['Ferie', 'Permesso', 'Allattamento', 'Malattia', 'Riposo Festivo', 'Off', 'Prestito', 'Training'];
    causaliOptions.forEach(v => {
      const o = document.createElement('option');
      o.value = v;
      o.textContent = v;
      selC.appendChild(o);
    });

    const prestWrap = document.createElement('div');
    prestWrap.className = 'mt-1 prestito';

    const prestSel = document.createElement('select');
    prestSel.className = 'form-select form-select-sm prestito-store';
    prestSel.dataset.k = prestKey;

    const p0 = document.createElement('option');
    p0.value = '';
    p0.textContent = prestPlaceholder;
    prestSel.appendChild(p0);

    prestWrap.appendChild(prestSel);

    // initial values
    selC.value = causValue;
    prestSel.value = prestValue;

    function hasAnyValue() {
      return !!String(selC.value || '').trim() || !!String(prestSel.value || '').trim();
    }

    function placePopup() {
      if (!toggleEl || !wrap || !wrap.parentElement) return;
      const hostRect = wrap.parentElement.getBoundingClientRect();
      const toggleRect = toggleEl.getBoundingClientRect();
      const top = Math.max(8, (toggleRect.bottom - hostRect.top) + 6);
      const left = Math.max(6, (toggleRect.left - hostRect.left) - 54);
      wrap.style.top = top + 'px';
      wrap.style.left = left + 'px';
    }

    function updateVisibility() {
      const show = !!userOpened;
      wrap.style.display = show ? 'block' : 'none';
      wrap.classList.toggle('is-open', show);
      if (show) placePopup();
    }

    function refresh() {
      const vv = String(selC.value || '').trim().toLowerCase();
      const isPrest = (vv === 'prestito');

      prestWrap.style.display = isPrest ? 'block' : 'none';
      prestSel.required = isPrest;

      if (isPrest && prestSel.options.length <= 1) {
        fillStoreOptions(prestSel);
        if (!presetApplied && prestValue) {
          prestSel.value = prestValue;
          presetApplied = true;
        }
      } else if (isPrest && !presetApplied && prestValue) {
        // In caso di reload/ricostruzione cella: ripristina il preset una sola volta
        prestSel.value = prestValue;
        presetApplied = true;
      }

      if (!isPrest) {
        prestSel.classList.remove('is-invalid');
        prestSel.value = '';
      }

      prestSel.classList.toggle('is-invalid', (isPrest && !prestSel.value));
      applyStoreFit(prestSel);

      updateVisibility();
    }

    function closePopup() {
      userOpened = false;
      updateVisibility();
    }

    function showPopup() {
      userOpened = true;
      updateVisibility();
    }

    function bindToggle(el) {
      toggleEl = el || null;
      if (!toggleEl) {
        updateVisibility();
        return;
      }

      // Accessibilità
      try {
        toggleEl.setAttribute('aria-label', t('reason', 'Causale'));
        toggleEl.setAttribute('title', t('reason', 'Causale'));
      } catch (e) { }

      const onToggle = (ev) => {
        if (ev) {
          ev.preventDefault();
          ev.stopPropagation();
        }

        closeAllCausalePops(userOpened ? null : wrap);
        userOpened = !userOpened;
        updateVisibility();
        if (userOpened) {
          try { box.dataset.keepOpen = '1'; } catch (e) {}
          setTimeout(() => { try { selC.focus(); } catch (e) { } }, 0);
          setTimeout(() => { try { delete box.dataset.keepOpen; } catch (e) {} }, 80);
        }
      };

      toggleEl.addEventListener('click', onToggle);
      toggleEl.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') onToggle(ev);
      });

      updateVisibility();
    }

    // first refresh
    refresh();

    content.appendChild(selC);
    content.appendChild(prestWrap);

    wrap.appendChild(content);

    return { wrap, selC, prestWrap, prestSel, refresh, bindToggle, closePopup, showPopup, hasAnyValue };
  }


  const summaryWrap = document.createElement('div');
  summaryWrap.className = 'turn-summary-wrap';

  function buildTurnSummary() {
      const row = document.createElement('div');
      row.className = 'turn-summary';

      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'turn-summary__chip';

      const text = document.createElement('span');
      text.className = 'turn-summary__time';
      chip.appendChild(text);

      const mobileBadge = document.createElement('span');
      mobileBadge.className = 'turn-summary__mobile-badge';
      chip.appendChild(mobileBadge);

      const mins = document.createElement('span');
      mins.className = 'turn-summary__mins';

    row.appendChild(chip);
    row.appendChild(mins);
    summaryWrap.appendChild(row);

    function syncChipLabel() {
      const primary = String(chip.dataset.primaryLabel || '');
      const hover = String(chip.dataset.hoverLabel || '');
      const showHover = chip.classList.contains('show-hover') && hover;
      text.textContent = showHover ? hover : primary;
      mobileBadge.textContent = hover || '';
      mobileBadge.style.display = hover ? '' : 'none';
      chip.title = hover || primary || '';
    }

    chip.addEventListener('mouseenter', function () {
      if (!chip.dataset.hoverLabel) return;
      chip.classList.add('show-hover');
      syncChipLabel();
    });
    chip.addEventListener('mouseleave', function () {
      chip.classList.remove('show-hover');
      syncChipLabel();
    });
    chip.addEventListener('focus', function () {
      if (!chip.dataset.hoverLabel) return;
      chip.classList.add('show-hover');
      syncChipLabel();
    });
    chip.addEventListener('blur', function () {
      chip.classList.remove('show-hover');
      syncChipLabel();
    });

    return { row, chip, text, mins, mobileBadge, syncChipLabel };
  }

  const s1 = buildTurnSummary();
  const s2 = buildTurnSummary();
  box.appendChild(summaryWrap);

  function formatTurnBadge(causaleValue, storeCode) {
    const raw = String(causaleValue || '').trim();
    if (!raw) return '';
    const low = raw.toLowerCase();
    if (low === 'prestito') {
      const code = String(storeCode || '').trim();
      if (!code) return '';
      const found = (storesAll || []).find(function (s) {
        const sCode = String((s && (s.site || s.code || s.value || s.id || s.store || s.codice)) || '').trim();
        return sCode === code;
      });
      return String((found && (found.label || found.name || found.nome || found.descrizione || found.title)) || code).trim();
    }
    return raw;
  }

  function refreshCompactSummary() {
    const norm = normalizeTime;
    const t1a = norm(in1.value || '');
    const t1b = norm(fi1.value || '');
    const t2a = norm(in2.value || '');
    const t2b = norm(fi2.value || '');

    const badge1 = formatTurnBadge(b1.selC.value, b1.prestSel.value);
    const badge2 = formatTurnBadge(b2.selC.value, b2.prestSel.value);

    const d1 = duration(t1a, t1b, { overnightStartMin: 12 * 60, overnightEndMax: 8 * 60 });
    const d2 = duration(t2a, t2b, { overnightStartMin: 12 * 60, overnightEndMax: 8 * 60 });
    const m1 = (d1 && !d1.err && Number.isFinite(d1.mins)) ? fmtHM(d1.mins) : '';
    const m2 = (d2 && !d2.err && Number.isFinite(d2.mins)) ? fmtHM(d2.mins) : '';

    const txt1 = (t1a && t1b) ? (t1a + '–' + t1b) : (badge1 || '—');
    const txt2 = (t2a && t2b) ? (t2a + '–' + t2b) : (badge2 || '—');
    const hover1 = (t1a && t1b && badge1) ? badge1 : '';
    const hover2 = (t2a && t2b && badge2) ? badge2 : '';

    s1.chip.dataset.primaryLabel = txt1;
    s1.chip.dataset.hoverLabel = hover1;
    s2.chip.dataset.primaryLabel = txt2;
    s2.chip.dataset.hoverLabel = hover2;
    s1.syncChipLabel();
    s2.syncChipLabel();
    s1.text.classList.toggle('turn-summary__empty', !(t1a && t1b) && !badge1);
    s2.text.classList.toggle('turn-summary__empty', !(t2a && t2b) && !badge2);
    s1.chip.classList.toggle('has-badge', !!badge1);
    s2.chip.classList.toggle('has-badge', !!badge2);
    s1.chip.classList.toggle('has-hover', !!hover1);
    s2.chip.classList.toggle('has-hover', !!hover2);
    s1.mins.textContent = m1 || '\u00A0';
    s2.mins.textContent = m2 || '\u00A0';
    s1.mins.classList.toggle('is-empty', !m1);
    s2.mins.classList.toggle('is-empty', !m2);
  }

  function closeTurnEditors() {
    box.querySelectorAll('.turn-editor.is-open').forEach(function (el) {
      if (el.querySelector('.is-invalid')) return;
      el.classList.remove('is-open');
    });
    b1.closePopup();
    b2.closePopup();
  }

  function openTurnEditor(editorEl, focusEl, causaleBlock) {
    closeAllColorPops();
    closeAllCausalePops();
    document.querySelectorAll('.turn-editor.is-open').forEach(function (el) {
      if (el !== editorEl && !el.querySelector('.is-invalid')) el.classList.remove('is-open');
    });
    editorEl.classList.add('is-open');
    if (causaleBlock && typeof causaleBlock.hasAnyValue === 'function' && causaleBlock.hasAnyValue()) {
      causaleBlock.showPopup();
    }
    try { focusEl.focus({ preventScroll: true }); } catch (e) {
      try { focusEl.focus(); } catch (ex) {}
    }
    try { focusEl.select(); } catch (e) {}
    window.setTimeout(function () {
      try { focusEl.focus({ preventScroll: true }); } catch (e) {
        try { focusEl.focus(); } catch (ex) {}
      }
      try { focusEl.select(); } catch (e) {}
    }, 0);

    const ownerCell = editorEl.closest ? editorEl.closest('.cell-box') : null;
    function ensureEditorVisible() {
      const target = ownerCell || editorEl;
      if (!target || !target.scrollIntoView) return;
      try {
        target.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
      } catch (e) {
        try { target.scrollIntoView(true); } catch (ex) {}
      }
    }
    ensureEditorVisible();
    window.setTimeout(ensureEditorVisible, 120);
    window.setTimeout(ensureEditorVisible, 320);
  }

  // Turno 1
  const t1 = document.createElement('div');
  t1.className = 'turn turn-editor';
  t1.dataset.slot = '1';

  const t1row = document.createElement('div');
  t1row.className = 't-row';

  const in1 = buildTimeInput('inizio_1', preset.inizio_1);
  const fi1 = buildTimeInput('fine_1', preset.fine_1);

  t1row.appendChild(in1);

  const dash1 = document.createElement('button');
  dash1.type = 'button';
  dash1.className = 'dash dash-toggle';
  dash1.textContent = '-';
  t1row.appendChild(dash1);

  t1row.appendChild(fi1);

  const p1 = document.createElement('span');
  p1.className = 't-dur-inline';
  p1.dataset.k = 'parz_1';
  p1.textContent = '0:00';
  t1row.appendChild(p1);

  t1.appendChild(t1row);

  const b1 = buildCausalePrestitoBlock({
    causKey: 'causale',
    prestKey: 's_prestito',
    causValue: preset.causale || '',
    prestValue: preset.s_prestito || '',
    causPlaceholder: t('reason', 'Causale'),
    prestPlaceholder: t('loanStore', 'Store prestito')
  });
  b1.bindToggle(dash1);
  t1.appendChild(b1.wrap);

  // Turno 2
  const t2 = document.createElement('div');
  t2.className = 'turn turn-editor';
  t2.dataset.slot = '2';

  const t2row = document.createElement('div');
  t2row.className = 't-row';

  const in2 = buildTimeInput('inizio_2', preset.inizio_2);
  const fi2 = buildTimeInput('fine_2', preset.fine_2);

  t2row.appendChild(in2);

  const dash2 = document.createElement('button');
  dash2.type = 'button';
  dash2.className = 'dash dash-toggle';
  dash2.textContent = '-';
  t2row.appendChild(dash2);

  t2row.appendChild(fi2);

  const p2 = document.createElement('span');
  p2.className = 't-dur-inline';
  p2.dataset.k = 'parz_2';
  p2.textContent = '0:00';
  t2row.appendChild(p2);

  t2.appendChild(t2row);

  const b2 = buildCausalePrestitoBlock({
    causKey: 'causale2',
    prestKey: 's_prestito2',
    causValue: preset.causale2 || '',
    prestValue: preset.s_prestito2 || '',
    causPlaceholder: t('reason', 'Causale'),
    prestPlaceholder: t('loanStore', 'Store prestito')
  });
  b2.bindToggle(dash2);
  t2.appendChild(b2.wrap);

  box.appendChild(t1);
  box.appendChild(t2);

  s1.chip.addEventListener('click', function (e) {
    e.preventDefault();
    e.stopPropagation();
    openTurnEditor(t1, in1, b1);
  });
  s2.chip.addEventListener('click', function (e) {
    e.preventDefault();
    e.stopPropagation();
    openTurnEditor(t2, in2, b2);
  });

  // Wire changes (orari)
  wireTimeInput(in1, () => { computeCellTotals(box); updateRowTotals(findRowContainer(box)); refreshCompactSummary(); });
  wireTimeInput(fi1, () => { computeCellTotals(box); updateRowTotals(findRowContainer(box)); refreshCompactSummary(); });
  wireTimeInput(in2, () => { computeCellTotals(box); updateRowTotals(findRowContainer(box)); refreshCompactSummary(); });
  wireTimeInput(fi2, () => { computeCellTotals(box); updateRowTotals(findRowContainer(box)); refreshCompactSummary(); });

  function bindTurnFlow(startInp, endInp, editorEl) {
    function ensureVisible(inp) {
      inp.addEventListener('focus', function () {
        const ownerCell = editorEl.closest ? editorEl.closest('.cell-box') : null;
        const target = ownerCell || editorEl;
        window.setTimeout(function () {
          if (!target || !target.scrollIntoView) return;
          try {
            target.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
          } catch (e) {
            try { target.scrollIntoView(true); } catch (ex) {}
          }
        }, 80);
      });
    }

    function normalize4Digits(inp, nextInp) {
      const raw = String(inp.value || '').replace(/\D/g, '');
      if (raw.length !== 4) return;
      inp.value = `${raw.slice(0, 2)}:${raw.slice(2, 4)}`;
      try { inp.dispatchEvent(new Event('blur')); } catch (e) {}
      if (nextInp) {
        window.setTimeout(function () {
          try { nextInp.focus(); } catch (e) {}
          try { nextInp.select(); } catch (e) {}
        }, 0);
      }
    }

    startInp.addEventListener('input', function () {
      normalize4Digits(startInp, endInp);
    });
    endInp.addEventListener('input', function () {
      normalize4Digits(endInp, null);
    });
    ensureVisible(startInp);
    ensureVisible(endInp);

    [startInp, endInp].forEach(function (inp) {
      inp.addEventListener('keydown', function (ev) {
        if (!ev || ev.key !== 'Enter') return;
        ev.preventDefault();
        try { inp.blur(); } catch (e) {}
        window.setTimeout(function () {
          if (!editorEl.querySelector('.is-invalid')) editorEl.classList.remove('is-open');
        }, 0);
      });
    });

  }

  bindTurnFlow(in1, fi1, t1);
  bindTurnFlow(in2, fi2, t2);

  function bindCommitClose(control, editorEl) {
    control.addEventListener('keydown', function (ev) {
      if (!ev || ev.key !== 'Enter') return;
      ev.preventDefault();
      window.setTimeout(function () {
        if (!editorEl.querySelector('.is-invalid')) editorEl.classList.remove('is-open');
      }, 0);
    });
  }

  bindCommitClose(b1.selC, t1);
  bindCommitClose(b1.prestSel, t1);
  bindCommitClose(b2.selC, t2);
  bindCommitClose(b2.prestSel, t2);

  // Wire changes (causali)
  b1.selC.addEventListener('change', () => {
    markDirty();
    b1.refresh();
    refreshCompactSummary();
  });
  b2.selC.addEventListener('change', () => {
    markDirty();
    b2.refresh();
    refreshCompactSummary();
  });

  b1.prestSel.addEventListener('change', () => {
    markDirty();
    b1.prestSel.classList.toggle('is-invalid', (String(b1.selC.value || '').trim().toLowerCase() === 'prestito' && !b1.prestSel.value));
    applyStoreFit(b1.prestSel);
    refreshCompactSummary();
  });
  b2.prestSel.addEventListener('change', () => {
    markDirty();
    b2.prestSel.classList.toggle('is-invalid', (String(b2.selC.value || '').trim().toLowerCase() === 'prestito' && !b2.prestSel.value));
    applyStoreFit(b2.prestSel);
    refreshCompactSummary();
  });

  // compute totals initial
  computeCellTotals(box);
  refreshCompactSummary();

  // Copy/Paste giornata (casella)
    const isInteractive = (el) => {
      if (!el) return false;
      const tag = String(el.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'select' || tag === 'textarea' || tag === 'button' || tag === 'a') return true;
      return !!(el.closest && el.closest('input,select,textarea,button,a,.color-pop'));
    };
  
    // SHIFT+click per incollare rapidamente (desktop)
    box.addEventListener('click', (e) => {
      if (!e || !e.shiftKey) return;
      if (!cellClipboard) return;
      if (isInteractive(e.target)) return;
      e.preventDefault();
      e.stopPropagation();
      hideCellMenu();
      closeAllColorPops();
      pasteClipboardToCell(box);
    }, true);
  
    // Tasto destro: menu copia/incolla
    box.addEventListener('contextmenu', (e) => {
    if (!e) return;
    if (e.target && e.target.closest && e.target.closest('.color-pop')) return;
      e.preventDefault();
      e.stopPropagation();
      closeAllColorPops();
      showCellMenuAt(box, e.clientX || 0, e.clientY || 0);
    });
  
    // Pressione lunga (touch): menu copia/incolla
    box.addEventListener('pointerdown', (e) => {
      if (!e) return;
      if (isInteractive(e.target)) return;
      const pt = String(e.pointerType || '');
      if (pt && pt !== 'touch' && pt !== 'pen') return;
      longPressPos = { x: e.clientX || 0, y: e.clientY || 0 };
      try { clearTimeout(longPressTimer); } catch (ex) { }
      longPressTimer = setTimeout(() => {
        try {
          closeAllColorPops();
          showCellMenuAt(box, ((longPressPos && longPressPos.x) ? longPressPos.x : 0), ((longPressPos && longPressPos.y) ? longPressPos.y : 0));
        } catch (ex) { }
      }, 450);
    });
  
    box.addEventListener('pointerup', () => { try { clearTimeout(longPressTimer); } catch (e) { } longPressTimer = null; });
    box.addEventListener('pointercancel', () => { try { clearTimeout(longPressTimer); } catch (e) { } longPressTimer = null; });
    box.addEventListener('pointermove', (e) => {
      if (!longPressTimer || !longPressPos) return;
      const dx = Math.abs((e.clientX || 0) - (longPressPos.x || 0));
      const dy = Math.abs((e.clientY || 0) - (longPressPos.y || 0));
      if (dx > 10 || dy > 10) {
        try { clearTimeout(longPressTimer); } catch (ex) { }
        longPressTimer = null;
      }
    });

    box.addEventListener('focusout', function () {
      window.setTimeout(function () {
        if (String(box.dataset.keepOpen || '') === '1') return;
        const active = document.activeElement;
        if (active && box.contains(active)) return;
        closeTurnEditors();
      }, 0);
    });
  
    return box;
  }


  function buildRowsDesktop(noms) {
    if (!elBody) return;
    elBody.innerHTML = '';

    if (!noms.length) {
      setStatus(t('selectPersonWarning', 'Seleziona almeno una persona.'));
      return;
    }
    setStatus('');

    noms.forEach(n => {
      const tr = document.createElement('tr');
      tr.dataset.nominativo = n;

      const tdName = document.createElement('td');
      tdName.className = 'sticky-col name-col';
      tdName.innerHTML = `<div class="name-wrap"><div class="name-text fw-semibold text-truncate">${esc(n)}</div><div class="name-metrics"><div class="met" data-k="contr"></div><div class="met" data-k="week_tot"></div><div class="met diff" data-k="week_diff"></div></div></div>`;
      const nameWrap = tdName.querySelector('.name-wrap');
      if (nameWrap) nameWrap.insertBefore(buildRowColorControl(n), nameWrap.querySelector('.name-metrics'));
      tr.appendChild(tdName);

      daysMeta.forEach(d => {
        const td = document.createElement('td');
        td.className = 'day-col';
        const key = `${n}|${d.date}`;
        const preset = cache.get(key) || { causale: '', causale2: '', inizio_1: '', fine_1: '', inizio_2: '', fine_2: '', s_prestito: '', s_prestito2: '', colore: 0 };
        const cell = buildCell(n, d.date, preset);
        td.appendChild(cell);
        tr.appendChild(td);
      });

      elBody.appendChild(tr);
      updateRowTotals(tr);
    });
  }

  function buildRowsMobile(noms) {
    if (!elMobile) return;
    elMobile.innerHTML = '';

    if (!noms.length) {
      setStatus(t('selectPersonWarning', 'Seleziona almeno una persona.'));
      return;
    }
    setStatus('');

    noms.forEach(n => {
      const card = document.createElement('div');
      card.className = 'card mb-3 orari-staff-card';
      card.dataset.row = '1';
      card.dataset.nominativo = n;
      const isExpanded = mobileExpandedStaff.has(n);

      const hdr = document.createElement('div');
      hdr.className = 'card-header py-2 orari-mobile-toggle';
      hdr.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
      hdr.setAttribute('role', 'button');
      hdr.tabIndex = 0;
      hdr.innerHTML = `
        <div class="d-flex align-items-start justify-content-between gap-2 orari-staff-head">
          <div class="orari-staff-namebox">
            <div class="fw-semibold text-truncate">${esc(n)}</div>
          </div>
          <div class="orari-mobile-toggle__side">
            <div class="name-metrics">
            <div class="met" data-k="contr"></div>
            <div class="met" data-k="week_tot"></div>
            <div class="met diff" data-k="week_diff"></div>
            </div>
            <div class="orari-mobile-toggle__chev">${isExpanded ? '−' : '+'}</div>
          </div>
        </div>
      `;
      const nameBox = hdr.querySelector('.orari-staff-namebox');
      if (nameBox) nameBox.appendChild(buildRowColorControl(n));
      card.appendChild(hdr);

      const body = document.createElement('div');
      body.className = 'card-body py-2';
      body.style.display = isExpanded ? '' : 'none';

      (daysMeta || []).forEach(d => {
        const day = document.createElement('div');
        day.className = 'border rounded-3 p-2 mb-2 orari-day-card';

        const tCls = ['day-title'];
        if (d.is_holiday) tCls.push('holiday');
        if (d.is_sunday) tCls.push('sunday');

        const top = document.createElement('div');
        top.className = 'd-flex align-items-center justify-content-between mb-2';
        top.innerHTML = `<div class="${tCls.join(' ')}">${esc(translateDayLabel(d.label || d.date))}</div>`;

        const key = `${n}|${d.date}`;
        const preset = cache.get(key) || { causale: '', causale2: '', inizio_1: '', fine_1: '', inizio_2: '', fine_2: '', s_prestito: '', s_prestito2: '', colore: 0 };
        const cell = buildCell(n, d.date, preset);

        day.appendChild(top);
        day.appendChild(cell);
        body.appendChild(day);
      });

      card.appendChild(body);
      elMobile.appendChild(card);
      updateRowTotals(card);

      hdr.addEventListener('click', function (e) {
        if (e && e.target && e.target.closest && e.target.closest('.row-color-btn, .row-color-pop, .color-swatch')) return;
        if (mobileExpandedStaff.has(n)) mobileExpandedStaff.delete(n);
        else mobileExpandedStaff.add(n);
        buildRows(lastTurni);
        scheduleStats();
      });
      hdr.addEventListener('keydown', function (e) {
        if (!e || (e.key !== 'Enter' && e.key !== ' ')) return;
        e.preventDefault();
        hdr.click();
      });
    });
  }

  function buildRows(turni) {
    cache.clear();
    (turni || []).forEach(t => {
      const key = `${String(t.nominativo || '').trim()}|${String(t.data || '').trim()}`;
      cache.set(key, t);
    });

    lastTurni = Array.isArray(turni) ? turni : [];

    const noms = selectedNames();

    if (isMobileView()) {
      if (elBody) elBody.innerHTML = '';
      buildRowsMobile(noms);
    } else {
      if (elMobile) elMobile.innerHTML = '';
      buildRowsDesktop(noms);
    }
  }


  async function loadWeek() {
    if (elWeekLabel) elWeekLabel.textContent = fmtRange(weekStart);
    let noms = (selectionMode === 'auto') ? [] : selectedNames();
    try {
      setStatus(t('loading', 'Caricamento...'));
      const r = await fetch('/orari/api/orari/week', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ week_start: toISO(weekStart), nominativi: noms })
      });

      let j = null;
      try { j = await r.json(); } catch (e) { j = null; }

      if (!r.ok || !j || !j.ok) {
        const msg = (j && j.error) ? String(j.error) : t('loadError', 'Errore caricamento.');
        throw new Error(msg);
      }

      if (selectionMode === 'auto') {
        const a = Array.isArray(j.auto_selected) ? j.auto_selected : [];
        if (a.length) {
          selectedSet = new Set(a.map(x => String(x || '').trim()).filter(Boolean));
          renderStaffList(elStaffSearch ? (elStaffSearch.value || '') : '');
          updateStaffButton();
        }
      }

      daysMeta = Array.isArray(j.days) ? j.days : [];
      salesCache = (j && j.sales && typeof j.sales === 'object') ? j.sales : {};
      prevYearCache = (j && j.prev_year && typeof j.prev_year === 'object') ? j.prev_year : {};
      renderHeaders();
      buildRows(Array.isArray(j.turni) ? j.turni : []);
      if (noms.length) setStatus('');
      setDirty(false);
      scheduleStats();
    } catch (e) {
      daysMeta = [];
      salesCache = {};
      prevYearCache = {};
      renderHeaders();
      buildRows([]);
      setStatus(t('loadError', 'Errore caricamento.'));
    }
  }

  function gatherSalesForSave() {
    const sales = {};
    const missing = [];
    (daysMeta || []).forEach(d => {
      const inp = document.querySelector(`input.sales-inp[data-date="${d.date}"]`);
      const n = parseMoney(inp ? inp.value : '');
      if (n == null) {
        missing.push(translateDayLabel(d.label || d.date));
        if (inp) inp.classList.add('is-invalid');
      } else {
        sales[d.date] = n;
        if (inp) inp.classList.remove('is-invalid');
      }
    });
    if (missing.length) {
      showModal(t('requiredField', 'Campo obbligatorio'), t('salesForecastRequired', 'Inserisci la previsione vendite per tutti i giorni della settimana.'));
      throw new Error('validation');
    }
    return sales;
  }

function gatherRowsForSave() {
    const rows = [];
    const errors = [];

    const containers = getRowContainers();
    containers.forEach(tr => {
      const nominativo = tr.dataset.nominativo || '';
      const cells = Array.from(tr.querySelectorAll('.cell-box'));
      cells.forEach(cell => {
        const data = cell.dataset.data || '';
        const causale = cell.querySelector('select[data-k="causale"]')?.value || '';
        const causale2 = cell.querySelector('select[data-k="causale2"]')?.value || '';
        let s_prestito = cell.querySelector('select[data-k="s_prestito"]')?.value || '';
        let s_prestito2 = cell.querySelector('select[data-k="s_prestito2"]')?.value || '';
        if (!_boolRule(_causaleRule(causale), 'requires_loan_store', String(causale||'').trim().toLowerCase() === 'prestito')) s_prestito = '';
        if (!_boolRule(_causaleRule(causale2), 'requires_loan_store', String(causale2||'').trim().toLowerCase() === 'prestito')) s_prestito2 = '';

        const rawIn1 = cell.querySelector('input[data-k="inizio_1"]')?.value || '';
        const rawFi1 = cell.querySelector('input[data-k="fine_1"]')?.value || '';
        const rawIn2 = cell.querySelector('input[data-k="inizio_2"]')?.value || '';
        const rawFi2 = cell.querySelector('input[data-k="fine_2"]')?.value || '';

        const vIn1 = validateTimeRaw(rawIn1);
        const vFi1 = validateTimeRaw(rawFi1);
        const vIn2 = validateTimeRaw(rawIn2);
        const vFi2 = validateTimeRaw(rawFi2);

        const in1 = vIn1.norm;
        const fi1 = vFi1.norm;
        const in2 = vIn2.norm;
        const fi2 = vFi2.norm;

        if (vIn1.err) errors.push(`${nominativo} ${data}: Turno 1 - Inizio: ${vIn1.err}`);
        if (vFi1.err) errors.push(`${nominativo} ${data}: Turno 1 - Fine: ${vFi1.err}`);
        if (vIn2.err) errors.push(`${nominativo} ${data}: Turno 2 - Inizio: ${vIn2.err}`);
        if (vFi2.err) errors.push(`${nominativo} ${data}: Turno 2 - Fine: ${vFi2.err}`);

        const colore = Number(cell.dataset.colore || 0);

        // Validazione prestito
        if (_boolRule(_causaleRule(causale), 'requires_loan_store', String(causale||'').trim().toLowerCase() === 'prestito') && !s_prestito) {
          errors.push(`${nominativo} ${data}: Turno 1 - seleziona lo store per Prestito.`);
        }
        if (_boolRule(_causaleRule(causale2), 'requires_loan_store', String(causale2||'').trim().toLowerCase() === 'prestito') && !s_prestito2) {
          errors.push(`${nominativo} ${data}: Turno 2 - seleziona lo store per Prestito.`);
        }

        const hasShift1 = !!(in1 && fi1);
        const hasShift2 = !!(in2 && fi2);
        if (_boolRule(_causaleRule(causale), 'requires_time_range', String(causale || '').trim().toLowerCase() === 'riposo festivo') && !hasShift1) {
          errors.push(`${nominativo} ${data}: Turno 1 - Riposo Festivo richiede un turno associato.`);
        }
        if (_boolRule(_causaleRule(causale2), 'requires_time_range', String(causale2 || '').trim().toLowerCase() === 'riposo festivo') && !hasShift2) {
          errors.push(`${nominativo} ${data}: Turno 2 - Riposo Festivo richiede un turno associato.`);
        }

        // Validazione orari (anche attraversamento mezzanotte)
        const r1 = (!vIn1.err && !vFi1.err) ? duration(in1, fi1, { overnightStartMin: 12*60, overnightEndMax: 8*60 }) : ({ mins: 0, err: null });
        const r2 = (!vIn2.err && !vFi2.err) ? duration(in2, fi2, { overnightStartMin: 12*60, overnightEndMax: 8*60 }) : ({ mins: 0, err: null });
        if (r1.err && in1 && fi1) errors.push(`${nominativo} ${data}: Turno 1 - ${r1.err}`);
        if (r2.err && in2 && fi2) errors.push(`${nominativo} ${data}: Turno 2 - ${r2.err}`);

        const st = staffByName(nominativo);
        rows.push({
          staff_id: String((st && st.id) || '').trim(),
          nominativo,
          data,
          causale,
          causale2,
          inizio_1: in1,
          fine_1: fi1,
          inizio_2: in2,
          fine_2: fi2,
          s_prestito,
          s_prestito2,
          colore
        });
      });
    });

    if (errors.length) {
      showModal('Dati non validi', errors.slice(0, 8).join('\n') + (errors.length > 8 ? ('\n... (+' + (errors.length-8) + ')') : ''));
      throw new Error('validation');
    }
    return rows;
  }


function validateContractHoursForSave(rows) {
  const map = buildAutoExtraSummary(rows);
  const anomalies = [];
  const names = Array.from(map.keys()).sort((a, b) => a.localeCompare(b));
  names.forEach(n => {
    const rec = map.get(n) || {};
    const contract = Number(rec.contract_mins || 0);
    const extra = Number((rec.explicit_extra_mins || 0) + (rec.auto_extra_mins || 0));
    const base = Number(rec.regular_mins || 0);
    const ruolo = String(rec.ruolo || '').trim().toUpperCase();
    const inqRule = _inquadramentoRule(ruolo);
    const requiresContractMatch = inqRule ? !!inqRule.requires_contract_match : !(ruolo.includes('STAGE') || ruolo.includes('TIROCIN') || ruolo.includes('INTERMITT') || ruolo.includes('INTERINAL'));

    if (!requiresContractMatch) {
      return;
    }

    if (!contract || contract <= 0) {
      anomalies.push({
        nominativo: n,
        contract_mins: contract || 0,
        base_mins: base,
        extra_mins: extra,
        action: 'Ore contrattuali non impostate in Anagrafica.'
      });
      return;
    }

    if (base < contract) {
      const miss = contract - base;
      anomalies.push({
        nominativo: n,
        contract_mins: contract,
        base_mins: base,
        extra_mins: extra,
        action: `Mancano ${fmtHM(miss)}. Inserisci ${_CONTRACT_CAUSALI_DEFICIT} fino al raggiungimento delle ore contrattuali.`
      });
      return;
    }

    if (Number(rec.unallocatable_extra_mins || 0) > 0) {
      const ex = Number(rec.unallocatable_extra_mins || 0);
      anomalies.push({
        nominativo: n,
        contract_mins: contract,
        base_mins: base,
        extra_mins: extra,
        action: `Ci sono ${fmtHM(ex)} ore oltre contratto non allocabili automaticamente come Extra. L'auto-assegnazione usa solo turni senza causale oppure con causale Prestito/Training.`
      });
      return;
    }
  });

  if (anomalies.length) {
    showContractValidationModal(anomalies);
    throw new Error('validation');
  }
}




  
  // Linear view (Gantt-like)
  function getLinearColorHex(code) {
    const n = Number(code || 0);
    const f = (COLOR_SWATCHES || []).find(x => Number(x.code) === n);
    if (n === 0) return '';
    return (f && f.value) ? f.value : '#ced4da';
  }

  function getLiveTurno(nominativo, dateIso) {
    const n = String(nominativo || '').trim();
    const d = String(dateIso || '').trim();
    if (!n || !d) return null;

    let cell = null;
    try {
      const sel = `.cell-box[data-nominativo="${cssEsc(n)}"][data-data="${cssEsc(d)}"]`;
      cell = document.querySelector(sel);
    } catch (e) {
      cell = null;
    }

    if (cell) {
      const v = (k) => (cell.querySelector(`input[data-k="${k}"]`)?.value || '');
      const sv = (k) => (cell.querySelector(`select[data-k="${k}"]`)?.value || '');
      return {
        nominativo: n,
        data: d,
        causale: sv('causale'),
        causale2: sv('causale2'),
        inizio_1: v('inizio_1'),
        fine_1: v('fine_1'),
        inizio_2: v('inizio_2'),
        fine_2: v('fine_2'),
        colore: Number(cell.dataset.colore || 0)
      };
    }

    const key = `${n}|${d}`;
    return cache.get(key) || null;
  }

  function renderLinear(dateIso) {
    if (!elLinearWrap) return;
    const noms = selectedNames();
    if (!noms.length) {
      elLinearWrap.innerHTML = `<div class="p-3 text-muted">${esc(t('selectPersonWarning', 'Seleziona almeno una persona.'))}</div>`;
      return;
    }

    const d = String(dateIso || '').trim();
    if (!d) {
      elLinearWrap.innerHTML = '<div class="p-3 text-muted">Seleziona un giorno.</div>';
      return;
    }

    const startMin = 7 * 60;
    const endMin = 24 * 60;
    const blocks = Math.floor((endMin - startMin) / 15);

    let html = '';
    html += '<table class="orari-linear">';
    html += '<thead><tr>';
    html += '<th class="lin-name">Nome</th>';
    for (let h = 7; h < 24; h++) {
      html += `<th colspan="4">${h}</th>`;
    }
    html += '</tr></thead>';
    html += '<tbody>';

    const fillBlocks = (arr, inizio, fine) => {
      const a = minsFromTime(inizio);
      const b0 = minsFromTime(fine);
      if (a == null || b0 == null) return;
      let b = b0;
      if (b < a) b = b + 24 * 60; // overnight
      const s = Math.max(a, startMin);
      const e = Math.min(b, endMin);
      if (e <= s) return;
      let i0 = Math.floor((s - startMin) / 15);
      let i1 = Math.ceil((e - startMin) / 15);
      i0 = Math.max(0, Math.min(blocks, i0));
      i1 = Math.max(0, Math.min(blocks, i1));
      for (let i = i0; i < i1; i++) arr[i] = true;
    };

    noms.forEach(n => {
      const t = getLiveTurno(n, d) || { colore: 0, causale: '', causale2: '', inizio_1: '', fine_1: '', inizio_2: '', fine_2: '' };
      const on = Array(blocks).fill(false);
      const c1 = String((t.causale ?? '')).trim();
      const c2 = String((t.causale2 ?? '')).trim();
      // Nel lineare mostriamo solo i turni "produttivi": alcune causali (ferie/off/prestito/malattia/permesso/allattamento/training)
      // escludono il turno. Altre (es. Extra) restano produttive.
      if (_isProdShift(c1)) fillBlocks(on, t.inizio_1, t.fine_1);
      if (_isProdShift(c2)) fillBlocks(on, t.inizio_2, t.fine_2);
      const bg = getLinearColorHex(t.colore);
      html += '<tr>';
      html += `<td class="lin-name">${esc(n)}</td>`;
      for (let i = 0; i < blocks; i++) {
        if (on[i]) {
          html += `<td class="lin-cell on" style="background:${bg};"></td>`;
        } else {
          html += '<td class="lin-cell"></td>';
        }
      }
      html += '</tr>';
    });

    html += '</tbody></table>';
    elLinearWrap.innerHTML = html;
  }

  function openLinear() {
    const noms = selectedNames();
    if (!noms.length) {
      showModal(t('showLinear', 'Mostra lineare'), t('selectPersonWarning', 'Seleziona almeno una persona.'));
      return;
    }
    if (!Array.isArray(daysMeta) || !daysMeta.length) {
      showModal(t('showLinear', 'Mostra lineare'), t('weekDataUnavailable', 'Dati settimana non disponibili.'));
      return;
    }

    if (elLinearDay) {
      const prev = elLinearDay.value || '';
      elLinearDay.innerHTML = '';
      daysMeta.forEach(d => {
        const opt = document.createElement('option');
        opt.value = d.date;
        opt.textContent = translateDayLabel(d.label || d.date);
        elLinearDay.appendChild(opt);
      });
      const stillOk = prev && daysMeta.some(d => String(d.date) === String(prev));
      elLinearDay.value = stillOk ? prev : String(daysMeta[0].date);
    }

    renderLinear(elLinearDay ? elLinearDay.value : (daysMeta[0]?.date || ''));

    if (elLinearModal && window.bootstrap && window.bootstrap.Modal) {
      const inst = window.bootstrap.Modal.getOrCreateInstance(elLinearModal);
      inst.show();
      return;
    }
  }

  function exportPdf() {
    // legacy signature kept for backward compatibility
    exportPdfAsync();
  }

function openImportWeek() {
  if (!elImportWeekModal) {
    showModal('Attenzione', 'Funzione importazione non disponibile.');
    return;
  }
  if (!confirmLoseChanges()) return;

  // default: previous week
  try {
    const prev = addDays(weekStart, -7);
    if (elImportWeekDate) elImportWeekDate.value = toISO(prev);
  } catch (e) {}

  if (window.bootstrap && window.bootstrap.Modal) {
    const inst = window.bootstrap.Modal.getOrCreateInstance(elImportWeekModal);
    inst.show();
  } else {
    // fallback: simple prompt
    const s = window.prompt('Inserisci una data (YYYY-MM-DD) nella settimana da copiare:');
    if (!s) return;
    doImportWeek(s);
  }
}

async function doImportWeek(dateIsoOpt) {
  const raw = String(dateIsoOpt || (elImportWeekDate ? elImportWeekDate.value : '') || '').trim();
  const d0 = parseISO(raw);
  if (!d0) {
    showModal('Attenzione', 'Seleziona una data valida (YYYY-MM-DD).');
    return;
  }
  const src = mondayOf(d0);

  const msg = `Importare la settimana ${fmtRange(src)} nella settimana corrente ${fmtRange(weekStart)}?\n\n` +
              `Gli orari della settimana corrente verranno sovrascritti.`;
  if (!window.confirm(msg)) return;

  try {
    if (btnImportWeekDo) btnImportWeekDo.disabled = true;
    setStatus('Importazione in corso...');

    const r = await fetch('/orari/api/orari/overwrite-week', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        source_week_start: toISO(src),
        target_week_start: toISO(weekStart),
      })
    });

    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j || j.ok !== true) {
      const err = (j && (j.error || j.message)) ? String(j.error || j.message) : ('HTTP ' + r.status);
      showModal('Errore importazione', err);
      setStatus('');
      return;
    }

    // close modal
    try {
      if (elImportWeekModal && window.bootstrap && window.bootstrap.Modal) {
        const inst = window.bootstrap.Modal.getOrCreateInstance(elImportWeekModal);
        inst.hide();
      }
    } catch (e) {}

    setDirty(false);
    await loadWeek();

    const info = `Copiati ${Number(j.source_rows || 0)} record.`;
    showModal('Importazione completata', info);
    setStatus('');
  } catch (e) {
    console.error(e);
    showModal('Errore importazione', 'Operazione non riuscita.');
    setStatus('');
  } finally {
    if (btnImportWeekDo) btnImportWeekDo.disabled = false;
  }
}



  function _btnBusy(el, isBusy, busyText) {
    if (!el) return;
    if (isBusy) {
      el.dataset._oldText = el.innerHTML;
      el.disabled = true;
      el.innerHTML = busyText || 'Attendi...';
    } else {
      el.disabled = false;
      if (el.dataset._oldText) {
        el.innerHTML = el.dataset._oldText;
        delete el.dataset._oldText;
      }
    }
  }

  function askPdfUnsavedChoice() {
    return new Promise((resolve) => {
      const modalEl = document.getElementById('orariUnsavedModal');
      const canBoot = !!(modalEl && window.bootstrap && window.bootstrap.Modal);
      if (!canBoot) {
        // fallback: ok => save, cancel => cancel
        const ok = window.confirm('Ci sono modifiche non salvate. Vuoi salvarle prima di generare il PDF?');
        resolve(ok ? 'save' : 'cancel');
        return;
      }

      const btnSave = document.getElementById('btnPdfSave');
      const btnNoSave = document.getElementById('btnPdfNoSave');
      const btnCancel = document.getElementById('btnPdfCancel');

      const modal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
      let choice = null;
      let cleaned = false;

      const cleanup = () => {
        if (cleaned) return;
        cleaned = true;
        if (btnSave) btnSave.removeEventListener('click', onSave);
        if (btnNoSave) btnNoSave.removeEventListener('click', onNoSave);
        if (btnCancel) btnCancel.removeEventListener('click', onCancel);
        modalEl.removeEventListener('hidden.bs.modal', onHidden);
      };

      const finish = (c) => {
        choice = c;
        cleanup();
        try { modal.hide(); } catch (e) {}
        resolve(choice);
      };

      const onSave = (e) => { e.preventDefault(); finish('save'); };
      const onNoSave = (e) => { e.preventDefault(); finish('nosave'); };
      const onCancel = (e) => { e.preventDefault(); finish('cancel'); };

      const onHidden = () => {
        // chiusura da X / backdrop
        cleanup();
        resolve(choice || 'cancel');
      };

      if (btnSave) btnSave.addEventListener('click', onSave);
      if (btnNoSave) btnNoSave.addEventListener('click', onNoSave);
      if (btnCancel) btnCancel.addEventListener('click', onCancel);
      modalEl.addEventListener('hidden.bs.modal', onHidden);

      modal.show();
    });
  }

  function calcExportScale(el) {
    const w = (el && (el.scrollWidth || el.offsetWidth)) ? (el.scrollWidth || el.offsetWidth) : 1200;
    // target ~3000px width for good readability, clamped
    const s = 3000 / Math.max(800, w);
    return Math.min(2, Math.max(1, s));
  }

  function syncFormValuesForClone(cloneDoc) {
    try {
      const origArea = document.getElementById('orariExportArea');
      const cloneArea = cloneDoc.getElementById('orariExportArea');
      if (!origArea || !cloneArea) return;
      const orig = origArea.querySelectorAll('input,select,textarea');
      const copy = cloneArea.querySelectorAll('input,select,textarea');
      const n = Math.min(orig.length, copy.length);
      for (let i = 0; i < n; i++) {
        const o = orig[i];
        const c = copy[i];
        if (!o || !c) continue;
        const tag = String(o.tagName || '').toUpperCase();
        if (tag === 'INPUT' || tag === 'TEXTAREA') {
          c.value = o.value;
        } else if (tag === 'SELECT') {
          c.value = o.value;
        }
      }
    } catch (e) {}
  }

  
  function _pdfFmtCausale(c, prest) {
    const raw = String(c || '').trim();
    if (!raw) return '';
    const low = raw.toLowerCase();
    if (low === 'prestito') {
      const p = String(prest || '').trim();
      return p ? (`Prestito→${p}`) : 'Prestito';
    }
    return raw;
  }

  function _pdfCellText(preset) {
    const p = preset || {};
    const t1a = normalizeTime(p.inizio_1 || '');
    const t1b = normalizeTime(p.fine_1 || '');
    const t2a = normalizeTime(p.inizio_2 || '');
    const t2b = normalizeTime(p.fine_2 || '');

    const times = [];
    if (t1a && t1b) times.push(`${t1a}-${t1b}`);
    if (t2a && t2b) times.push(`${t2a}-${t2b}`);

    const notes = [];
    const c1 = _pdfFmtCausale(p.causale || '', p.s_prestito || '');
    const c2 = _pdfFmtCausale(p.causale2 || '', p.s_prestito2 || '');
    if (c1) notes.push(c1);
    if (c2) notes.push(c2);

    const t = times.join(' ');
    const n = notes.join(' + ');
    if (t && n) return `${t} • ${n}`;
    return t || n || '';
  }

  function _pdfMinsForPreset(preset) {
    const p = preset || {};
    const d1 = duration(p.inizio_1 || '', p.fine_1 || '', { overnightStartMin: 12 * 60, overnightEndMax: 8 * 60 });
    const d2 = duration(p.inizio_2 || '', p.fine_2 || '', { overnightStartMin: 12 * 60, overnightEndMax: 8 * 60 });
    const m1 = (d1 && Number.isFinite(d1.mins)) ? Number(d1.mins) : 0;
    const m2 = (d2 && Number.isFinite(d2.mins)) ? Number(d2.mins) : 0;
    const prod1 = _isProdShift(p.causale || '');
    const prod2 = _isProdShift(p.causale2 || '');
    return (prod1 ? Math.max(0, m1) : 0) + (prod2 ? Math.max(0, m2) : 0);
  }

  function _pdfSalesTotal() {
    let salesTot = 0;
    document.querySelectorAll('input.sales-inp').forEach(inp => {
      const n = parseMoney(inp ? inp.value : '');
      if (n != null) salesTot += n;
    });
    return salesTot;
  }

  function buildPdfRenderArea(noms) {
    try {
      const names = Array.isArray(noms) ? noms : [];
      const elId = 'orariPdfRender';
      let host = document.getElementById(elId);
      if (!host) {
        host = document.createElement('div');
        host.id = elId;
        document.body.appendChild(host);
      }

      host.style.position = 'fixed';
      host.style.left = '-10000px';
      host.style.top = '0';
      host.style.width = '1100px';
      host.style.background = '#ffffff';
      host.style.color = '#000';
      host.style.padding = '12px';
      host.style.boxSizing = 'border-box';
      host.style.zIndex = '9999';

      const ws = toISO(weekStart);
      const we = toISO(addDays(weekStart, 6));
      const sc = String((boot && boot.store_code) ? boot.store_code : '').trim() || 'STORE';

      // Summary
      const salesTot = _pdfSalesTotal();
      let weekMins = 0;

      // Precompute per-person totals
      const rows = [];
      names.forEach(n => {
        let pm = 0;
        const cells = [];
        (daysMeta || []).forEach(d => {
          const key = `${n}|${d.date}`;
          const preset = cache.get(key) || { causale: '', causale2: '', inizio_1: '', fine_1: '', inizio_2: '', fine_2: '', s_prestito: '', s_prestito2: '' };
          const txt = _pdfCellText(preset);
          pm += _pdfMinsForPreset(preset);
          cells.push({ date: d.date, label: translateDayLabel(d.label || d.date), txt });
        });
        weekMins += pm;
        rows.push({ name: n, mins: pm, cells });
      });

      const hoursDec = weekMins > 0 ? (weekMins / 60) : 0;
      const prod = hoursDec > 0 ? (salesTot / hoursDec) : 0;

      const style = `
        <style>
          .pdf-hdr { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; margin-bottom:8px; }
          .pdf-title { font-weight:800; font-size:16px; line-height:1.1; }
          .pdf-sub { font-size:11px; color:#333; margin-top:2px; }
          .pdf-metrics { font-size:11px; text-align:right; white-space:nowrap; }
          .pdf-metrics div { line-height:1.35; }
          table.pdf { width:100%; border-collapse:collapse; table-layout:fixed; }
          table.pdf th, table.pdf td { border:1px solid #ddd; padding:3px 4px; vertical-align:top; font-size:9.5px; line-height:1.15; }
          table.pdf th { background:#f3f3f3; font-weight:800; text-align:center; }
          th.pdf-name { width:160px; text-align:left; }
          th.pdf-tot { width:60px; }
          td.pdf-name { font-weight:700; }
          td.pdf-tot { text-align:center; font-weight:800; font-variant-numeric:tabular-nums; }
          .pdf-cell { white-space:normal; word-break:break-word; }
        </style>
      `;

      let thead = `<tr><th class="pdf-name">${esc(t('personName', 'Nominativo'))}</th>`;
      (daysMeta || []).forEach(d => {
        const lbl = String(translateDayLabel(d.label || d.date) || '').replace(/\s+/g, ' ').trim();
        thead += `<th>${esc(lbl)}</th>`;
      });
      thead += `<th class="pdf-tot">Tot</th></tr>`;

      let tbody = '';
      rows.forEach(r => {
        tbody += `<tr>`;
        tbody += `<td class="pdf-name">${esc(r.name)}</td>`;
        (r.cells || []).forEach(c => {
          tbody += `<td><div class="pdf-cell">${esc(c.txt || '')}</div></td>`;
        });
        tbody += `<td class="pdf-tot">${esc(fmtHM(r.mins))}</td>`;
        tbody += `</tr>`;
      });

      host.innerHTML = `
        ${style}
        <div class="pdf-hdr">
          <div>
            <div class="pdf-title">Orari - ${esc(sc)}</div>
            <div class="pdf-sub">Settimana ${esc(ws)} → ${esc(we)}</div>
          </div>
          <div class="pdf-metrics">
            <div>Fatturato sett.: € ${esc(fmtEuro(salesTot))}</div>
            <div>Ore totali: ${esc(fmtHM(weekMins))}</div>
            <div>Produttività: € ${esc(fmtEuro(prod))}</div>
          </div>
        </div>
        <table class="pdf">
          <thead>${thead}</thead>
          <tbody>${tbody}</tbody>
        </table>
      `;

      return host;
    } catch (e) {
      return null;
    }
  }

  async function generatePdfFromLayout() {
    const noms = selectedNames();
    if (!noms.length) {
      showModal('PDF', t('selectPersonWarning', 'Seleziona almeno una persona.'));
      return;
    }

    // Build a consistent, schematic PDF layout (independent from mobile/desktop view)
    const pdfArea = buildPdfRenderArea(noms);
    if (!pdfArea) {
      showModal('PDF', 'Impossibile preparare l\'esportazione.');
      return;
    }

    if (!window.html2canvas || !(window.jspdf && window.jspdf.jsPDF)) {
      // fallback: vecchio PDF server-side
      if (pdfForm) {
        pdfForm.submit();
        return;
      }
      showModal('PDF', 'Librerie PDF non disponibili.');
      return;
    }

    closeAllColorPops();
    try {
      document.querySelectorAll('.dropdown-menu.show').forEach(m => {
        const dd = m.closest('.dropdown');
        const btn = dd ? dd.querySelector('[data-bs-toggle="dropdown"]') : null;
        if (btn && window.bootstrap && window.bootstrap.Dropdown) {
          const inst = window.bootstrap.Dropdown.getOrCreateInstance(btn);
          inst.hide();
        }
      });
    } catch (e) {}

    document.body.classList.add('orari-exporting');
    _btnBusy(btnPdf, true, 'PDF...');

    try {
      const scale = 2;
      const canvas = await window.html2canvas(pdfArea, {
        scale,
        backgroundColor: '#ffffff',
        useCORS: true
      });

      const { jsPDF } = window.jspdf;
      const doc = new jsPDF({ orientation: 'l', unit: 'pt', format: 'a4' });

      const pageW = doc.internal.pageSize.getWidth();
      const pageH = doc.internal.pageSize.getHeight();
      const margin = 18;
      const maxW = pageW - margin * 2;
      const maxH = pageH - margin * 2;

      const imgData = canvas.toDataURL('image/png', 1.0);

      const rW = maxW / Math.max(1, canvas.width);
      const rH = maxH / Math.max(1, canvas.height);
      const ratio = Math.min(rW, rH);

      const drawW = canvas.width * ratio;
      const drawH = canvas.height * ratio;

      const x = (pageW - drawW) / 2;
      const y = (pageH - drawH) / 2;

      doc.addImage(imgData, 'PNG', x, y, drawW, drawH, undefined, 'FAST');

      const sc = String((boot && boot.store_code) ? boot.store_code : '').trim();
      const ws = toISO(weekStart);
      const name = `Orari_${sc || 'STORE'}_${ws}.pdf`;
      doc.save(name);
    } catch (e) {
      showModal('Errore PDF', 'Impossibile generare il PDF.');
    } finally {
      document.body.classList.remove('orari-exporting');
      _btnBusy(btnPdf, false);
    }
  }

  async function exportPdfAsync() {
    const noms = selectedNames();
    if (!noms.length) {
      showModal('PDF', t('selectPersonWarning', 'Seleziona almeno una persona.'));
      return;
    }
    if (pdfWeekStart) pdfWeekStart.value = toISO(weekStart);
    if (pdfNames) pdfNames.value = noms.length ? noms.join('||') : '';

    if (dirty) {
      const choice = await askPdfUnsavedChoice();
      if (choice === 'cancel') return;
      if (choice === 'save') {
        const ok = await saveWeek({ showSuccess: false, reload: false });
        if (!ok) return;
      }
    }
    await generatePdfFromLayout();
  }


async function saveWeek(opts) {
    const o = opts || {};
    const showSuccess = (o.showSuccess !== false);
    const reloadAfter = (o.reload !== false);
    try {
      const noms = selectedNames();
      if (!noms.length) {
        setStatus(t('selectPersonWarning', 'Seleziona almeno una persona.'));
        return false;
      }
      const sales = gatherSalesForSave();
      const rows = gatherRowsForSave();
      validateContractHoursForSave(rows);
      setStatus('Salvataggio...');
      const r = await fetch('/orari/api/orari/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ week_start: toISO(weekStart), sales, rows, visible_nominativi: noms })
      });

      let j = null;
      try { j = await r.json(); } catch (e) { j = null; }

      if (!r.ok || !j || !j.ok) {
        const msg = (j && j.error) ? String(j.error) : 'Errore durante il salvataggio.';
        showModal('Errore salvataggio', msg);
        setStatus('Errore durante il salvataggio.');
        return false;
      }

      if (showSuccess) {
        showModal('Salvataggio completato', `Dati salvati correttamente.`);
      }
      setStatus(`Salvati: ${j.saved || 0} - Rimossi: ${j.deleted || 0}`);
      setDirty(false);
      selectionMode = 'auto';
      if (reloadAfter) await loadWeek();
      return true;
    } catch (e) {
      if (e && String(e.message || '') === 'validation') {
        setStatus('');
        return false;
      }
      showModal('Errore salvataggio', 'Errore durante il salvataggio.');
      setStatus('Errore durante il salvataggio.');
      return false;
    }
  }

  async function goToWeek(newWeekStart) {
    if (!confirmLoseChanges()) return;
    setDirty(false);
    weekStart = newWeekStart;
    selectionMode = 'auto';
    selectedSet = new Set();
    renderStaffList(elStaffSearch ? (elStaffSearch.value || '') : '');
    updateStaffButton();
    await loadWeek();
  }

  function wire() {
    if (elStaffSearch) {
      elStaffSearch.addEventListener('input', () => {
        const checked = new Set(selectedNames());
        renderStaffList(elStaffSearch.value || '');
        Array.from(elStaffList.querySelectorAll('input[type="checkbox"][data-staff="1"]')).forEach(cb => {
          cb.checked = checked.has(cb.value);
        });
        updateStaffButton();
      });
    }

    if (elStaffList) {
      elStaffList.addEventListener('change', (e) => {
        const t = e.target;
        if (t && t.matches && t.matches('input[type="checkbox"][data-staff="1"]')) {
          const v = String(t.value || '').trim();
          selectionMode = 'manual';
          if (t.checked) selectedSet.add(v);
          else selectedSet.delete(v);
          updateStaffButton();
          scheduleLoadWeek();
        }
      });
    }

    if (btnSelectAll) btnSelectAll.addEventListener('click', () => setAllChecked(true));
    if (btnClearSel) btnClearSel.addEventListener('click', () => setAllChecked(false));

    if (btnPrev) btnPrev.addEventListener('click', () => goToWeek(addDays(weekStart, -7)));
    if (btnNext) btnNext.addEventListener('click', () => goToWeek(addDays(weekStart, 7)));
    if (btnToday) btnToday.addEventListener('click', () => goToWeek(mondayOf(new Date())));
    if (btnImportWeek) btnImportWeek.addEventListener('click', () => openImportWeek());
    if (btnSave) btnSave.addEventListener('click', () => saveWeek());
    if (btnPdf) btnPdf.addEventListener('click', () => exportPdf());
    if (btnLinear) btnLinear.addEventListener('click', () => openLinear());
    if (btnImportWeekDo) btnImportWeekDo.addEventListener('click', () => doImportWeek());
    if (elLinearDay) elLinearDay.addEventListener('change', () => renderLinear(elLinearDay.value));

    if (elCellMenu) {
      elCellMenu.addEventListener('click', (e) => {
        const colorSw = e && e.target && e.target.closest ? e.target.closest('[data-color-code]') : null;
        if (colorSw && cellMenuTarget) {
          const code = Number(colorSw.dataset.colorCode || 0);
          applyCellColor(cellMenuTarget, code);
          markDirty();
          updateRowTotals(findRowContainer(cellMenuTarget));
          hideCellMenu();
          return;
        }
        const btn = e && e.target && e.target.closest ? e.target.closest('[data-act]') : null;
        if (!btn) return;
        if (btn.classList.contains('disabled')) return;
        const act = String(btn.dataset.act || '');
        if (act === 'copy') {
          if (cellMenuTarget) setClipboardFromCell(cellMenuTarget);
          hideCellMenu();
        } else if (act === 'paste') {
          if (cellMenuTarget) pasteClipboardToCell(cellMenuTarget);
          hideCellMenu();
        } else if (act === 'clear') {
          cellClipboard = null;
          clearClipboardHighlight();
          setStatus('');
          hideCellMenu();
        }
      });
    }

    if (pdfForm) {
      pdfForm.addEventListener('submit', (e) => {
        const noms = selectedNames();
        if (pdfWeekStart) pdfWeekStart.value = toISO(weekStart);
        if (pdfNames) pdfNames.value = noms.length ? noms.join('||') : '';
      });
    }

    document.addEventListener('click', (ev) => {
      if (elCellMenu && elCellMenu.style.display === 'block') {
        const inside = ev && ev.target && ev.target.closest ? ev.target.closest('#orariCellMenu') : null;
        if (!inside) hideCellMenu();
      }
      const insideEditingCell = ev && ev.target && ev.target.closest ? ev.target.closest('.cell-box') : null;
      const insideCausale = ev && ev.target && ev.target.closest ? ev.target.closest('.causale-block') : null;
      const insideDash = ev && ev.target && ev.target.closest ? ev.target.closest('.dash-toggle') : null;
      if (!insideCausale && !insideDash) closeAllCausalePops();
      closeAllEditingCells(insideEditingCell);
      closeAllColorPops();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        hideCellMenu();
        closeAllCausalePops();
        closeAllColorPops();
      }
    });
if (MOBILE_MQ && MOBILE_MQ.addEventListener) {
      MOBILE_MQ.addEventListener('change', () => {
        if (dirty) return;
        renderHeaders();
        buildRows(lastTurni);
        scheduleStats();
      });
    } else if (MOBILE_MQ && MOBILE_MQ.addListener) {
      // Safari older
      MOBILE_MQ.addListener(() => {
        if (dirty) return;
        renderHeaders();
        buildRows(lastTurni);
        scheduleStats();
      });
    }

const elValBody = document.getElementById('orariValidationBody');
if (elValBody) {
  elValBody.addEventListener('click', (e) => {
    const btn = e && e.target && e.target.closest ? e.target.closest('[data-act="goto-contract"]') : null;
    if (!btn) return;
    const name = String(btn.dataset.name || '').trim();
    if (!name) return;

    // chiudi modal
    const modalEl = document.getElementById('orariValidationModal');
    if (modalEl && window.bootstrap && window.bootstrap.Modal) {
      try { window.bootstrap.Modal.getOrCreateInstance(modalEl).hide(); } catch (ex) { }
    }
    _scrollToContractErr(name);
  });
}

  }

  async function init() {
    try {
      await loadStaffActive();
      renderStaffList('');
      wire();
      updateStaffButton();
      await loadStoresAll();
      await loadWeek();
    } catch (e) {
      try { console.error(e); } catch (e2) { }
      try { setStatus('Errore inizializzazione.'); } catch (e3) { }
      try { showModal('Errore', 'Errore inizializzazione Orari. Ricarica la pagina (Ctrl+F5).'); } catch (e4) { }
    }
  }

  init();
})();



