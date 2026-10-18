// Lighting Curves card: a Tapo-style list of points per schedule, a day graph,
// and a point editor built from Home Assistant's own pickers (ha-form).
// Served and registered by the integration; no build step.

const WS = "lighting_curves";
const ANCHORS = [
  ["clock", "Clock time"],
  ["sunrise", "Sunrise"],
  ["sunset", "Sunset"],
];

const pad = (n) => String(n).padStart(2, "0");
const hhmm = (m) => `${pad(Math.floor(m / 60) % 24)}:${pad(Math.floor(m % 60))}`;

// Approximate colour of a white temperature, for display only.
function kelvinRgb(k) {
  const t = k / 100;
  const c = (v) => Math.max(0, Math.min(255, Math.round(v)));
  if (t <= 66) {
    return [255, c(99.4708 * Math.log(t) - 161.1196), t <= 19 ? 0 : c(138.5177 * Math.log(t - 10) - 305.0448)];
  }
  return [c(329.6987 * Math.pow(t - 60, -0.1332)), c(288.1222 * Math.pow(t - 60, -0.0755)), 255];
}
const css = (s) => `rgb(${(s.rgb_color || kelvinRgb(s.color_temp_kelvin)).join(",")})`;

function describeTime(time) {
  const m = /^(sunrise|sunset)([+-]\d+)$/.exec(time);
  if (!m) return "Clock time";
  const off = Number(m[2]);
  const name = m[1] === "sunrise" ? "Sunrise" : "Sunset";
  return off === 0 ? name : `${name} ${off > 0 ? "+" : "−"} ${Math.abs(off)} min`;
}

function toForm(p) {
  const m = /^(sunrise|sunset)([+-]\d+)$/.exec(p.time);
  return {
    anchor: m ? m[1] : "clock",
    time: m ? "12:00:00" : `${p.time.padStart(5, "0")}:00`,
    offset: m ? Number(m[2]) : 0,
    brightness: p.brightness,
    colour: p.rgb_color ? "rgb" : "white",
    color_temp_kelvin: p.color_temp_kelvin ?? 2700,
    rgb_color: p.rgb_color ?? [255, 150, 50],
  };
}

function fromForm(d) {
  const off = Math.round(Number(d.offset) || 0);
  const p = {
    time: d.anchor === "clock" ? d.time.slice(0, 5) : `${d.anchor}${off < 0 ? "-" : "+"}${Math.abs(off)}`,
    brightness: Math.round(d.brightness),
  };
  if (d.colour === "rgb") p.rgb_color = d.rgb_color.map(Math.round);
  else p.color_temp_kelvin = Math.round(d.color_temp_kelvin);
  return p;
}

function formSchema(d, canPreview) {
  return [
    { name: "anchor", selector: { select: { mode: "box", options: ANCHORS.map(([value, label]) => ({ value, label })) } } },
    d.anchor === "clock"
      ? { name: "time", selector: { time: { no_second: true } } }
      : { name: "offset", selector: { number: { min: -240, max: 240, step: 5, mode: "slider", unit_of_measurement: "min" } } },
    { name: "brightness", selector: { number: { min: 1, max: 100, mode: "slider", unit_of_measurement: "%" } } },
    { name: "colour", selector: { select: { mode: "box", options: [{ value: "white", label: "White" }, { value: "rgb", label: "Colour" }] } } },
    d.colour === "rgb"
      ? { name: "rgb_color", selector: { color_rgb: {} } }
      : { name: "color_temp_kelvin", selector: { color_temp: { unit: "kelvin", min: 2000, max: 6500 } } },
    ...(canPreview ? [{ name: "preview", selector: { boolean: {} } }] : []),
  ];
}

const LABELS = {
  anchor: "Time",
  time: "Clock time",
  offset: "Minutes from the sun (− before, + after)",
  brightness: "Brightness",
  colour: "Colour",
  color_temp_kelvin: "White temperature",
  rgb_color: "Colour",
  preview: "Preview on the lights",
};

class LightingCurvesCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
  }

  getCardSize() {
    return 8;
  }

  static getStubConfig() {
    return {};
  }

  set hass(hass) {
    this._hass = hass;
    if (this._form) this._form.hass = hass;
    if (!this._schedules && !this._loading) this._load();
  }

  connectedCallback() {
    this._timer = setInterval(() => !this._edit && this._load(), 60000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._endPreview();
  }

  async _load() {
    if (!this._hass) return;
    this._loading = true;
    try {
      this._schedules = await this._hass.callWS({ type: `${WS}/schedules` });
      this._error = null;
    } catch (e) {
      this._error = e.message || String(e);
    }
    this._loading = false;
    const want = this._config.schedule;
    const list = this._schedules || [];
    if (!list.some((s) => s.entry_id === this._current)) {
      this._current = (list.find((s) => s.title === want || s.entry_id === want) || list[0] || {}).entry_id;
    }
    this._sample = null;
    this._render();
  }

  get _schedule() {
    return (this._schedules || []).find((s) => s.entry_id === this._current);
  }

  // ---- list view -------------------------------------------------------

  _render() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const s = this._schedule;
    const pinned = this._config.schedule;
    const tabs =
      !pinned && (this._schedules || []).length > 1
        ? `<div class="tabs">${this._schedules
            .map((x) => `<button class="tab ${x.entry_id === this._current ? "on" : ""}" data-tab="${x.entry_id}">${esc(x.title)}</button>`)
            .join("")}</div>`
        : "";
    let body;
    if (this._error) body = `<div class="msg">${esc(this._error)}</div>`;
    else if (!this._schedules) body = `<div class="msg">Loading…</div>`;
    else if (!s) body = `<div class="msg">No lighting curves yet. Add one in Settings → Devices &amp; services.</div>`;
    else if (this._edit) body = this._editorHtml();
    else body = this._listHtml(s);

    this.shadowRoot.innerHTML = `<style>${STYLE}</style>
      <ha-card header="${esc(this._config.title || (pinned && s ? s.title : "Lighting curves"))}">
        ${this._edit ? "" : tabs}
        ${s && !this._error ? this._graphHtml(this._sample || s) : ""}
        ${body}
      </ha-card>`;
    this._bind();
  }

  _listHtml(s) {
    const rows = s.points
      .map(
        (p, i) => `<button class="row" data-edit="${i}">
          <span class="when"><span class="t">${hhmm(s.times[i])}</span><span class="sub">${describeTime(p.time)}</span></span>
          <span class="val"><ha-icon icon="mdi:brightness-6"></ha-icon>${p.brightness}%</span>
          <span class="val col"><span class="dot" style="background:${css(p)}"></span>${p.rgb_color ? "RGB" : `${p.color_temp_kelvin} K`}</span>
          <ha-icon class="chev" icon="mdi:chevron-right"></ha-icon>
        </button>`
      )
      .join("");
    const now = s.target ? `Now: ${s.target.brightness}% · ${s.target.rgb_color ? "RGB" : `${s.target.color_temp_kelvin} K`}` : "";
    return `<div class="rows">${rows}</div>
      <button class="add" data-add><ha-icon icon="mdi:plus"></ha-icon>Add point</button>
      <div class="foot">${now}${s.switch ? "" : " · Sensor only"} · Sunrise ${hhmm(s.sunrise)} · Sunset ${hhmm(s.sunset)}</div>`;
  }

  _graphHtml(d) {
    const W = 1440, H = 100;
    const step = d.sample_step;
    const pts = d.samples.map((x, i) => `${i * step},${H - x.brightness}`).join(" ");
    const stops = d.samples
      .map((x, i) => `<stop offset="${((i * step) / W).toFixed(4)}" stop-color="${css(x)}"/>`)
      .join("");
    const marks = d.times
      // A zero-length round-capped line stays a circle when the SVG is stretched.
      .map((t, i) => `<path d="M${t} ${H - d.points[i].brightness}h0" class="pt"/>`)
      .join("");
    const ticks = [0, 6, 12, 18, 24].map((h) => `<span>${pad(h % 24)}:00</span>`).join("");
    return `<div class="graph">
      <svg viewBox="-10 -10 ${W + 20} ${H + 20}" preserveAspectRatio="none">
        <defs><linearGradient id="g">${stops}</linearGradient></defs>
        <polygon points="0,${H} ${pts} ${W},${H}" fill="url(#g)" opacity="0.85"/>
        <polyline points="${pts}" class="line"/>
        <line x1="${d.sunrise}" x2="${d.sunrise}" y1="0" y2="${H}" class="sun"/>
        <line x1="${d.sunset}" x2="${d.sunset}" y1="0" y2="${H}" class="sun"/>
        <line x1="${d.now}" x2="${d.now}" y1="-10" y2="${H + 10}" class="now"/>
        ${marks}
      </svg>
      <div class="ticks">${ticks}</div>
    </div>`;
  }

  // ---- editor ----------------------------------------------------------

  _editorHtml() {
    const e = this._edit;
    const canDelete = e.index !== null && this._schedule.points.length > 1;
    return `<div class="editor">
      <div class="etitle">${e.index === null ? "New point" : "Edit point"}</div>
      <div id="form"></div>
      <div class="buttons">
        ${canDelete ? `<button class="btn del" data-delete>Delete</button>` : ""}
        <span class="grow"></span>
        <button class="btn" data-cancel>Cancel</button>
        <button class="btn primary" data-save>Save</button>
      </div>
    </div>`;
  }

  _openEditor(index) {
    const s = this._schedule;
    let p;
    if (index === null) {
      const n = new Date();
      const m = Math.round((n.getHours() * 60 + n.getMinutes()) / 5) * 5;
      p = { time: hhmm(m % 1440), brightness: s.target?.brightness ?? 50 };
      if (s.target?.rgb_color) p.rgb_color = s.target.rgb_color;
      else p.color_temp_kelvin = s.target?.color_temp_kelvin ?? 2700;
    } else p = s.points[index];
    this._edit = { index, data: { ...toForm(p), preview: false } };
    this._render();
  }

  _draftPoints() {
    const pts = [...this._schedule.points];
    const p = fromForm(this._edit.data);
    if (this._edit.index === null) pts.push(p);
    else pts[this._edit.index] = p;
    return pts;
  }

  _changed(data) {
    const old = this._edit.data;
    this._edit.data = data;
    if (old.anchor !== data.anchor || old.colour !== data.colour) {
      this._form.schema = formSchema(data, this._canPreview());
    }
    this._form.data = data;
    clearTimeout(this._debounce);
    this._debounce = setTimeout(() => this._afterChange(old), 250);
  }

  async _afterChange(old) {
    const e = this._edit;
    if (!e) return;
    const p = fromForm(e.data);
    if (e.data.preview) {
      this._previewing = true;
      const setting = { ...p };
      delete setting.time;
      this._call("preview", { setting });
    } else if (old.preview) this._endPreview();
    try {
      this._sample = await this._hass.callWS({ type: `${WS}/sample`, points: this._draftPoints() });
      const g = this.shadowRoot.querySelector(".graph");
      if (g) g.outerHTML = this._graphHtml(this._sample);
    } catch (_) {
      /* an incomplete draft; the graph keeps the last valid curve */
    }
  }

  _canPreview() {
    return (this._schedule?.lights || []).length > 0;
  }

  _endPreview() {
    if (!this._previewing) return;
    this._previewing = false;
    this._call("preview_end", {});
  }

  _call(cmd, extra) {
    return this._hass
      .callWS({ type: `${WS}/${cmd}`, entry_id: this._current, ...extra })
      .catch((e) => this._toast(e.message || String(e)));
  }

  async _save(points) {
    this._endPreview();
    try {
      await this._hass.callWS({ type: `${WS}/save`, entry_id: this._current, points });
    } catch (e) {
      this._toast(e.message || String(e));
      return;
    }
    this._edit = null;
    this._form = null;
    await this._load();
  }

  _close() {
    this._endPreview();
    this._edit = null;
    this._form = null;
    this._sample = null;
    this._render();
  }

  _toast(message) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }

  _bind() {
    const root = this.shadowRoot;
    root.querySelectorAll("[data-tab]").forEach((b) =>
      b.addEventListener("click", () => {
        this._current = b.dataset.tab;
        this._render();
      })
    );
    root.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => this._openEditor(Number(b.dataset.edit))));
    root.querySelector("[data-add]")?.addEventListener("click", () => this._openEditor(null));
    root.querySelector("[data-cancel]")?.addEventListener("click", () => this._close());
    root.querySelector("[data-save]")?.addEventListener("click", () => this._save(this._draftPoints()));
    root.querySelectorAll("[data-delete]").forEach((b) =>
      b.addEventListener("click", () => this._save(this._schedule.points.filter((_, i) => i !== this._edit.index)))
    );
    const holder = root.getElementById("form");
    if (holder) {
      const form = document.createElement("ha-form");
      form.hass = this._hass;
      form.data = this._edit.data;
      form.schema = formSchema(this._edit.data, this._canPreview());
      form.computeLabel = (s) => LABELS[s.name] || s.name;
      form.addEventListener("value-changed", (ev) => this._changed({ ...this._edit.data, ...ev.detail.value }));
      holder.appendChild(form);
      this._form = form;
    }
  }
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const STYLE = `
  ha-card { overflow: hidden; }
  .tabs { display: flex; gap: 8px; padding: 0 16px 8px; flex-wrap: wrap; }
  .tab { border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color);
         border-radius: 16px; padding: 4px 12px; font: inherit; cursor: pointer; }
  .tab.on { background: var(--primary-color); color: var(--text-primary-color); border-color: var(--primary-color); }
  .graph { padding: 0 16px 8px; }
  svg { width: 100%; height: 110px; display: block; }
  .line { fill: none; stroke: var(--primary-text-color); stroke-width: 2; vector-effect: non-scaling-stroke; opacity: .6; }
  .pt { stroke: var(--primary-text-color); stroke-width: 9; stroke-linecap: round; vector-effect: non-scaling-stroke; }
  .sun { stroke: var(--secondary-text-color); stroke-dasharray: 4 4; vector-effect: non-scaling-stroke; opacity: .6; }
  .now { stroke: var(--primary-color); stroke-width: 2; vector-effect: non-scaling-stroke; }
  .ticks { display: flex; justify-content: space-between; font-size: 11px; color: var(--secondary-text-color); }
  .rows { display: flex; flex-direction: column; }
  .row { display: flex; align-items: center; gap: 12px; padding: 10px 16px; background: none; border: 0;
         border-top: 1px solid var(--divider-color); color: var(--primary-text-color); font: inherit; text-align: left; cursor: pointer; }
  .row:hover { background: var(--secondary-background-color); }
  .when { display: flex; flex-direction: column; flex: 1; }
  .t { font-size: 18px; font-weight: 500; }
  .sub { font-size: 12px; color: var(--secondary-text-color); }
  .val { display: flex; align-items: center; gap: 4px; min-width: 64px; }
  .val ha-icon { --mdc-icon-size: 18px; color: var(--secondary-text-color); }
  .col { min-width: 80px; }
  .dot { width: 14px; height: 14px; border-radius: 50%; border: 1px solid var(--divider-color); }
  .chev { color: var(--secondary-text-color); }
  .add { display: flex; align-items: center; gap: 8px; width: 100%; padding: 12px 16px; background: none; border: 0;
         border-top: 1px solid var(--divider-color); color: var(--primary-color); font: inherit; cursor: pointer; }
  .foot { padding: 8px 16px 12px; font-size: 12px; color: var(--secondary-text-color); }
  .msg { padding: 16px; color: var(--secondary-text-color); }
  .editor { padding: 0 16px 16px; }
  .etitle { font-weight: 500; margin: 4px 0 8px; }
  .buttons { display: flex; gap: 8px; margin-top: 16px; align-items: center; }
  .grow { flex: 1; }
  .btn { border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color);
         border-radius: 18px; padding: 8px 16px; font: inherit; cursor: pointer; }
  .btn.primary { background: var(--primary-color); color: var(--text-primary-color); border-color: var(--primary-color); }
  .btn.del { color: var(--error-color); border-color: var(--error-color); }
`;

// Home Assistant (2026.10) swaps in a polyfilled element registry while it starts, and
// this file can load before that; a card defined in the old registry is invisible to the
// dashboard. Wait for the polyfill, then define. ponytail: polling; after 10 s (an HA
// without the polyfill) define anyway.
(function define(tries) {
  if (window.CustomElementRegistryPolyfill || tries > 200) {
    if (!customElements.get("lighting-curves-card")) customElements.define("lighting-curves-card", LightingCurvesCard);
  } else setTimeout(() => define(tries + 1), 50);
})(0);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "lighting-curves-card",
  name: "Lighting Curves",
  description: "Edit the points of your lighting curves: time, brightness and colour.",
});
