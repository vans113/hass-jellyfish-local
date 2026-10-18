/*
 * JellyFish card — shipped with the JellyFish Local integration.
 *
 *   type: custom:jellyfish-card
 *   entity: light.jellyfish            # required
 *   entities:                          # optional: zone picker (e.g. all + each output)
 *     - light.jellyfish_all_zones
 *     - light.jellyfish_front
 *   name: Roofline                     # optional
 *   folders: [Colors, Halloween]       # optional: only show these folders
 *   exclude_folders: [Home Assistant]  # optional: hide these folders
 *   save_folder: Custom                # optional (where "Save as a look" stores)
 */
const VERSION = "0.4.0";

const PALETTE = [
  "#ffbe6e", "#ffffff", "#ff0000", "#ff3200", "#ff6400", "#ffc828",
  "#00ff00", "#3cff96", "#00e6d2", "#50b4ff", "#0000ff", "#8c00ff",
  "#ff00ff", "#ff5aaa",
];
const TYPE_HELP = {
  Color: "Holds still",
  Chase: "Runs along the roof",
  Paint: "Paints color over color",
  Stacker: "Stacks up from one end",
  Sequence: "Steps through the colors",
  "Multi-Paint": "Paints in blocks",
};
const MAX_SWATCH = 10;
const FAV = "__favorites__";

const hexToRgb = (h) => {
  const v = h.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16));
};
const rgbToHex = (r, g, b) =>
  "#" + [r, g, b].map((x) => Math.max(0, Math.min(255, x)).toString(16).padStart(2, "0")).join("");
const triples = (flat) => {
  const out = [];
  for (let i = 0; i + 2 < (flat || []).length; i += 3) out.push(rgbToHex(flat[i], flat[i + 1], flat[i + 2]));
  return out;
};
const lit = (hex) => {
  const [r, g, b] = hexToRgb(hex);
  return Math.max(r, g, b) > 24;
};
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

class JellyFishCard extends HTMLElement {
  static getStubConfig(hass) {
    const e = Object.keys(hass.states).find(
      (id) => id.startsWith("light.") && hass.states[id].attributes.zones !== undefined
    );
    return { entity: e || "light.jellyfish" };
  }

  setConfig(config) {
    if (!config || !config.entity) throw new Error("Set 'entity' to a JellyFish light");
    this._config = { save_folder: "Home Assistant", ...config };
    this._entities = config.entities && config.entities.length ? config.entities : [config.entity];
    this._active = this._active && this._entities.includes(this._active) ? this._active : config.entity;
    this._tab = this._tab || "looks";
    this._draft = null;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (e) => this._onClick(e));
      this.shadowRoot.addEventListener("input", (e) => this._onInput(e));
      this.shadowRoot.addEventListener("change", (e) => this._onChange(e));
      this.shadowRoot.addEventListener("keydown", (e) => this._onKey(e));
    }
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    const st = this._state;
    const sig = st ? `${st.state}|${st.attributes.effect}|${st.attributes.brightness}|${st.attributes.colors}` : "none";
    if (first || sig !== this._sig) {
      this._sig = sig;
      if (!this._dragging) this._render();
    }
    if (first || !this._catalog) this._loadCatalog();
  }

  getCardSize() {
    return 8;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  get _state() {
    return this._hass && this._hass.states[this._active];
  }

  async _loadCatalog(force = false) {
    if (!this._hass || (this._loading && !force)) return;
    this._loading = true;
    try {
      this._catalog = await this._hass.callWS({ type: "jellyfish_local/catalog", entity_id: this._active });
      this._favorites = new Set(this._catalog.favorites || []);
      if (!this._folder) {
        const names = this._folders().map((f) => f.name);
        this._folder = this._favorites.size ? FAV : names.includes("Accent") ? "Accent" : names[0];
      }
      this._error = null;
    } catch (err) {
      this._error = "Couldn't load patterns from the controller. They'll show up when it's reachable.";
    }
    this._loading = false;
    this._render();
  }

  _folders() {
    const all = (this._catalog && this._catalog.folders) || [];
    const only = this._config.folders;
    const exclude = this._config.exclude_folders || [];
    return all.filter(
      (f) => !f.name.startsWith("_") && (!only || only.includes(f.name)) && !exclude.includes(f.name),
    );
  }

  _call(domain, service, data = {}) {
    return this._hass.callService(domain, service, { entity_id: this._active, ...data }).catch((err) => {
      this._toast(err && err.message ? err.message : "That didn't work");
    });
  }

  _toast(message) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }

  _ensureDraft() {
    if (this._draft) return;
    const a = (this._state && this._state.attributes) || {};
    const colors = (a.colors || []).filter(lit).slice(0, 8);
    this._draft = {
      colors: colors.length ? colors : ["#ffbe6e"],
      type: a.pattern_type && TYPE_HELP[a.pattern_type] ? a.pattern_type : "Color",
      speed: a.speed || 50,
      direction: a.direction && a.direction !== "" ? a.direction : "Center",
      spacing: Number.isFinite(a.spacing) ? a.spacing : 2,
      saving: false,
      name: "",
      editing: null,
    };
  }

  // ------------------------------------------------------------------ events
  _onClick(e) {
    const t = e.target.closest("[data-act]");
    if (!t) return;
    const act = t.dataset.act;
    const v = t.dataset.v;
    const d = this._draft;
    switch (act) {
      case "power":
        this._call("light", "toggle");
        break;
      case "zone":
        this._active = v;
        this._draft = null;
        this._render();
        this._loadCatalog(true);
        break;
      case "tab":
        this._tab = v;
        if (v === "create") this._ensureDraft();
        this._render();
        break;
      case "folder":
        this._folder = v;
        this._render();
        break;
      case "look":
        this._call("light", "turn_on", { effect: v });
        break;
      case "fav":
        this._toggleFav(v);
        break;
      case "slot":
        this._draft.editing = Number(v);
        this._render();
        this.shadowRoot.querySelector(`#pick-${v}`)?.click();
        break;
      case "remove":
        if (d.colors.length > 1) d.colors.splice(Number(v), 1);
        this._render();
        break;
      case "add":
        if (d.colors.length < 8) d.colors.push(v || "#ffffff");
        this._render();
        break;
      case "type":
        d.type = v;
        this._render();
        break;
      case "dir":
        d.direction = v;
        this._render();
        break;
      case "spacing":
        d.spacing = Math.max(0, Math.min(50, d.spacing + Number(v)));
        this._render();
        break;
      case "from-current":
        this._draft = null;
        this._ensureDraft();
        this._render();
        break;
      case "try":
        this._run();
        break;
      case "save-start":
        d.saving = true;
        this._render();
        this.shadowRoot.querySelector("#save-name")?.focus();
        break;
      case "save-cancel":
        d.saving = false;
        this._render();
        break;
      case "save":
        this._save();
        break;
      default:
        break;
    }
  }

  _onInput(e) {
    const t = e.target;
    if (t.id === "bright") {
      this._dragging = true;
      this.shadowRoot.querySelector("#bright-val").textContent = `${t.value}%`;
    } else if (t.id === "live-speed") {
      this._dragging = true;
      this.shadowRoot.querySelector("#live-speed-val").textContent = this._speedWord(Number(t.value));
    } else if (t.id === "speed") {
      this._draft.speed = Number(t.value);
      this.shadowRoot.querySelector("#speed-val").textContent = this._speedWord(this._draft.speed);
    } else if (t.id === "save-name") {
      this._draft.name = t.value;
    } else if (t.dataset.pick !== undefined) {
      this._draft.colors[Number(t.dataset.pick)] = t.value;
      const dot = this.shadowRoot.querySelector(`[data-act="slot"][data-v="${t.dataset.pick}"]`);
      if (dot) dot.style.setProperty("--c", t.value);
    }
  }

  _onChange(e) {
    const t = e.target;
    if (t.id === "bright") {
      this._dragging = false;
      this._call("light", "turn_on", { brightness_pct: Number(t.value) });
    } else if (t.id === "live-speed") {
      this._dragging = false;
      this._call("jellyfish_local", "adjust_pattern", { speed: Number(t.value) });
    } else if (t.dataset.pick !== undefined) {
      this._render();
    }
  }

  _onKey(e) {
    if (e.target.id === "save-name" && e.key === "Enter") this._save();
    if (e.target.id === "save-name" && e.key === "Escape") {
      this._draft.saving = false;
      this._render();
    }
  }

  _payload() {
    const d = this._draft;
    const data = { colors: d.colors.map(hexToRgb), pattern_type: d.type, direction: d.direction, spacing: d.spacing };
    if (d.type !== "Color") data.speed = d.speed;
    return data;
  }

  async _run() {
    await this._call("jellyfish_local", "run_pattern", this._payload());
  }

  async _save() {
    const d = this._draft;
    const name = (d.name || "").trim();
    if (!name) {
      this._toast("Give the look a name first");
      return;
    }
    if (name.includes("/")) {
      this._toast("Names can't contain /");
      return;
    }
    try {
      await this._hass.callService("jellyfish_local", "run_pattern", { entity_id: this._active, ...this._payload() });
      await this._hass.callService("jellyfish_local", "save_pattern", {
        entity_id: this._active,
        name,
        folder: this._config.save_folder,
      });
      this._toast(`Saved "${name}"`);
      d.saving = false;
      d.name = "";
      this._folder = this._config.save_folder;
      this._tab = "looks";
      await this._loadCatalog(true);
    } catch (err) {
      this._toast(err && err.message ? err.message : "Couldn't save the look");
    }
  }

  _speedWord(v) {
    if (v <= 15) return "Slow";
    if (v <= 40) return "Relaxed";
    if (v <= 70) return "Lively";
    return "Fast";
  }

  // ------------------------------------------------------------------ render
  _string(colors, { count = 18, cls = "", motion = "" } = {}) {
    // Off (black) colors are spacers in a pattern: draw them as unlit bulbs.
    const use = colors && colors.length ? colors : ["#000000"];
    let dots = "";
    for (let i = 0; i < count; i++) {
      const c = use[i % use.length];
      dots += lit(c) ? `<i style="--c:${c};--i:${i}"></i>` : `<i class="unlit" style="--i:${i}"></i>`;
    }
    return `<span class="string ${cls}" data-motion="${motion}">${dots}</span>`;
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const hass = this._hass;
    const st = this._state;
    if (!hass) return;
    // Replacing innerHTML wholesale makes mobile browsers clamp the page scroll
    // back to the top. Pin the host height across the swap AND remember every
    // scroll container we sit inside, then put the scroll back — so tapping a
    // tab, folder or control never jumps the page.
    const prevH = this.offsetHeight;
    if (prevH) this.style.minHeight = `${prevH}px`;
    if (this._releaseH) cancelAnimationFrame(this._releaseH);
    this._releaseH = requestAnimationFrame(() => {
      this._releaseH = 0;
      this.style.minHeight = "";
    });
    const scrollers = this._scrollAncestors();
    const tops = scrollers.map((s) => s.scrollTop);
    const restoreScroll = () => {
      scrollers.forEach((s, i) => {
        if (s.scrollTop !== tops[i]) s.scrollTop = tops[i];
      });
    };
    if (!st) {
      this.shadowRoot.innerHTML = `<style>${CSS}</style><ha-card><div class="empty">${esc(this._active)} isn't available. Check the JellyFish Local integration.</div></ha-card>`;
      restoreScroll();
      requestAnimationFrame(restoreScroll);
      return;
    }
    const a = st.attributes;
    const on = st.state === "on";
    const unavailable = st.state === "unavailable";
    const pct = a.brightness ? Math.max(1, Math.round((a.brightness * 100) / 255)) : 0;
    const effect = a.effect && a.effect !== "off" ? a.effect : null;
    const colors = (a.colors || []).length ? a.colors : a.rgb_color ? [rgbToHex(...a.rgb_color)] : [];
    const look = effect ? effect.split("/").pop() : colors.length ? "Solid color" : "";
    const status = unavailable ? "Controller offline" : on ? `${look}${look ? ", " : ""}${pct}%` : "Off";
    const motion = on && a.pattern_type && a.pattern_type !== "Color" ? a.pattern_type.toLowerCase() : "";
    // A speed slider on the main card, shown only when an animated look is
    // playing (hidden for solid colors, which have no speed).
    const animated = on && !unavailable && effect && a.pattern_type && a.pattern_type !== "Color";
    const spd = Number.isFinite(a.speed) ? a.speed : 50;
    const name = this._config.name || a.friendly_name || "JellyFish";

    const zones =
      this._entities.length > 1
        ? `<div class="zones" role="tablist">${this._entities
            .map((id) => {
              const s = hass.states[id];
              const label = s ? (s.attributes.friendly_name || id).replace(/^JellyFish\s*/i, "") || "All" : id;
              return `<button role="tab" aria-selected="${id === this._active}" class="chip ${id === this._active ? "sel" : ""}" data-act="zone" data-v="${esc(id)}">${esc(label)}</button>`;
            })
            .join("")}</div>`
        : "";

    const body = this._tab === "create" ? this._createView() : this._looksView(effect);

    this.shadowRoot.innerHTML = `<style>${CSS}</style>
      <ha-card class="${on ? "on" : "off"}">
        <div class="roof" aria-hidden="true">${this._string(colors, { count: 26, cls: on ? "glow" : "dim", motion })}</div>
        <div class="head">
          <div class="title">
            <div class="name">${esc(name)}</div>
            <div class="status">${esc(status)}</div>
          </div>
          <button class="power ${on ? "is-on" : ""}" data-act="power" aria-pressed="${on}" aria-label="${on ? "Turn off" : "Turn on"}" ${unavailable ? "disabled" : ""}>
            <ha-icon icon="mdi:power"></ha-icon>
          </button>
        </div>
        ${zones}
        <label class="bright">
          <ha-icon icon="mdi:brightness-6"></ha-icon>
          <input id="bright" type="range" min="1" max="100" value="${pct || 100}" aria-label="Brightness" ${unavailable ? "disabled" : ""}>
          <span id="bright-val">${pct || 100}%</span>
        </label>
        ${
          animated
            ? `<label class="bright">
          <ha-icon icon="mdi:speedometer"></ha-icon>
          <input id="live-speed" type="range" min="1" max="100" value="${spd}" aria-label="Speed">
          <span id="live-speed-val">${this._speedWord(spd)}</span>
        </label>`
            : ""
        }
        <div class="tabs" role="tablist">
          <button role="tab" aria-selected="${this._tab === "looks"}" class="${this._tab === "looks" ? "sel" : ""}" data-act="tab" data-v="looks">Looks</button>
          <button role="tab" aria-selected="${this._tab === "create"}" class="${this._tab === "create" ? "sel" : ""}" data-act="tab" data-v="create">Make your own</button>
        </div>
        ${body}
      </ha-card>`;
    const row = this.shadowRoot.querySelector(".folders");
    if (row && this._folderScroll) row.scrollLeft = this._folderScroll;
    row?.addEventListener("scroll", () => (this._folderScroll = row.scrollLeft), { passive: true });
    restoreScroll();
    requestAnimationFrame(restoreScroll);
  }

  _scrollAncestors() {
    // Every element we're nested inside, across shadow boundaries, plus the
    // document scroller. Restoring scrollTop on a non-scrolling one is a no-op,
    // so recording them all is safe and finds whichever one actually scrolls.
    const list = [];
    let node = this;
    while (node) {
      let parent = node.parentNode || node.host;
      if (parent instanceof ShadowRoot) parent = parent.host;
      if (!parent || parent.nodeType !== 1) break;
      list.push(parent);
      node = parent;
    }
    const se = document.scrollingElement;
    if (se && !list.includes(se)) list.push(se);
    return list;
  }

  _allPatterns() {
    const map = {};
    for (const f of (this._catalog && this._catalog.folders) || []) {
      for (const p of f.patterns) map[p.key] = p;
    }
    return map;
  }

  async _toggleFav(key) {
    this._favorites = this._favorites || new Set();
    if (this._favorites.has(key)) this._favorites.delete(key);
    else this._favorites.add(key);
    this._render();
    try {
      const res = await this._hass.callWS({
        type: "jellyfish_local/set_favorites",
        entity_id: this._active,
        favorites: [...this._favorites],
      });
      this._favorites = new Set(res.favorites);
      if (this._catalog) this._catalog.favorites = res.favorites;
    } catch (err) {
      this._toast("Couldn't save favorites");
    }
    this._render();
  }

  _looksView(effect) {
    if (this._error && !this._catalog) return `<div class="empty">${esc(this._error)}</div>`;
    if (!this._catalog) return `<div class="empty">Loading looks…</div>`;
    const folders = this._folders();
    if (!folders.length) return `<div class="empty">No saved patterns on the controller yet. Make one in “Make your own”.</div>`;
    const favs = this._favorites || new Set();
    const showingFavs = this._folder === FAV;
    const all = this._allPatterns();
    let patterns;
    if (showingFavs) {
      patterns = [...favs].map((k) => all[k]).filter(Boolean);
    } else {
      const current = folders.find((f) => f.name === this._folder) || folders[0];
      this._folder = current.name;
      patterns = current.patterns;
    }
    const favChip = favs.size
      ? `<button class="chip ${showingFavs ? "sel" : ""}" data-act="folder" data-v="${FAV}" title="Favorites">&#9733;</button>`
      : "";
    const chips =
      favChip +
      folders
        .map(
          (f) =>
            `<button class="chip ${f.name === this._folder && !showingFavs ? "sel" : ""}" data-act="folder" data-v="${esc(f.name)}">${esc(f.name)}</button>`
        )
        .join("");
    const tiles = patterns
      .map((p) => {
        const sel = p.key === effect;
        const isFav = favs.has(p.key);
        return `<div class="look ${sel ? "sel" : ""}">
            <button class="look-play" data-act="look" data-v="${esc(p.key)}" aria-pressed="${sel}">
              ${this._string(triples(p.colors), { count: MAX_SWATCH, cls: "mini" })}
              <span class="look-name">${esc(p.name.trim())}</span>
            </button>
            <button class="fav ${isFav ? "on" : ""}" data-act="fav" data-v="${esc(p.key)}" aria-pressed="${isFav}" aria-label="${isFav ? "Remove favorite" : "Add favorite"}" title="${isFav ? "Remove favorite" : "Add favorite"}">${isFav ? "&#9733;" : "&#9734;"}</button>
          </div>`;
      })
      .join("");
    const hint = showingFavs && !patterns.length
      ? `<div class="empty">No favorites yet. Tap the &#9734; on any look to add it here.</div>`
      : `<div class="grid">${tiles}</div>`;
    return `<div class="folders">${chips}</div>${hint}`;
  }

  _createView() {
    this._ensureDraft();
    const d = this._draft;
    const slots = d.colors
      .map(
        (c, i) => `<span class="slot-wrap">
          <button class="slot" style="--c:${c}" data-act="slot" data-v="${i}" aria-label="Color ${i + 1}, change"></button>
          <input class="picker" id="pick-${i}" type="color" value="${c}" data-pick="${i}" tabindex="-1" aria-hidden="true">
          ${d.colors.length > 1 ? `<button class="x" data-act="remove" data-v="${i}" aria-label="Remove color ${i + 1}">×</button>` : ""}
        </span>`
      )
      .join("");
    const add =
      d.colors.length < 8
        ? `<div class="palette" aria-label="Add a color">${PALETTE.map(
            (c) => `<button class="swatch" style="--c:${c}" data-act="add" data-v="${c}" aria-label="Add ${c}"></button>`
          ).join("")}</div>`
        : `<p class="hint">That's the most colors one look can use here.</p>`;
    const types = Object.keys(TYPE_HELP)
      .map(
        (t) =>
          `<button class="chip ${d.type === t ? "sel" : ""}" data-act="type" data-v="${t}">${t === "Color" ? "Solid" : t}</button>`
      )
      .join("");
    const moving = d.type !== "Color";
    const dirs = ["Left", "Center", "Right"]
      .map(
        (v) =>
          `<button class="chip ${d.direction === v ? "sel" : ""}" data-act="dir" data-v="${v}">${{ Left: "← Left", Center: "Out from center", Right: "Right →" }[v]}</button>`
      )
      .join("");
    const save = d.saving
      ? `<div class="save-row">
          <input id="save-name" type="text" maxlength="60" placeholder="Name this look" value="${esc(d.name)}" aria-label="Name for this look">
          <button class="btn primary" data-act="save">Save</button>
          <button class="btn" data-act="save-cancel">Cancel</button>
        </div>`
      : `<div class="actions">
          <button class="btn primary" data-act="try">Show it on the house</button>
          <button class="btn" data-act="save-start">Save as a look…</button>
        </div>`;
    return `<div class="create">
        <div class="preview">${this._string(d.colors, { count: 24, cls: "glow", motion: moving ? d.type.toLowerCase() : "" })}</div>
        <div class="row-head"><span>Colors</span><button class="link" data-act="from-current">Start from what's on</button></div>
        <div class="slots">${slots}</div>
        ${add}
        <div class="row-head"><span>Movement</span><span class="sub">${esc(TYPE_HELP[d.type])}</span></div>
        <div class="chips">${types}</div>
        ${
          moving
            ? `<label class="speed"><span>Speed</span><input id="speed" type="range" min="1" max="100" value="${d.speed}" aria-label="Speed"><span id="speed-val">${this._speedWord(d.speed)}</span></label>
               <div class="chips">${dirs}</div>`
            : ""
        }
        <div class="spacing"><span>Gap between colors</span>
          <span class="stepper"><button data-act="spacing" data-v="-1" aria-label="Less gap">−</button><b>${d.spacing}</b><button data-act="spacing" data-v="1" aria-label="More gap">+</button></span>
        </div>
        ${save}
      </div>`;
  }
}

const CSS = `
:host { --jf-gap: 12px; --jf-radius: 14px; }
ha-card { padding: 0 0 16px; overflow: hidden; }
button { font: inherit; color: inherit; cursor: pointer; }
button:focus-visible, input:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
.empty { padding: 20px 16px; color: var(--secondary-text-color); }

/* The roofline: one row of bulbs in the colors that are on */
.roof { padding: 18px 16px 10px; overflow: hidden; }
.string { display: flex; justify-content: space-between; gap: 3px; min-width: 0; overflow: hidden; }
.string i { display: block; width: 8px; height: auto; aspect-ratio: 1; border-radius: 50%; background: var(--c); flex: 0 1 auto; min-width: 4px; }
.string i.unlit { background: color-mix(in srgb, var(--primary-text-color) 14%, transparent); box-shadow: none !important; animation: none !important; }
.roof .string i { width: 9px; }
.string.glow i { box-shadow: 0 0 6px 1px var(--c), 0 0 14px 2px color-mix(in srgb, var(--c) 45%, transparent); }
.string.dim i { opacity: .22; box-shadow: none; }
.string.mini { gap: 2px; justify-content: flex-start; }
.string.mini i { width: 7px; flex-shrink: 0; }

@media (prefers-reduced-motion: no-preference) {
  .string[data-motion="chase"] i, .string[data-motion="sequence"] i, .string[data-motion="multi-paint"] i {
    animation: jf-run 1.6s linear infinite; animation-delay: calc(var(--i) * -0.09s);
  }
  .string[data-motion="paint"] i, .string[data-motion="stacker"] i {
    animation: jf-fill 2.4s ease-in-out infinite; animation-delay: calc(var(--i) * 0.06s);
  }
}
@keyframes jf-run { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: .35; transform: scale(.8); } }
@keyframes jf-fill { 0% { opacity: .2; } 40%,100% { opacity: 1; } }

.head { display: flex; align-items: center; gap: var(--jf-gap); padding: 4px 16px 8px; }
.title { flex: 1; min-width: 0; }
.name { font-size: 1.25rem; font-weight: 500; line-height: 1.3; }
.status { color: var(--secondary-text-color); font-size: .9rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.power { width: 48px; height: 48px; border-radius: 50%; border: 1px solid var(--divider-color); background: transparent; display: grid; place-items: center; }
.power.is-on { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
.power:disabled { opacity: .4; cursor: default; }

.zones { display: flex; gap: 8px; padding: 0 16px 8px; flex-wrap: wrap; }
.bright { display: flex; align-items: center; gap: 10px; padding: 4px 16px 12px; color: var(--secondary-text-color); }
.bright input, .speed input { flex: 1; accent-color: var(--primary-color); min-height: 28px; }
#bright-val { width: 3.2em; text-align: right; font-variant-numeric: tabular-nums; color: var(--primary-text-color); }
#live-speed-val { width: 4.5em; text-align: right; color: var(--secondary-text-color); }

.tabs { display: flex; margin: 0 16px 12px; border-bottom: 1px solid var(--divider-color); }
.tabs button { flex: 1; background: none; border: 0; padding: 10px 0; color: var(--secondary-text-color); border-bottom: 2px solid transparent; margin-bottom: -1px; }
.tabs button.sel { color: var(--primary-text-color); border-bottom-color: var(--primary-color); font-weight: 500; }

.chip { border: 1px solid var(--divider-color); background: transparent; border-radius: 999px; padding: 6px 12px; white-space: nowrap; font-size: .9rem; }
.chip.sel { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
.folders { display: flex; gap: 8px; overflow-x: auto; padding: 0 16px 12px; scrollbar-width: none; }
.folders::-webkit-scrollbar { display: none; }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; padding: 0 16px; }
.look { position: relative; border-radius: var(--jf-radius); border: 1px solid var(--divider-color); min-height: 64px; }
.look.sel { border-color: var(--primary-color); box-shadow: inset 0 0 0 1px var(--primary-color); }
.look-play { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; text-align: left; padding: 12px 40px 12px 12px; background: transparent; border: 0; width: 100%; border-radius: var(--jf-radius); }
.look-name { font-size: .92rem; line-height: 1.25; }
.fav { position: absolute; top: 6px; right: 6px; width: 30px; height: 30px; border: 0; border-radius: 50%; background: transparent; color: var(--secondary-text-color); font-size: 1.1rem; line-height: 1; padding: 0; }
.fav.on { color: #f5c518; }
.folders { -webkit-mask-image: linear-gradient(90deg, #000 94%, transparent); mask-image: linear-gradient(90deg, #000 94%, transparent); }

.create { padding: 0 16px; display: flex; flex-direction: column; gap: 12px; }
.preview { padding: 14px 12px; border-radius: var(--jf-radius); background: color-mix(in srgb, var(--primary-text-color) 6%, transparent); }
.row-head { display: flex; align-items: baseline; justify-content: space-between; font-weight: 500; }
.row-head .sub { font-weight: 400; color: var(--secondary-text-color); font-size: .9rem; }
.link { background: none; border: 0; color: var(--primary-color); padding: 0; font-size: .9rem; }
.slots { display: flex; gap: 14px; flex-wrap: wrap; }
.slot-wrap { position: relative; }
.slot { width: 44px; height: 44px; border-radius: 50%; background: var(--c); border: 2px solid var(--card-background-color); box-shadow: 0 0 0 1px var(--divider-color), 0 0 12px color-mix(in srgb, var(--c) 60%, transparent); }
.picker { position: absolute; inset: 0; opacity: 0; pointer-events: none; width: 44px; height: 44px; }
.x { position: absolute; top: -6px; right: -6px; width: 22px; height: 22px; border-radius: 50%; border: 0; background: var(--secondary-background-color, #444); line-height: 1; padding: 0; }
.palette { display: flex; gap: 8px; flex-wrap: wrap; }
.swatch { width: 28px; height: 28px; border-radius: 50%; background: var(--c); border: 1px solid var(--divider-color); padding: 0; }
.chips { display: flex; gap: 8px; flex-wrap: wrap; }
.speed { display: flex; align-items: center; gap: 10px; }
#speed-val { width: 4.5em; text-align: right; color: var(--secondary-text-color); }
.spacing { display: flex; align-items: center; justify-content: space-between; }
.stepper { display: inline-flex; align-items: center; gap: 12px; }
.stepper button { width: 36px; height: 36px; border-radius: 50%; border: 1px solid var(--divider-color); background: transparent; font-size: 1.1rem; }
.stepper b { min-width: 2ch; text-align: center; font-variant-numeric: tabular-nums; }
.actions, .save-row { display: flex; gap: 8px; flex-wrap: wrap; }
.save-row input { flex: 1; min-width: 140px; padding: 10px 12px; border-radius: 10px; border: 1px solid var(--divider-color); background: transparent; color: inherit; font: inherit; }
.btn { padding: 10px 16px; border-radius: 999px; border: 1px solid var(--divider-color); background: transparent; }
.btn.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
.hint { margin: 0; color: var(--secondary-text-color); font-size: .9rem; }
`;

if (!customElements.get("jellyfish-card")) {
  customElements.define("jellyfish-card", JellyFishCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "jellyfish-card",
    name: "JellyFish",
    description: "Pick a look or make your own for JellyFish lights.",
    preview: false,
  });
  console.info(`%c JELLYFISH-CARD %c ${VERSION} `, "background:#7b2ff7;color:#fff", "");
}
