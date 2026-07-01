(function(){
  function parseStoresFromElement(id){
    const el = document.getElementById(id);
    if(!el) return [];
    try {
      const raw = JSON.parse(el.textContent || '[]');
      return (raw || []).map(function(s){
        return {
          code: String((s && (s.code || s.store_code)) || '').trim(),
          name: String((s && (s.name || s.store_name)) || '').trim()
        };
      }).filter(function(s){ return s.code; });
    } catch(e){
      return [];
    }
  }

  function buildQuestion(mode, period, focus){
    if(mode === 'administrative'){
      if(focus === 'admin') return 'Fammi una panoramica amministrativa di ' + period + ': spese, versamenti e differenze cassa';
      return 'Com\'e andata ' + period + ' dal punto di vista amministrativo: spese, versamenti e differenze cassa';
    }
    if(mode === 'mixed'){
      if(focus === 'delivery') return 'Fammi una panoramica completa di ' + period + ' con focus su delivery, budget e anno precedente';
      return 'Com\'e andata ' + period + ' nei miei store con panoramica completa tra business e dati amministrativi';
    }
    if(focus === 'budget') return 'Com\'e andata ' + period + ' nei miei store rispetto al budget e all\'anno precedente?';
    if(focus === 'delivery') return 'Come sta andando il delivery ' + period + ' nei miei store?';
    if(focus === 'admin') return 'Dammi una sintesi amministrativa di ' + period + ' con spese e versamenti';
    return 'Com\'e andata ' + period + ' nei miei store?';
  }

  function setupGuide(textarea){
    const mode = document.getElementById('guideMode');
    const period = document.getElementById('guidePeriod');
    const focus = document.getElementById('guideFocus');
    const btn = document.getElementById('guideBuildBtn');
    if(!textarea || !mode || !period || !focus || !btn) return;
    btn.addEventListener('click', function(){
      textarea.value = buildQuestion(mode.value, period.value, focus.value);
      textarea.focus();
      textarea.setSelectionRange(textarea.value.length, textarea.value.length);
    });
  }

  function setupMentions(textarea, menu, stores){
    if(!textarea || !menu || !stores.length) return;
    let activeIndex = -1;
    let currentMatches = [];
    let currentToken = null;

    function hideMenu(){
      menu.style.display = 'none';
      menu.innerHTML = '';
      activeIndex = -1;
      currentMatches = [];
      currentToken = null;
    }

    function getToken(){
      const pos = textarea.selectionStart || 0;
      const before = textarea.value.slice(0, pos);
      const match = before.match(/(^|\s)@([^\s@]*)$/);
      if(!match) return null;
      return {
        query: String(match[2] || '').toLowerCase(),
        start: pos - match[2].length - 1,
        end: pos
      };
    }

    function renderMenu(matches){
      if(!matches.length){ hideMenu(); return; }
      currentMatches = matches;
      menu.innerHTML = matches.map(function(s, idx){
        const label = s.name && s.name !== s.code ? (s.code + ' - ' + s.name) : s.code;
        return '<button type="button" class="dropdown-item' + (idx===0 ? ' active' : '') + '" data-idx="' + idx + '">@' + label + '</button>';
      }).join('');
      activeIndex = 0;
      menu.style.display = 'block';
    }

    function applySelection(store){
      if(!currentToken || !store) return;
      const before = textarea.value.slice(0, currentToken.start);
      const after = textarea.value.slice(currentToken.end);
      const mention = '@' + store.code + ' ';
      textarea.value = before + mention + after;
      const caret = (before + mention).length;
      textarea.focus();
      textarea.setSelectionRange(caret, caret);
      hideMenu();
    }

    function refresh(){
      currentToken = getToken();
      if(!currentToken){ hideMenu(); return; }
      const q = currentToken.query;
      const matches = stores.filter(function(s){
        if(!q) return true;
        return s.code.toLowerCase().indexOf(q) === 0 || s.name.toLowerCase().indexOf(q) === 0 || s.name.toLowerCase().indexOf(q) !== -1;
      }).slice(0, 8);
      renderMenu(matches);
    }

    textarea.addEventListener('input', refresh);
    textarea.addEventListener('click', refresh);
    textarea.addEventListener('keyup', function(ev){
      if(['ArrowDown','ArrowUp','Enter','Tab','Escape'].indexOf(ev.key) !== -1) return;
      refresh();
    });

    textarea.addEventListener('keydown', function(ev){
      if(menu.style.display !== 'block' || !currentMatches.length) return;
      if(ev.key === 'ArrowDown'){
        ev.preventDefault();
        activeIndex = (activeIndex + 1) % currentMatches.length;
      } else if(ev.key === 'ArrowUp'){
        ev.preventDefault();
        activeIndex = (activeIndex - 1 + currentMatches.length) % currentMatches.length;
      } else if(ev.key === 'Enter' || ev.key === 'Tab'){
        ev.preventDefault();
        applySelection(currentMatches[activeIndex]);
        return;
      } else if(ev.key === 'Escape'){
        hideMenu();
        return;
      } else {
        return;
      }
      Array.from(menu.querySelectorAll('.dropdown-item')).forEach(function(el, idx){
        el.classList.toggle('active', idx === activeIndex);
      });
    });

    menu.addEventListener('mousedown', function(ev){
      const btn = ev.target.closest('.dropdown-item');
      if(!btn) return;
      ev.preventDefault();
      const idx = parseInt(btn.getAttribute('data-idx') || '-1', 10);
      if(idx >= 0 && currentMatches[idx]) applySelection(currentMatches[idx]);
    });

    document.addEventListener('click', function(ev){
      if(ev.target === textarea || menu.contains(ev.target)) return;
      hideMenu();
    });
  }

  function formatMoney(value){
    const n = Number(value || 0);
    return n.toFixed(2);
  }

  function renderSummary(context, usage){
    const summaryCard = document.getElementById('aiAssistantSummaryCard');
    if(!summaryCard || !context) return;
    summaryCard.classList.remove('d-none');
    const period = document.getElementById('aiSummaryPeriod');
    const stores = document.getElementById('aiSummaryStores');
    const mode = document.getElementById('aiSummaryMode');
    const tokens = document.getElementById('aiSummaryTokens');
    if(period && context.period){
      period.textContent = String((context.period.label || '') + ' (' + (context.period.start_iso || '') + ' -> ' + (context.period.end_iso || '') + ')').trim();
    }
    if(stores) stores.textContent = String((context.stores || []).length || 0);
    if(mode && context.question_profile) mode.textContent = String(context.question_profile.mode || '');
    if(tokens && usage) tokens.textContent = String(usage.total_tokens || '');
  }

  function renderContext(context){
    const row = document.getElementById('aiAssistantDetailsRow');
    if(!row || !context) return;
    row.classList.remove('d-none');

    const storesWrap = document.getElementById('aiContextStores');
    if(storesWrap){
      storesWrap.innerHTML = (context.stores || []).map(function(s){
        const label = s.name && s.name !== s.code ? (s.code + ' - ' + s.name) : s.code;
        return '<span class="badge text-bg-light border">' + label + '</span>';
      }).join('');
    }

    const assumptions = document.getElementById('aiContextAssumptions');
    if(assumptions){
      assumptions.innerHTML = ((context.assumptions || []).map(function(item){ return '<li>' + item + '</li>'; }).join(''));
    }

    const unresolved = document.getElementById('aiContextUnresolved');
    if(unresolved){
      unresolved.innerHTML = ((context.unresolved_mentions || []).map(function(tag){ return '<span class="badge text-bg-warning">' + tag + '</span>'; }).join(''));
    }
  }

  function renderTotals(totals){
    const totalsCard = document.getElementById('aiAssistantTotalsCard');
    if(!totalsCard || !totals) return;
    totalsCard.classList.remove('d-none');
    const map = {
      revenues_actual_net: formatMoney(totals.revenues_actual_net),
      revenues_budget: formatMoney(totals.revenues_budget),
      revenues_ly: formatMoney(totals.revenues_ly),
      vs_budget: formatMoney(totals.vs_budget) + (totals.vs_budget_pct != null ? ' (' + formatMoney(totals.vs_budget_pct) + '%)' : ''),
      vs_ly: formatMoney(totals.vs_ly) + (totals.vs_ly_pct != null ? ' (' + formatMoney(totals.vs_ly_pct) + '%)' : ''),
      delivery_incidence_pct: totals.delivery_incidence_pct != null ? (formatMoney(totals.delivery_incidence_pct) + '%') : '-',
      diff_cassa: formatMoney(totals.diff_cassa),
      spese_gross: formatMoney(totals.spese_gross),
      versamenti: formatMoney(totals.versamenti),
      hours_total: formatMoney(totals.hours_total),
      hours_training: formatMoney(totals.hours_training),
      labor_cost: formatMoney(totals.labor_cost),
      productivity_eur_per_hour: formatMoney(totals.productivity_eur_per_hour)
    };
    Object.keys(map).forEach(function(key){
      const el = document.querySelector('[data-key="' + key + '"]');
      if(el) el.textContent = map[key];
    });
  }

  function renderAnswer(answer, context, usage){
    const answerCard = document.getElementById('aiAssistantAnswerCard');
    const answerEl = document.getElementById('aiAssistantAnswer');
    if(answerCard) answerCard.classList.remove('d-none');
    if(answerEl) answerEl.textContent = String(answer || '');
    renderSummary(context, usage);
    renderContext(context);
    renderTotals((context || {}).totals || {});
  }

  function setLoadingState(form, inlineLoading, isLoading){
    const btn = form ? form.querySelector('#aiAssistantSubmitBtn') : null;
    if(btn) btn.classList.toggle('is-loading', !!isLoading);
    if(inlineLoading) inlineLoading.classList.toggle('d-none', !isLoading);
  }

  function setupAiPageAjax(stores){
    const form = document.getElementById('aiAssistantForm');
    if(!form) return;
    const textarea = document.getElementById('questionInput');
    const menu = document.getElementById('aiMentionMenu');
    const inlineLoading = document.getElementById('aiAssistantInlineLoading');
    const errorBox = document.getElementById('aiAssistantInlineError');
    setupGuide(textarea);
    setupMentions(textarea, menu, stores);

    form.addEventListener('submit', function(ev){
      ev.preventDefault();
      if(errorBox){
        errorBox.classList.add('d-none');
        errorBox.textContent = '';
      }
      setLoadingState(form, inlineLoading, true);
      if(window.loadingOverlay) window.loadingOverlay.push('Analisi AI in corso...');
      const body = new FormData(form);
      fetch('/api/ai-assistant/query', {
        method: 'POST',
        body: body,
        headers: {'X-No-Overlay': '1'}
      })
      .then(function(r){ return r.json().then(function(data){ return { ok: r.ok, data: data }; }); })
      .then(function(res){
        if(!res.ok || !res.data || res.data.ok !== true){
          throw new Error((res.data && res.data.error) || 'Errore generando la risposta AI.');
        }
        renderAnswer(res.data.answer, res.data.context, res.data.usage || {});
      })
      .catch(function(err){
        if(errorBox){
          errorBox.textContent = err && err.message ? err.message : 'Errore generando la risposta AI.';
          errorBox.classList.remove('d-none');
        }
      })
      .finally(function(){
        setLoadingState(form, inlineLoading, false);
        if(window.loadingOverlay) window.loadingOverlay.pop();
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function(){
    const stores = parseStoresFromElement('aiAssistantStoresData');
    setupAiPageAjax(stores);
  });
})();
