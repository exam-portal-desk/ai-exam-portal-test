/* static/shared/plan-validity.js
   Keeps the plan chip and the profile's "Your Plan" validity line true while a page stays open. The server owns every value
   (GET /api/v01/me/plan); this only asks again once a minute and at the plan's expiry instant, then redraws. When the plan
   lapses the chip switches to the plan the account is on now, and the profile page reloads to show what that plan includes. */
(function () {
  var chip = document.querySelector('.sb-plan');
  var hero = document.getElementById('planExpiry');
  var source = (chip && chip.getAttribute('data-plan-expires')) || (hero && hero.getAttribute('data-plan-expires'));
  if (!source) return;

  var timer = 0;

  function delayUntil(iso) {
    var left = Date.parse(iso) - Date.now();
    return Math.max(1000, Math.min(60000, left + 1000));
  }

  function draw(d) {
    var ex = d.expiry || {};
    var remaining = document.querySelector('[data-plan-remaining]');
    if (remaining && ex.remaining_text) remaining.textContent = ex.remaining_text;
    if (!chip) return;
    var sub = ex.state === 'expired' ? (ex.expired_plan_label || 'Your') + ' plan expired ' + ex.expires_display
      : (ex.expires_display ? 'Expires ' + ex.expires_display + ' · ' + ex.remaining_text : 'No expiry');
    chip.setAttribute('data-tip', d.plan_label + ' plan');
    chip.setAttribute('data-tip-sub', sub);
    chip.setAttribute('aria-label', 'Your plan: ' + d.plan_label);
    var badge = chip.querySelector('.plan-badge');
    if (!badge) return;
    badge.setAttribute('data-tone', d.tone);
    var icon = badge.querySelector('i');
    if (icon) icon.className = 'fas ' + d.icon;
    var text = badge.querySelector('.pb-text');
    if (text) text.textContent = d.plan_label;
  }

  function refresh() {
    fetch('/api/v01/me/plan', { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.success) { timer = setTimeout(refresh, 60000); return; }
        draw(d);
        var ex = d.expiry || {};
        if (ex.state === 'active' || ex.state === 'expiring_soon') {
          timer = setTimeout(refresh, delayUntil(ex.expires_at_utc));
        } else if (ex.state === 'expired' && hero) {
          window.location.reload();
        }
      })
      .catch(function () { timer = setTimeout(refresh, 60000); });
  }

  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) { clearTimeout(timer); refresh(); }
  });
  timer = setTimeout(refresh, delayUntil(source));
})();
