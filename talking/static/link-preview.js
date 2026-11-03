(() => {
  const $ = (id) => document.getElementById(id);
  const feed = $("chatFeed");
  const form = $("chatForm");
  const input = $("chatInput");
  const overlay = $("miniBrowser");
  const frame = $("mbFrame");
  const urlEl = $("mbUrl");
  if (!feed || !form || !overlay || !frame) return;

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

  function renderMessage(msg) {
    const art = document.createElement("article");
    art.className = "chat-msg";
    art.dataset.id = msg.id || "";
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
    feed.prepend(art);
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

  async function loadMessages() {
    try {
      const data = await fetch("/api/chat/messages").then((r) => r.json());
      (data.messages || []).forEach(renderMessage);
    } catch (_) { /* ignore */ }
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = (input.value || "").trim();
    if (!text) return;
    form.querySelector("button").disabled = true;
    try {
      const r = await fetch("/api/chat/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (r.status === 429) { toast("Слишком часто"); return; }
      const data = await r.json();
      if (!r.ok) { toast(data.detail || "Ошибка"); return; }
      input.value = "";
      renderMessage(data.message);
    } catch (err) {
      toast("Сеть: " + err.message);
    } finally {
      form.querySelector("button").disabled = false;
    }
  });

  loadMessages();
})();
