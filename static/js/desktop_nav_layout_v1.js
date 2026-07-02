(function () {
  const STORAGE_KEY = 'storehub-desktop-nav-layout-v1';
  const DESKTOP_QUERY = '(min-width: 1200px)';
  const MODES = ['top', 'top-compact', 'sidebar', 'sidebar-compact'];

  function isDesktop() {
    try {
      return window.matchMedia(DESKTOP_QUERY).matches;
    } catch (e) {
      return window.innerWidth >= 1200;
    }
  }

  function getMode() {
    try {
      const stored = localStorage.getItem(STORAGE_KEY) || 'top';
      return MODES.includes(stored) ? stored : 'top';
    } catch (e) {
      return 'top';
    }
  }

  function setMode(mode) {
    try {
      localStorage.setItem(STORAGE_KEY, mode);
    } catch (e) {}
  }

  function applyMode(mode) {
    const desktop = isDesktop();
    document.body.classList.toggle('app-shell--sidebar', desktop && (mode === 'sidebar' || mode === 'sidebar-compact'));
    document.body.classList.toggle('app-shell--sidebar-collapsed', desktop && mode === 'sidebar-compact');
    document.body.classList.toggle('app-shell--top-compact', desktop && mode === 'top-compact');
    const btn = document.getElementById('toggleDesktopNavLayout');
    if (btn) {
      const labels = {
        'top': 'Layout desktop: orizzontale',
        'top-compact': 'Layout desktop: orizzontale compatto',
        'sidebar': 'Layout desktop: laterale',
        'sidebar-compact': 'Layout desktop: laterale compatto'
      };
      btn.textContent = labels[mode] || 'Layout barra desktop';
    }
  }

  function nextMode(current) {
    const idx = MODES.indexOf(current);
    return MODES[(idx + 1) % MODES.length];
  }

  function init() {
    let mode = getMode();
    applyMode(mode);

    const btn = document.getElementById('toggleDesktopNavLayout');
    if (btn) {
      btn.addEventListener('click', function () {
        mode = nextMode(getMode());
        setMode(mode);
        applyMode(mode);
      });
    }

    try {
      window.matchMedia(DESKTOP_QUERY).addEventListener('change', function () {
        applyMode(getMode());
      });
    } catch (e) {
      window.addEventListener('resize', function () {
        applyMode(getMode());
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
