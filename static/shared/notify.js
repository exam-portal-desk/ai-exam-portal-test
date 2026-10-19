/* Notify — the single notification API for the User and Admin portals (styles: shared/notify.css).

   Usage
     Notify.success('Notebook created');                       Notify.error('Could not save', 'Check your connection and try again.');
     Notify.warning('Session expires soon');                    Notify.info('Exam starts at 10:00');
     Notify.show({ type, title, message, sub, duration, sticky, key })  → id        Notify.dismiss(id)   Notify.clear()
     Notify.welcome({ name })                                   a distinct, larger greeting card
     Notify.flash([[category, message], …])                     server (Flask) flashes; run once automatically from #notifyFlash

   One message, one notification: identical text+type that is already on screen only restarts its timer, the flash payload is consumed
   once per page load, and the legacy helpers (appToast, adminToast) are thin wrappers over this API — never a second implementation.
   Plain text only, except through `html: true` (adminToast's historic contract), which is sanitised to <b> <strong> <i> <em> <code> <br>. */
(function (global) {
  'use strict';
  if (global.Notify && global.Notify.__v) return;                             // loaded twice: keep the first instance

  var MAX_VISIBLE = 3;
  var DURATION = { success: 4500, info: 4500, warning: 6500, error: 8000, welcome: 5800 };
  var ICON = { success: 'fa-check', error: 'fa-xmark', warning: 'fa-triangle-exclamation', info: 'fa-circle-info', logout: 'fa-right-from-bracket' };
  var live = [], queue = [], recent = {}, seq = 0, regions = {}, flashDone = false;

  function kind(type) {
    type = String(type || 'info').toLowerCase();
    if (type === 'danger' || type === 'error' || type === 'fail' || type === 'failure') return 'error';
    if (type === 'warn' || type === 'warning') return 'warning';
    if (type === 'success' || type === 'ok') return 'success';
    return 'info';
  }

  function isPhone() { return !!(global.matchMedia && global.matchMedia('(max-width: 640px)').matches); }
  function whenBody(fn) { if (document.body) fn(); else document.addEventListener('DOMContentLoaded', fn, { once: true }); }
  function region(top) {
    var id = top ? 'notifyRegionTop' : 'notifyRegion';
    if (regions[id] && regions[id].isConnected) return regions[id];
    var el = document.getElementById(id) || document.createElement('div');
    el.id = id; el.className = 'nf-region' + (top ? ' nf-region-top' : ''); el.setAttribute('role', 'region'); el.setAttribute('aria-label', 'Notifications');
    if (!el.isConnected) document.body.appendChild(el);
    regions[id] = el; return el;
  }

  var ALLOWED = { B: 1, STRONG: 1, I: 1, EM: 1, CODE: 1, BR: 1 };
  var DROP = { SCRIPT: 1, STYLE: 1, IFRAME: 1, OBJECT: 1, EMBED: 1, TEMPLATE: 1, NOSCRIPT: 1, TEXTAREA: 1 };       // their contents are code, not words: removed entirely
  function sanitize(html) {
    var doc = new DOMParser().parseFromString('<body>' + html + '</body>', 'text/html'), out = document.createDocumentFragment();
    (function walk(node, into) {
      Array.prototype.forEach.call(node.childNodes, function (child) {
        if (child.nodeType === 3) into.appendChild(document.createTextNode(child.nodeValue));
        else if (child.nodeType === 1) {
          if (ALLOWED[child.tagName]) { var copy = document.createElement(child.tagName.toLowerCase()); walk(child, copy); into.appendChild(copy); }
          else if (!DROP[child.tagName]) walk(child, into);                    // unknown element: keep its text, drop the element (and every attribute)
        }
      });
    })(doc.body, out);
    return out;
  }
  function fill(el, text, html) { if (html) el.appendChild(sanitize(String(text))); else el.textContent = String(text); }

  function initials(name) {
    var parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    return ((parts[0] || '?').charAt(0) + (parts.length > 1 ? parts[parts.length - 1].charAt(0) : '')).toUpperCase();
  }

  function startTimer(t) {
    if (t.sticky || t.timer) return;
    t.started = Date.now(); t.timer = setTimeout(function () { dismiss(t.id); }, t.remaining);
    t.el.classList.remove('nf-paused');
  }
  function pauseTimer(t) {
    if (!t.timer) return;
    clearTimeout(t.timer); t.timer = null; t.remaining = Math.max(400, t.remaining - (Date.now() - t.started));
    t.el.classList.add('nf-paused');
  }

  function build(o) {
    var welcome = o.variant === 'welcome';
    var el = document.createElement('div');
    el.className = 'nf-toast nf-' + o.type + (welcome ? ' nf-welcome' : '') + (o.sticky ? ' nf-sticky' : '');
    el.setAttribute('role', (o.type === 'error' || o.type === 'warning') ? 'alert' : 'status');
    el.setAttribute('aria-atomic', 'true'); el.tabIndex = -1;
    el.style.setProperty('--nf-ms', o.duration + 'ms');
    var lead = document.createElement(welcome ? 'span' : 'span');
    var body = document.createElement('div'); body.className = 'nf-body';
    if (welcome) {
      lead.className = 'nf-avatar'; lead.setAttribute('aria-hidden', 'true'); lead.textContent = initials(o.name);
      var eyebrow = document.createElement('div'); eyebrow.className = 'nf-eyebrow'; eyebrow.textContent = 'Welcome back';
      var name = document.createElement('div'); name.className = 'nf-name'; name.textContent = o.name;
      body.append(eyebrow, name);
      if (o.sub) { var sub = document.createElement('div'); sub.className = 'nf-sub'; sub.textContent = o.sub; body.appendChild(sub); }
    } else {
      lead.className = 'nf-ic'; lead.setAttribute('aria-hidden', 'true');
      var i = document.createElement('i'); i.className = 'fas ' + (ICON[o.icon] || ICON[o.type]); lead.appendChild(i);
      if (o.title) { var title = document.createElement('div'); title.className = 'nf-title'; fill(title, o.title, o.html); body.appendChild(title); }
      if (o.message) { var msg = document.createElement('div'); msg.className = 'nf-msg'; fill(msg, o.message, o.html); body.appendChild(msg); }
    }
    var x = document.createElement('button'); x.type = 'button'; x.className = 'nf-x'; x.setAttribute('aria-label', 'Dismiss notification'); x.innerHTML = '<i class="fas fa-xmark" aria-hidden="true"></i>';
    el.append(lead, body, x);
    if (!o.sticky) { var bar = document.createElement('span'); bar.className = 'nf-bar'; bar.setAttribute('aria-hidden', 'true'); el.appendChild(bar); }
    return el;
  }

  function show(opts) {
    opts = opts || {};
    var o = {
      type: kind(opts.type), variant: opts.variant || 'default', title: opts.title || '', message: opts.message == null ? '' : String(opts.message), name: opts.name || '',
      sub: opts.sub || '', icon: opts.icon || '', html: !!opts.html, sticky: !!opts.sticky, duration: 0,
    };
    if (o.variant === 'welcome') o.type = 'info';
    if (o.variant !== 'welcome' && !o.title && !o.message) return 0;             // nothing to say
    if (o.variant !== 'welcome' && o.sub && !o.title) { o.title = o.message; o.message = o.sub; o.sub = ''; }   // legacy (message, type, sub) → title + detail
    o.duration = Math.max(1500, Number(opts.duration) || DURATION[o.variant === 'welcome' ? 'welcome' : o.type]);
    o.key = String(opts.key || (o.variant + '|' + o.type + '|' + o.title + '|' + o.message + '|' + o.name));
    for (var n = 0; n < live.length; n++) {                                      // the same notification is already on screen: restart its timer, do not stack a twin
      if (live[n].key === o.key) { var t = live[n]; pauseTimer(t); t.remaining = o.duration; startTimer(t); t.el.classList.remove('nf-bump'); void t.el.offsetWidth; t.el.classList.add('nf-bump'); return t.id; }
    }
    if (recent[o.key] && Date.now() - recent[o.key] < 700) return 0;             // a double-fired handler
    recent[o.key] = Date.now();
    o.id = ++seq;
    var top = o.variant === 'welcome';
    if (!top && live.filter(function (t) { return !t.top; }).length >= MAX_VISIBLE) { queue.push(o); return o.id; }
    mount(o, top);
    return o.id;
  }

  function mount(o, top) {
    whenBody(function () {
      var el = build(o), t = { id: o.id, key: o.key, el: el, sticky: o.sticky, remaining: o.duration, timer: null, started: 0, top: top };
      live.push(t);
      var host = region(top || isPhone()); host.appendChild(el);                // phones use ONE column (top), so the welcome card and the toasts never overlap
      el.querySelector('.nf-x').addEventListener('click', function () { dismiss(t.id); });
      el.addEventListener('mouseenter', function () { pauseTimer(t); });
      el.addEventListener('mouseleave', function () { if (!el.contains(document.activeElement)) startTimer(t); });
      el.addEventListener('focusin', function () { pauseTimer(t); });
      el.addEventListener('focusout', function () { if (!el.matches(':hover')) startTimer(t); });
      if (document.hidden) t.el.classList.add('nf-paused'); else startTimer(t);
    });
  }

  function dismiss(id) {
    var idx = -1;
    live.forEach(function (t, i) { if (t.id === id) idx = i; });
    if (idx < 0) { queue = queue.filter(function (q) { return q.id !== id; }); return; }
    var t = live.splice(idx, 1)[0];
    clearTimeout(t.timer); t.timer = null;
    var done = false, finish = function () { if (done) return; done = true; t.el.remove(); drain(); };
    t.el.classList.add('nf-leave'); t.el.addEventListener('animationend', finish, { once: true }); setTimeout(finish, 320);
  }
  function drain() {
    while (queue.length && live.filter(function (t) { return !t.top; }).length < MAX_VISIBLE) { var o = queue.shift(); mount(o, false); }
  }
  function clear() { queue = []; live.slice().forEach(function (t) { dismiss(t.id); }); }

  document.addEventListener('visibilitychange', function () {
    live.forEach(function (t) { if (document.hidden) pauseTimer(t); else if (!t.el.matches(':hover') && !t.el.contains(document.activeElement)) startTimer(t); });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    var box = document.activeElement && document.activeElement.closest && document.activeElement.closest('.nf-toast');
    if (!box) return;
    live.forEach(function (t) { if (t.el === box) dismiss(t.id); });
  });

  // ── server (Flask) flashes ───────────────────────────────────────────────────────────────────
  function cleanText(text) { return String(text == null ? '' : text).replace(/^(?:[✅❌⚠ℹ]️?\s*)+/, '').trim(); }
  function portal() { var m = document.querySelector('meta[name="notify-portal"]'); return (m && m.content) || 'user'; }

  function present(category, text) {
    text = cleanText(text);
    if (!text) return;
    var cat = kind(category), welcome = /^Welcome back,\s*(.+?)\s*!?$/i.exec(text);
    if (welcome && cat === 'success') {
      show({ variant: 'welcome', name: welcome[1], sub: portal() === 'admin' ? 'Admin portal' : 'Good to see you again', key: 'welcome|' + welcome[1] });
    } else if (cat === 'success' && /\blog(?:ged)? ?out\b/i.test(text)) {
      show({ type: 'success', icon: 'logout', title: 'Signed out', message: text });
    } else show({ type: cat, message: text });
  }
  function flash(list) { (list || []).forEach(function (pair) { if (Array.isArray(pair)) present(pair[0], pair[1]); }); }
  function flashFromPage() {
    if (flashDone) return; flashDone = true;
    var node = document.getElementById('notifyFlash');
    if (!node) return;
    try { flash(JSON.parse(node.textContent || '[]')); } catch (e) { /* malformed payload: nothing to show */ }
    node.remove();                                                                // consumed: a re-run (bfcache, repeated init) shows nothing twice
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', flashFromPage, { once: true }); else flashFromPage();

  function shorthand(type) {
    return function (message, extra) {
      var opts = typeof extra === 'string' ? { sub: extra } : (extra || {});
      return show(Object.assign({ type: type, message: message }, opts));
    };
  }
  global.Notify = Object.freeze({
    __v: 1, show: show, dismiss: dismiss, clear: clear, flash: flash,
    success: shorthand('success'), error: shorthand('error'), warning: shorthand('warning'), info: shorthand('info'),
    welcome: function (opts) { return show(Object.assign({ variant: 'welcome' }, opts || {})); },
  });

  // Legacy entry points kept so every existing call site keeps working unchanged.
  global.appToast = function (msg, type, sub, ms) { return show({ type: type || 'success', message: msg, sub: sub, duration: ms }); };      // historic default: success
  global.adminToast = function (msg, type, sub, ms) { return show({ type: type || 'success', message: msg, sub: sub, duration: ms, html: true }); };
})(window);
