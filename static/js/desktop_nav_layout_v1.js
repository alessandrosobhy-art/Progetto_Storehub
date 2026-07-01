(function () {
  const STORAGE_KEY = 'storehub-desktop-nav-layout-v1';
  const DESKTOP_QUERY = '(min-width: 1200px)';

  function isDesktop() {
    try {
      return window.matchMedia(DESKTOP_QUERY).matches;
    } catch (e) {
      return window.innerWidth >= 1200;
    }
  }

  function getMode() {
    try {
      return localStorage.getItem(STORAGE_KEY) || 'top';
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
    document.body.classList.toggle('app-shell--sidebar', mode === 'sidebar' && isDesktop());
    const btn = document.getElementById('toggleDesktopNavLayout');
    if (btn) {
      btn.textContent = mode === 'sidebar'
        ? 'Barra desktop orizzontale'
        : 'Barra desktop laterale';
    }
  }

  function init() {
    let mode = getMode();
    applyMode(mode);

    const btn = document.getElementById('toggleDesktopNavLayout');
    if (btn) {
      btn.addEventListener('click', function () {
        mode = (getMode() === 'sidebar') ? 'top' : 'sidebar';
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
