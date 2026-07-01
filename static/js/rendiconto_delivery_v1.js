(function () {
  function parseWeekValue(value) {
    const s = String(value || '').trim();
    const m = /^(\d{4})-W(\d{2})$/.exec(s);
    if (!m) return null;
    const year = Number(m[1]);
    const week = Number(m[2]);
    if (!Number.isFinite(year) || !Number.isFinite(week)) return null;
    const jan4 = new Date(year, 0, 4);
    const jan4Day = (jan4.getDay() + 6) % 7;
    const mondayWeek1 = new Date(year, 0, 4 - jan4Day);
    mondayWeek1.setDate(mondayWeek1.getDate() + ((week - 1) * 7));
    return mondayWeek1;
  }

  function weekValueFromDate(dateObj) {
    const d = new Date(dateObj.getFullYear(), dateObj.getMonth(), dateObj.getDate());
    d.setHours(0, 0, 0, 0);
    const dayNr = (d.getDay() + 6) % 7;
    d.setDate(d.getDate() - dayNr + 3);
    const firstThursday = new Date(d.getFullYear(), 0, 4);
    const firstDayNr = (firstThursday.getDay() + 6) % 7;
    firstThursday.setDate(firstThursday.getDate() - firstDayNr + 3);
    const week = 1 + Math.round((d - firstThursday) / 604800000);
    return d.getFullYear() + '-W' + String(week).padStart(2, '0');
  }

  function parseIsoDate(value) {
    const s = String(value || '').trim();
    if (!s) return null;
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
    if (!m) return null;
    const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    if (Number.isNaN(d.getTime())) return null;
    return d;
  }

  function isoDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
  }

  function mondayOf(dateObj) {
    const d = new Date(dateObj.getFullYear(), dateObj.getMonth(), dateObj.getDate());
    const wd = (d.getDay() + 6) % 7;
    d.setDate(d.getDate() - wd);
    return d;
  }

  function addDays(dateObj, days) {
    const d = new Date(dateObj.getFullYear(), dateObj.getMonth(), dateObj.getDate());
    d.setDate(d.getDate() + Number(days || 0));
    return d;
  }

  function fmtDateIt(dateObj) {
    return dateObj.toLocaleDateString('it-IT', { day: '2-digit', month: '2-digit', year: 'numeric' });
  }

  function parseNum(value) {
    if (value === null || value === undefined) return 0;
    const s = String(value).replace(/\s+/g, '').replace(',', '.');
    const n = parseFloat(s);
    return Number.isFinite(n) ? n : 0;
  }

  function parseIntSafe(value) {
    const n = parseInt(String(value == null ? '' : value).replace(',', '.'), 10);
    return Number.isFinite(n) ? n : 0;
  }

  function approxEq(a, b, eps) {
    const tol = Number.isFinite(Number(eps)) ? Number(eps) : 0.009;
    return Math.abs(parseNum(a) - parseNum(b)) <= tol;
  }

  function fmt2(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '';
    return n.toLocaleString('it-IT', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function fmtSigned2(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '';
    const prefix = n > 0 ? '+' : '';
    return prefix + fmt2(n);
  }

  function setReadonly(card, field, value) {
    const el = card.querySelector('[data-field="' + field + '"]');
    if (!el) return;
    el.value = value;
  }

  function isGlovoClosureMode(card) {
    const mode = String(card.getAttribute('data-opening-mode') || '').trim().toLowerCase();
    return mode === 'closure';
  }

  function normalizeOpeningPct(card, rawPct) {
    if (rawPct === null || rawPct === undefined || !Number.isFinite(Number(rawPct))) return null;
    let pct = Number(rawPct);
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    if (isGlovoClosureMode(card)) {
      pct = 100 - pct;
    }
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    return pct;
  }

  let distintaProviders = null;
  let distintaWeek = null;
  let distintaError = null;

  function weekLabelText() {
    return (distintaWeek && distintaWeek.start && distintaWeek.end)
      ? (distintaWeek.start + ' → ' + distintaWeek.end)
      : 'settimana';
  }

  function getCardPlatformLabel(card) {
    const plat = String(card.getAttribute('data-platform') || '').trim();
    if (!plat) return 'Piattaforma';
    return plat.charAt(0).toUpperCase() + plat.slice(1);
  }

  function getProviderEntry(card) {
    const base = String(card.getAttribute('data-provider-base') || '').trim().toUpperCase();
    if (!base || !distintaProviders) return null;
    if (!Object.prototype.hasOwnProperty.call(distintaProviders, base)) return null;
    return distintaProviders[base] || null;
  }

  function getPaymentInputs(card) {
    return {
      online: card.querySelector('[data-field="payment_online"]'),
      cash: card.querySelector('[data-field="payment_cash"]'),
    };
  }

  function fieldsLookEmptyOrZero(card) {
    const inputs = getPaymentInputs(card);
    if (!inputs.online || !inputs.cash) return false;
    const onlineRaw = String(inputs.online.value || '').trim();
    const cashRaw = String(inputs.cash.value || '').trim();
    if (!onlineRaw && !cashRaw) return true;
    return approxEq(onlineRaw || 0, 0) && approxEq(cashRaw || 0, 0);
  }

  function setImportStatus(card, text, tone) {
    const el = card.querySelector('[data-field="import_status"]');
    if (!el) return;
    el.className = 'small mt-1';
    if (tone === 'success') el.classList.add('text-success');
    else if (tone === 'warning') el.classList.add('text-warning');
    else if (tone === 'danger') el.classList.add('text-danger');
    else el.classList.add('text-muted');
    el.textContent = text || '';
  }

  function importPaymentsIntoCard(card, force) {
    const entry = getProviderEntry(card);
    const inputs = getPaymentInputs(card);
    if (!inputs.online || !inputs.cash) return false;

    if (!entry) {
      if (force) {
        inputs.online.value = '0.00';
        inputs.cash.value = '0.00';
        card.dataset.importedOnline = '0.00';
        card.dataset.importedCash = '0.00';
        setImportStatus(card, 'Nessuna movimentazione trovata in distinta per questo provider: impostato 0 / 0.', 'warning');
        updateCard(card);
        return true;
      }
      setImportStatus(card, 'Nessuna movimentazione trovata in distinta per questo provider.', 'warning');
      updateCard(card);
      return false;
    }

    if (!force && !fieldsLookEmptyOrZero(card)) {
      const currOnline = parseNum(inputs.online.value);
      const currCash = parseNum(inputs.cash.value);
      const impOnline = parseNum(entry.online);
      const impCash = parseNum(entry.cash);
      if (approxEq(currOnline, impOnline) && approxEq(currCash, impCash)) {
        card.dataset.importedOnline = fmt2(impOnline).replace('.', ',');
        card.dataset.importedCash = fmt2(impCash).replace('.', ',');
        setImportStatus(card, 'Valori già allineati alla distinta cassa.', 'success');
      } else {
        setImportStatus(card, 'Valori salvati o già inseriti mantenuti. Usa “Importa distinta” se vuoi sostituirli.', 'muted');
      }
      updateCard(card);
      return false;
    }

    const onlineVal = Number(entry.online || 0);
    const cashVal = Number(entry.cash || 0);
    inputs.online.value = onlineVal.toFixed(2);
    inputs.cash.value = cashVal.toFixed(2);
    card.dataset.importedOnline = onlineVal.toFixed(2);
    card.dataset.importedCash = cashVal.toFixed(2);
    setImportStatus(card, 'Pagamenti importati dalla distinta cassa: puoi modificarli prima del salvataggio.', 'success');
    updateCard(card);
    return true;
  }

  function importPaymentsAll(force) {
    const cards = Array.from(document.querySelectorAll('.delivery-card'));
    cards.forEach(function (card) {
      importPaymentsIntoCard(card, !!force);
    });
  }

  async function loadDistintaTotals() {
    const wkEl = document.getElementById('weekStart');
    const weekStart = wkEl ? String(wkEl.value || '').trim() : '';

    distintaProviders = null;
    distintaWeek = null;
    distintaError = null;

    if (!weekStart) {
      updateAllAlerts();
      return;
    }

    try {
      const url = '/rendiconto/api/delivery-distinta-totals?week_start=' + encodeURIComponent(weekStart);
      const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
      const data = await res.json();
      if (!data || !data.ok) {
        throw new Error((data && data.error) ? data.error : 'Errore lettura distinte');
      }

      distintaProviders = data.providers || {};
      distintaWeek = { start: data.week_start, end: data.week_end };
      importPaymentsAll(false);
    } catch (e) {
      distintaError = (e && e.message) ? e.message : String(e);
      const cards = document.querySelectorAll('.delivery-card');
      cards.forEach(function (card) {
        setImportStatus(card, 'Importazione distinta non disponibile: ' + distintaError, 'danger');
      });
    }

    updateAllAlerts();
  }

  function getMatchState(card) {
    const inputs = getPaymentInputs(card);
    if (!inputs.online || !inputs.cash) {
      return { ok: false, code: 'missing-inputs' };
    }

    const onlineVal = parseNum(inputs.online.value);
    const cashVal = parseNum(inputs.cash.value);

    if (distintaError) {
      return { ok: false, code: 'distinta-error', message: distintaError, onlineVal, cashVal };
    }
    if (!distintaProviders) {
      return { ok: false, code: 'distinta-unavailable', onlineVal, cashVal };
    }

    const entry = getProviderEntry(card);
    if (!entry) {
      if (approxEq(onlineVal, 0) && approxEq(cashVal, 0)) {
        return { ok: true, code: 'provider-missing-zero', onlineVal, cashVal };
      }
      return { ok: false, code: 'provider-missing', onlineVal, cashVal };
    }

    const expOnline = parseNum(entry.online);
    const expCash = parseNum(entry.cash);
    const mOnline = approxEq(onlineVal, expOnline);
    const mCash = approxEq(cashVal, expCash);

    return {
      ok: mOnline && mCash,
      code: (mOnline && mCash) ? 'ok' : 'mismatch',
      mOnline,
      mCash,
      expOnline,
      expCash,
      onlineVal,
      cashVal,
    };
  }

  function updateMatchAlert(card) {
    const alertEl = card.querySelector('[data-field="distinta_alert"]');
    const inputs = getPaymentInputs(card);
    if (!alertEl || !inputs.online || !inputs.cash) return;

    inputs.online.classList.remove('is-valid', 'is-invalid');
    inputs.cash.classList.remove('is-valid', 'is-invalid');

    const weekLabel = weekLabelText();
    const state = getMatchState(card);

    if (state.code === 'distinta-error') {
      alertEl.className = 'alert alert-secondary py-2 mb-0 delivery-distinta-alert';
      alertEl.textContent = 'Verifica distinta cassa non disponibile: ' + (state.message || 'errore');
      return;
    }

    if (state.code === 'distinta-unavailable') {
      alertEl.className = 'alert alert-secondary py-2 mb-0 delivery-distinta-alert';
      alertEl.textContent = 'Verifica distinta cassa (' + weekLabel + '): dati non disponibili per la settimana selezionata.';
      return;
    }

    if (state.code === 'provider-missing-zero') {
      inputs.online.classList.add('is-valid');
      inputs.cash.classList.add('is-valid');
      alertEl.className = 'alert alert-success py-2 mb-0 delivery-distinta-alert';
      alertEl.textContent = 'Verifica distinta cassa (' + weekLabel + '): nessuna movimentazione trovata per questo provider, valori 0 / 0 coerenti (OK).';
      return;
    }

    if (state.code === 'provider-missing') {
      alertEl.className = 'alert alert-warning py-2 mb-0 delivery-distinta-alert';
      alertEl.textContent = 'Verifica distinta cassa (' + weekLabel + '): nessun valore trovato per questo provider.';
      return;
    }

    if (state.code === 'missing-inputs') {
      alertEl.className = 'alert alert-secondary py-2 mb-0 delivery-distinta-alert';
      alertEl.textContent = 'Verifica distinta cassa: campi pagamento non trovati.';
      return;
    }

    if (state.mOnline) inputs.online.classList.add('is-valid'); else inputs.online.classList.add('is-invalid');
    if (state.mCash) inputs.cash.classList.add('is-valid'); else inputs.cash.classList.add('is-invalid');

    if (state.ok) {
      alertEl.className = 'alert alert-success py-2 mb-0 delivery-distinta-alert';
      alertEl.textContent = 'Verifica distinta cassa (' + weekLabel + '): pagamenti coerenti (OK).';
      return;
    }

    const diffs = [];
    if (!state.mOnline) diffs.push('Online');
    if (!state.mCash) diffs.push('Contanti');
    alertEl.className = 'alert alert-warning py-2 mb-0 delivery-distinta-alert';
    alertEl.textContent = 'Verifica distinta cassa (' + weekLabel + '): valori non coerenti per ' + diffs.join(' e ') + '. Il salvataggio è bloccato finché non coincidono con la distinta.';
  }

  function updateCard(card) {
    const online = card.querySelector('[data-field="payment_online"]');
    const cash = card.querySelector('[data-field="payment_cash"]');
    const orders = card.querySelector('[data-field="orders"]');
    const openingPctEl = card.querySelector('[data-field="opening_pct"]');
    const complaints = card.querySelector('[data-field="complaints_received"]');
    const appeals = card.querySelector('[data-field="appeals_accepted"]');
    const rating = card.querySelector('[data-field="rating"]');

    const onlineVal = parseNum(online ? online.value : 0);
    const cashVal = parseNum(cash ? cash.value : 0);
    const total = onlineVal + cashVal;
    setReadonly(card, 'total_payment', fmt2(total));

    const openingRaw = openingPctEl && String(openingPctEl.value || '').trim() !== '' ? parseNum(openingPctEl.value) : null;
    const openingPct = normalizeOpeningPct(card, openingRaw);
    let openingPotential = '';
    let openingLost = '';
    if (openingPct !== null && openingPct > 0) {
      const potential = total / (openingPct / 100);
      const lost = Math.max(potential - total, 0);
      openingPotential = fmt2(potential);
      openingLost = fmt2(lost);
    }
    setReadonly(card, 'opening_potential_sales', openingPotential);
    setReadonly(card, 'opening_lost_sales', openingLost);

    const ordersVal = parseIntSafe(orders ? orders.value : 0);
    const complaintsVal = parseIntSafe(complaints ? complaints.value : 0);
    const appealsVal = parseIntSafe(appeals ? appeals.value : 0);

    const pct = ordersVal > 0 ? (complaintsVal / ordersVal) * 100 : 0;
    const netComplaints = Math.max(complaintsVal - appealsVal, 0);
    const pctNet = ordersVal > 0 ? (netComplaints / ordersVal) * 100 : 0;

    setReadonly(card, 'complaints_pct', fmt2(pct));
    setReadonly(card, 'complaints_pct_net', fmt2(pctNet));

    const ratingVal = rating && String(rating.value || '').trim() !== '' ? parseNum(rating.value) : null;
    const prevRatingRaw = card.getAttribute('data-prev-rating');
    const prevRating = prevRatingRaw && String(prevRatingRaw).trim() !== '' ? parseNum(prevRatingRaw) : null;

    let delta = '';
    if (ratingVal !== null && prevRating !== null) {
      delta = fmtSigned2(ratingVal - prevRating);
    }
    setReadonly(card, 'rating_delta', delta);

    updateMatchAlert(card);
  }

  function updateAllAlerts() {
    const cards = document.querySelectorAll('.delivery-card');
    cards.forEach(function (card) {
      updateMatchAlert(card);
    });
  }

  function getBlockingVerificationIssues() {
    const cards = Array.from(document.querySelectorAll('.delivery-card'));
    const issues = [];
    cards.forEach(function (card) {
      const state = getMatchState(card);
      if (state.ok) return;
      const plat = getCardPlatformLabel(card);
      let msg = '';
      switch (state.code) {
        case 'distinta-error':
          msg = plat + ': errore lettura distinta cassa';
          break;
        case 'distinta-unavailable':
          msg = plat + ': distinta cassa non disponibile per la settimana selezionata';
          break;
        case 'provider-missing':
          msg = plat + ': provider non trovato nella distinta cassa';
          break;
        case 'mismatch': {
          const bad = [];
          if (!state.mOnline) bad.push('Online');
          if (!state.mCash) bad.push('Contanti');
          msg = plat + ': importi non coerenti (' + bad.join(' / ') + ')';
          break;
        }
        default:
          msg = plat + ': verifica distinta non superata';
      }
      issues.push(msg);
    });
    return issues;
  }

  function bind() {
    const cards = document.querySelectorAll('.delivery-card');
    cards.forEach(function (card) {
      const inputs = card.querySelectorAll('input');
      inputs.forEach(function (i) {
        i.addEventListener('input', function () { updateCard(card); });
        i.addEventListener('change', function () { updateCard(card); });
      });

      const importBtn = card.querySelector('[data-action="import-provider"]');
      if (importBtn) {
        importBtn.addEventListener('click', function () {
          importPaymentsIntoCard(card, true);
        });
      }

      updateCard(card);
    });

    const wkHiddenEl = document.getElementById('weekStart');
    const wkPickerEl = document.getElementById('weekPicker');

    function syncWeekStart() {
      if (!wkPickerEl || !wkHiddenEl) return;
      const mon = parseWeekValue(wkPickerEl.value);
      if (!mon) return;
      wkHiddenEl.value = isoDate(mondayOf(mon));
    }

    if (wkPickerEl) {
      wkPickerEl.addEventListener('change', function () {
        syncWeekStart();
        loadDistintaTotals();
      });
    }

    const importAllBtn = document.getElementById('importAllDeliveryPayments');
    if (importAllBtn) {
      importAllBtn.addEventListener('click', function () {
        importPaymentsAll(true);
      });
    }

    const form = document.getElementById('deliveryForm');
    if (form) {
      form.addEventListener('submit', function (ev) {
        cards.forEach(function (card) { updateCard(card); });
        const issues = getBlockingVerificationIssues();
        if (issues.length) {
          ev.preventDefault();
          alert('Salvataggio bloccato: verifica distinta cassa non superata.\n\n' + issues.join('\n'));
        }
      });
    }

    loadDistintaTotals();
    if (wkHiddenEl && wkPickerEl && !String(wkPickerEl.value || '').trim()) {
      const parsed = parseIsoDate(wkHiddenEl.value);
      if (parsed) wkPickerEl.value = weekValueFromDate(parsed);
    }
    syncWeekStart();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
})();
