(function(){
  function parseStores(){
    const el = document.getElementById('globalAiStoresData');
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

  function setLoading(form, loadingBox, answerBox, isLoading){
    const btn = document.getElementById('globalAiSubmitBtn');
    if(btn) btn.classList.toggle('is-loading', !!isLoading);
    if(loadingBox) loadingBox.classList.toggle('d-none', !isLoading);
    if(answerBox && isLoading){
      answerBox.textContent = 'Sto analizzando la richiesta...';
      answerBox.classList.remove('is-empty');
    }
  }

  document.addEventListener('DOMContentLoaded', function(){
    const form = document.getElementById('globalAiQuickForm');
    const textarea = document.getElementById('globalAiQuestionInput');
    const menu = document.getElementById('globalAiMentionMenu');
    const loadingBox = document.getElementById('globalAiPanelLoading');
    const errorBox = document.getElementById('globalAiPanelError');
    const answerBox = document.getElementById('globalAiPanelAnswer');
    if(!form || !textarea || !answerBox) return;

    answerBox.classList.add('is-empty');
    setupMentions(textarea, menu, parseStores());

    form.addEventListener('submit', function(ev){
      ev.preventDefault();
      if(errorBox){
        errorBox.classList.add('d-none');
        errorBox.textContent = '';
      }
      setLoading(form, loadingBox, answerBox, true);
      if(window.loadingOverlay) window.loadingOverlay.push('Analisi AI in corso...');
      fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        headers: {'X-No-Overlay': '1'}
      })
      .then(function(r){ return r.json().then(function(data){ return { ok: r.ok, data: data }; }); })
      .then(function(res){
        if(!res.ok || !res.data || res.data.ok !== true){
          throw new Error((res.data && res.data.error) || 'Errore generando la risposta AI.');
        }
        answerBox.textContent = String(res.data.answer || '');
        answerBox.classList.remove('is-empty');
      })
      .catch(function(err){
        if(errorBox){
          errorBox.textContent = err && err.message ? err.message : 'Errore generando la risposta AI.';
          errorBox.classList.remove('d-none');
        }
      })
      .finally(function(){
        setLoading(form, loadingBox, answerBox, false);
        if(window.loadingOverlay) window.loadingOverlay.pop();
      });
    });
  });
})();
