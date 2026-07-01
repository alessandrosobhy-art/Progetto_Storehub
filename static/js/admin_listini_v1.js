(() => {
  const $ = (sel) => document.querySelector(sel);

  const normalize = (v) => {
    if (v === null || v === undefined) return '';
    return String(v).trim();
  };


  const defaultForColumn = (col) => {
    const u = String(col || '').toUpperCase();
    if (u === 'CONV' || u === 'QTACAR' || u === 'QTAINT' || u === 'PREZZO') return '0';
    if (u.includes('QTA') || u.includes('PREZZ') || u.includes('COST') || u.includes('IVA') || u.includes('PERC') || u.includes('%')) return '0';
    return '';
  };

  const keyOf = (row, supplierCol, descCol) => {
    const s = normalize(row?.[supplierCol]);
    const d = normalize(row?.[descCol]);
    if (!s || !d) return '';
    return (s + '␟' + d).toLowerCase();
  };

  const escapeHtml = (s) => String(s ?? '')
    .replaceAll('&','&amp;')
    .replaceAll('<','&lt;')
    .replaceAll('>','&gt;')
    .replaceAll('"','&quot;')
    .replaceAll("'",'&#39;');

  const fetchJson = async (url, opts={}) => {
    const r = await fetch(url, opts);
    const ct = (r.headers.get('content-type') || '').toLowerCase();
    if (ct.includes('application/json')) {
      const j = await r.json();
      if (!r.ok) {
        throw new Error(j?.error || `HTTP ${r.status}`);
      }
      return j;
    }
    const txt = await r.text();
    throw new Error(`Risposta non JSON (HTTP ${r.status}). ${txt?.slice(0, 200) || ''}`);
  };

  document.addEventListener('DOMContentLoaded', () => {
    const lstType = $('#lstType');
    const priceList = $('#priceList');
    const btnLoad = $('#btnLoad');
    const btnApply = $('#btnApply');
    const btnExportAll = $('#btnExportAll');
    const statusEl = $('#lstStatus');
    const wrap = $('#tableWrap');
    const searchEl = $('#lstSearch');
    const btnResetFilters = $('#btnResetFilters');
    const badgeCount = $('#lstCount');
    const badgeDirty = $('#lstDirty');
    const btnDeleteSelected = $('#btnDeleteSelected');
    const badgeSelected = $('#lstSelected');
    const copySourceList = $('#copySourceList');
    const copyTargetList = $('#copyTargetList');
    const copyType = $('#copyType');
    const copyOverwrite = $('#copyOverwrite');
    const copySelectedOnly = $('#copySelectedOnly');
    const btnCopyProducts = $('#btnCopyProducts');
    const copyStatus = $('#copyStatus');
    const btnExportListino = $('#btnExportListino');
    const exportStatus = $('#exportStatus');

    const csvImportCard = $('#csvImportCard');
    const csvFile = $('#csvFile');
    const csvSupplierFilter = $('#csvSupplierFilter');
    const btnImportCsv = $('#btnImportCsv');
    const btnClearCsv = $('#btnClearCsv');
    const csvInfo = $('#csvInfo');
    const csvError = $('#csvError');

    const newProdCard = $('#newProdCard');
    const newProdFields = $('#newProdFields');
    const btnAddProd = $('#btnAddProd');
    const newProdError = $('#newProdError');

    const applyModalEl = $('#applyModal');
    const applyModalTitle = $('#applyModalTitle');
    const applyModalMeta = $('#applyModalMeta');
    const btnApplyConfirm = $('#btnApplyConfirm');

    const progressModalEl = $('#progressModal');
    const progressTitle = $('#progressTitle');
    const progressText = $('#progressText');
    const progressBar = $('#progressBar');
    const progressLog = $('#progressLog');
    const progressClose = $('#progressClose');
    const progressCloseX = $('#progressCloseX');

    let applyModal = null;
    let progressModal = null;
    if (window.bootstrap && applyModalEl) applyModal = new bootstrap.Modal(applyModalEl);
    if (window.bootstrap && progressModalEl) progressModal = new bootstrap.Modal(progressModalEl);

    const state = {
      loaded: false,
      dirty: false,
      listinoType: 'FoodPaper',
      priceListId: '',
      priceListName: '',
      priceLists: [],
      table: '',
      columns: [],
      rows: [],
      supplierCol: 'FORNITORE',
      descCol: 'DESCRIZIONE',
      keyCol: null,
      sourceStore: '9001',
      // snapshot per delta
      baseline: new Map(),
      selectedKeys: new Set(),
      pendingDeletes: [],
      pendingDeleteKeys: new Set(),
      search: '',
      lastAction: null, // 'master' | 'all'
      newProd: {},
      lastAddedKey: null,
      suppliers: [],
      groups: [],
      types: [],
    };

    const syncSelectedPriceListState = () => {
      const selectedId = normalize(priceList?.value || state.priceListId);
      state.priceListId = selectedId;
      if (priceList) {
        const selectedOption = Array.from(priceList.options || []).find(o => normalize(o.value) === selectedId);
        if (selectedOption) state.priceListName = normalize(selectedOption.textContent);
      }
      return selectedId;
    };


    const setCsvUi = (msg=null, isError=false) => {
      if (csvError) csvError.textContent = isError ? (msg || '') : '';
      if (csvInfo) {
        if (msg && !isError) {
          csvInfo.textContent = msg;
          csvInfo.classList.remove('d-none');
        } else {
          csvInfo.textContent = '';
          csvInfo.classList.add('d-none');
        }
      }
    };

    const onCsvFileChange = () => {
      setCsvUi(null, false);
      const hasFile = !!(csvFile && csvFile.files && csvFile.files[0]);
      if (btnImportCsv) btnImportCsv.disabled = !(state.loaded && hasFile);
      if (btnClearCsv) btnClearCsv.disabled = !hasFile;
    };

    const selectedCsvSuppliers = () => {
      if (!csvSupplierFilter) return new Set();
      return new Set(Array.from(csvSupplierFilter.selectedOptions || [])
        .map(o => normalize(o.value || o.textContent).toLowerCase())
        .filter(Boolean));
    };

    const syncCsvSupplierFilter = () => {
      if (!csvSupplierFilter) return;
      const current = new Set(Array.from(csvSupplierFilter.selectedOptions || []).map(o => o.value));
      const values = new Set();
      const supplierCol = state.supplierCol || 'FORNITORE';
      for (const s of state.suppliers || []) {
        const raw = normalize(s?.Fornitore || s?.fornitore || s?.name || s?.code || s);
        if (raw) values.add(raw);
      }
      for (const r of state.rows || []) {
        const raw = normalize(r?.[supplierCol]);
        if (raw) values.add(raw);
      }
      const opts = Array.from(values).sort((a, b) => a.localeCompare(b, 'it', { sensitivity: 'base' }));
      csvSupplierFilter.innerHTML = opts.map(v => `<option value="${escapeHtml(v)}" ${current.has(v) ? 'selected' : ''}>${escapeHtml(v)}</option>`).join('');
    };

    const importCsv = async () => {
      if (!state.loaded) return;
      const file = csvFile?.files?.[0];
      if (!file) return;

      setCsvUi(null, false);
      setStatus('Import CSV in corso…', 'muted');
      if (btnImportCsv) btnImportCsv.disabled = true;

      try {
        const fd = new FormData();
        fd.append('type', state.listinoType || 'FoodPaper');
        fd.append('price_list_id', syncSelectedPriceListState() || '');
        fd.append('file', file);

        const r = await fetch('/admin/api/listini/import-csv', {
          method: 'POST',
          headers: { 'Accept': 'application/json' },
          body: fd,
        });

        const ct = (r.headers.get('content-type') || '').toLowerCase();
        const j = ct.includes('application/json') ? await r.json() : null;
        if (!r.ok) throw new Error(j?.error || `HTTP ${r.status}`);
        if (!j?.ok) throw new Error(j?.error || 'Errore import CSV');

        let rows = Array.isArray(j.rows) ? j.rows : [];
        const mappedCols = Array.isArray(j.mapped_cols) ? j.mapped_cols : [];
        const rowsBeforeFilter = rows.length;
        const supplierFilter = selectedCsvSuppliers();
        if (supplierFilter.size) {
          rows = rows.filter(rr => supplierFilter.has(normalize(rr?.[state.supplierCol] || rr?.FORNITORE || rr?.Fornitore).toLowerCase()));
        }

        // Ensure key columns are included for merge
        if (state.descCol && !mappedCols.includes(state.descCol)) mappedCols.push(state.descCol);
        if (state.supplierCol && !mappedCols.includes(state.supplierCol)) mappedCols.push(state.supplierCol);

        const idxMap = new Map();
        for (const rr of state.rows) {
          const kk = keyOf(rr, state.supplierCol, state.descCol);
          if (kk) idxMap.set(kk, rr);
        }

        let added = 0;
        let updated = 0;
        let skippedLocal = 0;

        for (const rr of rows) {
          const kk = keyOf(rr, state.supplierCol, state.descCol);
          if (!kk) { skippedLocal += 1; continue; }

          const existing = idxMap.get(kk);
          if (existing) {
            for (const c of mappedCols) {
              if (!state.columns.includes(c)) continue;
              if (rr[c] !== undefined) existing[c] = rr[c];
            }
            updated += 1;
          } else {
            const nr = {};
            for (const c of state.columns) nr[c] = defaultForColumn(c);
            for (const c of mappedCols) {
              if (!state.columns.includes(c)) continue;
              if (rr[c] !== undefined) nr[c] = rr[c];
            }
            state.rows.push(nr);
            idxMap.set(kk, nr);
            added += 1;
          }
        }

        sortRowsDefault();
        buildTable();
        setDirty(true);

        const skipped = Number(j.rows_skipped || 0) + skippedLocal;
        const filterMsg = supplierFilter.size ? ` - Filtro fornitori ${supplierFilter.size} (${rowsBeforeFilter - rows.length} escluse)` : '';
        const msg = `CSV: ${rows.length} righe · Nuovi ${added} · Aggiornati ${updated}` + (skipped ? ` · Scartati ${skipped}` : '');
        setCsvUi(filterMsg ? `${msg}${filterMsg}` : msg, false);
        setStatus('Import CSV completato.', 'success');

      } catch (e) {
        console.error(e);
        setCsvUi(String(e.message || e), true);
        setStatus('Errore import CSV.', 'danger');
      } finally {
        onCsvFileChange();
      }
    };


    const setStatus = (msg, kind='muted') => {
      if (!statusEl) return;
      statusEl.className = 'small text-' + kind;
      statusEl.textContent = msg;
    };

    const setDirty = (v) => {
      state.dirty = !!v;
      if (badgeDirty) badgeDirty.classList.toggle('d-none', !state.dirty);
    };

    const setCopyStatus = (msg, kind='muted') => {
      if (!copyStatus) return;
      copyStatus.className = 'small mt-2 text-' + kind;
      copyStatus.textContent = msg || '';
    };

    const setExportStatus = (msg, kind='muted') => {
      if (!exportStatus) return;
      exportStatus.className = 'small mt-2 text-' + kind;
      exportStatus.textContent = msg || '';
    };

    const syncCopyControls = () => {
      const lists = Array.isArray(state.priceLists) && state.priceLists.length
        ? state.priceLists
        : Array.from(priceList?.options || []).map(o => ({ row_uuid: o.value, nome: o.textContent || o.value }));
      const opts = lists.map(p => `<option value="${escapeHtml(p.row_uuid || '')}">${escapeHtml(p.nome || '')}</option>`).join('');
      if (copySourceList && opts) copySourceList.innerHTML = opts;
      if (copyTargetList && opts) copyTargetList.innerHTML = opts;
      if (copySourceList && state.priceListId) copySourceList.value = state.priceListId;
      if (copyTargetList && state.priceListId && copyTargetList.options.length > 1) {
        const firstOther = Array.from(copyTargetList.options).find(o => o.value && o.value !== state.priceListId);
        if (firstOther) copyTargetList.value = firstOther.value;
      }
      if (copyType) {
        const current = state.listinoType || lstType?.value || 'FoodPaper';
        copyType.value = Array.from(copyType.options).some(o => o.value === current) ? current : '';
      }
      const validLists = lists.filter(p => normalize(p.row_uuid));
      if (btnCopyProducts) btnCopyProducts.disabled = validLists.length < 2;
    };

    const copyProducts = async () => {
      const sourceId = normalize(copySourceList?.value);
      const targetId = normalize(copyTargetList?.value);
      const type = normalize(copyType?.value);
      const overwrite = !!copyOverwrite?.checked;
      const selectedOnly = !!copySelectedOnly?.checked;

      if (!sourceId || !targetId) {
        setCopyStatus('Seleziona origine e destinazione.', 'danger');
        return;
      }
      if (sourceId === targetId) {
        setCopyStatus('Origine e destinazione devono essere diverse.', 'danger');
        return;
      }
      const selectedProducts = [];
      if (selectedOnly) {
        for (const r of state.rows || []) {
          const k = keyOf(r, state.supplierCol, state.descCol);
          if (!k || !state.selectedKeys.has(k)) continue;
          selectedProducts.push({
            supplier: normalize(r?.[state.supplierCol]),
            description: normalize(r?.[state.descCol]),
          });
        }
        if (!selectedProducts.length) {
          setCopyStatus('Seleziona almeno un prodotto nella tabella oppure togli "Solo selezionati".', 'danger');
          return;
        }
      }

      const sourceName = copySourceList?.selectedOptions?.[0]?.textContent || 'origine';
      const targetName = copyTargetList?.selectedOptions?.[0]?.textContent || 'destinazione';
      const labelType = type || 'tutti i tipi';
      const scopeLabel = selectedOnly ? `${selectedProducts.length} prodotti selezionati` : 'tutti i prodotti';
      const ok = window.confirm(`Copiare ${scopeLabel} da "${sourceName}" a "${targetName}" (${labelType})?`);
      if (!ok) return;

      try {
        if (btnCopyProducts) btnCopyProducts.disabled = true;
        setCopyStatus('Copia in corso...', 'muted');
        const j = await fetchJson('/admin/api/listini/copy-products', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
          body: JSON.stringify({
            source_price_list_id: sourceId,
            target_price_list_id: targetId,
            type,
            overwrite,
            products: selectedOnly ? selectedProducts : [],
          }),
        });
        const copied = Number(j.copied || 0);
        const selectedMsg = selectedOnly ? ` su ${selectedProducts.length} selezionati` : '';
        setCopyStatus(`${overwrite ? 'Copiati/aggiornati' : 'Copiati'} ${copied} prodotti${selectedMsg}.`, 'success');
        if (targetId === state.priceListId || sourceId === state.priceListId) {
          await loadListino();
        }
      } catch (e) {
        console.error(e);
        setCopyStatus(`Errore copia: ${e.message || String(e)}`, 'danger');
      } finally {
        syncCopyControls();
      }
    };

    const exportCurrentPricelist = async () => {
      const type = normalize(lstType?.value || state.listinoType || 'FoodPaper');
      const priceListId = normalize(priceList?.value || state.priceListId);
      if (!type) {
        setExportStatus('Seleziona un tipo listino.', 'danger');
        return;
      }
      const url = `/admin/api/listini/export?price_list_id=${encodeURIComponent(priceListId)}&type=${encodeURIComponent(type)}`;
      try {
        if (btnExportListino) btnExportListino.disabled = true;
        setExportStatus('Preparazione CSV...', 'muted');
        const r = await fetch(url, { headers: { 'Accept': 'text/csv,application/json' } });
        const ct = (r.headers.get('content-type') || '').toLowerCase();
        if (!r.ok) {
          if (ct.includes('application/json')) {
            const j = await r.json();
            throw new Error(j?.error || `HTTP ${r.status}`);
          }
          throw new Error(`HTTP ${r.status}`);
        }
        const blob = await r.blob();
        const cd = r.headers.get('content-disposition') || '';
        const m = /filename="?([^"]+)"?/i.exec(cd);
        const filename = m?.[1] || `listino_${type}.csv`;
        const a = document.createElement('a');
        const objectUrl = URL.createObjectURL(blob);
        a.href = objectUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(objectUrl);
        setExportStatus('CSV esportato.', 'success');
      } catch (e) {
        console.error(e);
        setExportStatus(`Errore export: ${e.message || String(e)}`, 'danger');
      } finally {
        if (btnExportListino) btnExportListino.disabled = false;
      }
    };

    const setCount = (visible, total) => {
      if (!badgeCount) return;
      badgeCount.textContent = `${visible} / ${total} righe`;
    };


    const updateSelectedUI = () => {
      const n = state.selectedKeys ? state.selectedKeys.size : 0;
      if (btnDeleteSelected) btnDeleteSelected.disabled = n === 0;
      if (badgeSelected) {
        if (n === 0) {
          badgeSelected.classList.add('d-none');
          badgeSelected.textContent = '0 selezionati';
        } else {
          badgeSelected.classList.remove('d-none');
          badgeSelected.textContent = `${n} selezionati`;
        }
      }
    };

    const getVisibleRowKeys = () => {
      const tbody = wrap?.querySelector('tbody');
      if (!tbody) return [];
      const keys = [];
      tbody.querySelectorAll('tr[data-row]').forEach(tr => {
        if (tr.style.display === 'none') return;
        const k = tr.getAttribute('data-key') || '';
        if (k) keys.push(k);
      });
      return keys;
    };

    const syncSelectAllState = () => {
      const selAll = wrap?.querySelector('#selAll');
      if (!selAll) return;
      const visible = getVisibleRowKeys();
      if (!visible.length) {
        selAll.checked = false;
        selAll.indeterminate = false;
        return;
      }
      const selected = state.selectedKeys || new Set();
      const selectedVisible = visible.filter(k => selected.has(k)).length;
      selAll.checked = selectedVisible === visible.length;
      selAll.indeterminate = selectedVisible > 0 && selectedVisible < visible.length;
    };

    const enableActions = () => {
      const ok = state.loaded && Array.isArray(state.rows) && state.rows.length > 0;
      if (btnApply) btnApply.disabled = !ok;
      if (btnExportAll) btnExportAll.disabled = !ok;
    };
    const findColByKeywords = (keywords) => {
      const kws = (keywords || []).map(k => String(k || '').toLowerCase());
      for (const c of state.columns) {
        const lc = String(c || '').toLowerCase();
        if (kws.some(k => k && lc.includes(k))) return c;
      }
      return null;
    };

    const clearNewProdError = () => {
      if (newProdError) newProdError.textContent = '';
    };

    const buildNewProdForm = () => {
  if (!newProdCard || !newProdFields) return;

  if (!state.loaded || !state.columns.length) {
    newProdCard.classList.add('d-none');
    state.newProdRequired = [];
    if (btnAddProd) btnAddProd.disabled = true;
    return;
  }

  newProdCard.classList.remove('d-none');
  clearNewProdError();

  state.newProd = {};
  state.newProdRequired = [];

  const colEq = (a, b) => String(a || '').toLowerCase() === String(b || '').toLowerCase();
  const realCol = (name) => state.columns.find(c => colEq(c, name)) || null;

  const supplierCol = state.supplierCol || findColByKeywords(['fornit']) || 'FORNITORE';
  const descCol = state.descCol || findColByKeywords(['descr']) || 'DESCRIZIONE';

  const groupCol = realCol('GRUPPO') || findColByKeywords(['grupp']);
  const codeCol  = realCol('CODICE') || findColByKeywords(['codic']);
  const unitCol  = realCol('UNITA')  || realCol('UNITÀ') || findColByKeywords(['unit', 'um', 'u.m']);
  const priceCol = realCol('PREZZO') || findColByKeywords(['prezzo', 'price']);
  const qtaCarCol = realCol('QTACAR') || findColByKeywords(['qtacar', 'qta car', 'qta_car']);
  const qtaIntCol = realCol('QTAINT') || findColByKeywords(['qtaint', 'qta int', 'qta_int']);

  const convCol = state.columns.find(c => String(c).toUpperCase() === 'CONV');

  // Build ordered list:
  // known first, then remaining columns, then CONV (FoodPaper)
  const used = new Set();
  const ordered = [];

  const pushCol = (c) => {
    if (!c) return;
    const rc = state.columns.find(x => colEq(x, c)) || c;
    const key = String(rc).toLowerCase();
    if (used.has(key)) return;
    used.add(key);
    ordered.push(rc);
  };

  pushCol(supplierCol);
  pushCol(descCol);
  pushCol(groupCol);
  pushCol(codeCol);
  pushCol(unitCol);
  pushCol(priceCol);
  pushCol(qtaCarCol);
  pushCol(qtaIntCol);

  // Add remaining columns (excluding CONV)
  for (const c of state.columns) {
    if (String(c).toUpperCase() === 'CONV') continue;
    const k = String(c).toLowerCase();
    if (used.has(k)) continue;
    used.add(k);
    ordered.push(c);
  }

  // Add virtual CONV only for FoodPaper
  if (state.listinoType === 'FoodPaper' && convCol) {
    ordered.push('CONV');
  }

  const labelMap = (c) => {
    const u = String(c || '').toUpperCase();
    if (u === String(supplierCol).toUpperCase()) return 'Fornitore';
    if (u === String(descCol).toUpperCase()) return 'Descrizione';
    if (u === 'GRUPPO') return 'Gruppo';
    if (u === 'CODICE') return 'Codice';
    if (u === 'UNITA' || u === 'UNITÀ') return 'Unità';
    if (u === 'PREZZO') return 'Prezzo';
    if (u === 'QTACAR') return 'QtaCar';
    if (u === 'QTAINT') return 'QtaInt';
    if (u === 'CONV') return 'CONV';
    return c;
  };

  // All fields are mandatory
  state.newProdRequired = ordered.slice();

  newProdFields.innerHTML = ordered.map(c => {
    const u = String(c || '').toUpperCase();
    const ph = (u === 'CONV' || u === 'QTACAR' || u === 'QTAINT' || u === 'PREZZO') ? '0' : c;
    if (u === String(supplierCol).toUpperCase()) {
      const opts = (state.suppliers || []).map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join('');
      return `
      <div class="col-12 col-md-3">
        <label class="form-label small text-muted mb-1">${escapeHtml(labelMap(c))} *</label>
        <select class="admin-listini-newprod-input form-select form-select-sm" data-new-col="${escapeHtml(c)}">
          <option value=""></option>${opts}
        </select>
      </div>
      `;
    }
    if (u === 'GRUPPO') {
      const opts = (state.groups || []).map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join('');
      return `
      <div class="col-12 col-md-3">
        <label class="form-label small text-muted mb-1">${escapeHtml(labelMap(c))} *</label>
        <select class="admin-listini-newprod-input form-select form-select-sm" data-new-col="${escapeHtml(c)}">
          <option value=""></option>${opts}
        </select>
      </div>
      `;
    }
    return `
    <div class="col-12 col-md-3">
      <label class="form-label small text-muted mb-1">${escapeHtml(labelMap(c))} *</label>
      <input class="admin-listini-newprod-input" type="text" data-new-col="${escapeHtml(c)}" placeholder="${escapeHtml(ph || '')}">
    </div>
    `;
  }).join('');

  newProdFields.querySelectorAll('[data-new-col]').forEach(inp => {
    const syncField = (ev) => {
      const el = ev.target;
      const c = el.getAttribute('data-new-col');
      if (!c) return;
      state.newProd[c] = el.value;
      clearNewProdError();
      validateNewProd();
    };
    inp.addEventListener('input', syncField);
    inp.addEventListener('change', syncField);
  });

  if (btnAddProd) {
    btnAddProd.disabled = true;
    btnAddProd.onclick = () => addNewProduct();
  }

  validateNewProd();
};

    const validateNewProd = () => {
  if (!btnAddProd) return;
  const required = Array.isArray(state.newProdRequired) ? state.newProdRequired : [];
  const missing = required.filter(c => normalize(state.newProd?.[c]) === '');
  btnAddProd.disabled = missing.length > 0;
};

    const addNewProduct = () => {
  clearNewProdError();

  const supplierCol = state.supplierCol;
  const descCol = state.descCol;

  const required = Array.isArray(state.newProdRequired) ? state.newProdRequired : [];
  const missing = required.filter(c => normalize(state.newProd?.[c]) === '');
  if (missing.length) {
    if (newProdError) newProdError.textContent = `Compila tutti i campi obbligatori. Mancano: ${missing.slice(0, 6).join(', ')}${missing.length > 6 ? '…' : ''}`;
    return;
  }

  const supplier = normalize(state.newProd?.[supplierCol] ?? state.newProd?.['FORNITORE'] ?? '');
  const descr = normalize(state.newProd?.[descCol] ?? state.newProd?.['DESCRIZIONE'] ?? '');

  if (!supplier || !descr) {
    if (newProdError) newProdError.textContent = 'Fornitore e Descrizione sono obbligatori.';
    return;
  }

  const row = {};
  for (const c of state.columns) row[c] = '';

  // assign all fields (including virtual CONV)
  for (const [k, v] of Object.entries(state.newProd || {})) {
    if (!k) continue;
    if (k === 'CONV') {
      row['CONV'] = v;
      continue;
    }
    const real = state.columns.find(c => String(c).toLowerCase() === String(k).toLowerCase());
    if (real) row[real] = v;
  }

  // enforce key cols with current casing
  row[supplierCol] = supplier;
  row[descCol] = descr;

  const k = keyOf(row, supplierCol, descCol);
  if (!k) {
    if (newProdError) newProdError.textContent = 'Chiave non valida.';
    return;
  }

  const existsNow = state.rows.some(r => keyOf(r, supplierCol, descCol) === k);
  if (existsNow) {
    if (newProdError) newProdError.textContent = 'Prodotto già presente (stesso Fornitore + Descrizione).';
    return;
  }

  state.rows.push(row);
  sortRowsDefault();
  buildTable();
  setDirty(true);
  enableActions();

  state.lastAddedKey = k;

  // clear inputs
  if (newProdFields) {
    newProdFields.querySelectorAll('[data-new-col]').forEach(inp => { inp.value = ''; });
  }
  state.newProd = {};
  validateNewProd();

  // scroll to new row
  const idx = state.rows.findIndex(r => keyOf(r, supplierCol, descCol) === k);
  if (idx >= 0) {
    const tr = wrap?.querySelector(`tr[data-row="${idx}"]`);
    if (tr && tr.scrollIntoView) tr.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
};


    const deleteSelectedRows = () => {
      const keys = Array.from(state.selectedKeys || []);
      if (!keys.length) return;
      const ok = window.confirm(`Eliminare ${keys.length} prodotti selezionati?\n\nL'operazione verrà applicata al database solo dopo Salva/Esporta.`);
      if (!ok) return;

      const supplierCol = state.supplierCol;
      const descCol = state.descCol;

      const remaining = [];
      for (const r of state.rows) {
        const k = keyOf(r, supplierCol, descCol);
        if (k && state.selectedKeys.has(k)) {
          if (!state.pendingDeleteKeys.has(k)) {
            const inBaseline = state.baseline && state.baseline.has(k);
            if (inBaseline) {
              const marker = { __deleted: true };
              marker[supplierCol] = r?.[supplierCol];
              marker[descCol] = r?.[descCol];
              state.pendingDeletes.push(marker);
              state.pendingDeleteKeys.add(k);
            }
          }
          continue;
        }
        remaining.push(r);
      }

      state.rows = remaining;
      state.selectedKeys.clear();
      sortRowsDefault();
      buildTable();
      setDirty(true);
      enableActions();
      setStatus(`Selezionati eliminati: ${keys.length}.`, 'muted');
    };


    const sortRowsDefault = () => {
      const sCol = state.supplierCol;
      const dCol = state.descCol;
      state.rows.sort((a, b) => {
        const as = normalize(a?.[sCol]).toLowerCase();
        const bs = normalize(b?.[sCol]).toLowerCase();
        const ad = normalize(a?.[dCol]).toLowerCase();
        const bd = normalize(b?.[dCol]).toLowerCase();
        const c1 = as.localeCompare(bs, 'it', { sensitivity: 'base' });
        if (c1 !== 0) return c1;
        return ad.localeCompare(bd, 'it', { sensitivity: 'base' });
      });
    };

    const buildBaseline = () => {
      state.baseline.clear();
      for (const r of state.rows) {
        const k = keyOf(r, state.supplierCol, state.descCol);
        if (!k) continue;
        const snap = {};
        for (const c of state.columns) snap[c] = normalize(r?.[c]);
        state.baseline.set(k, snap);
      }
    };

    const computeDeltaRows = () => {
      const deltas = [];
      for (const r of state.rows) {
        const k = keyOf(r, state.supplierCol, state.descCol);
        if (!k) continue;

        const base = state.baseline.get(k);
        if (!base) {
          deltas.push(r);
          continue;
        }
        let changed = false;
        for (const c of state.columns) {
          const now = normalize(r?.[c]);
          const old = normalize(base?.[c]);
          if (now !== old) { changed = true; break; }
        }
        if (changed) deltas.push(r);
      }
      return deltas;
    };

    const rebuildSearchAttr = (tr, row) => {
      try {
        const s = state.columns.map(c => normalize(row?.[c])).join(' ').toLowerCase();
        tr.setAttribute('data-search', s);
      } catch {
        tr.setAttribute('data-search', '');
      }
    };

    const applySearchFilter = () => {
      const term = (state.search || '').trim().toLowerCase();
      const tbody = wrap?.querySelector('tbody');
      if (!tbody) return;

      let visible = 0;
      const total = state.rows.length;

      tbody.querySelectorAll('tr[data-row]').forEach(tr => {
        const s = (tr.getAttribute('data-search') || '').toLowerCase();
        const ok = !term || s.includes(term);
        tr.style.display = ok ? '' : 'none';
        if (ok) visible += 1;
      });

      setCount(visible, total);
      if (btnResetFilters) btnResetFilters.disabled = !term;
      syncSelectAllState();
      updateSelectedUI();
    };

    const buildTable = () => {
      if (!wrap) return;
      if (!state.columns.length) {
        wrap.innerHTML = '<div class="text-muted small">Nessun dato caricato.</div>';
        return;
      }

      const cols = state.columns.slice();
      const thead = `
        <thead>
          <tr>
            <th class="admin-listini-row-sel">
              <input type="checkbox" class="form-check-input" id="selAll">
            </th>
            <th class="admin-listini-row-idx">#</th>
            ${cols.map(c => `<th>${escapeHtml(c)}</th>`).join('')}
          </tr>
        </thead>
      `;

      const tbody = state.rows.map((row, idx) => {
        const rowKey = keyOf(row, state.supplierCol, state.descCol);

        const tds = cols.map(c => {
          const v = row?.[c];
          const vStr = (v === null || v === undefined) ? '' : String(v);
          const isKey = (String(c).toLowerCase() === String(state.supplierCol).toLowerCase()) ||
                        (String(c).toLowerCase() === String(state.descCol).toLowerCase());
          return `
            <td>
              <input class="admin-listini-input ${isKey ? 'admin-listini-key' : ''}"
                     data-row="${idx}" data-col="${escapeHtml(c)}"
                     value="${escapeHtml(vStr)}">
            </td>
          `;
        }).join('');

        const rowSearch = cols.map(c => normalize(row?.[c])).join(' ').toLowerCase();
        const checked = rowKey && state.selectedKeys && state.selectedKeys.has(rowKey) ? 'checked' : '';
        return `<tr data-row="${idx}" data-key="${escapeHtml(rowKey)}" data-search="${escapeHtml(rowSearch)}">
          <td class="admin-listini-row-sel">
            <input type="checkbox" class="form-check-input admin-listini-sel" data-key="${escapeHtml(rowKey)}" ${checked}>
          </td>
          <td class="admin-listini-row-idx">${idx + 1}</td>
          ${tds}
        </tr>`;
      }).join('');

      wrap.innerHTML = `
        <table class="admin-listini-table table table-sm mb-0">
          ${thead}
          <tbody>${tbody}</tbody>
        </table>
      `;


      // selection handlers
      const selAll = wrap.querySelector('#selAll');
      if (selAll) {
        selAll.addEventListener('change', () => {
          const want = !!selAll.checked;
          const tbody = wrap.querySelector('tbody');
          if (!tbody) return;
          tbody.querySelectorAll('tr[data-row]').forEach(tr => {
            if (tr.style.display === 'none') return;
            const k = tr.getAttribute('data-key') || '';
            if (!k) return;
            const cb = tr.querySelector('input.admin-listini-sel');
            if (cb) cb.checked = want;
            if (want) state.selectedKeys.add(k);
            else state.selectedKeys.delete(k);
          });
          syncSelectAllState();
          updateSelectedUI();
        });
      }

      wrap.querySelectorAll('input.admin-listini-sel').forEach(cb => {
        cb.addEventListener('change', (ev) => {
          const el = ev.target;
          const k = el.getAttribute('data-key') || '';
          if (!k) return;
          if (el.checked) state.selectedKeys.add(k);
          else state.selectedKeys.delete(k);
          syncSelectAllState();
          updateSelectedUI();
        });
      });

      syncSelectAllState();
      updateSelectedUI();

      wrap.querySelectorAll('input.admin-listini-input').forEach(inp => {
        inp.addEventListener('input', (ev) => {
          const el = ev.target;
          const ri = parseInt(el.dataset.row || '-1', 10);
          const col = el.dataset.col;
          if (Number.isNaN(ri) || ri < 0 || !col) return;
          if (!state.rows[ri]) return;

          state.rows[ri][col] = el.value;
          setDirty(true);

          const tr = el.closest('tr');
          if (tr) rebuildSearchAttr(tr, state.rows[ri]);
          applySearchFilter();
        });
      });

      applySearchFilter();
    };

    const loadListino = async () => {
      const type = (lstType?.value || 'FoodPaper').trim() || 'FoodPaper';
      state.listinoType = type;
      syncSelectedPriceListState();

      setStatus('Caricamento...', 'muted');
      if (btnLoad) btnLoad.disabled = true;
      if (btnApply) btnApply.disabled = true;
      if (btnExportAll) btnExportAll.disabled = true;
      setDirty(false);
      if (wrap) wrap.innerHTML = `<div class="admin-listini-loading text-muted small">Caricamento…</div>`;

      try {
        const j = await fetchJson(`/admin/api/listini/load?type=${encodeURIComponent(type)}&price_list_id=${encodeURIComponent(state.priceListId || '')}`, {
          headers: { 'Accept': 'application/json' },
        });

        if (!j.ok) throw new Error(j.error || 'Errore caricamento listino');

        state.loaded = true;
        state.columns = Array.isArray(j.columns) ? j.columns : [];
        state.rows = Array.isArray(j.rows) ? j.rows : [];
        state.selectedKeys = new Set();
        state.pendingDeletes = [];
        state.pendingDeleteKeys = new Set();

        state.table = j.table || '';
        state.sourceStore = j.source_store || '9001';
        state.priceListId = j.price_list_uuid || state.priceListId || '';
        state.priceListName = j.price_list_name || '';
        state.priceLists = Array.isArray(j.price_lists) ? j.price_lists : [];
        if (priceList && state.priceLists.length) {
          priceList.innerHTML = state.priceLists.map(p => `<option value="${escapeHtml(p.row_uuid || '')}">${escapeHtml(p.nome || '')}</option>`).join('');
          priceList.value = state.priceListId;
        }
        syncCopyControls();

        state.keyCol = j.key_column || null;
        state.descCol = j.desc_column || state.descCol;
        state.supplierCol = j.supplier_column || state.supplierCol;
        state.suppliers = Array.isArray(j.suppliers) ? j.suppliers : [];
        state.groups = Array.isArray(j.groups) ? j.groups : [];
        state.types = Array.isArray(j.types) ? j.types : [];

        // default sort
        sortRowsDefault();

        // snapshot delta
        buildBaseline();

        // nuovo prodotto
        buildNewProdForm();
        syncCsvSupplierFilter();

        // CSV import
        if (csvImportCard) csvImportCard.classList.remove('d-none');
        if (csvFile) csvFile.value = '';
        if (btnImportCsv) btnImportCsv.disabled = true;
        if (btnClearCsv) btnClearCsv.disabled = true;
        setCsvUi(null, false);

        // reset search
        state.search = '';
        if (searchEl) searchEl.value = '';

        buildTable();
        setStatus(`Caricato: ${state.priceListName || 'Listino'} · ${type} · ${state.rows.length} righe`, 'muted');
        setDirty(false);
        enableActions();
      } catch (e) {
        console.error(e);
        state.loaded = false;
        state.columns = [];
        state.rows = [];
        if (wrap) wrap.innerHTML = `<div class="alert alert-danger mb-0">Errore: ${escapeHtml(e.message || String(e))}</div>`;
        setStatus('Errore caricamento.', 'danger');
      } finally {
        if (btnLoad) btnLoad.disabled = false;
      }
    };

    const openConfirm = (actionScope) => {
      syncSelectedPriceListState();
      state.lastAction = actionScope; // 'master' | 'all'
      const deltas = computeDeltaRows();
      const deletes = Array.isArray(state.pendingDeletes) ? state.pendingDeletes.length : 0;
      const title = actionScope === 'all' ? 'Conferma applicazione (tutti gli store)' : 'Conferma salvataggio SQL';
      const meta = [
        `Listino: ${state.listinoType}`,
        state.priceListName ? `Elenco: ${state.priceListName}` : '',
        `Righe totali: ${state.rows.length}`,
        `Righe da inviare: ${deltas.length + deletes}`,
        deletes ? `Eliminazioni: ${deletes}` : '',
        `Sorgente: store ${state.sourceStore}`,
        state.table ? `Tabella: ${state.table}` : '',
      ].filter(Boolean).join(' · ');

      if (applyModalTitle) applyModalTitle.textContent = title;
      if (applyModalMeta) applyModalMeta.textContent = meta;

      if (applyModal) {
        applyModal.show();
      } else {
        const ok = window.confirm(`${title}\n\n${meta}`);
        if (ok) startJob(actionScope);
      }
    };

    const setProgressUI = ({title, text, pct, logs, finished}) => {
      if (progressTitle) progressTitle.textContent = title || 'Operazione in corso…';
      if (progressText) progressText.textContent = text || '';
      if (progressBar) progressBar.style.width = `${Math.max(0, Math.min(100, pct || 0))}%`;
      if (progressLog) {
        const arr = Array.isArray(logs) ? logs : [];
        if (arr.length) {
          progressLog.classList.remove('d-none');
          progressLog.textContent = arr.join('\n');
        } else {
          progressLog.classList.add('d-none');
          progressLog.textContent = '';
        }
      }
      if (progressClose) progressClose.disabled = !finished;
      if (progressCloseX) progressCloseX.disabled = !finished;
    };

    let pollTimer = null;
    const stopPolling = () => {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = null;
    };

    const pollProgress = async (jobId) => {
      try {
        const j = await fetchJson(`/admin/api/listini/apply/progress?job_id=${encodeURIComponent(jobId)}`, {
          headers: { 'Accept': 'application/json' },
        });
        const job = j.job || {};
        const done = Number(job.done || 0);
        const total = Number(job.total || 0);
        const pct = total > 0 ? Math.round((done / total) * 100) : 0;
        const cs = normalize(job.current_store);
        const title = job.finished
          ? `Completato · OK: ${Number(job.stores_ok||0)} · Errori: ${Number(job.stores_fail||0)}`
          : 'Operazione in corso…';
        const text = job.finished
          ? `Fatto: ${done}/${total}`
          : `Fatto: ${done}/${total}${cs ? ' · Store: ' + cs : ''}`;

        setProgressUI({ title, text, pct, logs: job.logs || [], finished: !!job.finished });

        if (job.finished) {
          stopPolling();
          // Dopo export: ricarica baseline perché ora il DB è allineato a ciò che vedi
          buildBaseline();
          setDirty(false);
          setStatus(`Operazione completata. OK: ${Number(job.stores_ok||0)} · Errori: ${Number(job.stores_fail||0)}`, Number(job.stores_fail||0) ? 'warning' : 'success');
          // clear pending deletions/selection
          state.pendingDeletes = [];
          state.pendingDeleteKeys = new Set();
          state.selectedKeys = new Set();
          updateSelectedUI();
          syncSelectAllState();
        }
      } catch (e) {
        console.error(e);
        // Non fermiamo il polling al primo errore: potrebbe essere transitorio
      }
    };

    const startJob = async (scope) => {
      if (!state.loaded) return;
      const selectedPriceListId = syncSelectedPriceListState();
      if (!selectedPriceListId) {
        setStatus('Seleziona un elenco prezzi prima di salvare.', 'danger');
        if (applyModal) applyModal.hide();
        return;
      }

      const deltas = computeDeltaRows();
      const deletes = Array.isArray(state.pendingDeletes) ? state.pendingDeletes : [];
      const rowsToSend = deltas.concat(deletes);

      if (!rowsToSend.length) {
        setStatus('Nessuna modifica da salvare.', 'muted');
        if (applyModal) applyModal.hide();
        return;
      }

      const payload = {
        type: state.listinoType,
        price_list_id: selectedPriceListId,
        source_store: '9001',
        columns: state.columns,
        rows: rowsToSend,
        scope: scope, // 'master' | 'all'
      };

      try {
        // UI lock
        if (btnLoad) btnLoad.disabled = true;
        if (btnApply) btnApply.disabled = true;
        if (btnExportAll) btnExportAll.disabled = true;
        if (btnApplyConfirm) btnApplyConfirm.disabled = true;

        setProgressUI({ title: 'Operazione in corso…', text: 'Avvio…', pct: 0, logs: [], finished: false });
        if (progressModal) progressModal.show();

        const j = await fetchJson('/admin/api/listini/apply/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
          body: JSON.stringify(payload),
        });

        const jobId = j.job_id;
        if (!jobId) throw new Error('job_id mancante');

        // primo poll immediato + interval
        await pollProgress(jobId);
        stopPolling();
        pollTimer = setInterval(() => pollProgress(jobId), 900);
      } catch (e) {
        console.error(e);
        setStatus(`Errore: ${e.message || String(e)}`, 'danger');
        setProgressUI({ title: 'Errore', text: e.message || String(e), pct: 0, logs: [], finished: true });
      } finally {
        if (btnLoad) btnLoad.disabled = false;
        enableActions();
        if (btnApplyConfirm) btnApplyConfirm.disabled = false;
        if (applyModal) applyModal.hide();
      }
    };

    // events
    if (btnLoad) btnLoad.addEventListener('click', loadListino);
    if (priceList) priceList.addEventListener('change', () => {
      syncSelectedPriceListState();
      loadListino();
    });

    if (csvFile) csvFile.addEventListener('change', onCsvFileChange);
    if (btnClearCsv) btnClearCsv.addEventListener('click', () => {
      if (csvFile) csvFile.value = '';
      onCsvFileChange();
      setCsvUi(null, false);
    });
    if (btnImportCsv) btnImportCsv.addEventListener('click', importCsv);

    if (searchEl) {
      searchEl.addEventListener('input', () => {
        state.search = searchEl.value || '';
        applySearchFilter();
      });
    }

    if (btnResetFilters) {
      btnResetFilters.addEventListener('click', () => {
        state.search = '';
        if (searchEl) searchEl.value = '';
        applySearchFilter();
      });
    }

    if (btnDeleteSelected) btnDeleteSelected.addEventListener('click', deleteSelectedRows);
    if (btnCopyProducts) btnCopyProducts.addEventListener('click', copyProducts);
    if (btnExportListino) btnExportListino.addEventListener('click', exportCurrentPricelist);
    if (btnApply) btnApply.addEventListener('click', () => openConfirm('master'));
    if (btnExportAll) btnExportAll.addEventListener('click', () => openConfirm('all'));
    if (btnApplyConfirm) btnApplyConfirm.addEventListener('click', () => startJob(state.lastAction || 'master'));

    syncCopyControls();

    // warn before unload
    window.addEventListener('beforeunload', (e) => {
      if (!state.dirty) return;
      e.preventDefault();
      e.returnValue = '';
    });
  });
})();
