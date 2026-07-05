/*
 * Iniezione automatica del token CSRF (Flask-WTF) lato client.
 * Copre: submit nativo dei form (anche creati da JS), form.submit() programmatico,
 * fetch() e XMLHttpRequest verso lo stesso origin. Va caricato PRIMA degli altri
 * script di pagina, e richiede <meta name="csrf-token" content="..."> nell'head.
 */
(function () {
  'use strict';

  var meta = document.querySelector('meta[name="csrf-token"]');
  var token = meta ? meta.getAttribute('content') : '';
  if (!token) return;

  function needsToken(method) {
    method = String(method || 'GET').toUpperCase();
    return method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS';
  }

  function isSameOrigin(url) {
    if (!url) return true; // URL relativo/assente = stessa pagina
    try {
      return new URL(url, window.location.href).origin === window.location.origin;
    } catch (e) {
      return false;
    }
  }

  function ensureFormToken(form) {
    try {
      if (!form || !needsToken(form.method)) return;
      if (form.querySelector('input[name="csrf_token"]')) return;
      var input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'csrf_token';
      input.value = token;
      form.appendChild(input);
    } catch (e) { /* mai bloccare un submit */ }
  }

  // Submit via interazione utente o requestSubmit()
  document.addEventListener('submit', function (ev) {
    ensureFormToken(ev.target);
  }, true);

  // Submit programmatico: form.submit() non emette l'evento 'submit'
  var nativeSubmit = HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit = function () {
    ensureFormToken(this);
    return nativeSubmit.apply(this, arguments);
  };

  if (window.fetch) {
    var nativeFetch = window.fetch;
    window.fetch = function (input, init) {
      try {
        var isRequest = (typeof Request !== 'undefined') && (input instanceof Request);
        var url = isRequest ? input.url : String(input || '');
        var method = (init && init.method) || (isRequest ? input.method : 'GET');
        if (needsToken(method) && isSameOrigin(url)) {
          init = init || {};
          var headers = new Headers(init.headers || (isRequest ? input.headers : undefined));
          if (!headers.has('X-CSRFToken')) headers.set('X-CSRFToken', token);
          init.headers = headers;
        }
      } catch (e) { /* in caso di dubbio, non alterare la richiesta */ }
      return nativeFetch.call(this, input, init);
    };
  }

  var nativeOpen = XMLHttpRequest.prototype.open;
  var nativeSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__csrfNeeded = needsToken(method) && isSameOrigin(url);
    return nativeOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    try {
      if (this.__csrfNeeded) this.setRequestHeader('X-CSRFToken', token);
    } catch (e) { /* header già impostato o richiesta non valida */ }
    return nativeSend.apply(this, arguments);
  };
})();
