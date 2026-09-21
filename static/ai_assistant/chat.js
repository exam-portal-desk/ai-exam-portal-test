/* static/ai_assistant/chat.js — multi-conversation AI Study Assistant */

import { StreamView } from './stream-render.js';   // how a reply looks while it is still arriving (uses the formatter below)

const AiFormatter = {
  SECTIONS: [
    { re: /^\[FINAL ANSWER\]/i, cls: 'label-answer', title: 'Final Answer' },
    { re: /^\[GIVEN\]/i, cls: 'label-given', title: 'Given' },
    { re: /^\[SOLUTION\]/i, cls: 'label-solution', title: 'Solution' },
    { re: /^\[EXPLANATION\]/i, cls: 'label-explain', title: 'Explanation' },
  ],
  escHtml(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); },
  // While a reply is still arriving, a reasoning block may be open (not yet closed): never show it.
  // True once there is real content to draw: a text that is only section headers so far ("[FINAL ANSWER]" waiting for its
  // body) would be drawn by format() as a stray line of plain text, so streaming waits for the first line of body.
  hasBody(text) { return String(text).split('\n').some(l => l.trim() && !this.SECTIONS.some(d => d.re.test(l.trim()))); },
  stripThink(t) { return String(t).replace(/<think>[\s\S]*?(?:<\/think>|$)/gi, '').replace(/^\s+/, ''); },
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

    this.dailyLimit = null;                // from /init: the plan's daily limit; null = no limit
    this.maxMessages = null;               // from /init: messages allowed per conversation; null = no limit
    this.questionsUsed = 0;
    this.isSending = false;
    this.lastFailedMessage = null;
    this.pendingDeleteId = null;
    this.pendingRenameId = null;

    this._searchDebounce = null;
    this._stick = true;          // follow new messages only while the student is at (or near) the bottom

    // "Discuss with AI": the question this chat is about — references only ({question_id, result_id}); the card is
    // just labels for the context bar. `focusStored` = the conversation already keeps it (else it goes with the first message).
    this.focus = null;
    this.focusCard = null;
    this.focusStored = false;

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
      this.maxMessages = d.maxMessages;
      this.questionsUsed = d.questionsUsed;
      this.conversations = d.conversations || [];
      this.convOffset = this.conversations.length;
      this.convHasMore = !!d.hasMoreConversations;
      this.updateUsageUI();
      this._renderConvList();

      // Opened with "Discuss with AI" on a question: /ai-assistant?q=<question>&r=<result>
      const params = new URLSearchParams(location.search);
      if (params.get('q')) {
        const qid = Number(params.get('q')), rid = params.get('r') ? Number(params.get('r')) : null;
        history.replaceState(null, '', location.pathname);           // a reload must not re-open it
        await this.openFocus(qid, rid);
      } else if (this.conversations.length) {
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

  _dailyRemaining() { return this.dailyLimit == null ? null : this.dailyLimit - this.questionsUsed; }
  _dailyAtLimit() { return this.dailyLimit != null && this.questionsUsed >= this.dailyLimit; }

  updateUsageUI() {
    const rem = this._dailyRemaining();
    const ids = ['dailyLimit', 'usedToday', 'remaining']; const vals = [rem === null ? 'No limit' : this.dailyLimit, this.questionsUsed, rem === null ? '—' : rem];
    ids.forEach((id, i) => { const el = document.getElementById(id); if (el) el.textContent = vals[i]; });
    const h = document.getElementById('hintText'); if (h) h.textContent = rem === null ? '' : `${rem} question${rem !== 1 ? 's' : ''} remaining today`;
    this._updateDailyBanner(rem);
  }

  _updateDailyBanner(remaining) {
    const banner = document.getElementById('dailyLimitBanner');
    if (!banner) return;
    const atLimit = remaining !== null && remaining <= 0;
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
      this._applyFocusPayload(d.focus);                      // the question bar for a focused chat; none for a general chat
      this.messagesHasMore = !!d.hasMore;
      this.messagesOffset = (d.messages || []).length;

      c.innerHTML = '';
      if (this.messagesHasMore) c.appendChild(this._buildLoadOlderRow());
      const frag = document.createDocumentFragment();
      (d.messages || []).forEach(m => frag.appendChild(this._buildMsg(m.text, m.isUser, m.timestamp)));
      c.appendChild(frag);
      this._scroll(true);
      requestAnimationFrame(() => { this.renderMath(); this._scroll(true); });

      this._conversationCount = d.conversation.messageCount || 0;
      this._updateConvLimitBanner();
      this._updateDailyBanner(this._dailyRemaining());
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
    this._clearFocusUI();                                     // a new chat is a general chat unless "Discuss with AI" starts it
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

  // ── "Discuss with AI": the question context bar and starter actions ─────────────────────────────────────
  // Everything here is display. Whether the student may discuss the question is decided by the server on every message.
  async openFocus(questionId, resultId) {
    try {
      const r = await fetch(`/api/v01/assistant/focus?question_id=${encodeURIComponent(questionId)}` + (resultId ? `&result_id=${encodeURIComponent(resultId)}` : ''));
      const d = await r.json();
      if (d.success && d.allowed) {
        if (d.conversation_id) { await this.selectConversation(d.conversation_id, { skipMobileClose: true }); return; }   // carries its own bar
        this.newChat();
        this._setFocus({ question_id: d.card.question_id, result_id: d.card.result_id }, d, false);
        this._showEmptyState();
        return;
      }
      this.newChat();
      this._showLockedContext(d.message || "This question isn't available for discussion.");
    } catch (e) {
      this.newChat();
      this._showLockedContext("Couldn't open the question. Please try again.");
    }
  }

  _applyFocusPayload(p) {                                       // from the messages endpoint of a focused conversation
    if (p && p.allowed) this._setFocus({ question_id: p.card.question_id, result_id: p.card.result_id }, p, true);
    else if (p) { this.focus = null; this.focusStored = true; this._showLockedContext(p.message); }
    else this._clearFocusUI();
  }

  _setFocus(focus, payload, stored) {
    this.focus = focus; this.focusCard = payload.card; this.focusStored = !!stored;
    const bar = document.getElementById('aiContext'); if (!bar) return;
    bar.classList.remove('locked');
    const c = payload.card;
    document.getElementById('aiContextTitle').textContent = c.exam ? `${c.label} · ${c.exam}` : c.label;
    const meta = document.getElementById('aiContextMeta'); meta.innerHTML = '';
    [c.topic, c.type].filter(Boolean).forEach(t => { const s = document.createElement('span'); s.textContent = t; meta.appendChild(s); });
    if (c.status) {
      const s = document.createElement('span'); s.className = 'ai-ctx-status'; s.dataset.status = c.status;
      const label = { correct: 'Correct', incorrect: 'Incorrect', skipped: 'Skipped' }[c.status] || c.status;
      const marks = (c.status !== 'skipped' && c.marks !== null && c.marks !== undefined) ? ` (${Number(c.marks) > 0 ? '+' : ''}${c.marks})` : '';
      s.textContent = label + marks; meta.appendChild(s);
    }
    const view = document.getElementById('aiContextView'); view.href = c.view_url; view.hidden = false;
    document.getElementById('aiContextMeta').hidden = false;
    bar.hidden = false;
    this._renderChips(payload.chips || []);
  }

  _showLockedContext(message) {                                  // the question cannot be discussed now: say why, keep the chat usable
    this.focusCard = null;
    const bar = document.getElementById('aiContext'); if (!bar) return;
    bar.classList.add('locked');
    document.getElementById('aiContextTitle').textContent = message;
    document.getElementById('aiContextMeta').innerHTML = '';
    document.getElementById('aiContextView').hidden = true;
    bar.hidden = false;
    this._renderChips([]);
  }

  _clearFocusUI() {
    this.focus = null; this.focusCard = null; this.focusStored = false;
    const bar = document.getElementById('aiContext'); if (bar) { bar.hidden = true; bar.classList.remove('locked'); }
    this._renderChips([]);
  }

  _renderChips(chips) {
    const box = document.getElementById('aiChips'); if (!box) return;
    box.innerHTML = '';
    chips.forEach(ch => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'ai-chip'; b.textContent = ch.label;
      b.addEventListener('click', () => this.sendMessage(ch.message));   // a chip is just an ordinary message
      box.appendChild(b);
    });
    box.hidden = !chips.length;
  }

  // The × on the bar. While a question is attached it asks first (removing it changes how every later message is
  // answered); a "not available" notice has no context to lose, so that one is simply dismissed.
  closeContext() {
    if (this.focusCard) this._openContextModal();
    else this._dismissLockedContext();
  }

  _openContextModal() {
    const m = document.getElementById('contextModal'); if (!m) return;
    this._contextModalReturnTo = document.activeElement;
    m.classList.add('show');
    setTimeout(() => document.getElementById('contextModalKeep')?.focus(), 30);      // the safe choice is the default
  }

  _closeContextModal() {
    document.getElementById('contextModal')?.classList.remove('show');
    const back = this._contextModalReturnTo; this._contextModalReturnTo = null;
    if (back && document.contains(back)) back.focus();
  }

  async confirmRemoveContext() {
    this._closeContextModal();
    if (!this.focusCard) return;                                  // it went away while the dialog was open
    if (this.activeConversationId && this.focusStored) {          // stop the server using it for the next message; the messages stay
      let ok = false;
      try {
        const r = await fetch(`/api/v01/assistant/conversations/${this.activeConversationId}/focus`, { method: 'DELETE' });
        ok = r.ok && (await r.json()).success === true;
      } catch (e) { /* reported below */ }
      if (!ok) { this._renderNotice("Couldn't remove the question context. Please try again."); return; }   // still attached: say so
    }
    const wasEmpty = !this.activeConversationId;
    this._clearFocusUI();
    if (wasEmpty) this._showEmptyState();
  }

  async _dismissLockedContext() {
    if (this.activeConversationId && this.focusStored) {
      try { await fetch(`/api/v01/assistant/conversations/${this.activeConversationId}/focus`, { method: 'DELETE' }); } catch (e) { /* the notice is closed either way */ }
    }
    const wasEmpty = !this.activeConversationId;
    this._clearFocusUI();
    if (wasEmpty) this._showEmptyState();
  }

  _showEmptyState() {
    const c = document.getElementById('chatMessages');
    this._setHeaderTitle('AI Study Assistant');
    if (this.focus && this.focusCard) {                           // about to discuss a question: no generic suggestions
      c.innerHTML = `
        <div class="ai-empty-state">
          <div class="ai-empty-icon"><i class="fas fa-comments"></i></div>
          <div class="ai-empty-title">Discuss this question</div>
          <div class="ai-empty-sub">Ask a follow-up in your own words, or pick one of the actions below.</div>
        </div>`;
      this._updateConvLimitBanner();
      return;
    }
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
  addMsg(text, isUser) {
    const c = document.getElementById('chatMessages'); const g = this._buildMsg(text, isUser, null); c.appendChild(g);
    this._scroll(isUser);                       // the student's own message always scrolls into view
    if (!isUser) requestAnimationFrame(() => { this.renderMath(g); this._scroll(); });
    return g;
  }

  // ── Scrolling: only the message list scrolls, and it follows new content only while the student is at the bottom
  _isNearBottom(px = 96) { const c = document.getElementById('chatMessages'); return !c || c.scrollHeight - c.scrollTop - c.clientHeight <= px; }
  _showJump(show) { const b = document.getElementById('jumpLatestBtn'); if (b) b.hidden = !show; }
  _scroll(force = false) {
    const c = document.getElementById('chatMessages'); if (!c) return;
    if (!force && !this._stick) { this._showJump(true); return; }      // they scrolled up to read: do not yank them down
    requestAnimationFrame(() => { this._autoScrollAt = performance.now(); c.scrollTop = c.scrollHeight; this._stick = true; this._showJump(false); });
  }

  // While a reply streams: glide toward the bottom (a fraction of the remaining distance per frame) instead of jumping
  // on every update. Does nothing if the student has scrolled up; shows the "New messages" pill instead.
  _followStream() {
    if (!this._stick) { this._showJump(true); return; }
    if (this._followRaf) return;
    const c = document.getElementById('chatMessages'); if (!c) return;
    const ease = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 1 : 0.28;
    const step = () => {
      const gap = c.scrollHeight - c.clientHeight - c.scrollTop;
      if (!this._stick || gap < 1) { this._followRaf = 0; return; }
      this._autoScrollAt = performance.now();
      c.scrollTop += Math.max(1, gap * ease);
      this._followRaf = requestAnimationFrame(step);
    };
    this._followRaf = requestAnimationFrame(step);
  }

  // The bubble a streamed reply is written into while it arrives, with a small "generating" indicator (the same
  // dots as the typing indicator). It says a reply is being produced — it never claims to show reasoning.
  _startLiveBubble() {
    const g = this._buildMsg('', false, null); g.classList.add('is-live');
    const mt = g.querySelector('.message-text');
    const ind = document.createElement('div');
    ind.className = 'stream-indicator'; ind.setAttribute('role', 'status'); ind.setAttribute('aria-label', 'Generating response');
    ind.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>';
    g.querySelector('.message-bubble').appendChild(ind);
    document.getElementById('chatMessages').appendChild(g);
    return { g, mt, ind };
  }
  _endLive(live) {
    if (!live) return;
    live.ind?.remove();
    live.g.classList.remove('is-live');
  }

  // The stream stopped early (provider error, timeout, dropped connection). Keep whatever complete text already
  // arrived (an unfinished formula is left out, never shown as raw source), say clearly that it is incomplete and
  // was not saved, and offer Retry. With nothing to keep, show the plain error bubble instead of an empty reply.
  _failLive(view, live, m, message) {
    view.flush();
    this.hideTyping();
    if (live && view.hasContent) {
      this._endLive(live);
      live.g.classList.add('is-partial');
      const s = document.createElement('div');
      s.className = 'ai-stream-status'; s.setAttribute('role', 'alert');
      s.innerHTML = '<i class="fas fa-circle-exclamation"></i><span class="ai-stream-status-text"></span><button class="ai-retry-btn" type="button">Retry</button>';
      s.querySelector('.ai-stream-status-text').textContent = (message || 'The response was interrupted.') + ' The reply above is incomplete and was not saved.';
      s.querySelector('.ai-retry-btn').addEventListener('click', () => { live.g.remove(); this.sendMessage(m, { isRetry: true }); });
      live.g.querySelector('.message-bubble').appendChild(s);
      this._followStream();
    } else {
      live?.g.remove();
      this._renderRetryBubble(m, message);
    }
  }
  showTyping() { const c = document.getElementById('chatMessages'); const t = document.createElement('div'); t.id = 'typingIndicator'; t.className = 'message-group'; t.innerHTML = '<div class="message-avatar ai-avatar-msg"><i class="fas fa-robot" style="font-size:.8rem"></i></div><div class="typing-indicator"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div>'; c.appendChild(t); this._scroll(); }
  hideTyping() { document.getElementById('typingIndicator')?.remove(); }
  _busy(b) { this.isSending = b; const btn = document.getElementById('sendBtn'), inp = document.getElementById('chatInput'); if (btn) btn.disabled = b || this._atAnyLimit(); if (inp) { inp.readOnly = b; inp.classList.toggle('is-busy', b); } }

  _conversationAtLimit() {
    return this.maxMessages != null && this.activeConversationId && (this._conversationCount || 0) >= this.maxMessages;
  }
  _atAnyLimit() { return this._dailyAtLimit() || this._conversationAtLimit(); }

  _updateConvLimitBanner() {
    const banner = document.getElementById('convLimitBanner');
    if (!banner) return;
    const atLimit = this._conversationAtLimit();
    banner.classList.toggle('show', atLimit);
    const input = document.getElementById('chatInput');
    if (input) input.disabled = atLimit || this._dailyAtLimit();
  }

  // `reason` is the server's own, safe-to-show explanation (rate limit, provider timeout, ...). textContent only.
  _renderRetryBubble(text, reason) {
    const g = document.createElement('div'); g.className = 'message-group';
    g.innerHTML = `<div class="message-avatar ai-avatar-msg"><i class="fas fa-robot" style="font-size:.8rem"></i></div>
      <div class="message-wrapper"><div class="message-bubble ai-bubble ai-error-bubble" role="alert">
        <span class="ai-error-text"></span>
        <button class="ai-retry-btn" type="button">Retry</button>
      </div></div>`;
    g.querySelector('.ai-error-text').textContent = reason || 'Something went wrong while generating the response.';
    g.querySelector('.ai-retry-btn').addEventListener('click', () => { g.remove(); this.sendMessage(text, { isRetry: true }); });
    const c = document.getElementById('chatMessages'); c.appendChild(g); this._scroll();
  }

  // The exchange was answered AND saved: update counters, header and the chat list.
  _onExchangeSaved(d) {
    const wasNew = !this.activeConversationId;
    if (wasNew && this.focus) this.focusStored = true;         // the first message stored the focus with the new conversation
    this.activeConversationId = d.conversation_id;
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
  }

  // A complete JSON answer (refusals, validation problems, limits, failures, and providers that cannot stream).
  _handleJsonReply(r, d, m) {
    if (d.success) {
      this.addMsg(d.response, false);
      this._onExchangeSaved(d);
    } else if (d.limit_reached === 'conversation') {
      this.maxMessages = this._conversationCount || 0;
      this._updateConvLimitBanner();
    } else if (d.limit_reached) {
      this._updateDailyBanner(0);
    } else if (d.focus_locked) {
      // The question cannot be discussed right now (result not released, an attempt in progress, ...). Nothing was sent
      // to the AI and nothing was saved: explain it plainly and offer no pointless Retry.
      this._showLockedContext(d.message);
      this._renderNotice(d.message);
    } else if (d.retryable === false) {
      // A permanent reason (e.g. the Assistant's model cannot view this question's diagram): nothing was sent or saved
      // and trying again would not change it, so no Retry.
      this._renderNotice(d.message);
    } else {
      this._renderRetryBubble(m, d.message);
    }
  }

  // A plain, themed message from the Assistant's side of the chat (no Retry).
  _renderNotice(text) {
    const g = document.createElement('div'); g.className = 'message-group';
    g.innerHTML = `<div class="message-avatar ai-avatar-msg"><i class="fas fa-robot" style="font-size:.8rem"></i></div>
      <div class="message-wrapper"><div class="message-bubble ai-bubble ai-error-bubble" role="status"><span class="ai-error-text"></span></div></div>`;
    g.querySelector('.ai-error-text').textContent = text;
    document.getElementById('chatMessages').appendChild(g); this._scroll();
  }

  // A streamed answer. Chunks go into a buffer; StreamView draws only the part that is safe (no half-written formula,
  // marker or word), through the same AiFormatter + KaTeX the finished message uses, updating only what changed.
  // The typing indicator shows until the first safe text; then the reply bubble takes over with a small generating
  // indicator that goes away when the reply is complete (or fails).
  async _consumeStream(r, m) {
    const reader = r.body.getReader(); const dec = new TextDecoder('utf-8');
    let buf = '', live = null, verdict = false;
    const view = new StreamView({
      mount: () => { this.hideTyping(); live = this._startLiveBubble(); return live.mt; },
      format: (t) => AiFormatter.format(t),
      renderMath: (el) => this.renderMath(el),                       // only the blocks that changed, never the whole chat
      clean: (t) => AiFormatter.stripThink(t),
      hasBody: (t) => AiFormatter.hasBody(t),
      onPaint: () => this._followStream(),
    });

    const onEvent = (ev) => {
      if (ev.type === 'delta') {
        view.push(ev.text);
      } else if (ev.type === 'done') {
        verdict = true; this.hideTyping();
        view.finish(ev.response);                                    // the exact final text, through the normal renderer
        this._endLive(live);
        this._onExchangeSaved(ev);
        this._followStream();                                        // glide to the end if the student is at the bottom
      } else if (ev.type === 'error') {
        verdict = true;
        this._failLive(view, live, m, ev.message);
      }
      // any other event type (for example a separate reasoning stream from some other model) is ignored here:
      // this screen only ever shows the answer itself.
    };

    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n\n')) !== -1) {
          const block = buf.slice(0, i); buf = buf.slice(i + 2);
          for (const line of block.split('\n')) {
            if (!line.startsWith('data:')) continue;
            try { onEvent(JSON.parse(line.slice(5))); } catch (e) { /* a malformed event is skipped */ }
          }
        }
      }
    } catch (e) {                                                    // the connection broke while reading
      if (!verdict) { verdict = true; this._failLive(view, live, m, 'The connection was interrupted.'); }
    }
    if (!verdict) {                                                  // it ended without saying how it went
      this._failLive(view, live, m, 'The response was interrupted before it finished.');
    }
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
        // `focus` is only sent with the FIRST message of a chat about a question; after that the conversation keeps it
        // and the server re-checks access on every message.
        body: JSON.stringify({ message: m, conversation_id: this.activeConversationId, stream: true,
                               focus: (!this.activeConversationId && this.focus) ? this.focus : undefined }),
      });
      if ((r.headers.get('content-type') || '').includes('text/event-stream')) {
        await this._consumeStream(r, m);
      } else {
        const d = await r.json();
        this.hideTyping();
        this._handleJsonReply(r, d, m);
      }
    } catch (e) {
      this.hideTyping();
      document.querySelector('#chatMessages .message-group.is-live')?.remove();
      this._renderRetryBubble(m, "Couldn't reach the server. Check your connection and try again.");
    } finally {
      this._busy(false);
    }
  }

  setupEventListeners() {
    const inp = document.getElementById('chatInput'); if (!inp) return;
    inp.addEventListener('input', function () { this.style.height = 'auto'; this.style.height = Math.min(this.scrollHeight, 160) + 'px'; });
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.sendMessage(); } });

    const box = document.getElementById('chatMessages');
    box?.addEventListener('scroll', () => {
      if (performance.now() - (this._autoScrollAt || 0) < 150) return;   // that was our own follow-scroll, not the student
      this._stick = this._isNearBottom(); if (this._stick) this._showJump(false);
    }, { passive: true });
    // Reaching for the wheel / a finger / the keys to go UP always wins over following a reply that is still arriving.
    box?.addEventListener('wheel', (e) => { if (e.deltaY < 0) this._stick = false; }, { passive: true });
    let touchY = null;
    box?.addEventListener('touchstart', (e) => { touchY = e.touches[0].clientY; }, { passive: true });
    box?.addEventListener('touchmove', (e) => { const y = e.touches[0].clientY; if (touchY !== null && y > touchY + 4) this._stick = false; touchY = y; }, { passive: true });
    box?.addEventListener('keydown', (e) => { if (e.key === 'PageUp' || e.key === 'ArrowUp' || e.key === 'Home') this._stick = false; });
    document.getElementById('jumpLatestBtn')?.addEventListener('click', () => this._scroll(true));

    document.getElementById('aiContextClose')?.addEventListener('click', () => this.closeContext());
    const ctxModal = document.getElementById('contextModal');
    document.getElementById('contextModalKeep')?.addEventListener('click', () => this._closeContextModal());
    document.getElementById('contextModalRemove')?.addEventListener('click', () => this.confirmRemoveContext());
    ctxModal?.addEventListener('click', (e) => { if (e.target === ctxModal) this._closeContextModal(); });   // the backdrop = Keep
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && ctxModal?.classList.contains('show')) this._closeContextModal(); });
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
    document.body.style.overflow = 'hidden';
  }
  closeMobileSidebar() {
    document.getElementById('aiSidebar')?.classList.remove('mobile-open');
    document.getElementById('aiSbOverlay')?.classList.remove('show');
    document.body.style.overflow = '';
  }

  // Formula size is the page's job, not the model's (same rule as the AI Explanation): the model's own font-size commands
  // (\Large on one formula, \small on the next) are dropped so every formula gets the one size set in chat.css, and
  // \frac is drawn as \dfrac so an inline fraction is as large as the text around it instead of shrinking.
  static MATH_MACROS = (() => {
    const m = { '\\frac': '\\dfrac{#1}{#2}' };
    ['tiny', 'scriptsize', 'footnotesize', 'small', 'normalsize', 'large', 'Large', 'LARGE', 'huge', 'Huge'].forEach((n) => { m['\\' + n] = ''; });
    return m;
  })();

  renderMath(root) {
    const c = root || document.getElementById('chatMessages');
    if (!c || !window.renderMathInElement) return;
    try {
      renderMathInElement(c, {
        macros: { ...AIAssistant.MATH_MACROS }, strict: 'ignore',
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
