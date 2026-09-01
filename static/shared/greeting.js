/* static/shared/greeting.js
 * Rewrites the dashboard's time-of-day greeting word using the viewer's
 * actual local clock instead of the server-rendered value (the server's
 * configured timezone can differ from wherever the person actually is).
 * Only the greeting word itself is touched — element id "dashGreetWord" —
 * the name next to it is left exactly as the server rendered it, so this
 * never re-serializes user-controlled text.
 */
(function () {
  function applyLocalGreeting() {
    var el = document.getElementById('dashGreetWord');
    if (!el) return;
    var hour = new Date().getHours();
    var word = (hour < 5)  ? 'Good night'
             : (hour < 12) ? 'Good morning'
             : (hour < 17) ? 'Good afternoon'
             : (hour < 21) ? 'Good evening'
             : 'Good night';
    el.textContent = word;
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applyLocalGreeting);
  } else {
    applyLocalGreeting();
  }
})();
