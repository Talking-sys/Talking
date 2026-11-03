(() => {
  const $ = (id) => document.getElementById(id);
  let csrf = "";

  function toast(text) {
    const el = $("toast");
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.hidden = true; }, 2800);
  }

  function headers(json) {
    const h = { Origin: location.origin };
    if (csrf) h["X-CSRF-Token"] = csrf;
    if (json) h["Content-Type"] = "application/json";
    return h;
  }

  async function api(path, opts) {
    const r = await fetch(path, opts);
    let body = null;
    try { body = await r.json(); } catch { body = { detail: await r.text() }; }
    return { ok: r.ok, status: r.status, body };
  }

  function eventLabel(ev) {
    const map = {
      anonymous_create: "Создан анонимный аккаунт",
      recover: "Вход по коду восстановления",
      phone_start: "Отправлен код на телефон",
      phone_verify: "Вход по телефону",
      phone_link: "Привязан телефон",
      method_unlink: "Отвязан метод входа",
      logout: "Выход",
      logout_all: "Выход на всех устройствах",
      session_revoke: "Сессия отозвана",
      refresh_reuse: "Семейство сессий отозвано (повтор refresh)",
      account_delete_requested: "Запрошено удаление",
      account_delete_cancelled: "Удаление отменено",
      account_deleted: "Аккаунт удалён",
      export_requested: "Запрошен экспорт",
      export_ready: "Экспорт готов",
      export_failed: "Экспорт не удался",
    };
    return map[ev] || ev;
  }

  function renderProfile(me, account) {
    const kv = $("profileKv");
    kv.hidden = false;
    kv.innerHTML = "";
    const rows = [
      ["ID", me.id],
      ["Статус", me.status || account.status],
      ["Режим", me.auth_mode || ""],
    ];
    if (account.delete_after) rows.push(["Удаление в", account.delete_after]);
    rows.forEach(([k, v]) => {
      const dt = document.createElement("dt");
      dt.textContent = k;
      const dd = document.createElement("dd");
      dd.textContent = v || "—";
      kv.appendChild(dt);
      kv.appendChild(dd);
    });
    $("accountHint").textContent = account.fresh_session
      ? "Сессия свежая — удаление доступно."
      : "Для удаления войдите заново (нужна сессия младше 10 минут).";
    const pending = (me.status || account.status) === "deleting";
    $("deleteBanner").hidden = !pending;
    $("cancelDelete").hidden = !pending;
    if (pending) {
      $("deleteBanner").textContent = "Удаление запланировано на " + (account.delete_after || "скоро") + ". Можно отменить до истечения паузы.";
    }
  }

  async function load() {
    const me = await api("/api/auth/me");
    if (me.status === 401) {
      $("accountHint").textContent = "Вы не вошли. На хостинге создайте аккаунт (анонимно или по телефону).";
      return;
    }
    if (!me.ok) {
      $("accountHint").textContent = "Не удалось загрузить профиль.";
      return;
    }
    csrf = me.body.csrf_token || "";
    if (me.body.auth_mode === "local") {
      $("accountHint").textContent = "Локальный режим (AUTH_MODE=local): экспорт и удаление аккаунта доступны только в режиме accounts.";
      return;
    }
    const account = await api("/api/auth/account");
    renderProfile(me.body, account.ok ? account.body : { status: me.body.status });
    const events = await api("/api/auth/events?limit=50");
    const ul = $("eventsList");
    ul.innerHTML = "";
    (events.body.events || []).forEach((ev) => {
      const li = document.createElement("li");
      const when = (ev.created_at || "").replace("T", " ").slice(0, 19);
      li.textContent = when + " — " + eventLabel(ev.event);
      ul.appendChild(li);
    });
    if (!ul.children.length) {
      const li = document.createElement("li");
      li.textContent = "Пока нет событий.";
      ul.appendChild(li);
    }
  }

  $("exportBtn").addEventListener("click", async () => {
    const r = await api("/api/auth/export", { method: "POST", headers: headers(true), body: "{}" });
    if (!r.ok) {
      toast(r.body.detail || "Экспорт недоступен");
      $("exportStatus").textContent = String(r.body.detail || r.status);
      return;
    }
    const st = await api("/api/auth/export/" + r.body.id);
    $("exportStatus").textContent = "Статус: " + (st.body.status || r.body.status);
    if (st.body.status === "ready" && st.body.download_token) {
      const a = $("exportLink");
      a.hidden = false;
      a.href = "/api/auth/export/" + r.body.id + "/download?token=" + encodeURIComponent(st.body.download_token);
      a.textContent = "Скачать talking-export.json";
    }
  });

  $("deleteBtn").addEventListener("click", async () => {
    const confirm = $("deleteConfirm").value.trim();
    const r = await api("/api/auth/account/delete", {
      method: "POST",
      headers: headers(true),
      body: JSON.stringify({ confirm }),
    });
    if (!r.ok) {
      toast(r.body.detail || "Не удалось удалить");
      $("deleteStatus").textContent = String(r.body.detail || r.status);
      return;
    }
    $("deleteStatus").textContent = r.body.status === "deleted"
      ? "Аккаунт удалён."
      : "Удаление запланировано на " + (r.body.delete_after || "");
    await load();
  });

  $("cancelDelete").addEventListener("click", async () => {
    const r = await api("/api/auth/account/delete/cancel", { method: "POST", headers: headers(true), body: "{}" });
    if (!r.ok) {
      toast(r.body.detail || "Не удалось отменить");
      return;
    }
    toast("Удаление отменено");
    await load();
  });

  load().catch((e) => toast("Ошибка: " + e.message));
})();
