// No browser needed: exercise the actual card's preview refresh against WS replies.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const elements = new Map();
const context = vm.createContext({
  HTMLElement: class {},
  window: { CustomElementRegistryPolyfill: true },
  customElements: { get: (name) => elements.get(name), define: (name, value) => elements.set(name, value) },
});
vm.runInContext(fs.readFileSync("custom_components/lighting_curves/frontend/lighting-curves-card.js", "utf8"), context);
(async () => {
  const card = new (elements.get("lighting-curves-card"))();
  const status = {};
  let preview = { active: true, expires_at: "2026-11-07T23:59:00+00:00" };
  card.shadowRoot = { querySelector: () => status };
  card._current = "one";
  card._edit = { kind: "point", index: 0, data: { preview: true } };
  card._form = { data: {} };
  card._previewing = true;
  card._hass = { callWS: async (message) => {
    assert.equal(message.type, "lighting_curves/schedules");
    return [{ entry_id: "one", points: [{time: "06:00"}, {}], preview }];
  } };
  await card._refreshPreview();
  assert.equal(card._edit.data.preview, true);
  assert.match(status.textContent, /Preview ends at/);
  assert.match(card._editorHtml(), /id="preview-status"/);
  preview = { active: false, expires_at: null };
  await card._refreshPreview();
  assert.equal(card._edit.data.preview, false);
  assert.equal(card._form.data.preview, false);
  assert.equal(card._previewing, false);
  assert.equal(status.textContent, "");
  console.log("ok: card displays preview expiry and clears stale preview from backend");
})().catch((error) => { console.error(error); process.exitCode = 1; });
