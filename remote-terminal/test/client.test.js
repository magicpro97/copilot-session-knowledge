const assert = require("node:assert/strict");
const test = require("node:test");

const {
  THEME_DEFINITIONS,
  THEME_STORAGE_KEY,
  VIRTUAL_KEY_ROWS,
  getThemeDefinition,
  getVirtualKeySequence,
  normalizeThemeName,
} = require("../public/client.js");

test("six premium terminal themes are available and normalize safely", () => {
  assert.deepEqual(Object.keys(THEME_DEFINITIONS), [
    "default",
    "light",
    "dracula",
    "monokai",
    "solarized-dark",
    "solarized-light",
  ]);
  assert.equal(THEME_STORAGE_KEY, "remote-terminal-theme");
  assert.equal(normalizeThemeName("monokai"), "monokai");
  assert.equal(normalizeThemeName("missing-theme"), "default");
  assert.equal(getThemeDefinition("missing-theme").label, "Default");
});

test("virtual keyboard layout includes modifier rows arrows and function keys", () => {
  const ids = VIRTUAL_KEY_ROWS.flat().map((key) => key.id);
  for (const requiredKey of ["ctrl", "alt", "shift", "esc", "tab", "left", "up", "down", "right", "f1", "f12"]) {
    assert.equal(ids.includes(requiredKey), true, `missing virtual key: ${requiredKey}`);
  }
});

test("virtual keyboard sequences cover control combos and navigation modifiers", () => {
  assert.equal(getVirtualKeySequence("c", { ctrl: true }), "\u0003");
  assert.equal(getVirtualKeySequence("d", { ctrl: true }), "\u0004");
  assert.equal(getVirtualKeySequence("tab", { shift: true }), "\u001b[Z");
  assert.equal(getVirtualKeySequence("up", { ctrl: true }), "\u001b[1;5A");
  assert.equal(getVirtualKeySequence("left", { alt: true }), "\u001b[1;3D");
  assert.equal(getVirtualKeySequence("f12", {}), "\u001b[24~");
});
