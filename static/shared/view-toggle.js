/* static/shared/view-toggle.js
 * Reusable grid/list view toggle. Markup mirrors the existing
 * `.view-toggle` structure used by My Notes (templates/notes/index.html)
 * so the same CSS applies. Two persistence modes:
 *
 *   - storageKey: per-browser only, via localStorage (e.g. Admin Exams).
 *   - syncUrl + section (+ initial): server-persisted per-user, PATCHed
 *     to syncUrl as {section, view_mode} — same idea as Notes' own
 *     users.notes_view_mode column/endpoint, generalized via
 *     users.view_prefs so every new toggle doesn't need its own column.
 *     `initial` should be the value the server already rendered the page
 *     with (avoids a flash-of-wrong-view before this script runs), and
 *     the toggle optimistically flips on click, reverting only if the
 *     PATCH fails — same pattern Notes' notebooks.js uses.
 *
 *   createViewToggle({
 *     toggleEl: document.getElementById('viewToggle'),  // has button[data-view="grid"|"list"]
 *     targetEl: document.getElementById('someGrid'),     // gets `listClass` toggled on it
 *     listClass: 'list-view',    // default
 *     storageKey: 'examsViewMode',              // OR:
 *     syncUrl: '/api/v01/portal/view-mode', section: 'categories', initial: 'grid',
 *     onChange(view) {},         // optional
 *   })
 */
(function (global) {
  function createViewToggle({ toggleEl, targetEl, storageKey, syncUrl, section, initial, listClass = 'list-view', onChange }) {
    if (!toggleEl) return null;

    function apply(view, persist) {
      if (targetEl) targetEl.classList.toggle(listClass, view === 'list');
      toggleEl.querySelectorAll('button[data-view]').forEach(btn => {
        const active = btn.dataset.view === view;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      if (persist && storageKey) {
        try { localStorage.setItem(storageKey, view); } catch (e) { /* private mode / storage blocked */ }
      }
      if (onChange) onChange(view);
    }

    let startView = 'grid';
    if (initial) {
      startView = initial;
    } else if (storageKey) {
      try { startView = localStorage.getItem(storageKey) || 'grid'; } catch (e) { /* ignore */ }
    }
    apply(startView, false);

    toggleEl.addEventListener('click', event => {
      const btn = event.target.closest('button[data-view]');
      if (!btn || btn.classList.contains('active')) return;
      const view = btn.dataset.view;
      const previous = toggleEl.querySelector('button.active')?.dataset.view || startView;
      apply(view, !syncUrl);

      if (syncUrl && section) {
        fetch(syncUrl, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ section, view_mode: view }),
        })
          .then(res => { if (!res.ok) throw new Error('save failed'); })
          .catch(() => apply(previous, false));
      }
    });

    return { apply, current: () => (toggleEl.querySelector('button.active')?.dataset.view || 'grid') };
  }

  global.createViewToggle = createViewToggle;
})(window);
