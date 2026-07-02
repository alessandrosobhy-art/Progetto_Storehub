(function () {
  const DESKTOP_QUERY = '(min-width: 1200px)';
  const RAIL_KEY = 'storehub-desktop-rail-collapsed-v1';
  const SECTIONBAR_KEY = 'storehub-desktop-sectionbar-collapsed-v1';

  function isDesktop() {
    try {
      return window.matchMedia(DESKTOP_QUERY).matches;
    } catch (e) {
      return window.innerWidth >= 1200;
    }
  }

  function readBool(key) {
    try {
      return localStorage.getItem(key) === '1';
    } catch (e) {
      return false;
    }
  }

  function writeBool(key, value) {
    try {
      localStorage.setItem(key, value ? '1' : '0');
    } catch (e) {}
  }

  function applyDesktopShellState() {
    const desktop = isDesktop();
    const railCollapsed = readBool(RAIL_KEY);
    const sectionbarCollapsed = readBool(SECTIONBAR_KEY);

    document.body.classList.toggle('app-shell--rail-collapsed', desktop && railCollapsed);
    document.body.classList.toggle('app-shell--sectionbar-collapsed', desktop && sectionbarCollapsed);

    const railBtn = document.getElementById('toggleDesktopRail');
    if (railBtn) {
      const title = railCollapsed ? 'Espandi barra laterale' : 'Comprimi barra laterale';
      railBtn.title = title;
      railBtn.setAttribute('aria-label', title);
    }

    const sectionBtn = document.getElementById('toggleDesktopSectionbar');
    if (sectionBtn) {
      const title = sectionbarCollapsed ? 'Espandi barra sezioni' : 'Comprimi barra sezioni';
      sectionBtn.title = title;
      sectionBtn.setAttribute('aria-label', title);
    }
  }

  function initSectionbarScroll() {
    const track = document.getElementById('desktopSectionTrack');
    const prev = document.getElementById('desktopSectionPrev');
    const next = document.getElementById('desktopSectionNext');
    if (!track || !prev || !next) return;

    function update() {
      const overflow = track.scrollWidth - track.clientWidth > 8;
      const desktop = isDesktop() && !document.body.classList.contains('app-shell--sectionbar-collapsed');
      prev.hidden = !desktop || !overflow || track.scrollLeft <= 4;
      next.hidden = !desktop || !overflow || (track.scrollLeft + track.clientWidth >= track.scrollWidth - 4);
    }

    function scrollByAmount(dir) {
      const amount = Math.max(220, Math.round(track.clientWidth * 0.45)) * dir;
      track.scrollBy({ left: amount, behavior: 'smooth' });
    }

    prev.addEventListener('click', function () { scrollByAmount(-1); });
    next.addEventListener('click', function () { scrollByAmount(1); });
    track.addEventListener('scroll', update, { passive: true });
    window.addEventListener('resize', update);
    window.setTimeout(update, 0);
  }

  function init() {
    applyDesktopShellState();
    initSectionbarScroll();

    const railBtn = document.getElementById('toggleDesktopRail');
    if (railBtn) {
      railBtn.addEventListener('click', function () {
        writeBool(RAIL_KEY, !readBool(RAIL_KEY));
        applyDesktopShellState();
      });
    }

    const sectionBtn = document.getElementById('toggleDesktopSectionbar');
    if (sectionBtn) {
      sectionBtn.addEventListener('click', function () {
        writeBool(SECTIONBAR_KEY, !readBool(SECTIONBAR_KEY));
        applyDesktopShellState();
      });
    }

    try {
      window.matchMedia(DESKTOP_QUERY).addEventListener('change', function () {
        applyDesktopShellState();
      });
    } catch (e) {
      window.addEventListener('resize', applyDesktopShellState);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
