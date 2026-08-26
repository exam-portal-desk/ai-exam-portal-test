/* static/admin/list-controller.js
 * Shared search + filter + page-number pagination controller for admin
 * list pages (Exams, Categories, Subjects, Users, Requests). Previously
 * each page hand-rolled its own debounced search / filter wiring / fetch /
 * paginator markup (3 near-duplicate implementations); this is the one
 * shared version. Rendering the fetched page is left to the caller via
 * onData(json) — some pages want a server-rendered HTML partial swapped
 * in, others want to build rows client-side from JSON, and forcing both
 * into one shape would cost more than it'd save.
 *
 * createAdminList({
 *   endpoint,               // e.g. '/api/v01/admin/exams'
 *   paginatorEl,            // container to render the paginator into
 *   paginatorMode = 'pages', // 'pages' (prev/next + numbers) or 'loadmore' (single button, for card grids)
 *   searchInputEl,          // optional <input> for free-text search
 *   searchParam = 'q',      // query param name the endpoint expects
 *   filters = {},           // { paramName: <select/input element> } — sent when truthy
 *   perPage = 20,
 *   debounceMs = 400,
 *   onData(json),           // required — render the response however this page needs.
 *                           // json.page tells you whether this is a fresh search (page===1,
 *                           // replace) or a "Load more" continuation (page>1, append).
 *   extraParams(),          // optional fn returning {paramName: value} computed at fetch time
 * })
 * returns { load(page), reload() }
 */
(function (global) {
  function createAdminList(opts) {
    const {
      endpoint, paginatorEl, paginatorMode = 'pages', searchInputEl, searchParam = 'q',
      filters = {}, perPage = 20, debounceMs = 400, onData, extraParams,
    } = opts;

    let currentPage = 1;

    function buildParams(page) {
      const params = new URLSearchParams();
      params.set('page', page);
      params.set('per_page', perPage);
      const q = searchInputEl ? searchInputEl.value.trim() : '';
      if (q) params.set(searchParam, q);
      Object.entries(filters).forEach(([key, el]) => {
        if (el && el.value) params.set(key, el.value);
      });
      if (extraParams) {
        Object.entries(extraParams() || {}).forEach(([key, val]) => {
          if (val) params.set(key, val);
        });
      }
      return params;
    }

    function renderLoadMore(data) {
      const cur = data.page || 1, tp = data.total_pages || 1, total = data.total || 0;
      if (cur >= tp) { paginatorEl.innerHTML = ''; return; }
      const loaded = Math.min(cur * perPage, total);
      paginatorEl.innerHTML =
        `<button type="button" class="admin-pg-btn admin-pg-loadmore">Load more</button>` +
        `<span class="admin-pg-total">${loaded} of ${total}</span>`;
      paginatorEl.querySelector('.admin-pg-loadmore').addEventListener('click', () => load(cur + 1));
    }

    function renderPaginator(data) {
      if (!paginatorEl) return;
      if (paginatorMode === 'loadmore') { renderLoadMore(data); return; }
      const cur = data.page || 1, tp = data.total_pages || 1, total = data.total || 0;
      if (tp <= 1) { paginatorEl.innerHTML = ''; return; }
      const startP = Math.max(1, cur - 2), endP = Math.min(tp, cur + 2);
      let html = `<button type="button" class="admin-pg-btn" data-page="${cur - 1}" ${cur <= 1 ? 'disabled' : ''} aria-label="Previous page"><i class="fas fa-chevron-left"></i></button>`;
      if (startP > 1) html += `<button type="button" class="admin-pg-btn" data-page="1">1</button>${startP > 2 ? '<span class="admin-pg-ellipsis">…</span>' : ''}`;
      for (let i = startP; i <= endP; i++) {
        html += `<button type="button" class="admin-pg-btn ${i === cur ? 'active' : ''}" data-page="${i}">${i}</button>`;
      }
      if (endP < tp) html += `${endP < tp - 1 ? '<span class="admin-pg-ellipsis">…</span>' : ''}<button type="button" class="admin-pg-btn" data-page="${tp}">${tp}</button>`;
      html += `<button type="button" class="admin-pg-btn" data-page="${cur + 1}" ${cur >= tp ? 'disabled' : ''} aria-label="Next page"><i class="fas fa-chevron-right"></i></button>`;
      html += `<span class="admin-pg-total">${total} total</span>`;
      paginatorEl.innerHTML = html;
      paginatorEl.querySelectorAll('.admin-pg-btn[data-page]:not([disabled])').forEach(btn => {
        btn.addEventListener('click', () => load(parseInt(btn.dataset.page, 10)));
      });
    }

    async function load(page) {
      currentPage = page || 1;
      const params = buildParams(currentPage);
      const res = await fetch(`${endpoint}?${params.toString()}`);
      const data = await res.json();
      onData(data);
      renderPaginator(data);
      return data;
    }

    let debTimer;
    if (searchInputEl) {
      searchInputEl.addEventListener('input', () => {
        clearTimeout(debTimer);
        debTimer = setTimeout(() => load(1), debounceMs);
      });
    }
    Object.values(filters).forEach(el => {
      if (el) el.addEventListener('change', () => load(1));
    });

    return {
      load,
      reload: () => load(currentPage),
      // Render the paginator from server-provided counts on first paint,
      // with no fetch — the initial page is already server-rendered.
      hydrate: data => { currentPage = data.page || 1; renderPaginator(data); },
    };
  }

  global.createAdminList = createAdminList;
})(window);
