/* Telegram-like message edit: state machine + i18n.
 * DOM chrome is applied from link-preview.js so this file stays require()-able in Node tests.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.TalkingChatEdit = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const STRINGS = {
    ru: {
      edit: "Изменить",
      edited: "изменено",
      editing: "Редактирование",
      cancel: "Отмена",
      cancelEdit: "Отменить редактирование",
      save: "Сохранить",
      send: "Отправить",
      tooOften: "Слишком часто",
      error: "Ошибка",
      windowExpired: "Нельзя изменить — время истекло",
      notAllowed: "Нельзя изменить это сообщение",
      notFound: "Сообщение не найдено",
      menu: "Действия с сообщением",
      placeholder: "Напишите сообщение со ссылкой…",
      placeholderEdit: "Измените сообщение…",
    },
    en: {
      edit: "Edit",
      edited: "edited",
      editing: "Editing",
      cancel: "Cancel",
      cancelEdit: "Cancel editing",
      save: "Save",
      send: "Send",
      tooOften: "Too many requests",
      error: "Error",
      windowExpired: "This message can no longer be edited",
      notAllowed: "You cannot edit this message",
      notFound: "Message not found",
      menu: "Message actions",
      placeholder: "Write a message with a link…",
      placeholderEdit: "Edit the message…",
    },
  };

  function detectLang(doc, nav) {
    const html = String((doc && doc.documentElement && doc.documentElement.getAttribute("lang")) || "").toLowerCase();
    if (html.startsWith("en")) return "en";
    if (html.startsWith("ru")) return "ru";
    const loc = String((nav && (nav.language || nav.userLanguage)) || "").toLowerCase();
    if (loc.startsWith("en")) return "en";
    return "ru";
  }

  function t(lang, key) {
    const pack = STRINGS[lang] || STRINGS.ru;
    return pack[key] || STRINGS.ru[key] || key;
  }

  function createState() {
    return { editingId: null, originalText: "", draft: "" };
  }

  function isEditing(state) {
    return !!(state && state.editingId);
  }

  function startEdit(state, msg) {
    if (!state || !msg || !msg.id) return state;
    state.editingId = msg.id;
    state.originalText = msg.text || "";
    state.draft = msg.text || "";
    return state;
  }

  function cancel(state) {
    if (!state) return state;
    state.editingId = null;
    state.originalText = "";
    state.draft = "";
    return state;
  }

  function shouldStartLastEdit(opts) {
    const o = opts || {};
    if (o.key !== "ArrowUp") return false;
    if (o.isEditing) return false;
    if (String(o.inputValue || "") !== "") return false;
    if (o.selectionStart != null && o.selectionStart !== 0) return false;
    return true;
  }

  function shouldCancelEdit(opts) {
    const o = opts || {};
    return !!o.isEditing && o.key === "Escape";
  }

  function isOwn(msg, currentUser) {
    return !!msg && (msg.sender_id || "local") === (currentUser || "local");
  }

  function isEditableKind(msg) {
    const kind = (msg && (msg.kind || msg.type)) || "text";
    return kind === "text";
  }

  function withinWindow(msg, nowS, windowS) {
    if (!windowS || windowS <= 0) return true;
    const created = Number(msg && msg.created_at) || 0;
    return (nowS - created) <= windowS;
  }

  function canOfferEdit(msg, currentUser, nowS, windowS) {
    return isOwn(msg, currentUser) && isEditableKind(msg) && withinWindow(msg, nowS, windowS);
  }

  function formatTime(ts, lang) {
    const d = new Date((Number(ts) || 0) * 1000);
    if (Number.isNaN(d.getTime())) return "";
    const loc = lang === "en" ? "en-GB" : "ru-RU";
    return d.toLocaleTimeString(loc, { hour: "2-digit", minute: "2-digit" });
  }

  function errorToast(status, lang) {
    if (status === 429) return t(lang, "tooOften");
    if (status === 403) return t(lang, "notAllowed");
    if (status === 404) return t(lang, "notFound");
    if (status === 409) return t(lang, "windowExpired");
    return t(lang, "error");
  }

  return {
    STRINGS,
    detectLang,
    t,
    createState,
    isEditing,
    startEdit,
    cancel,
    shouldStartLastEdit,
    shouldCancelEdit,
    isOwn,
    isEditableKind,
    withinWindow,
    canOfferEdit,
    formatTime,
    errorToast,
  };
});
