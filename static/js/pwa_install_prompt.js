(function () {
  'use strict';

  var deferredPrompt = null;
  var storageKey = 'storehub-pwa-install-dismissed-v1';
  var banner = null;
  var message = null;
  var actionBtn = null;
  var laterBtn = null;
  var dismissBtn = null;
  var topbarBtn = null;
  var installInFlight = false;

  function isIos() {
    return /iphone|ipad|ipod/i.test(navigator.userAgent || '');
  }

  function isAndroid() {
    return /android/i.test(navigator.userAgent || '');
  }

  function isStandalone() {
    return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  }

  function canShow() {
    if (isStandalone()) return false;
    try {
      return localStorage.getItem(storageKey) !== '1';
    } catch (_) {
      return true;
    }
  }

  function hideBanner(persist) {
    if (!banner) return;
    banner.classList.remove('is-visible');
    banner.setAttribute('aria-hidden', 'true');
    if (persist) {
      try { localStorage.setItem(storageKey, '1'); } catch (_) {}
    }
  }

  function showBanner(copy) {
    if (!banner || !canShow()) return;
    if (message) message.textContent = copy;
    banner.classList.add('is-visible');
    banner.setAttribute('aria-hidden', 'false');
  }

  function wait(ms) {
    return new Promise(function (resolve) {
      window.setTimeout(resolve, ms);
    });
  }

  async function promptIfAvailable() {
    if (!deferredPrompt) return false;
    try {
      deferredPrompt.prompt();
      await deferredPrompt.userChoice;
      return true;
    } catch (_) {
      return false;
    } finally {
      deferredPrompt = null;
    }
  }

  async function openInstallGuide() {
    if (installInFlight) return;
    installInFlight = true;
    try {
      if (await promptIfAvailable()) return;

      if ('serviceWorker' in navigator) {
        try {
          await Promise.race([navigator.serviceWorker.ready, wait(1200)]);
        } catch (_) {}
        if (await promptIfAvailable()) return;
      }

      if (isIos()) {
        showBanner('Su iPhone apri questo sito in Safari, tocca Condividi e poi Aggiungi a Home.');
        return;
      }
      if (isAndroid()) {
        showBanner('Su Android usa il menu del browser e scegli Installa app oppure Aggiungi a schermata Home.');
        return;
      }
      showBanner('Su Chrome desktop usa l’icona Installa app nella barra indirizzi oppure il menu del browser. Se non compare subito, ricarica la pagina e riprova.');
    } finally {
      installInFlight = false;
    }
  }

  function bind() {
    banner = document.getElementById('pwaInstallBanner');
    message = document.getElementById('pwaInstallMessage');
    actionBtn = document.getElementById('pwaInstallAction');
    laterBtn = document.getElementById('pwaInstallLater');
    dismissBtn = document.getElementById('pwaInstallDismiss');
    topbarBtn = document.getElementById('topbar-install-app');

    if (actionBtn) actionBtn.addEventListener('click', function () { openInstallGuide(); });
    if (laterBtn) laterBtn.addEventListener('click', function () { hideBanner(true); });
    if (dismissBtn) dismissBtn.addEventListener('click', function () { hideBanner(true); });
    if (topbarBtn) topbarBtn.addEventListener('click', function () { openInstallGuide(); });

    window.addEventListener('beforeinstallprompt', function (ev) {
      ev.preventDefault();
      deferredPrompt = ev;
      showBanner('Installa l’app sul dispositivo per aprirla più velocemente e usarla come una vera web app.');
    });

    window.addEventListener('appinstalled', function () {
      deferredPrompt = null;
      hideBanner(true);
    });

    if (!canShow()) return;
    if (isIos()) {
      window.setTimeout(function () {
        showBanner('Per usare Store Hub 360 come app su iPhone, apri in Safari e scegli Aggiungi a Home.');
      }, 1200);
      return;
    }
    if (isAndroid()) {
      window.setTimeout(function () {
        showBanner('Puoi installare Store Hub 360 dal browser con Installa app o Aggiungi a schermata Home.');
      }, 1200);
    }
  }

  document.addEventListener('DOMContentLoaded', bind);
})();
