(() => {
  const $ = (id) => document.getElementById(id);
  const feed = $("chatFeed");
  const form = $("chatForm");
  const input = $("chatInput");
  const overlay = $("miniBrowser");
  const frame = $("mbFrame");
  const urlEl = $("mbUrl");
  if (!feed || !form || !overlay || !frame) return;

  const Edit = window.TalkingChatEdit || null;
  const lang = Edit ? Edit.detectLang(document, navigator) : "ru";
  const tt = (key) => (Edit ? Edit.t(lang, key) : key);
  const currentUser = "local";
  const editState = Edit ? Edit.createState() : { editingId: null, originalText: "", draft: "" };
  const byId = new Map();
  let editWindowS = 48 * 3600;
  let pressTimer = null;
  let pressStart = null;
  const history = [];
  let dragY = null;

  function toast(text) {
    const el = $("toast");
    if (!el) return;
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.hidden = true; }, 2800);
  }

  function isSafeHttpUrl(u) {
    if (!u || typeof u !== "string") return false;
    try {
      const parsed = new URL(u, location.origin);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
      const href = parsed.href.toLowerCase();
      if (href.startsWith("javascript:") || href.startsWith("data:") || href.startsWith("blob:") || href.startsWith("file:") || href.startsWith("vbscript:")) {
        return false;
      }
      return true;
    } catch (_) {
      return false;
    }
  }

  function resolveBrowseUrl(preview, fallbackTextUrl) {
    const raw = (preview && (preview.browse_url || preview.url)) || fallbackTextUrl || "";
    try {
      return new URL(raw, location.origin).href;
    } catch (_) {
      return "";
    }
  }

  function highlightLinks(text) {
    const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const re = /\bhttps?:\/\/[^\s<>\[\]()"']+/gi;
    let out = "";
    let last = 0;
    const src = text || "";
    src.replace(re, (m, idx) => {
      out += esc(src.slice(last, idx));
      const clean = m.replace(/[.,;:!?)]+$/, "");
      out += '<a href="' + esc(clean) + '" data-mini="1">' + esc(clean) + "</a>";
      last = idx + m.length;
      return m;
    });
    out += esc(src.slice(last));
    return out;
  }

  function cardEl(preview) {
    if (!preview) return null;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "link-card" + (preview.image_path ? "" : " no-img");
    btn.setAttribute("data-browse", resolveBrowseUrl(preview, preview.url));
    const img = preview.image_path
      ? '<img class="thumb" alt="" src="' + preview.image_path + '">'
      : "";
    btn.innerHTML = img +
      '<div class="meta">' +
      '<div class="site"></div><div class="title"></div><div class="desc"></div>' +
      "</div>";
    btn.querySelector(".site").textContent = preview.site_name || preview.display_url || "";
    btn.querySelector(".title").textContent = preview.title || preview.site_name || "";
    btn.querySelector(".desc").textContent = preview.description || "";
    btn.addEventListener("click", () => openMini(btn.getAttribute("data-browse")));
    return btn;
  }

  function closeMenu() {
    const menu = $("chatMenu");
    if (!menu) return;
    menu.hidden = true;
    menu.innerHTML = "";
  }

  function openMenu(x, y, msg) {
    const menu = $("chatMenu");
    if (!menu || !Edit || !Edit.canOfferEdit(msg, currentUser, Date.now() / 1000, editWindowS)) return;
    menu.innerHTML = "";
    menu.setAttribute("aria-label", tt("menu"));
    const item = document.createElement("button");
    item.type = "button";
    item.setAttribute("role", "menuitem");
    item.dataset.action = "edit";
    item.textContent = tt("edit");
    item.addEventListener("click", () => {
      closeMenu();
      beginEdit(msg);
    });
    menu.appendChild(item);
    menu.hidden = false;
    const pad = 8;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    requestAnimationFrame(() => {
      const rect = menu.getBoundingClientRect();
      let left = x;
      let top = y;
      if (left + rect.width > vw - pad) left = Math.max(pad, vw - rect.width - pad);
      if (top + rect.height > vh - pad) top = Math.max(pad, y - rect.height);
      menu.style.left = left + "px";
      menu.style.top = top + "px";
      item.focus();
    });
  }

  function bindGestures(art, msg) {
    if (!Edit || !Edit.isOwn(msg, currentUser)) return;
    art.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      openMenu(e.clientX, e.clientY, byId.get(msg.id) || msg);
    });
    art.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      if (e.target.closest("a, button, .link-card")) return;
      pressStart = { x: e.clientX, y: e.clientY };
      clearTimeout(pressTimer);
      pressTimer = setTimeout(() => {
        pressTimer = null;
        openMenu(e.clientX, e.clientY, byId.get(msg.id) || msg);
      }, 450);
    });
    const cancelPress = (e) => {
      if (pressStart && e && (Math.abs(e.clientX - pressStart.x) > 12 || Math.abs(e.clientY - pressStart.y) > 12)) {
        clearTimeout(pressTimer);
        pressTimer = null;
      }
    };
    art.addEventListener("pointermove", cancelPress);
    art.addEventListener("pointerup", () => { clearTimeout(pressTimer); pressTimer = null; pressStart = null; });
    art.addEventListener("pointercancel", () => { clearTimeout(pressTimer); pressTimer = null; pressStart = null; });
  }

  function markBeingEdited() {
    feed.querySelectorAll(".chat-msg.is-being-edited").forEach((el) => el.classList.remove("is-being-edited"));
    if (editState.editingId) {
      const art = feed.querySelector('.chat-msg[data-id="' + CSS.escape(editState.editingId) + '"]');
      if (art) art.classList.add("is-being-edited");
    }
  }

  function syncBanner() {
    const banner = $("editBanner");
    const title = $("editBannerTitle");
    const preview = $("editBannerPreview");
    const send = $("chatSend") || form.querySelector("button[type=submit]");
    if (!banner) return;
    const editing = Edit && Edit.isEditing(editState);
    banner.hidden = !editing;
    form.classList.toggle("is-editing", !!editing);
    if (title) title.textContent = tt("editing");
    if (preview) preview.textContent = editState.originalText || "";
    if (send) send.textContent = editing ? tt("save") : tt("send");
    input.setAttribute("placeholder", editing ? tt("placeholderEdit") : tt("placeholder"));
    input.setAttribute("aria-label", editing ? tt("editing") : (lang === "en" ? "Message text" : "Текст сообщения"));
    const cancelBtn = $("editCancel");
    if (cancelBtn) cancelBtn.setAttribute("aria-label", tt("cancelEdit"));
    markBeingEdited();
  }

  function beginEdit(msg) {
    if (!Edit || !msg) return;
    if (!Edit.canOfferEdit(msg, currentUser, Date.now() / 1000, editWindowS)) {
      toast(tt("windowExpired"));
      return;
    }
    Edit.startEdit(editState, msg);
    input.value = msg.text || "";
    syncBanner();
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }

  function cancelEdit() {
    if (!Edit) return;
    Edit.cancel(editState);
    input.value = "";
    syncBanner();
  }

  function lastOwnEditable() {
    const now = Date.now() / 1000;
    for (const art of feed.querySelectorAll(".chat-msg")) {
      const msg = byId.get(art.dataset.id);
      if (msg && Edit && Edit.canOfferEdit(msg, currentUser, now, editWindowS)) return msg;
    }
    return null;
  }

  function renderMessage(msg, opts) {
    const update = !!(opts && opts.update);
    let art = update && msg.id ? feed.querySelector('.chat-msg[data-id="' + CSS.escape(msg.id) + '"]') : null;
    const isNew = !art;
    if (!art) {
      art = document.createElement("article");
      art.className = "chat-msg";
    } else {
      art.replaceChildren();
    }
    art.dataset.id = msg.id || "";
    art.dataset.kind = msg.kind || msg.type || "text";
    art.dataset.sender = msg.sender_id || "";
    const own = Edit ? Edit.isOwn(msg, currentUser) : (msg.sender_id || "local") === currentUser;
    art.dataset.own = own ? "1" : "0";
    byId.set(msg.id, msg);

    const body = document.createElement("div");
    body.className = "chat-text";
    body.innerHTML = highlightLinks(msg.text || "");
    body.querySelectorAll("a[data-mini]").forEach((a) => {
      a.addEventListener("click", (e) => {
        e.preventDefault();
        openMini(a.getAttribute("href"));
      });
    });
    art.appendChild(body);
    const card = cardEl(msg.preview);
    if (card) art.appendChild(card);

    const meta = document.createElement("div");
    meta.className = "chat-meta";
    if (own && Edit && Edit.isEditableKind(msg)) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "chat-msg-edit";
      more.setAttribute("aria-label", tt("edit"));
      more.textContent = "⋮";
      more.addEventListener("click", (e) => {
        e.stopPropagation();
        const r = more.getBoundingClientRect();
        openMenu(r.left, r.bottom + 4, byId.get(msg.id) || msg);
      });
      meta.appendChild(more);
    }
    const stamp = document.createElement("span");
    stamp.className = "chat-stamp";
    const time = document.createElement("time");
    const created = msg.created_at || 0;
    try { time.dateTime = new Date(created * 1000).toISOString(); } catch (_) { /* ignore */ }
    time.textContent = Edit ? Edit.formatTime(created, lang) : "";
    stamp.appendChild(time);
    if (msg.edited_at || msg.edited) {
      const ed = document.createElement("span");
      ed.className = "chat-edited";
      ed.textContent = tt("edited");
      stamp.appendChild(ed);
    }
    meta.appendChild(stamp);
    art.appendChild(meta);
    bindGestures(art, msg);
    if (isNew) feed.prepend(art);
    if (editState.editingId === msg.id) art.classList.add("is-being-edited");
  }

  function openMini(url) {
    const abs = resolveBrowseUrl({ url }, url);
    if (!isSafeHttpUrl(abs)) {
      toast("Ссылка недоступна в мини-браузере");
      return;
    }
    history.push(abs);
    urlEl.textContent = abs;
    urlEl.title = abs;
    frame.setAttribute("sandbox", "");
    frame.setAttribute("referrerpolicy", "no-referrer");
    frame.src = abs;
    overlay.hidden = false;
  }

  function closeMini() {
    overlay.hidden = true;
    frame.src = "about:blank";
    history.length = 0;
  }

  function goBack() {
    if (history.length <= 1) {
      closeMini();
      return;
    }
    history.pop();
    const prev = history[history.length - 1];
    urlEl.textContent = prev;
    urlEl.title = prev;
    frame.src = prev;
  }

  function openExternal() {
    const u = history[history.length - 1] || "";
    if (!isSafeHttpUrl(u)) return;
    window.open(u, "_blank", "noopener,noreferrer");
  }

  $("mbClose").addEventListener("click", closeMini);
  $("mbBack").addEventListener("click", goBack);
  $("mbExternal").addEventListener("click", openExternal);

  const bar = $("mbBar");
  bar.addEventListener("touchstart", (e) => { dragY = e.touches[0].clientY; }, { passive: true });
  bar.addEventListener("touchend", (e) => {
    if (dragY == null) return;
    const dy = e.changedTouches[0].clientY - dragY;
    dragY = null;
    if (dy > 64) closeMini();
  }, { passive: true });

  document.addEventListener("click", (e) => {
    const menu = $("chatMenu");
    if (menu && !menu.hidden && !menu.contains(e.target) && !e.target.closest(".chat-msg-edit")) closeMenu();
  });

  const cancelBtn = $("editCancel");
  if (cancelBtn) cancelBtn.addEventListener("click", cancelEdit);

  input.addEventListener("keydown", (e) => {
    if (!Edit) return;
    if (Edit.shouldCancelEdit({ isEditing: Edit.isEditing(editState), key: e.key })) {
      e.preventDefault();
      cancelEdit();
      return;
    }
    if (Edit.shouldStartLastEdit({
      isEditing: Edit.isEditing(editState),
      inputValue: input.value,
      key: e.key,
      selectionStart: input.selectionStart,
    })) {
      e.preventDefault();
      const last = lastOwnEditable();
      if (last) beginEdit(last);
    }
  });

  async function saveEdit(text) {
    const id = editState.editingId;
    form.querySelector("button").disabled = true;
    try {
      const r = await fetch("/api/chat/messages/" + encodeURIComponent(id), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) {
        toast((Edit && Edit.errorToast(r.status, lang)) || tt("error"));
        return;
      }
      const data = await r.json();
      if (data.message) renderMessage(data.message, { update: true });
      cancelEdit();
    } catch (err) {
      toast("Сеть: " + err.message);
    } finally {
      form.querySelector("button").disabled = false;
    }
  }

  async function loadMessages() {
    try {
      const data = await fetch("/api/chat/messages").then((r) => r.json());
      if (typeof data.edit_window_s === "number") editWindowS = data.edit_window_s;
      if (data.user_id) currentUser = data.user_id;
      (data.messages || []).forEach((m) => renderMessage(m));
    } catch (_) { /* ignore */ }
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = (input.value || "").trim();
    if (!text) return;
    if (Edit && Edit.isEditing(editState)) {
      await saveEdit(text);
      return;
    }
    form.querySelector("button").disabled = true;
    try {
      const r = await fetch("/api/chat/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (r.status === 429) { toast(tt("tooOften")); return; }
      const data = await r.json();
      if (!r.ok) { toast(data.detail || tt("error")); return; }
      input.value = "";
      renderMessage(data.message);
    } catch (err) {
      toast("Сеть: " + err.message);
    } finally {
      form.querySelector("button").disabled = false;
    }
  });

  async function connectChatWs() {
    try {
      const tok = await fetch("/api/ws-token").then((r) => r.json());
      if (!tok || !tok.token) return;
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(proto + "://" + location.host + "/ws/chat?t=" + encodeURIComponent(tok.token));
      ws.onmessage = (e) => {
        try {
          const ev = JSON.parse(e.data);
          if (ev && ev.type === "message_edited" && ev.message) {
            renderMessage(ev.message, { update: true });
            if (editState.editingId === ev.message.id && ev.message.text !== input.value) {
              /* another tab saved; leave local draft */
            }
          }
        } catch (_) { /* ignore */ }
      };
    } catch (_) { /* ignore */ }
  }

  syncBanner();
  loadMessages();
  connectChatWs();
})();
