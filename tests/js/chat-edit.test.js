"use strict";
const assert = require("assert");
const path = require("path");
const edit = require(path.join(__dirname, "../../talking/static/chat-edit.js"));

function testCreateStartCancel() {
  const s = edit.createState();
  assert.strictEqual(edit.isEditing(s), false);
  edit.startEdit(s, { id: "m1", text: "hello world" });
  assert.strictEqual(edit.isEditing(s), true);
  assert.strictEqual(s.editingId, "m1");
  assert.strictEqual(s.originalText, "hello world");
  assert.strictEqual(s.draft, "hello world");
  edit.cancel(s);
  assert.strictEqual(edit.isEditing(s), false);
  assert.strictEqual(s.editingId, null);
  assert.strictEqual(s.originalText, "");
}

function testArrowUpOnlyWhenEmpty() {
  assert.strictEqual(edit.shouldStartLastEdit({
    key: "ArrowUp", isEditing: false, inputValue: "", selectionStart: 0,
  }), true);
  assert.strictEqual(edit.shouldStartLastEdit({
    key: "ArrowUp", isEditing: false, inputValue: "x", selectionStart: 0,
  }), false);
  assert.strictEqual(edit.shouldStartLastEdit({
    key: "ArrowUp", isEditing: true, inputValue: "", selectionStart: 0,
  }), false);
  assert.strictEqual(edit.shouldStartLastEdit({
    key: "Enter", isEditing: false, inputValue: "", selectionStart: 0,
  }), false);
  assert.strictEqual(edit.shouldStartLastEdit({
    key: "ArrowUp", isEditing: false, inputValue: "", selectionStart: 2,
  }), false);
}

function testEscapeCancelsOnlyWhileEditing() {
  assert.strictEqual(edit.shouldCancelEdit({ isEditing: true, key: "Escape" }), true);
  assert.strictEqual(edit.shouldCancelEdit({ isEditing: false, key: "Escape" }), false);
  assert.strictEqual(edit.shouldCancelEdit({ isEditing: true, key: "Enter" }), false);
}

function testI18n() {
  assert.strictEqual(edit.t("ru", "edit"), "Изменить");
  assert.strictEqual(edit.t("ru", "edited"), "изменено");
  assert.strictEqual(edit.t("en", "edit"), "Edit");
  assert.strictEqual(edit.t("en", "edited"), "edited");
  assert.strictEqual(edit.detectLang({ documentElement: { getAttribute: () => "ru" } }), "ru");
  assert.strictEqual(edit.detectLang({ documentElement: { getAttribute: () => "en" } }), "en");
}

function testCanOfferEdit() {
  const msg = { id: "1", sender_id: "local", kind: "text", created_at: 1_700_000_000, text: "hi" };
  assert.strictEqual(edit.canOfferEdit(msg, "local", 1_700_000_010, 48 * 3600), true);
  assert.strictEqual(edit.canOfferEdit(msg, "other", 1_700_000_010, 48 * 3600), false);
  assert.strictEqual(edit.canOfferEdit({ ...msg, kind: "voice_note" }, "local", 1_700_000_010, 48 * 3600), false);
  assert.strictEqual(edit.canOfferEdit(msg, "local", 1_700_000_000 + 49 * 3600, 48 * 3600), false);
  assert.strictEqual(edit.canOfferEdit(msg, "local", 1_700_000_000 + 49 * 3600, 0), true);
}

const tests = [
  testCreateStartCancel,
  testArrowUpOnlyWhenEmpty,
  testEscapeCancelsOnlyWhileEditing,
  testI18n,
  testCanOfferEdit,
];
for (const fn of tests) fn();
console.log("ok", tests.length);
