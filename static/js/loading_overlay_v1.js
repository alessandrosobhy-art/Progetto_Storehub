/* Global loading overlay (v1.0.4)
   - Blocks clicks while long operations are running
   - Auto-hooks form submit + fetch (with delay to avoid flicker)
   - Persists feedback across full-page navigations
*/
(function () {
  const OVERLAY_ID = 'globalLoadingOverlay';
  const SHOW_DELAY_MS = 250;
  const NAV_STORAGE_KEY = 'storehubNavLoading';
  const NAV_STORAGE_TTL_MS = 15000;
  const PRESS_CLASS = 'is-pressing';

  let overlayEl = null;
  let msgEl = null;
  let pending = 0;
  let showTimer = null;
  let navRestoreVisible = false;
  const i18n = window.StoreHubI18n || {};
  const defaultLoading = i18n.loading || 'Caricamento...';
  const defaultSaving = i18n.saving || 'Salvataggio in corso...';
  const defaultOperation = i18n.operationInProgress || 'Operazione in corso...';
  let lastMessage = defaultLoading;

  function ensureElements() {
    if (overlayEl) return true;
    overlayEl = document.getElementById(OVERLAY_ID);
    if (!overlayEl) return false;
    msgEl = overlayEl.querySelector('[data-loading-message]');
    return true;
  }

  function setMessage(message) {
    lastMessage = message || lastMessage || defaultLoading;
    if (msgEl) msgEl.textContent = lastMessage;
  }

  function scheduleShow() {
    if (showTimer) return;
    showTimer = window.setTimeout(function () {
      showTimer = null;
      if (!ensureElements()) return;
      overlayEl.classList.add('show');
      document.body.classList.add('loading-overlay-open');
      setMessage(lastMessage);
    }, SHOW_DELAY_MS);
  }

  function show(message) {
    if (message) setMessage(message);
    pending += 1;
    if (pending === 1) scheduleShow();
  }

  function showNow(message) {
    if (message) setMessage(message);
    if (showTimer) {
      window.clearTimeout(showTimer);
      showTimer = null;
    }
    if (!ensureElements()) return;
    overlayEl.classList.add('show');
    document.body.classList.add('loading-overlay-open');
    setMessage(lastMessage);
  }

  function hide() {
    if (pending > 0) pending -= 1;
    if (pending !== 0) return;

    if (showTimer) {
      window.clearTimeout(showTimer);
      showTimer = null;
    }
    if (!ensureElements()) return;
    overlayEl.classList.remove('show');
    document.body.classList.remove('loading-overlay-open');
    setMessage(defaultLoading);
  }

  // Expose small API for pages that want manual control
  window.loadingOverlay = {
    push: show,
    pop: hide,
    show: function (message) { showNow(message); },
    hide: function () { while (pending > 0) hide(); },
    setMessage: setMessage
  };

  function markPress(target) {
    if (!target || !target.classList) return;
    target.classList.add(PRESS_CLASS);
    window.setTimeout(function () {
      try { target.classList.remove(PRESS_CLASS); } catch (_) {}
    }, 180);
  }

  function rememberNavigation(message) {
    try {
      sessionStorage.setItem(NAV_STORAGE_KEY, JSON.stringify({
        ts: Date.now(),
        message: message || defaultLoading,
      }));
    } catch (_) {}
  }

  function clearRememberedNavigation() {
    try { sessionStorage.removeItem(NAV_STORAGE_KEY); } catch (_) {}
    document.documentElement.classList.remove('storehub-page-loading');
  }

  function beginNavigationLoad(message) {
    rememberNavigation(message);
    showNow(message || defaultLoading);
  }

  function isNavigationalAnchor(anchor) {
    if (!anchor || !anchor.href) return false;
    if (anchor.hasAttribute('download') || anchor.getAttribute('target') === '_blank') return false;
    if (anchor.dataset.noOverlay === '1' || anchor.hasAttribute('data-no-overlay')) return false;
    const href = anchor.getAttribute('href') || '';
    if (!href || href === '#' || href.startsWith('javascript:')) return false;
    try {
      const url = new URL(anchor.href, window.location.href);
      return url.origin === window.location.origin;
    } catch (_) {
      return false;
    }
  }

  function shouldHandleNavigationClick(ev, anchor) {
    if (!isNavigationalAnchor(anchor)) return false;
    if (!ev) return true;
    if (ev.defaultPrevented) return false;
    if (typeof ev.button === 'number' && ev.button !== 0) return false;
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return false;
    return true;
  }

  // Hook form submissions (full page or background)
  document.addEventListener('submit', function (ev) {
    const form = ev.target;
    if (!form || !(form instanceof HTMLFormElement)) return;
    if (form.hasAttribute('data-no-overlay') || form.dataset.noOverlay === '1') return;

    const msg = form.dataset.overlayMessage || defaultLoading;
    beginNavigationLoad(msg);
  }, true);

  document.addEventListener('pointerdown', function (ev) {
    const tapTarget = ev.target && ev.target.closest
      ? ev.target.closest('.app-header .nav-link, .app-header .dropdown-item, .app-header .navbar-toggler')
      : null;
    if (tapTarget) markPress(tapTarget);
  }, true);

  document.addEventListener('touchstart', function (ev) {
    const tapTarget = ev.target && ev.target.closest
      ? ev.target.closest('.app-header .nav-link, .app-header .dropdown-item, .app-header .navbar-toggler')
      : null;
    if (tapTarget) markPress(tapTarget);
  }, { capture: true, passive: true });

  document.addEventListener('click', function (ev) {
    const anchor = ev.target && ev.target.closest ? ev.target.closest('a[href]') : null;
    if (!shouldHandleNavigationClick(ev, anchor)) return;
    const message = anchor.dataset.overlayMessage || defaultLoading;
    ev.preventDefault();
    beginNavigationLoad(message);
    window.requestAnimationFrame(function () {
      window.setTimeout(function () {
        window.location.assign(anchor.href);
      }, 24);
    });
  }, true);

  // Navigation: show overlay while browser navigates away
  window.addEventListener('beforeunload', function () {
    // Some navigations (notably file downloads via window.location) may trigger
    // beforeunload but keep the current page. In those cases the overlay would
    // stay visible forever. We auto-hide after a short grace period if the page
    // is still active and there is no pending operation.
    try {
      if (!ensureElements()) return;
      showNow(lastMessage || defaultLoading);

      window.setTimeout(function () {
        try {
          if (pending === 0 && document.visibilityState === 'visible') {
            overlayEl.classList.remove('show');
            document.body.classList.remove('loading-overlay-open');
          }
        } catch (_) {}
      }, 1500);
    } catch (e) {}
  });

  // Hook fetch with a delay (prevents flicker for fast calls)
  const _fetch = window.fetch;
  if (typeof _fetch === 'function') {
    window.fetch = function (input, init) {
      init = init || {};
      const method = (init.method || 'GET').toUpperCase();

      // Allow opt-out
      if (init.noOverlay === true) {
        return _fetch(input, init);
      }

      let url = '';
      try {
        url = (typeof input === 'string') ? input : (input && input.url) ? input.url : '';
      } catch (e) {}

      // Allow opt-out via header
      try {
        const h = init.headers;
        if (h) {
          const val = (typeof h.get === 'function') ? h.get('X-No-Overlay') : (h['X-No-Overlay'] || h['x-no-overlay']);
          if (String(val || '') === '1') {
            return _fetch(input, init);
          }
        }
      } catch (e) {}

      let msg = defaultLoading;
      if (method !== 'GET') msg = defaultSaving;
      if (/import|upload|sync|backup/i.test(url)) msg = defaultOperation;

      show(msg);
      return _fetch(input, init).finally(function () {
        hide();
      });
    };
  }

  // Ensure overlay elements exist after DOM is ready
  document.addEventListener('DOMContentLoaded', function () {
    ensureElements();
    try {
      const raw = sessionStorage.getItem(NAV_STORAGE_KEY);
      if (!raw) return;
      const data = JSON.parse(raw);
      if (!data || !data.ts || (Date.now() - Number(data.ts)) > NAV_STORAGE_TTL_MS) {
        clearRememberedNavigation();
        return;
      }
      navRestoreVisible = true;
      showNow(data.message || defaultLoading);
    } catch (_) {
      clearRememberedNavigation();
    }
  });

  window.addEventListener('load', function () {
    clearRememberedNavigation();
    if (!navRestoreVisible) return;
    navRestoreVisible = false;
    if (pending === 0 && ensureElements()) {
      overlayEl.classList.remove('show');
      document.body.classList.remove('loading-overlay-open');
      setMessage(defaultLoading);
    }
  });
})();
