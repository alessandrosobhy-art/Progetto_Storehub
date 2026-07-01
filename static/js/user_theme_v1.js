(function () {
  const root = document.documentElement;
  const form = document.getElementById('themePickerForm');
  const resetBtn = document.getElementById('themeResetBtn');
  const metaTheme = document.getElementById('metaThemeColor');

  const THEME_META = {
    base: '#4f8fcf',
    classic: '#374151',
    midnight: '#355c8a',
    mint: '#3b9d8f',
    sand: '#b9834d',
    lavender: '#7b6fc9'
  };

  function normalizeTheme(value) {
    const key = String(value || '').trim().toLowerCase();
    return Object.prototype.hasOwnProperty.call(THEME_META, key) ? key : 'base';
  }

  function applyTheme(themeKey) {
    const key = normalizeTheme(themeKey);
    root.setAttribute('data-app-theme', key);
    if (metaTheme) metaTheme.setAttribute('content', THEME_META[key] || THEME_META.base);
  }

  function checkedTheme() {
    if (!form) return 'base';
    const checked = form.querySelector('input[name="theme_key"]:checked');
    return normalizeTheme(checked ? checked.value : 'base');
  }

  applyTheme(root.getAttribute('data-app-theme') || 'base');

  if (form) {
    form.addEventListener('change', function (ev) {
      const t = ev.target;
      if (t && t.name === 'theme_key') {
        applyTheme(t.value);
      }
    });

    form.addEventListener('submit', function () {
      applyTheme(checkedTheme());
    });
  }

  if (resetBtn && form) {
    resetBtn.addEventListener('click', function () {
      const target = normalizeTheme(resetBtn.getAttribute('data-theme-key') || 'base');
      const input = form.querySelector(`input[name="theme_key"][value="${target}"]`);
      if (input) {
        input.checked = true;
        applyTheme(target);
      }
    });
  }
})();
