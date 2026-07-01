(() => {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const fmtEur = (v) => (v === null || v === undefined || v === '' || isNaN(Number(v))) ? '—' : new Intl.NumberFormat('it-IT',{style:'currency',currency:'EUR'}).format(Number(v));
  const fmtNum = (v, d=2) => (v === null || v === undefined || v === '' || isNaN(Number(v))) ? '—' : new Intl.NumberFormat('it-IT',{minimumFractionDigits:d, maximumFractionDigits:d}).format(Number(v));
  const fmtPct = (v, d=2) => (v === null || v === undefined || v === '' || isNaN(Number(v))) ? '—' : `${fmtNum(Number(v), d)}%`;

  async function fetchJson(url, opts={}) {
    const r = await fetch(url, { headers: { 'Accept': 'application/json', ...(opts.headers||{}) }, ...opts });
    const ct = (r.headers.get('content-type')||'').toLowerCase();
    const j = ct.includes('application/json') ? await r.json() : null;
    if (!r.ok || (j && j.ok === false)) {
      throw new Error(j?.error || `HTTP ${r.status}`);
    }
    return j;
  }

  const ROLE_RATE_DEFAULTS = (Array.isArray(window.ROLE_RATE_DEFAULTS) && window.ROLE_RATE_DEFAULTS.length)
    ? window.ROLE_RATE_DEFAULTS
    : [
        { role_code: 'STORE_MANAGER', role_label: 'Store Manager' },
        { role_code: 'ASSISTANT', role_label: 'Assistant' },
        { role_code: 'BANCONISTA', role_label: 'Banconista' },
        { role_code: 'APPRENDISTA', role_label: 'Apprendista' },
        { role_code: 'STAGE', role_label: 'Stage' },
        { role_code: 'INTERMITTENTE', role_label: 'Intermittente' },
      ];

  const state = {
    profiles: [],
    selectedProfileId: null,
    currentExtras: [],
    currentRoleRates: [],
    employeeRows: [],
    selectedEmployeeRowIdx: null,
    lastProjection: null,
    cmoContracts: [],
  };

  function profileRow(p) {
    return `
      <tr data-id="${p.id}">
        <td class="text-nowrap">${p.id}</td>
        <td>${esc(p.code||'')}</td>
        <td>${esc(p.name||'')}</td>
        <td>${esc(p.sector||'')}</td>
        <td class="text-end">${p.default_hourly_rate==null ? '—' : fmtEur(p.default_hourly_rate)}</td>
        <td class="text-end">${fmtPct((Number(p.employer_inps_pct||0)*100),2)}</td>
        <td class="text-end">${fmtPct((Number(p.inail_pct||0)*100),2)}</td>
        <td class="text-center">${p.is_active ? '✅' : '—'}</td>
        <td class="text-end"><button class="btn btn-sm btn-outline-primary js-edit-profile">Apri</button></td>
      </tr>`;
  }

  function esc(s){ return String(s ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;'); }

  function renderProfiles() {
    const tb = $('#lcProfilesTable tbody');
    if (!tb) return;
    tb.innerHTML = (state.profiles||[]).map(profileRow).join('') || '<tr><td colspan="9" class="text-center text-muted py-3">Nessun profilo</td></tr>';
    updateProfileSelects();
  }

  function updateProfileSelects() {
    for (const sel of ['#lcProfileSelectTest','#lcEmpCompanyProfile','#lcImportCompanyProfile']) {
      const el = $(sel);
      if (!el) continue;
      const cur = el.value;
      el.innerHTML = '<option value="">(seleziona)</option>' + (state.profiles||[]).map(p => `<option value="${p.id}">${esc(p.code)} · ${esc(p.name)}</option>`).join('');
      if (cur && Array.from(el.options).some(o=>o.value===cur)) el.value = cur;
      if (!el.value && state.profiles.length) el.value = String(state.profiles[0].id);
    }
    if (!state.selectedProfileId && state.profiles.length) state.selectedProfileId = state.profiles[0].id;
  }

  function resetProfileForm(p={}) {
    state.currentRoleRates = [];
    $('#lcProfileId').value = p.id || '';
    $('#lcProfileCode').value = p.code || '';
    $('#lcProfileName').value = p.name || '';
    $('#lcProfileSector').value = p.sector || '';
    $('#lcProfileDefaultHourly').value = p.default_hourly_rate ?? '';
    $('#lcProfileAnnualHours').value = p.annual_hours_full_time ?? 2076;
    $('#lcProfileINPS').value = p.employer_inps_pct ?? 0.30;
    $('#lcProfileINAIL').value = p.inail_pct ?? 0.02;
    $('#lcProfileTFR').value = p.tfr_pct ?? 0.0741;
    $('#lcProfileFeriePerm').value = p.ferie_permessi_accrual_pct ?? 0.12;
    $('#lcProfile13').value = p.tredicesima_pct ?? 0.0833;
    $('#lcProfile14').value = p.quattordicesima_pct ?? 0;
    $('#lcProfileOther').value = p.other_accruals_pct ?? 0;
    $('#lcProfileActive').checked = p.is_active !== false;
    $('#lcProfileNotes').value = p.notes || '';
  }

  function renderExtras() {
    const tb = $('#lcExtrasTable tbody');
    if (!tb) return;
    const rows = (state.currentExtras||[]);
    tb.innerHTML = rows.map((e,i)=>`
      <tr data-idx="${i}">
        <td><input class="form-control form-control-sm js-extra-code" value="${esc(e.extra_code||'')}"></td>
        <td><input class="form-control form-control-sm js-extra-label" value="${esc(e.label||'')}"></td>
        <td><input class="form-control form-control-sm js-extra-pct" type="number" step="0.0001" value="${e.pct ?? 0}"></td>
        <td><input class="form-control form-control-sm js-extra-sort" type="number" step="1" value="${e.sort_order ?? (i+1)*10}"></td>
        <td><input class="form-control form-control-sm js-extra-applies" value="${esc(e.applies_to||'')}"></td>
        <td class="text-center"><input class="form-check-input js-extra-active" type="checkbox" ${e.is_active === false ? '' : 'checked'}></td>
        <td class="text-end"><button class="btn btn-sm btn-outline-danger js-extra-del">✕</button></td>
      </tr>
    `).join('') || '<tr><td colspan="7" class="text-center text-muted py-3">Nessuna maggiorazione</td></tr>';
  }

  function mergeRoleRates(rows) {
    const m = new Map((rows || []).map(r => [String(r.role_code || '').toUpperCase(), r]));
    return ROLE_RATE_DEFAULTS.map((d, i) => {
      const r = m.get(d.role_code) || {};
      return {
        role_code: d.role_code,
        role_label: r.role_label || d.role_label,
        employer_inps_pct: r.employer_inps_pct ?? '',
        inail_pct: r.inail_pct ?? '',
        sort_order: r.sort_order ?? ((i + 1) * 10),
        is_active: r.is_active ?? true,
      };
    });
  }

  function renderRoleRates() {
    const tb = $('#lcRoleRatesTbody');
    if (!tb) return;
    state.currentRoleRates = mergeRoleRates(state.currentRoleRates);
    tb.innerHTML = state.currentRoleRates.map((r, idx) => `
      <tr data-idx="${idx}" data-role-code="${r.role_code}">
        <td><strong>${esc(r.role_label || r.role_code)}</strong></td>
        <td class="text-end"><input type="number" step="0.0001" class="form-control form-control-sm js-rr-inps text-end" value="${r.employer_inps_pct === '' ? '' : Number(r.employer_inps_pct)}" placeholder="default"></td>
        <td class="text-end"><input type="number" step="0.0001" class="form-control form-control-sm js-rr-inail text-end" value="${r.inail_pct === '' ? '' : Number(r.inail_pct)}" placeholder="default"></td>
      </tr>
    `).join('');
  }

  function readRoleRatesFromTable() {
    const rows = [];
    $$('#lcRoleRatesTbody tr').forEach((tr, idx) => {
      const role_code = tr.dataset.roleCode || '';
      const role_label = tr.querySelector('td')?.textContent?.trim() || role_code;
      const inpsRaw = tr.querySelector('.js-rr-inps')?.value ?? '';
      const inailRaw = tr.querySelector('.js-rr-inail')?.value ?? '';
      rows.push({
        role_code,
        role_label,
        employer_inps_pct: inpsRaw === '' ? null : Number(inpsRaw),
        inail_pct: inailRaw === '' ? null : Number(inailRaw),
        sort_order: (idx + 1) * 10,
        is_active: true,
      });
    });
    return rows;
  }

  function readExtrasFromTable() {
    return $$('#lcExtrasTable tbody tr[data-idx]').map((tr, i) => ({
      extra_code: tr.querySelector('.js-extra-code')?.value?.trim()?.toUpperCase() || '',
      label: tr.querySelector('.js-extra-label')?.value?.trim() || '',
      pct: Number(tr.querySelector('.js-extra-pct')?.value || 0),
      sort_order: Number(tr.querySelector('.js-extra-sort')?.value || (i+1)*10),
      applies_to: tr.querySelector('.js-extra-applies')?.value?.trim() || '',
      is_active: !!tr.querySelector('.js-extra-active')?.checked,
    })).filter(x => x.extra_code);
  }

  async function loadSetup() {
    const st = $('#lcStatus');
    try {
      if (st) st.textContent = 'Caricamento configurazioni…';
      const j = await fetchJson('/admin/api/labor-cost/setup');
      state.profiles = j.profiles || [];
      state.cmoContracts = j.cmo_contracts || [];
      renderProfiles();
      $('#lcCmoCount').textContent = String(state.cmoContracts.length || 0);
      $('#lcCmoExamples').textContent = (state.cmoContracts||[]).slice(0,6).map(x => `${x.contratto} (${fmtEur(x.valore)}/h)`).join(' · ') || 'Nessun dato CMO letto';
      if (st) st.textContent = 'Configurazioni caricate';
    } catch (e) {
      console.error(e);
      if (st) st.textContent = `Errore setup: ${e.message}`;
    }
  }

  async function openProfile(id) {
    try {
      const p = await fetchJson(`/admin/api/labor-cost/company-profiles/${id}`);
      resetProfileForm(p.profile || {});
      state.selectedProfileId = Number(id);
      state.currentExtras = p.extras || [];
      state.currentRoleRates = p.role_rates || p.profile?.role_rates || [];
      renderExtras();
      renderRoleRates();
      const modal = bootstrap.Modal.getOrCreateInstance($('#lcProfileModal'));
      modal.show();
    } catch (e) {
      alert(e.message || e);
    }
  }

  async function saveProfileAndExtras() {
    const payload = {
      id: $('#lcProfileId').value || null,
      code: $('#lcProfileCode').value,
      name: $('#lcProfileName').value,
      sector: $('#lcProfileSector').value,
      default_hourly_rate: $('#lcProfileDefaultHourly').value,
      annual_hours_full_time: $('#lcProfileAnnualHours').value,
      employer_inps_pct: $('#lcProfileINPS').value,
      inail_pct: $('#lcProfileINAIL').value,
      tfr_pct: $('#lcProfileTFR').value,
      ferie_permessi_accrual_pct: $('#lcProfileFeriePerm').value,
      tredicesima_pct: $('#lcProfile13').value,
      quattordicesima_pct: $('#lcProfile14').value,
      other_accruals_pct: $('#lcProfileOther').value,
      is_active: $('#lcProfileActive').checked,
      notes: $('#lcProfileNotes').value,
      role_rates: readRoleRatesFromTable(),
    };
    try {
      const j = await fetchJson('/admin/api/labor-cost/company-profiles/save', {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload)
      });
      const pid = Number(j.id);
      const extras = readExtrasFromTable();
      await fetchJson(`/admin/api/labor-cost/company-profiles/${pid}/extras/save`, {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({extras})
      });
      bootstrap.Modal.getOrCreateInstance($('#lcProfileModal')).hide();
      await loadSetup();
      await openProfile(pid);
      alert('Profilo salvato');
    } catch (e) {
      alert(e.message || e);
    }
  }

  async function saveEmployeeManual() {
    const inqOverride = String($('#lcEmpInq')?.value || '').trim();
    const roleHint = inqOverride.toUpperCase();
    const needsFixedCmo = roleHint.includes('STAGE') || roleHint.includes('TIROCIN') || (roleHint.includes('INTERMITT') || roleHint.includes('INTERINAL'));
    if (needsFixedCmo && !(Number($('#lcEmpStageFixedHourlyCost')?.value || 0) > 0)) {
      $('#lcEmpSaveMsg').textContent = 'Errore: CMO fisso obbligatorio per Stage/Intermittente (inquadramento override)';
      return;
    }
    const payload = {
      employee_code: $('#lcEmpCode').value,
      employee_name: $('#lcEmpName').value,
      store_code: $('#lcEmpStore').value,
      company_profile_id: $('#lcEmpCompanyProfile').value,
      contract_type: null,
      ral: $('#lcEmpRAL').value,
      hourly_rate_override: $('#lcEmpHourly').value,
      stage_fixed_hourly_cost: $('#lcEmpStageFixedHourlyCost').value,
      inquadramento_override: $('#lcEmpInq').value,
      employer_inps_pct_override: $('#lcEmpInpsOverride').value,
      inail_pct_override: $('#lcEmpInailOverride').value,
      tfr_pct_override: $('#lcEmpTfrOverride').value,
      source_type: 'manual',
      source_note: $('#lcEmpNote').value,
      is_active: $('#lcEmpActive').checked,
    };
    try {
      await fetchJson('/admin/api/labor-cost/employees/upsert', {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)
      });
      $('#lcEmpSaveMsg').textContent = 'Salvato';
      loadEmployeeList();
    } catch (e) {
      $('#lcEmpSaveMsg').textContent = `Errore: ${e.message}`;
    }
  }

  function fillEmployeeFormFromRow(r, rowIndex=null) {
    if (!r) return;
    state.selectedEmployeeRowIdx = (rowIndex == null ? null : Number(rowIndex));
    $('#lcEmpCode').value = r.employee_code || r.staff_codice_dipendente || '';
    $('#lcEmpName').value = r.employee_name || r.staff_nome_cognome || '';
    $('#lcEmpStore').value = r.store_code || ($('#lcEmpStoreFilter')?.value?.trim() || '');
    if ($('#lcEmpCompanyProfile')) $('#lcEmpCompanyProfile').value = (r.company_profile_id == null ? '' : String(r.company_profile_id));
    $('#lcEmpRAL').value = r.ral ?? '';
    $('#lcEmpHourly').value = r.hourly_rate_override ?? '';
    $('#lcEmpStageFixedHourlyCost').value = r.stage_fixed_hourly_cost ?? '';
    $('#lcEmpInq').value = r.inquadramento_override || r.staff_ruolo || '';
    $('#lcEmpInpsOverride').value = r.employer_inps_pct_override ?? '';
    $('#lcEmpInailOverride').value = r.inail_pct_override ?? '';
    $('#lcEmpTfrOverride').value = r.tfr_pct_override ?? '';
    $('#lcEmpNote').value = r.source_note || '';
    $('#lcEmpActive').checked = (r.is_active !== false);
    const origin = String(r.row_origin || 'config');
    const originLabel = origin === 'orari_only'
      ? 'Anagrafica Orari (nessuna config costo ancora salvata)'
      : origin === 'orari+config'
        ? 'Anagrafica Orari + config costo esistente'
        : 'Config costo (non trovata in anagrafica Orari del filtro store)';
    $('#lcEmpSaveMsg').textContent = `Modifica record: ${originLabel}`;
    $$('#lcEmpTable tbody tr').forEach((tr, i) => tr.classList.toggle('table-primary', i === state.selectedEmployeeRowIdx));
  }

  async function loadEmployeeList() {
    const store = $('#lcEmpStoreFilter')?.value?.trim() || '';
    try {
      const q = new URLSearchParams();
      if (store) q.set('store_code', store);
      q.set('limit','300');
      const j = await fetchJson(`/admin/api/labor-cost/employees/list?${q.toString()}`);
      const rows = j.rows || [];
      state.employeeRows = rows;
      const tb = $('#lcEmpTable tbody');
      tb.innerHTML = rows.map((r, idx) => {
        const origin = String(r.row_origin || 'config');
        const hasOrari = origin.includes('orari');
        const name = r.employee_name || r.staff_nome_cognome || '';
        const nameMeta = [];
        if (hasOrari && r.staff_ruolo) nameMeta.push(r.staff_ruolo);
        if (hasOrari && r.staff_ore_contrattuali != null && r.staff_ore_contrattuali !== '') nameMeta.push(`h contr. ${fmtNum(r.staff_ore_contrattuali, 2)}`);
        let originBadge = '';
        if (origin === 'orari_only') originBadge = ' <span class="badge text-bg-light border">ORARI</span>';
        else if (origin === 'orari+config') originBadge = ' <span class="badge text-bg-success">ABBINATO</span>';
        else if (origin === 'config_only') originBadge = ' <span class="badge text-bg-warning">SOLO CONFIG</span>';
        const profileName = r.company_profile_name || '';
        return `
        <tr data-row-idx="${idx}" title="Clicca per caricare nel form a sinistra">
          <td>${esc(r.employee_code || r.staff_codice_dipendente || '')}</td>
          <td>${esc(name)}${originBadge}${nameMeta.length ? `<div class="small text-muted">${esc(nameMeta.join(' · '))}</div>` : ''}</td>
          <td>${esc(r.store_code || '')}</td>
          <td>${esc(profileName)}</td>
          <td class="text-end">${r.ral == null ? '—' : fmtEur(r.ral)}</td>
          <td class="text-end">${r.hourly_rate_override == null ? '—' : fmtEur(r.hourly_rate_override)}</td>
          <td class="text-end">${r.stage_fixed_hourly_cost == null ? '—' : fmtEur(r.stage_fixed_hourly_cost)}</td>
          <td class="text-end">${r.employer_inps_pct_override == null ? '—' : fmtPct(r.employer_inps_pct_override,2)}</td>
          <td class="text-end">${r.inail_pct_override == null ? '—' : fmtPct(r.inail_pct_override,2)}</td>
          <td class="text-end">${r.tfr_pct_override == null ? '—' : fmtPct(r.tfr_pct_override,2)}</td>
          <td class="text-center">${r.is_active ? '✅':'—'}</td>
        </tr>`;
      }).join('') || '<tr><td colspan="11" class="text-center text-muted py-3">Nessun record</td></tr>';
      $('#lcEmpCount').textContent = String(rows.length);
      if (store && !$('#lcEmpStore').value) $('#lcEmpStore').value = store;
      state.selectedEmployeeRowIdx = null;
    } catch (e) {
      console.error(e);
    }
  }

  async function importEmployeeCsv() {
    const fi = $('#lcImportCsv');
    const f = fi?.files?.[0];
    if (!f) return;
    const fd = new FormData();
    fd.append('file', f);
    if ($('#lcImportStore')?.value) fd.append('default_store_code', $('#lcImportStore').value.trim());
    if ($('#lcImportCompanyProfile')?.value) fd.append('default_company_profile_id', $('#lcImportCompanyProfile').value);
    const out = $('#lcImportMsg');
    out.textContent = 'Import in corso…';
    try {
      const r = await fetch('/admin/api/labor-cost/employees/import-csv', { method:'POST', body: fd, headers: {'Accept':'application/json'} });
      const j = await r.json();
      if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`);
      out.innerHTML = `Salvati <b>${j.saved}</b> · Scartati <b>${j.skipped}</b> · Delimitatore <b>${esc(j.delimiter||'')}</b>` + (j.errors?.length ? `<br><span class="text-danger">${j.errors.map(esc).join('<br>')}</span>` : '');
      loadEmployeeList();
    } catch (e) {
      out.textContent = `Errore import: ${e.message}`;
    }
  }

  function renderProjection(data) {
    state.lastProjection = data;
    const s = data.summary || {};
    $('#lcKpiEmployees').textContent = String(s.employees_count ?? 0);
    $('#lcKpiHours').textContent = fmtNum(s.hours_worked,2);
    $('#lcKpiNetCost').textContent = fmtEur(s.total_net);
    $('#lcKpiLaborPct').textContent = s.labor_cost_pct_net == null ? '—' : fmtPct(s.labor_cost_pct_net,2);

    const agg = $('#lcAggBreakdown');
    if (agg) {
      const aggRows = [
        ['Retribuzione diretta', s.direct_comp],
        ['Oneri INPS', s.oneri_inps],
        ['Oneri INAIL', s.oneri_inail],
        ['TFR', s.acc_tfr],
        ['Ferie/Permessi acc.', s.acc_ferie_permessi],
        ['13a', s.acc_13],
        ['14a', s.acc_14],
        ['Altri acc.', s.acc_other],
        ['Totale lordo stimato', s.total_gross],
        ['Storno prestiti', -Math.abs(Number(s.prestito_storno || 0))],
        ['Totale netto stimato', s.total_net],
      ];
      agg.innerHTML = aggRows.map((row, idx) => `
        <div class="lc-kv lc-mono ${idx === aggRows.length-1 ? 'fw-semibold' : ''}">
          <span>${esc(row[0])}</span>
          <span>${Number(row[1]||0) < 0 ? '-' : ''}${fmtEur(Math.abs(Number(row[1] || 0)))}</span>
        </div>
      `).join('') + `
        <div class="lc-kv small text-muted mt-2">
          <span>Ore ferie / perm. / ROL (no costo diretto)</span>
          <span class="lc-mono">${fmtNum(Number(s.hours_ferie||0),2)} / ${fmtNum(Number(s.hours_permessi||0),2)} / ${fmtNum(Number(s.hours_rol||0),2)}</span>
        </div>`;
    }

    $('#lcMetaWeek').textContent = `${data.meta?.week_start || ''} → ${data.meta?.week_end || ''}`;
    $('#lcMetaStore').textContent = data.meta?.store_code || '';
    $('#lcMetaProfile').textContent = data.meta?.company_profile_name || '';

    const stackWrap = $('#lcCostStack');
    stackWrap.innerHTML = (data.cost_stack || []).map(x => `
      <div class="lc-kv lc-mono">
        <span>${esc(x.label||'')}</span>
        <span>${Number(x.value||0) < 0 ? '-' : ''}${fmtEur(Math.abs(Number(x.value||0)))}</span>
      </div>
    `).join('');

    const warn = $('#lcWarnings');
    warn.innerHTML = (data.warnings || []).length ? (data.warnings||[]).map(w => `<div class="alert alert-warning py-2 px-3 mb-2 small">${esc(w)}</div>`).join('') : '<div class="text-muted small">Nessun avviso</div>';

    const tb = $('#lcProjectionTable tbody');
    tb.innerHTML = (data.lines || []).map(r => {
      const src = r.base_hourly_source || r.hourly_rate_source || '';
      const roleLabel = [r.contract_type || r.ruolo || '', r.inquadramento || ''].filter(Boolean).join(' · ');
      return `
      <tr>
        <td>${esc(r.employee_name||'')}</td>
        <td>${esc(r.employee_code||'')}</td>
        <td>${esc(roleLabel || r.inquadramento || '')}</td>
        <td>${esc(src)}</td>
        <td class="text-end">${fmtNum(r.hours_worked,2)}</td>
        <td class="text-end">${fmtNum(r.hours_extra,2)}</td>
        <td class="text-end">${fmtNum(r.hours_ferie,2)}</td>
        <td class="text-end">${fmtNum(r.hours_permessi,2)}</td>
        <td class="text-end">${fmtNum(r.hours_rol,2)}</td>
        <td class="text-end">${fmtNum(r.hours_prestito,2)}</td>
        <td class="text-end">${fmtEur(r.base_hourly ?? r.hourly_rate)}</td>
        <td class="text-end" title="Base ${fmtEur(r.direct_base)} · Straord ${fmtEur(r.prem_straordinaria)} · Dom ${fmtEur(r.prem_domenicale)} · Notte ${fmtEur(r.prem_notturna)} · Fest ${fmtEur(r.prem_festiva)}">${fmtEur(r.direct_comp)}</td>
        <td class="text-end" title="INPS ${fmtEur(r.oneri_inps)} · INAIL ${fmtEur(r.oneri_inail)} · TFR ${fmtEur(r.acc_tfr)} · Ferie/Perm ${fmtEur(r.acc_ferie_permessi ?? r.acc_ferie_perm)} · 13a ${fmtEur(r.acc_13)} · 14a ${fmtEur(r.acc_14)} · Altri ${fmtEur(r.acc_other)}">${fmtEur(r.total_gross)}</td>
        <td class="text-end">-${fmtEur(r.prestito_storno)}</td>
        <td class="text-end fw-semibold">${fmtEur(r.total_net)}</td>
      </tr>`;
    }).join('') || '<tr><td colspan="15" class="text-center text-muted py-3">Nessun dato</td></tr>';

    const cards = $('#lcProjectionEmployeeCards');
    if (cards) {
      cards.innerHTML = (data.lines || []).map(r => {
        const src = r.base_hourly_source || r.hourly_rate_source || '';
        const roleLabel = [r.contract_type || r.ruolo || '', r.inquadramento || ''].filter(Boolean).join(' · ');
        const accFeriePerm = Number(r.acc_ferie_permessi ?? r.acc_ferie_perm ?? 0);
        const accTot = Number(r.acc_tfr||0) + accFeriePerm + Number(r.acc_13||0) + Number(r.acc_14||0) + Number(r.acc_other||0);
        const oneriTot = Number(r.oneri_inps||0) + Number(r.oneri_inail||0);
        const roleBucket = String(r.role_bucket||'').toUpperCase();
        const isStage = !!r.is_stage || roleBucket === 'STAGE';
        const isIntermittente = !!r.is_interinale || roleBucket === 'INTERMITTENTE' || roleBucket === 'INTERINALE';
        const roleBadge = isStage ? '<span class="badge text-bg-primary">STAGE</span>' : (isIntermittente ? '<span class="badge text-bg-warning">INTERMITTENTE</span>' : '');
        return `
          <div class="lc-employee-card">
            <div class="d-flex justify-content-between align-items-start gap-2 flex-wrap">
              <div>
                <div class="fw-semibold">${esc(r.employee_name||'')} <span class="small text-muted">${esc(r.employee_code||'')}</span> ${roleBadge}</div>
                <div class="small text-muted">${esc(roleLabel || '-')}</div>
              </div>
              <div class="text-end">
                <div class="small text-muted">Totale netto</div>
                <div class="fw-bold">${fmtEur(r.total_net)}</div>
              </div>
            </div>
            <div class="row g-2 mt-1">
              <div class="col-lg-2 col-md-4 col-6"><div class="lc-mini"><div class="lc-head">Ore costo</div><div class="lc-val">${fmtNum(r.hours_worked,2)}</div></div></div>
              <div class="col-lg-2 col-md-4 col-6"><div class="lc-mini"><div class="lc-head">Ferie/Perm/ROL</div><div class="lc-val">${fmtNum(Number(r.hours_ferie||0)+Number(r.hours_permessi||0)+Number(r.hours_rol||0),2)}</div></div></div>
              <div class="col-lg-2 col-md-4 col-6"><div class="lc-mini"><div class="lc-head">Costo h</div><div class="lc-val">${fmtEur(r.base_hourly ?? r.hourly_rate)}</div><div class="small text-muted">${esc(src)}</div></div></div>
              <div class="col-lg-2 col-md-4 col-6"><div class="lc-mini"><div class="lc-head">Diretto</div><div class="lc-val">${fmtEur(r.direct_comp)}</div></div></div>
              <div class="col-lg-2 col-md-4 col-6"><div class="lc-mini"><div class="lc-head">Accanton.</div><div class="lc-val">${fmtEur(accTot)}</div></div></div>
              <div class="col-lg-2 col-md-4 col-6"><div class="lc-mini"><div class="lc-head">Oneri</div><div class="lc-val">${fmtEur(oneriTot)}</div></div></div>
            </div>
            <div class="small text-muted mt-2">Prestito: ${fmtNum(r.hours_prestito,2)} h · Storno ${fmtEur(r.prestito_storno)} · INPS ${fmtPct(r.inps_pct,2)} · INAIL ${fmtPct(r.inail_pct,2)} · TFR ${fmtPct(r.tfr_pct,2)}</div>
          </div>`;
      }).join('') || '<div class="text-muted small">Nessun dettaglio.</div>';
    }

    $('#lcProjectionSummaryJson').textContent = JSON.stringify(data.summary || {}, null, 2);
    const linesJson = $('#lcProjectionLinesJson');
    if (linesJson) linesJson.textContent = JSON.stringify(data.lines || [], null, 2);
  }

  async function runProjection() {
    const payload = {
      store_code: $('#lcTestStore').value,
      week_start: $('#lcTestWeek').value,
      company_profile_id: $('#lcProfileSelectTest').value,
      revenues_actual: $('#lcTestRevenues').value,
    };
    const out = $('#lcProjectionStatus');
    out.textContent = 'Calcolo in corso…';
    try {
      const data = await fetchJson('/admin/api/labor-cost/projection-test', {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)
      });
      renderProjection(data);
      out.textContent = 'Calcolo completato';
    } catch (e) {
      out.textContent = `Errore: ${e.message}`;
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    // Defaults
    const d = new Date();
    const day = d.getDay(); // 0 Sun
    const deltaToMonday = (day === 0 ? -6 : 1 - day);
    d.setDate(d.getDate() + deltaToMonday);
    $('#lcTestWeek').value = d.toISOString().slice(0,10);

    loadSetup();
    loadEmployeeList();

    $('#lcBtnNewProfile').addEventListener('click', () => {
      state.currentExtras = [
        {extra_code:'STRAORDINARIA', label:'Maggiorazione straordinario', pct:0.15, sort_order:10, applies_to:'worked', is_active:true},
        {extra_code:'DOMENICALE', label:'Maggiorazione domenicale', pct:0.30, sort_order:20, applies_to:'worked', is_active:true},
        {extra_code:'NOTTURNA', label:'Maggiorazione notturna', pct:0.25, sort_order:30, applies_to:'worked', is_active:true},
        {extra_code:'FESTIVA', label:'Maggiorazione festiva', pct:0.30, sort_order:40, applies_to:'worked', is_active:true},
      ];
      resetProfileForm({});
      renderExtras();
      renderRoleRates();
      bootstrap.Modal.getOrCreateInstance($('#lcProfileModal')).show();
    });

    $('#lcProfilesTable').addEventListener('click', (e) => {
      const btn = e.target.closest('.js-edit-profile');
      if (!btn) return;
      const tr = btn.closest('tr');
      const id = tr?.dataset?.id;
      if (id) openProfile(id);
    });

    $('#lcBtnAddExtra').addEventListener('click', () => {
      state.currentExtras.push({ extra_code:'', label:'', pct:0, sort_order:(state.currentExtras.length+1)*10, applies_to:'worked', is_active:true });
      renderExtras();
    });

    $('#lcExtrasTable').addEventListener('click', (e) => {
      const btn = e.target.closest('.js-extra-del');
      if (!btn) return;
      const tr = btn.closest('tr');
      const idx = Number(tr?.dataset?.idx || -1);
      if (idx >= 0) {
        state.currentExtras.splice(idx, 1);
        renderExtras();
      }
    });

    $('#lcBtnSaveProfile').addEventListener('click', saveProfileAndExtras);
    $('#lcBtnEmpSave').addEventListener('click', saveEmployeeManual);
    $('#lcBtnEmpRefresh').addEventListener('click', loadEmployeeList);
    $('#lcEmpStoreFilter')?.addEventListener('change', () => {
      const s = $('#lcEmpStoreFilter')?.value?.trim() || '';
      if (s && !($('#lcEmpStore')?.value || '').trim()) $('#lcEmpStore').value = s;
    });
    $('#lcEmpTable').addEventListener('click', (e) => {
      const tr = e.target.closest('tbody tr[data-row-idx]');
      if (!tr) return;
      const idx = Number(tr.dataset.rowIdx || -1);
      if (!Number.isFinite(idx) || idx < 0) return;
      const row = state.employeeRows?.[idx];
      fillEmployeeFormFromRow(row, idx);
    });
    $('#lcBtnImportCsv').addEventListener('click', importEmployeeCsv);
    $('#lcBtnRunProjection').addEventListener('click', runProjection);
  });
})();
