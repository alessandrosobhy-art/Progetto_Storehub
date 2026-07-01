/* Global loading overlay (v1.0.1)
   - Blocks clicks while long operations are running
   - Auto-hooks form submit + fetch (with delay to avoid flicker)
*/
(function () {
  const OVERLAY_ID = 'globalLoadingOverlay';
  const SHOW_DELAY_MS = 250;

  let overlayEl = null;
  let msgEl = null;
  let pending = 0;
  let showTimer = null;
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
    show: function (message) { show(message); },
    hide: function () { while (pending > 0) hide(); },
    setMessage: setMessage
  };

  // Hook form submissions (full page or background)
  document.addEventListener('submit', function (ev) {
    const form = ev.target;
    if (!form || !(form instanceof HTMLFormElement)) return;
    if (form.hasAttribute('data-no-overlay') || form.dataset.noOverlay === '1') return;

    const msg = form.dataset.overlayMessage || defaultLoading;
    show(msg);
  }, true);

  // Navigation: show overlay while browser navigates away
  window.addEventListener('beforeunload', function () {
    // Some navigations (notably file downloads via window.location) may trigger
    // beforeunload but keep the current page. In those cases the overlay would
    // stay visible forever. We auto-hide after a short grace period if the page
    // is still active and there is no pending operation.
    try {
      if (!ensureElements()) return;
      overlayEl.classList.add('show');
      document.body.classList.add('loading-overlay-open');

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
  });
})();
