/* static/ai_assistant/chat.js — multi-conversation AI Study Assistant */

const AiFormatter = {
  SECTIONS: [
    { re: /^\[FINAL ANSWER\]/i, cls: 'label-answer', title: 'Final Answer' },
    { re: /^\[GIVEN\]/i, cls: 'label-given', title: 'Given' },
    { re: /^\[SOLUTION\]/i, cls: 'label-solution', title: 'Solution' },
    { re: /^\[EXPLANATION\]/i, cls: 'label-explain', title: 'Explanation' },
  ],
  escHtml(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); },
  // Matches any LaTeX/mhchem math region so it can be protected from the
  // markdown bold/italic stripping below, which would otherwise mangle the
  // underscores/asterisks/braces inside math (subscripts, \ce{...}, etc).
  MATH_RE: /\$\$[\s\S]+?\$\$|\$[^\n$]+?\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\\ce\{(?:[^{}]|\{[^{}]*\})*\}/g,
  _protectMath(text) {
    const store = [];
    const out = String(text).replace(this.MATH_RE, m => { store.push(m); return `\u0000MATH${store.length - 1}\u0000`; });
    return { out, store };
  },
  _restoreMath(html, store) {
    // Escape < and > within the restored math so the browser's HTML parser
    // can't misread e.g. the "<=>" equilibrium arrow as a tag - everything
    // else (backslashes, braces, _, ^, $) is written back untouched.
    return html.replace(/\u0000MATH(\d+)\u0000/g, (_, i) => store[Number(i)].replace(/</g, '&lt;').replace(/>/g, '&gt;'));
  },
  inlineFmt(l) {
    l = l.replace(/\*\*(.+?)\*\*/g, '$1').replace(/__(.+?)__/g, '$1').replace(/\*(.+?)\*/g, '$1').replace(/_(.+?)_/g, '$1');
    l = l.replace(/`([^`]+)`/g, '<code>$1</code>'); return l;
  },
  isStepLine(l) { return /^(step\s*)?\d+[\.\)]\s+/i.test(l.trim()); },
  parseStep(l) { const m = l.trim().match(/^(?:step\s*)?(\d+)[\.\)]\s+(.*)/i); return m ? { num: m[1], content: m[2] } : null; },
  buildBody(lines) {
    let h = '', i = 0;
    while (i < lines.length) {
      const raw = lines[i], line = raw.trim(); if (!line) { i++; continue; }
      if (this.isStepLine(line)) { const s = this.parseStep(line); if (s) { h += `<div class="step-row"><span class="step-num">${s.num}</span><div class="step-content">${this.inlineFmt(s.content)}</div></div>`; i++; continue; } }
      if (/^[-•]\s+/.test(line)) { let it = ''; while (i < lines.length && /^[-•]\s+/.test(lines[i].trim())) { it += `<li>${this.inlineFmt(lines[i].trim().replace(/^[-•]\s+/, ''))}</li>`; i++; } h += `<ul>${it}</ul>`; continue; }
      h += `<p>${this.inlineFmt(line)}</p>`; i++;
    }
    return h;
  },
  format(text) {
    const { out: protectedText, store } = this._protectMath(text);
    const raw = protectedText.split('\n'); const sections = []; let cur = null;
    for (const line of raw) {
      let matched = false;
      for (const def of this.SECTIONS) { if (def.re.test(line.trim())) { if (cur) sections.push(cur); cur = { cls: def.cls, title: def.title, lines: [] }; matched = true; break; } }
      if (!matched) { if (!cur) cur = { cls: 'label-default', title: null, lines: [] }; cur.lines.push(line); }
    }
    if (cur) sections.push(cur);
    let h = '';
    for (const sec of sections) {
      if (sec.lines.every(l => !l.trim())) continue;
      const lbl = sec.title ? `<span class="section-label ${sec.cls}">${sec.title}</span>` : '';
      h += `<div class="msg-section">${lbl}<div class="section-body">${this.buildBody(sec.lines)}</div></div>`;
    }
    h = h || `<p>${this.inlineFmt(protectedText)}</p>`;
    return this._restoreMath(h, store);
  }
};

const SUGGESTED_PROMPTS = [
  "Explain Bernoulli's theorem",
  "Help me understand this concept",
  "Create practice questions",
  "Explain this question step-by-step",
];

class AIAssistant {
  constructor() {
    this.conversations = [];
    this.convOffset = 0;
    this.convHasMore = false;
    this.convSearch = '';
    this.activeConversationId = null;
    this.activeTitle = null;

    this.messagesOffset = 0;
    this.messagesHasMore = false;

    this.dailyLimit = 50;
    this.questionsUsed = 0;
    this.isSending = false;
    this.lastFailedMessage = null;
    this.pendingDeleteId = null;
    this.pendingRenameId = null;

    this._searchDebounce = null;

    this._applyStoredSidebarState();
    this._initAsync();
    this.setupEventListeners();
  }

  // ── Init ────────────────────────────────────────────────────────────
  _applyStoredSidebarState() {
    if (window.innerWidth > 768 && localStorage.getItem('aiSidebarCollapsed') === 'true') {
      document.getElementById('aiSidebar')?.classList.add('collapsed');
    }
  }

  async _initAsync() {
    this._showSkeleton();
    try {
      const r = await fetch('/api/v01/assistant/init');
      const d = await r.json();
      this._rmSkeleton();
      if (!d.success) { this._renderConvList(); this._showEmptyState(); return; }

      this.dailyLimit = d.dailyLimit;
      this.questionsUsed = d.questionsUsed;
      this.conversations = d.conversations || [];
      this.convOffset = this.conversations.length;
      this.convHasMore = !!d.hasMoreConversations;
      this.updateUsageUI();
      this._renderConvList();

      if (this.conversations.length) {
        await this.selectConversation(this.conversations[0].id, { skipMobileClose: true });
      } else {
        this._showEmptyState();
      }
    } catch (e) {
      this._rmSkeleton();
      this._renderConvList();
      this._showEmptyState();
    }
  }

  updateUsageUI() {
    const rem = this.dailyLimit - this.questionsUsed;
    const ids = ['dailyLimit', 'usedToday', 'remaining']; const vals = [this.dailyLimit, this.questionsUsed, rem];
    ids.forEach((id, i) => { const el = document.getElementById(id); if (el) el.textContent = vals[i]; });
    const h = document.getElementById('hintText'); if (h) h.textContent = `${rem} question${rem !== 1 ? 's' : ''} remaining today`;
    this._updateDailyBanner(rem);
  }

  _updateDailyBanner(remaining) {
    const banner = document.getElementById('dailyLimitBanner');
    if (!banner) return;
    const atLimit = remaining <= 0;
    banner.classList.toggle('show', atLimit);
    const input = document.getElementById('chatInput');
    if (input) input.disabled = atLimit || this._conversationAtLimit();
  }

  // ── Sidebar: conversation list ─────────────────────────────────────
  _showSkeleton() {
    const c = document.getElementById('chatMessages');
    const sk = document.createElement('div');
    sk.id = 'historySkeleton'; sk.className = 'history-loading';
    sk.innerHTML = '<div class="skeleton-msg ai"></div><div class="skeleton-msg user"></div><div class="skeleton-msg ai"></div>';
    c.appendChild(sk);
  }
  _rmSkeleton() { document.getElementById('historySkeleton')?.remove(); }

  _dateBucket(iso) {
    if (!iso) return 'Older';
    const d = new Date(iso); const now = new Date();
    const startOf = dt => new Date(dt.getFullYear(), dt.getMonth(), dt.getDate()).getTime();
    const diffDays = Math.round((startOf(now) - startOf(d)) / 86400000);
    if (diffDays <= 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    return 'Older';
  }

  _renderConvList(append = false) {
    const list = document.getElementById('convList');
    if (!append) list.innerHTML = '';

    if (!this.conversations.length) {
      list.innerHTML = `<div class="conv-list-empty"><i class="fas fa-comments" style="display:block;font-size:1.1rem;margin-bottom:.4rem;opacity:.5"></i>${this.convSearch ? 'No matching chats.' : 'No chats yet. Start one below.'}</div>`;
      this._toggleLoadMore(false);
      return;
    }

    let lastBucket = null;
    const frag = document.createDocumentFragment();
    for (const c of this.conversations) {
      const bucket = this._dateBucket(c.updatedAt);
      if (bucket !== lastBucket) {
        const lbl = document.createElement('div'); lbl.className = 'ai-conv-group-label'; lbl.textContent = bucket;
        frag.appendChild(lbl); lastBucket = bucket;
      }
      frag.appendChild(this._buildConvItem(c));
    }
    if (!append) list.innerHTML = '';
    list.appendChild(frag);
    this._toggleLoadMore(this.convHasMore);
  }

  _toggleLoadMore(show) {
    const row = document.getElementById('loadMoreRow');
    if (row) row.hidden = !show;
  }

  _buildConvItem(c) {
    const item = document.createElement('div');
    item.className = 'conv-item dd-wrap' + (c.id === this.activeConversationId ? ' active' : '');
    item.dataset.convId = c.id;

    const info = document.createElement('div');
    info.className = 'conv-info';
    info.innerHTML = `<div class="conv-name"></div><div class="conv-meta"></div>`;
    info.querySelector('.conv-name').textContent = c.title || 'New Chat';
    info.querySelector('.conv-meta').textContent = `${c.messageCount || 0} message${c.messageCount === 1 ? '' : 's'}`;
    info.addEventListener('click', () => this.selectConversation(c.id));

    const menuBtn = document.createElement('button');
    menuBtn.className = 'conv-menu-btn'; menuBtn.type = 'button';
    menuBtn.innerHTML = '<i class="fas fa-ellipsis-vertical"></i>';
    menuBtn.addEventListener('click', (e) => { e.stopPropagation(); this._toggleConvMenu(item, c); });

    item.appendChild(info); item.appendChild(menuBtn);
    return item;
  }

  _toggleConvMenu(item, c) {
    document.querySelectorAll('.dropdown-menu.show').forEach(m => { if (m.parentElement !== item) m.remove(); });
    let menu = item.querySelector('.dropdown-menu');
    if (menu) { menu.remove(); return; }
    menu = document.createElement('div');
    menu.className = 'dropdown-menu show';
    menu.innerHTML = `
      <div class="dropdown-item" data-act="rename"><i class="fas fa-pen"></i> Rename</div>
      <div class="dropdown-item danger" data-act="delete"><i class="fas fa-trash"></i> Delete</div>
    `;
    menu.querySelector('[data-act="rename"]').addEventListener('click', (e) => { e.stopPropagation(); menu.remove(); this.openRenameModal(c.id, c.title); });
    menu.querySelector('[data-act="delete"]').addEventListener('click', (e) => { e.stopPropagation(); menu.remove(); this.openDeleteModal(c.id); });
    item.appendChild(menu);
  }

  async loadMoreConversations() {
    const r = await fetch(`/api/v01/assistant/conversations?limit=20&offset=${this.convOffset}${this.convSearch ? '&search=' + encodeURIComponent(this.convSearch) : ''}`);
    const d = await r.json();
    if (!d.success) return;
    this.conversations = this.conversations.concat(d.conversations || []);
    this.convOffset += (d.conversations || []).length;
    this.convHasMore = !!d.hasMore;
    this._renderConvList();
  }

  async searchConversations(term) {
    this.convSearch = term.trim();
    const r = await fetch(`/api/v01/assistant/conversations?limit=20&offset=0${this.convSearch ? '&search=' + encodeURIComponent(this.convSearch) : ''}`);
    const d = await r.json();
    if (!d.success) return;
    this.conversations = d.conversations || [];
    this.convOffset = this.conversations.length;
    this.convHasMore = !!d.hasMore;
    this._renderConvList();
  }

  // ── Conversation switching ─────────────────────────────────────────
  async selectConversation(id, opts = {}) {
    this.activeConversationId = id;
    this.messagesOffset = 0;
    this.messagesHasMore = false;
    document.querySelectorAll('.conv-item').forEach(el => el.classList.toggle('active', parseInt(el.dataset.convId) === id));

    const c = document.getElementById('chatMessages');
    c.innerHTML = ''; this._showSkeleton();

    try {
      const r = await fetch(`/api/v01/assistant/conversations/${id}/messages?limit=30&offset=0`);
      const d = await r.json();
      this._rmSkeleton();
      if (!d.success) { this._showEmptyState(); return; }

      this.activeTitle = d.conversation.title;
      this._setHeaderTitle(this.activeTitle);
      this.messagesHasMore = !!d.hasMore;
      this.messagesOffset = (d.messages || []).length;

      c.innerHTML = '';
      if (this.messagesHasMore) c.appendChild(this._buildLoadOlderRow());
      const frag = document.createDocumentFragment();
      (d.messages || []).forEach(m => frag.appendChild(this._buildMsg(m.text, m.isUser, m.timestamp)));
      c.appendChild(frag);
      this._scroll();
      requestAnimationFrame(() => this.renderMath());

      this._conversationCount = d.conversation.messageCount || 0;
      this._updateConvLimitBanner();
      this._updateDailyBanner(this.dailyLimit - this.questionsUsed);
    } catch (e) {
      this._rmSkeleton();
    }

    if (!opts.skipMobileClose) this.closeMobileSidebar();
  }

  _buildLoadOlderRow() {
    const row = document.createElement('div');
    row.className = 'ai-load-older';
    row.innerHTML = '<button type="button"><i class="fas fa-arrow-up"></i> Load older messages</button>';
    row.querySelector('button').addEventListener('click', () => this.loadOlderMessages());
    return row;
  }

  async loadOlderMessages() {
    if (!this.activeConversationId) return;
    const c = document.getElementById('chatMessages');
    const prevHeight = c.scrollHeight;
    const r = await fetch(`/api/v01/assistant/conversations/${this.activeConversationId}/messages?limit=30&offset=${this.messagesOffset}`);
    const d = await r.json();
    if (!d.success) return;

    this.messagesHasMore = !!d.hasMore;
    this.messagesOffset += (d.messages || []).length;

    const oldRow = c.querySelector('.ai-load-older'); if (oldRow) oldRow.remove();
    const frag = document.createDocumentFragment();
    if (this.messagesHasMore) frag.appendChild(this._buildLoadOlderRow());
    (d.messages || []).forEach(m => frag.appendChild(this._buildMsg(m.text, m.isUser, m.timestamp)));
    c.insertBefore(frag, c.firstChild);
    requestAnimationFrame(() => this.renderMath(c));

    // Preserve scroll position relative to the content that was already visible
    c.scrollTop = c.scrollHeight - prevHeight;
  }

  newChat() {
    this.activeConversationId = null;
    this.activeTitle = null;
    this.messagesOffset = 0;
    this.messagesHasMore = false;
    document.querySelectorAll('.conv-item').forEach(el => el.classList.remove('active'));
    this._setHeaderTitle('AI Study Assistant');
    this._showEmptyState();
    this._conversationCount = 0;
    this._updateConvLimitBanner();
    this.closeMobileSidebar();
  }

  _setHeaderTitle(title) {
    const el = document.getElementById('aiHeaderTitle');
    if (el) el.textContent = title || 'AI Study Assistant';
  }

  _showEmptyState() {
    const c = document.getElementById('chatMessages');
    this._setHeaderTitle('AI Study Assistant');
    const chips = SUGGESTED_PROMPTS.map(p => `<div class="ai-suggested-prompt" data-prompt="${AiFormatter.escHtml(p)}">${AiFormatter.escHtml(p)}</div>`).join('');
    c.innerHTML = `
      <div class="ai-empty-state">
        <div class="ai-empty-icon"><i class="fas fa-robot"></i></div>
        <div class="ai-empty-title">AI Study Assistant</div>
        <div class="ai-empty-sub">Ask me anything about your studies, exam preparation, or learning material.</div>
        <div class="ai-suggested-prompts">${chips}</div>
      </div>`;
    c.querySelectorAll('.ai-suggested-prompt').forEach(el => {
      el.addEventListener('click', () => this.sendMessage(el.dataset.prompt));
    });
    this._updateConvLimitBanner();
  }

  // ── Messages ─────────────────────────────────────────────────────
  _buildMsg(text, isUser, ts) {
    const init = (window.AI_ASSISTANT_USER_INITIAL || 'U').charAt(0).toUpperCase() || 'U';
    const g = document.createElement('div'); g.className = `message-group${isUser ? ' user-message-group' : ''}`;
    const av = document.createElement('div'); av.className = `message-avatar ${isUser ? 'user-avatar-msg' : 'ai-avatar-msg'}`;
    if (isUser && window.AI_ASSISTANT_AVATAR_URL) {
      const img = document.createElement('img');
      img.src = window.AI_ASSISTANT_AVATAR_URL;
      img.alt = '';
      img.style.cssText = 'width:100%;height:100%;border-radius:inherit;object-fit:cover';
      av.appendChild(img);
    } else {
      av.innerHTML = isUser ? `<span>${init}</span>` : '<i class="fas fa-robot" style="font-size:.8rem"></i>';
    }
    const wr = document.createElement('div'); wr.className = 'message-wrapper';
    const bu = document.createElement('div'); bu.className = `message-bubble ${isUser ? 'user-bubble' : 'ai-bubble'}`;
    const mt = document.createElement('div'); mt.className = 'message-text';
    mt.innerHTML = isUser ? text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br>') : AiFormatter.format(text);
    bu.appendChild(mt);
    const tm = document.createElement('span'); tm.className = 'message-time';
    tm.textContent = ts || new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    wr.appendChild(bu); wr.appendChild(tm); g.appendChild(av); g.appendChild(wr); return g;
  }
  addMsg(text, isUser) { const c = document.getElementById('chatMessages'); const g = this._buildMsg(text, isUser, null); c.appendChild(g); this._scroll(); if (!isUser) requestAnimationFrame(() => this.renderMath(g)); return g; }
  _scroll() { const c = document.getElementById('chatMessages'); requestAnimationFrame(() => { c.scrollTop = c.scrollHeight; }); }
  showTyping() { const c = document.getElementById('chatMessages'); const t = document.createElement('div'); t.id = 'typingIndicator'; t.className = 'message-group'; t.innerHTML = '<div class="message-avatar ai-avatar-msg"><i class="fas fa-robot" style="font-size:.8rem"></i></div><div class="typing-indicator"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div>'; c.appendChild(t); this._scroll(); }
  hideTyping() { document.getElementById('typingIndicator')?.remove(); }
  _busy(b) { this.isSending = b; const btn = document.getElementById('sendBtn'), inp = document.getElementById('chatInput'); if (btn) btn.disabled = b || this._atAnyLimit(); if (inp) { inp.readOnly = b; inp.classList.toggle('is-busy', b); } }

  _conversationAtLimit() {
    return this.activeConversationId && (this._conversationCount || 0) >= (window.AI_MAX_MESSAGES_PER_CONVERSATION || 100);
  }
  _atAnyLimit() { return this.questionsUsed >= this.dailyLimit || this._conversationAtLimit(); }

  _updateConvLimitBanner() {
    const banner = document.getElementById('convLimitBanner');
    if (!banner) return;
    const atLimit = this._conversationAtLimit();
    banner.classList.toggle('show', atLimit);
    const input = document.getElementById('chatInput');
    if (input) input.disabled = atLimit || this.questionsUsed >= this.dailyLimit;
  }

  _renderRetryBubble(text) {
    const g = document.createElement('div'); g.className = 'message-group';
    g.innerHTML = `<div class="message-avatar ai-avatar-msg"><i class="fas fa-robot" style="font-size:.8rem"></i></div>
      <div class="message-wrapper"><div class="message-bubble ai-bubble ai-error-bubble">
        <span>Something went wrong while generating the response.</span>
        <button class="ai-retry-btn" type="button">Retry</button>
      </div></div>`;
    g.querySelector('.ai-retry-btn').addEventListener('click', () => { g.remove(); this.sendMessage(text, { isRetry: true }); });
    const c = document.getElementById('chatMessages'); c.appendChild(g); this._scroll();
  }

  async sendMessage(msg = null, opts = {}) {
    if (this.isSending) return;
    const inp = document.getElementById('chatInput');
    const m = msg || (inp ? inp.value.trim() : '');
    if (!m) return;
    if (this._atAnyLimit()) return;

    if (!opts.isRetry) {
      const c = document.getElementById('chatMessages');
      if (c.querySelector('.ai-empty-state')) c.innerHTML = '';
      this.addMsg(m, true);
      if (inp && !msg) { inp.value = ''; inp.style.height = 'auto'; }
    }
    this.lastFailedMessage = m;
    this._busy(true); this.showTyping();

    try {
      const r = await fetch('/api/v01/assistant/messages', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: m, conversation_id: this.activeConversationId }),
      });
      const d = await r.json();
      this.hideTyping();

      if (d.success) {
        const wasNew = !this.activeConversationId;
        this.activeConversationId = d.conversation_id;
        this.addMsg(d.response, false);
        if (!d.refused) this.questionsUsed++;
        this._conversationCount = (this._conversationCount || 0) + 2;
        this.updateUsageUI();
        this._updateConvLimitBanner();
        this._setHeaderTitle(d.title);
        this.lastFailedMessage = null;

        if (wasNew) {
          this.conversations.unshift({ id: d.conversation_id, title: d.title, messageCount: this._conversationCount, updatedAt: new Date().toISOString(), createdAt: new Date().toISOString() });
          this._renderConvList();
        } else {
          const idx = this.conversations.findIndex(c => c.id === this.activeConversationId);
          if (idx !== -1) {
            const [c] = this.conversations.splice(idx, 1);
            c.messageCount = this._conversationCount; c.updatedAt = new Date().toISOString(); c.title = d.title;
            this.conversations.unshift(c);
            this._renderConvList();
          }
        }
      } else if (d.limit_reached === 'conversation') {
        this._conversationCount = window.AI_MAX_MESSAGES_PER_CONVERSATION || 100;
        this._updateConvLimitBanner();
      } else if (d.limit_reached) {
        this._updateDailyBanner(0);
      } else if (r.status === 429) {
        this._renderRetryBubble(m);
      } else {
        this._renderRetryBubble(m);
      }
    } catch (e) {
      this.hideTyping();
      this._renderRetryBubble(m);
    } finally {
      this._busy(false);
    }
  }

  setupEventListeners() {
    const inp = document.getElementById('chatInput'); if (!inp) return;
    inp.addEventListener('input', function () { this.style.height = 'auto'; this.style.height = Math.min(this.scrollHeight, 160) + 'px'; });
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.sendMessage(); } });

    document.getElementById('newChatBtn')?.addEventListener('click', () => this.newChat());
    document.getElementById('loadMoreBtn')?.addEventListener('click', () => this.loadMoreConversations());

    const search = document.getElementById('chatSearchInput');
    search?.addEventListener('input', () => {
      clearTimeout(this._searchDebounce);
      this._searchDebounce = setTimeout(() => this.searchConversations(search.value), 300);
    });

    document.getElementById('sbCollapseBtn')?.addEventListener('click', () => this.toggleSidebarCollapse());
    document.getElementById('aiMobileMenuBtn')?.addEventListener('click', () => this.openMobileSidebar());
    document.getElementById('aiSbOverlay')?.addEventListener('click', () => this.closeMobileSidebar());

    document.addEventListener('click', (e) => {
      if (!e.target.closest('.dd-wrap')) document.querySelectorAll('.dropdown-menu.show').forEach(m => m.remove());
    });
  }

  toggleSidebarCollapse() {
    const sb = document.getElementById('aiSidebar');
    if (!sb) return;
    const collapsed = sb.classList.toggle('collapsed');
    localStorage.setItem('aiSidebarCollapsed', collapsed ? 'true' : 'false');
  }
  openMobileSidebar() {
    document.getElementById('aiSidebar')?.classList.add('mobile-open');
    document.getElementById('aiSbOverlay')?.classList.add('show');
  }
  closeMobileSidebar() {
    document.getElementById('aiSidebar')?.classList.remove('mobile-open');
    document.getElementById('aiSbOverlay')?.classList.remove('show');
  }

  renderMath(root) {
    const c = root || document.getElementById('chatMessages');
    if (!c || !window.renderMathInElement) return;
    try {
      renderMathInElement(c, {
        delimiters: [{ left: '$$', right: '$$', display: true }, { left: '$', right: '$', display: false }, { left: '\\[', right: '\\]', display: true }, { left: '\\(', right: '\\)', display: false }],
        throwOnError: false, ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre'],
      });
    } catch (e) {}
  }

  // ── Rename ──────────────────────────────────────────────────────
  openRenameModal(id, currentTitle) {
    this.pendingRenameId = id;
    const input = document.getElementById('renameInput');
    if (input) input.value = currentTitle || '';
    document.getElementById('renameModal')?.classList.add('show');
    setTimeout(() => input?.focus(), 50);
  }
  closeRenameModal() { document.getElementById('renameModal')?.classList.remove('show'); this.pendingRenameId = null; }
  async submitRename() {
    if (!this.pendingRenameId) return;
    const input = document.getElementById('renameInput');
    const title = (input?.value || '').trim();
    if (!title) return;
    try {
      const r = await fetch(`/api/v01/assistant/conversations/${this.pendingRenameId}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }),
      });
      const d = await r.json();
      if (d.success) {
        const c = this.conversations.find(x => x.id === this.pendingRenameId);
        if (c) c.title = d.conversation.title;
        if (this.activeConversationId === this.pendingRenameId) this._setHeaderTitle(d.conversation.title);
        this._renderConvList();
      }
    } catch (e) {}
    this.closeRenameModal();
  }

  // ── Delete ──────────────────────────────────────────────────────
  openDeleteModal(id) {
    this.pendingDeleteId = id;
    document.getElementById('confirmModal')?.classList.add('show');
  }
  closeConfirmModal() { document.getElementById('confirmModal')?.classList.remove('show'); this.pendingDeleteId = null; }
  async confirmDelete() {
    if (!this.pendingDeleteId) return;
    const id = this.pendingDeleteId;
    try {
      const r = await fetch(`/api/v01/assistant/conversations/${id}`, { method: 'DELETE' });
      const d = await r.json();
      if (d.success) {
        this.conversations = this.conversations.filter(c => c.id !== id);
        this._renderConvList();
        if (this.activeConversationId === id) this.newChat();
      }
    } catch (e) {}
    this.closeConfirmModal();
  }
}

let assistant = new AIAssistant();
window.assistant = assistant;
function sendMessage() { if (assistant) assistant.sendMessage(); }
function confirmClearChat() { /* superseded by per-conversation delete */ }
function closeConfirmModal() { if (assistant) assistant.closeConfirmModal(); }
function confirmDeleteConversation() { if (assistant) assistant.confirmDelete(); }
function closeRenameModal() { if (assistant) assistant.closeRenameModal(); }
function submitRename() { if (assistant) assistant.submitRename(); }
window.sendMessage = sendMessage;
window.closeConfirmModal = closeConfirmModal;
window.confirmDeleteConversation = confirmDeleteConversation;
window.closeRenameModal = closeRenameModal;
window.submitRename = submitRename;
