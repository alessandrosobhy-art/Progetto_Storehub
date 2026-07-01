(function () {
  'use strict';

  const tbody = document.getElementById('spesaRowsTbody');
  const rowsCountInp = document.getElementById('rows_count');
  const btnAdd = document.getElementById('spesaAddRow');
  const tpl = document.getElementById('spesaRowTemplate');
  const grandTotalEl = document.getElementById('spesaGrandTotal');

  if (!tbody || !rowsCountInp) {
    return;
  }

  function parseNumberEU(v) {
    if (v === null || v === undefined) return 0;
    let s = String(v).trim();
    if (!s) return 0;
    // remove spaces
    s = s.replace(/\s+/g, '');
    // if both dot and comma, assume dot is thousands and comma is decimals
    if (s.indexOf(',') !== -1 && s.indexOf('.') !== -1) {
      s = s.replace(/\./g, '').replace(',', '.');
    } else if (s.indexOf(',') !== -1) {
      s = s.replace(',', '.');
    }
    const n = parseFloat(s);
    return isNaN(n) ? 0 : n;
  }

  function formatEU2(n) {
    try {
      const f = Number(n);
      if (!isFinite(f)) return '0,00';
      return f.toFixed(2).replace('.', ',');
    } catch (e) {
      return '0,00';
    }
  }

  function recalcRow(tr) {
    if (!tr) return { qta: 0, val: 0 };

    const colli = parseNumberEU((tr.querySelector('.spesa-colli') || {}).value);
    const pezzi = parseNumberEU((tr.querySelector('.spesa-pezzi') || {}).value);

    let unitFactor = parseNumberEU((tr.querySelector('.spesa-unit') || {}).value);
    if (!unitFactor || unitFactor <= 0) unitFactor = 1;

    const price = parseNumberEU((tr.querySelector('.spesa-price') || {}).value);

    const qta = colli + (pezzi / unitFactor);
    const val = qta * price;

    const qtaEl = tr.querySelector('.spesa-qta-tot');
    const valEl = tr.querySelector('.spesa-valore');

    if (qtaEl) qtaEl.value = qta > 0 ? formatEU2(qta) : '';
    if (valEl) valEl.value = (Math.abs(val) > 1e-9) ? formatEU2(val) : '';

    return { qta, val };
  }

  function recalcAll() {
    let total = 0;
    tbody.querySelectorAll('tr.spesa-row').forEach((tr) => {
      const r = recalcRow(tr);
      total += r.val;
    });

    if (grandTotalEl) {
      grandTotalEl.textContent = formatEU2(total);
    }
  }

  function attachRowListeners(tr) {
    if (!tr) return;
    const fields = tr.querySelectorAll('input, select');
    fields.forEach((el) => {
      const evt = (el.tagName === 'SELECT') ? 'change' : 'input';
      el.addEventListener(evt, () => {
        recalcRow(tr);
        recalcAll();
      });
    });
  }
function updateRowNumbers() {
    const rows = Array.from(tbody.querySelectorAll('tr.spesa-row'));
    rows.forEach((tr, idx) => {
      tr.dataset.rowIndex = String(idx);
      const firstCell = tr.querySelector('td');
      if (firstCell) firstCell.textContent = String(idx + 1);

      // rename inputs
      tr.querySelectorAll('input[name^="row-"], select[name^="row-"]').forEach((inp) => {
        const name = inp.getAttribute('name') || '';
        const m = name.match(/^row-(\d+)-(.*)$/);
        if (!m) return;
        const tail = m[2];
        inp.setAttribute('name', `row-${idx}-${tail}`);
      });
    });

    rowsCountInp.value = String(rows.length);
  }

  function addRow() {
    const currentCount = parseInt(rowsCountInp.value || '0', 10) || 0;
    if (!tpl) return;

    const html = tpl.innerHTML.replace(/__i__/g, String(currentCount)).replace(/__n__/g, String(currentCount + 1));
    const tmp = document.createElement('tbody');
    tmp.innerHTML = html.trim();
    const tr = tmp.querySelector('tr');
    if (!tr) return;

    tbody.appendChild(tr);
    rowsCountInp.value = String(currentCount + 1);

    attachRowListeners(tr);
    recalcRow(tr);
    recalcAll();
  }

  // init
  tbody.querySelectorAll('tr.spesa-row').forEach((tr) => attachRowListeners(tr));
  updateRowNumbers();
  recalcAll();

  if (btnAdd) {
    btnAdd.addEventListener('click', () => {
      addRow();
    });
  }


  // Validazione: se l'utente compila una riga, allora TUTTI i campi della riga devono essere compilati
  const form = document.getElementById('spesaForm');
  const validationAlert = document.getElementById('spesaValidationAlert');

  function clearRowValidation() {
    if (validationAlert) {
      validationAlert.classList.add('d-none');
      validationAlert.textContent = '';
    }
    tbody.querySelectorAll('.is-invalid').forEach((el) => el.classList.remove('is-invalid'));
  }

  function getField(tr, suffix) {
    return tr.querySelector(`[name$="-${suffix}"]`);
  }

  function fieldValue(el) {
    if (!el) return '';
    return String(el.value || '').trim();
  }

  function validateRowsBeforeSubmit(e) {
    if (!form) return;

    clearRowValidation();

    const invalidRows = [];
    const rows = Array.from(tbody.querySelectorAll('tr.spesa-row'));

    rows.forEach((tr, idx) => {
      const fields = {
        code: getField(tr, 'code'),
        desc: getField(tr, 'desc'),
        group: getField(tr, 'group'),
        price: getField(tr, 'price'),
        qtacar: getField(tr, 'qtacar'),
        unita: getField(tr, 'unita'),
        colli: getField(tr, 'colli'),
        pezzi: getField(tr, 'pezzi'),
      };

      const isEmpty = Object.values(fields).every((el) => fieldValue(el) === '');
      if (isEmpty) return;

      let ok = true;
      Object.values(fields).forEach((el) => {
        if (fieldValue(el) === '') {
          ok = false;
          if (el) el.classList.add('is-invalid');
        }
      });

      if (!ok) invalidRows.push(idx + 1);
    });

    if (invalidRows.length > 0) {
      e.preventDefault();
      e.stopPropagation();

      if (validationAlert) {
        validationAlert.textContent = 'Compila tutti i campi per le righe: ' + invalidRows.join(', ') + '.';
        validationAlert.classList.remove('d-none');
        validationAlert.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }
  }

  if (form) {
    form.addEventListener('submit', validateRowsBeforeSubmit);
  }


  // If browser autocompletes, recalc on load
  window.addEventListener('load', () => {
    recalcAll();
  });
})();
