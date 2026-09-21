/* static/shared/page-nav.js
 * Floating "Page Up / Page Down" for long pages. Same look and feel as the CSV editor's floating scroll navigation
 * (round buttons, progress ring, tooltip), but it moves from section to section instead of jumping to the very top/bottom.
 *
 *   PageNav.init({ sections: '.question-response-card', offset: 64 });
 *
 * It scrolls the MAIN page (window), never an inner box.
 *   Page Down  ->  the top of the NEXT section (the bottom of the page after the last one)
 *   Page Up    ->  the top of the section you are in, or, when you are already at its top, the PREVIOUS one
 * However tall a section is (a question with an open explanation can be thousands of pixels), one press is one section.
 * The control that cannot move any further is disabled. Nothing here uses the keyboard: PageUp/PageDown keep doing what
 * the browser does.
 */
(function () {
  'use strict';

  var AT_TOP = 12;              // px: closer than this to a section's top counts as "at" it
  var NEAR_TOP = 0.3;           // ...and Page Up from within this fraction of a screen below a section's top goes to the previous one

  function el(tag, attrs, html) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (html) e.innerHTML = html;
    return e;
  }

  function init(opts) {
    opts = opts || {};
    if (document.getElementById('pgNav')) return null;

    var RING = 2 * Math.PI * 18;
    var nav = el('div', { id: 'pgNav', 'class': 'pgnav', role: 'group', 'aria-label': 'Page navigation' });
    nav.hidden = true;
    var up = el('button', { type: 'button', 'class': 'pgnav-btn', 'data-dir': 'up', 'data-tooltip': 'Page Up', title: 'Page Up', 'aria-label': 'Page up' },
      '<svg class="pgnav-ring" viewBox="0 0 40 40" aria-hidden="true"><circle class="bg" cx="20" cy="20" r="18"></circle><circle class="fg" cx="20" cy="20" r="18"></circle></svg><i class="fas fa-chevron-up" aria-hidden="true"></i>');
    var down = el('button', { type: 'button', 'class': 'pgnav-btn', 'data-dir': 'down', 'data-tooltip': 'Page Down', title: 'Page Down', 'aria-label': 'Page down' },
      '<i class="fas fa-chevron-down" aria-hidden="true"></i>');
    nav.appendChild(up); nav.appendChild(down);
    document.body.appendChild(nav);
    var fg = up.querySelector('.fg');
    fg.style.strokeDasharray = String(RING);

    var doc = document.documentElement;
    var pending = null, frame = 0;
    var reduced = function () { return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches; };
    var offset = function () { return typeof opts.offset === 'function' ? opts.offset() : (opts.offset || 0); };
    var maxY = function () { return Math.max(0, doc.scrollHeight - window.innerHeight); };
    var curY = function () { return pending !== null ? pending : window.scrollY; };

    function sections() {                                                   // [{el, top}] in page order, tops measured right now
      var nodes = opts.sections ? Array.prototype.slice.call(document.querySelectorAll(opts.sections)) : [];
      var off = offset(), y = window.scrollY;
      return nodes.filter(function (n) { return n.offsetParent !== null || getComputedStyle(n).position === 'fixed'; })
        .map(function (n) { return { el: n, top: Math.round(n.getBoundingClientRect().top + y - off) }; })
        .sort(function (x, z) { return x.top - z.top; });
    }

    function target(dir) {                                                  // -> { el, y }: the section to land on (el is null for the very top / bottom)
      var cur = curY(), vh = window.innerHeight, max = maxY(), secs = sections(), i, hit = null;
      if (dir > 0) {
        for (i = 0; i < secs.length; i++) { if (secs[i].top > cur + AT_TOP) { hit = secs[i]; break; } }
        return hit ? { el: hit.el, y: hit.top } : { el: null, y: max };        // after the last section: the bottom of the page
      }
      var here = -1;                                                          // the section we are in: the last one that starts above us
      for (i = secs.length - 1; i >= 0; i--) { if (secs[i].top < cur - AT_TOP) { here = i; break; } }
      if (here >= 0 && cur - secs[here].top < vh * NEAR_TOP) here -= 1;       // just below its top counts as "at the top": go one further
      return here >= 0 ? { el: secs[here].el, y: secs[here].top } : { el: null, y: 0 };
    }

    // The page can change height while it glides (a question's saved-explanation panel appears as it comes near, an explanation
    // opens): so when the glide has stopped, land exactly on the section, wherever it is now.
    var settleToken = 0;
    function settle(el, wantBottom) {
      var token = ++settleToken, last = window.scrollY, still = 0, started = Date.now();
      (function tick() {
        if (token !== settleToken) return;                                     // a newer press took over
        var y = window.scrollY;
        still = Math.abs(y - last) < 1 ? still + 1 : 0; last = y;
        if (still < 6 && Date.now() - started < 3000) { setTimeout(tick, 40); return; }
        pending = null;
        var want = el ? Math.round(el.getBoundingClientRect().top + window.scrollY - offset()) : (wantBottom ? maxY() : 0);
        want = Math.max(0, Math.min(maxY(), want));
        if (Math.abs(want - window.scrollY) > 3) window.scrollTo({ top: want, behavior: 'auto' });
        update();
      })();
    }

    function go(dir) {
      var cur = curY(), t = target(dir);
      if (Math.abs(t.y - cur) < 2) return;
      pending = t.y;                                     // a second press during the glide continues from where the first one is going
      window.scrollTo({ top: t.y, behavior: reduced() ? 'auto' : 'smooth' });
      settle(t.el, dir > 0);
    }

    function userTakesOver() { pending = null; settleToken++; }   // the wheel, a finger or a key: their scrolling wins over ours

    function update() {
      frame = 0;
      var st = window.scrollY, max = maxY();
      nav.hidden = max < 120;                            // nothing to page through
      up.disabled = st <= 2;
      down.disabled = st >= max - 2;
      up.setAttribute('aria-disabled', String(up.disabled)); down.setAttribute('aria-disabled', String(down.disabled));
      if (max > 0) fg.style.strokeDashoffset = String(RING - RING * Math.min(1, st / max));
      if (pending !== null && Math.abs(st - pending) < 2) pending = null;
    }
    var schedule = function () { if (!frame) frame = requestAnimationFrame(update); };

    up.addEventListener('click', function () { go(-1); });
    down.addEventListener('click', function () { go(1); });
    ['wheel', 'touchstart', 'keydown'].forEach(function (ev) { window.addEventListener(ev, userTakesOver, { passive: true }); });
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    if (window.ResizeObserver) new ResizeObserver(schedule).observe(doc);        // the page grows as explanations and discussions open
    update();
    return { update: update, go: go };
  }

  window.PageNav = { init: init };
})();
