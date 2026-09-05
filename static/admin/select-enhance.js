/* static/admin/select-enhance.js
 * Turns a plain <select> into the same themed dropdown-button look used
 * elsewhere in the admin UI, instead of the browser's unstyled native
 * option list. Purely a presentation layer: the original <select> stays
 * in the DOM (just hidden) and fully functional, so any existing code that
 * reads el.value or listens for its 'change' event — e.g. the filters={}
 * wiring in static/admin/list-controller.js — needs zero changes.
 *
 * enhanceSelect(selectEl, { icon: 'fas fa-layer-group', block: true }) -> { refresh() }
 * icon is optional, shown before the current label. block (default false)
 * makes it a full-width form field matching .form-control/.form-select,
 * for a select sitting in a form grid rather than a compact filter toolbar.
 */
(function (global) {
  let _openWrap = null; // only one enhanced dropdown open at a time, page-wide

  function closeOpen() {
    if (_openWrap) { _openWrap.classList.remove('open'); _openWrap = null; }
  }
  document.addEventListener('click', () => closeOpen());
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeOpen(); });
  // The open menu's position is computed once, at open time (see
  // positionMenu() below) — close instead of leaving it stale if the page
  // scrolls or resizes underneath it. Scroll events don't bubble, but a
  // capture-phase listener on window still sees every one of them as they
  // travel down to their real target — including the open menu's OWN
  // internal overflow-y:auto scrollbar (long option lists, see
  // select-enhance.css's max-height), whose target is the menu itself.
  // Without the guard below, that self-scroll was indistinguishable from
  // "the page scrolled underneath the menu" and closed the dropdown on the
  // very first wheel/touch scroll tick inside it. Skip closing only when
  // the scroll came from inside the currently-open menu — any other
  // scroll (the page, a modal body, etc.) still closes it as before.
  window.addEventListener('scroll', e => {
    if (_openWrap && e.target && typeof e.target.contains === 'function' && _openWrap.contains(e.target)) return;
    closeOpen();
  }, true);
  window.addEventListener('resize', () => closeOpen());

  function enhanceSelect(selectEl, opts) {
    if (!selectEl || selectEl._enhanced) return null;
    selectEl._enhanced = true;
    opts = opts || {};

    const wrap = document.createElement('div');
    wrap.className = 'sel-enh' + (opts.block ? ' sel-enh-block' : '');

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'sel-enh-trigger';
    trigger.innerHTML =
      (opts.icon ? `<i class="${opts.icon}"></i>` : '') +
      '<span class="sel-enh-label"></span><i class="fas fa-chevron-down sel-enh-chev"></i>';

    const menu = document.createElement('div');
    menu.className = 'sel-enh-menu';

    wrap.appendChild(trigger);
    wrap.appendChild(menu);
    selectEl.insertAdjacentElement('afterend', wrap);
    selectEl.style.display = 'none';

    function render() {
      menu.innerHTML = '';
      Array.from(selectEl.options).forEach(opt => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'sel-enh-item' + (opt.value === selectEl.value ? ' active' : '');
        item.textContent = opt.textContent;
        item.addEventListener('click', e => {
          e.stopPropagation();
          if (selectEl.value !== opt.value) {
            selectEl.value = opt.value; // triggers render() itself — see the wrapped setter below
            selectEl.dispatchEvent(new Event('change', { bubbles: true }));
          }
          closeOpen();
        });
        menu.appendChild(item);
      });
      const current = Array.from(selectEl.options).find(o => o.value === selectEl.value);
      trigger.querySelector('.sel-enh-label').textContent = current ? current.textContent : '';
    }

    // The menu is positioned with `fixed` coordinates computed here, not
    // CSS `position:absolute; top:100%` — a select sitting near the bottom
    // of a rounded-corner .card (which needs `overflow:hidden` to clip the
    // table inside it) would otherwise have its dropdown silently clipped
    // by that same boundary despite `display:block` and a high z-index —
    // overflow:hidden clips absolutely-positioned descendants regardless of
    // stacking order. `fixed` coordinates escape that (as long as no
    // ancestor has transform/filter/perspective, which none here do).
    function positionMenu() {
      const rect = trigger.getBoundingClientRect();
      menu.style.position = 'fixed';
      menu.style.left = rect.left + 'px';
      menu.style.minWidth = rect.width + 'px';
      // select-enhance.css gives the "block" (full-width form field) variant
      // `width:100%` so it matches the field while the menu is `absolute` —
      // but position:fixed's containing block is the viewport, not this
      // wrap, so that 100% would otherwise stretch the menu across the
      // whole screen. Pin it to the trigger's real width instead.
      if (wrap.classList.contains('sel-enh-block')) menu.style.width = rect.width + 'px';
      menu.style.top = (rect.bottom + 4) + 'px';
      menu.style.bottom = '';
      const menuRect = menu.getBoundingClientRect();
      // Flip upward if there's not enough room below but there IS above —
      // otherwise leave it below and let the menu's own max-height/scroll
      // (see select-enhance.css) handle a genuinely short viewport.
      if (menuRect.bottom > window.innerHeight && rect.top > menuRect.height) {
        menu.style.top = (rect.top - menuRect.height - 4) + 'px';
      }
      // Clamp so it never runs past the right edge of the viewport.
      if (rect.left + menuRect.width > window.innerWidth) {
        menu.style.left = Math.max(4, window.innerWidth - menuRect.width - 4) + 'px';
      }
    }

    trigger.addEventListener('click', e => {
      e.stopPropagation();
      const willOpen = !wrap.classList.contains('open');
      closeOpen();
      if (willOpen) {
        wrap.classList.add('open');
        _openWrap = wrap;
        render();
        positionMenu();
      }
    });
    wrap.addEventListener('click', e => e.stopPropagation()); // clicks inside the open menu must not bubble to the page-level closer

    // The underlying <select>'s own option list can be rebuilt elsewhere
    // (e.g. a category filter repopulating a dependent subcategory filter)
    // — watch for that instead of requiring every such call site to know
    // this enhancement exists.
    new MutationObserver(render).observe(selectEl, { childList: true });

    // Existing code elsewhere on a page often sets selectEl.value directly
    // (form reset, populating an Edit modal, preselecting from another
    // field) with no option-list change for the MutationObserver above to
    // catch. Wrapping the native value accessor makes ANY such assignment
    // — past or future call sites, this page or any other — refresh the
    // trigger label automatically, with zero changes needed at those call
    // sites and no risk of the label silently going stale again later.
    const nativeValueDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
    Object.defineProperty(selectEl, 'value', {
      configurable: true,
      get() { return nativeValueDesc.get.call(this); },
      set(v) { nativeValueDesc.set.call(this, v); render(); },
    });

    render();
    return { refresh: render };
  }

  global.enhanceSelect = enhanceSelect;
})(window);
