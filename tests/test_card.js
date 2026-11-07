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
  const opened = [{ time: "06:00" }, { time: "20:00" }];
  card._schedules = [{ entry_id: "one", points: opened, preview: { active: false } }];
  card._hass = { config: { time_zone: "Australia/Adelaide" }, callWS: async (message) => {
    assert.equal(message.type, "lighting_curves/schedules");
    // Someone else inserted a point earlier in the day while the editor is open.
    return [{ entry_id: "one", points: [{ time: "05:00" }, { time: "06:00" }, { time: "20:00" }], preview }];
  } };
  await card._refreshPreview();
  assert.equal(card._schedule.points, opened, "the open editor keeps the points it opened with");
  assert.equal(card._edit.data.preview, true);
  assert.match(status.textContent, /Preview ends at 10:29/, "HA's time zone, not the browser's");
  card._hass.config.time_zone = "Not/AZone";
  assert.match(card._previewStatus(), /Preview ends at \d\d:\d\d/, "an unknown zone does not throw");
  card._hass.config.time_zone = "Australia/Adelaide";

  // A refresh that lands while a preview call is in flight must not untick the box.
  preview = { active: false, expires_at: null };
  card._previewPending = true;
  await card._refreshPreview();
  assert.equal(card._edit.data.preview, true);
  assert.equal(card._previewing, true);
  card._previewPending = false;
  assert.match(card._editorHtml(), /id="preview-status"/);
  await card._refreshPreview();
  assert.equal(card._edit.data.preview, false);
  assert.equal(card._form.data.preview, false);
  assert.equal(card._previewing, false);
  assert.equal(status.textContent, "");
  console.log("ok: card displays preview expiry and clears stale preview from backend");
})().catch((error) => { console.error(error); process.exitCode = 1; });
