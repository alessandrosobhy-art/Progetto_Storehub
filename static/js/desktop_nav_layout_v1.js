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
    const inlineBtn = document.getElementById('toggleDesktopNavLayoutInline');
    const compactBtns = [
      document.getElementById('toggleDesktopNavCompact'),
      document.getElementById('toggleDesktopNavCompactInline')
    ].filter(Boolean);
    if (btn) {
      const labels = {
        'top': 'Layout desktop: orizzontale',
        'top-compact': 'Layout desktop: orizzontale compatto',
        'sidebar': 'Layout desktop: laterale',
        'sidebar-compact': 'Layout desktop: laterale compatto'
      };
      btn.textContent = labels[mode] || 'Layout barra desktop';
    }
    if (inlineBtn) {
      inlineBtn.title = mode.startsWith('sidebar')
        ? 'Passa alla barra orizzontale'
        : 'Passa alla barra laterale';
      inlineBtn.setAttribute('aria-label', inlineBtn.title);
    }
    const compact = mode === 'top-compact' || mode === 'sidebar-compact';
    compactBtns.forEach(function (compactBtn) {
      compactBtn.title = compact ? 'Espandi menu desktop' : 'Comprimi menu desktop';
      compactBtn.setAttribute('aria-label', compactBtn.title);
      compactBtn.dataset.compact = compact ? '1' : '0';
    });
  }

  function nextMode(current) {
    if (current === 'sidebar' || current === 'sidebar-compact') {
      return current === 'sidebar-compact' ? 'top-compact' : 'top';
    }
    return current === 'top-compact' ? 'sidebar-compact' : 'sidebar';
  }

  function toggleCompact(current) {
    if (current === 'sidebar') return 'sidebar-compact';
    if (current === 'sidebar-compact') return 'sidebar';
    if (current === 'top') return 'top-compact';
    return 'top';
  }

  function init() {
    let mode = getMode();
    applyMode(mode);

    const btn = document.getElementById('toggleDesktopNavLayout');
    const inlineBtn = document.getElementById('toggleDesktopNavLayoutInline');
    const compactBtns = [
      document.getElementById('toggleDesktopNavCompact'),
      document.getElementById('toggleDesktopNavCompactInline')
    ].filter(Boolean);
    if (btn) {
      btn.addEventListener('click', function () {
        mode = nextMode(getMode());
        setMode(mode);
        applyMode(mode);
      });
    }
    if (inlineBtn) {
      inlineBtn.addEventListener('click', function () {
        mode = nextMode(getMode());
        setMode(mode);
        applyMode(mode);
      });
    }
    compactBtns.forEach(function (compactBtn) {
      compactBtn.addEventListener('click', function () {
        mode = toggleCompact(getMode());
        setMode(mode);
        applyMode(mode);
      });
    });

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
