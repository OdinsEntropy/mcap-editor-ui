// Settings: loaded from the server (defaults + the user's overrides), applied to the page (accent, theme), and
// edited in the settings dialog: sections left; rows with name and help left,
// control right; a row whose value differs from the default shows that default and a reset icon.

import { api } from "./api.js";
import { $, toast } from "./ui.js";

let data = null;            // {schema, defaults, values, overrides, file}
let draft = {};             // values being edited in the dialog
let section = null;
let onSaved = () => {};

const THEME_KEY = "mcap-editor-theme", ACCENT_KEY = "mcap-editor-accent";

/** Apply accent and theme to the page; cached in localStorage so the next page load paints right away. */
function applyLook(values) {
  const root = document.documentElement;
  const theme = values["interface.theme"] === "system"
    ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : values["interface.theme"];
  root.dataset.theme = theme;
  if (values["interface.accent"] === "orange") delete root.dataset.accent;
  else root.dataset.accent = values["interface.accent"];
  localStorage.setItem(THEME_KEY, theme);
  localStorage.setItem(ACCENT_KEY, values["interface.accent"]);
}

/** Load the settings from the server and apply them; ``saved(values, before)`` runs after every save. */
export async function loadSettings(saved) {
  onSaved = saved;
  data = await api.settings();
  applyLook(data.values);
  return data.values;
}

export const settingValue = (key) => data?.values[key];
export const allSettings = () => ({ ...data.values });

/** Save ``values`` (all keys); the server keeps only what differs from the defaults. */
export async function saveSettings(values) {
  const before = data.values;
  data = await api.saveSettings(values);
  applyLook(data.values);
  onSaved(data.values, before);
}

// ---- dialog ----
const KEYS_SECTION = "Keys";
// key combinations of the browser UI (shown, not changeable)
const KEYS = [
  ["Topic list", [
    ["Select a topic for the export", "Click its checkbox"],
    ["Select rows to work on several at once", "Ctrl + click a row"],
    ["Select a range of rows", "Shift + click a row"],
    ["… then for all of them: export checkbox, open / close, move", "Click a checkbox or a row, or drag a ⠿ grip"],
    ["Clear the row selection", "Esc"],
    ["Open / close a topic's timeline", "Click the row"],
    ["Open / close all shown topics", "Click the arrow in the table header"],
    ["Change the order by hand", "Drag the ⠿ grip"],
    ["Group selected / opened topics first", "Click the checkbox or chevron column header"],
    ["Sort", "Click a column header (again: other direction)"],
    ["Undo / redo selection and window", "Ctrl + Z  /  Ctrl + Shift + Z or Ctrl + Y"],
  ]],
  ["Cursor", [
    ["Place the cursor", "Click the bar or a topic timeline"],
    ["Snap to the nearest message of that topic", "Alt + click a topic timeline"],
    ["Step to the previous / next message of the active topic", "← / →"],
  ]],
  ["Window and zoom", [
    ["Move the window start / end", "Drag a handle on the bar"],
    ["… and snap it to the cursor or the zoom ends", "Alt + drag a handle"],
    ["Move the whole window", "Drag the range on the bar"],
    ["Set a new window by range", "Shift + drag on the bar or a timeline (or Shift + click, then click)"],
    ["Zoom the topic timelines to a range", "Ctrl + drag on the bar or a timeline (or Ctrl + click, then click)"],
    ["… with the ends snapping", "hold Alt as well"],
    ["Zoom the topic timelines", "Wheel on the bar, or Ctrl + wheel on a timeline"],
    ["Move the zoomed range", "Drag the dashed rectangle on the bar, or Shift + wheel"],
    ["Widen / narrow the zoomed range", "Drag an edge of the dashed rectangle"],
    ["Cancel a range being picked", "Esc or click elsewhere"],
  ]],
  ["General", [
    ["Open a recording", "Ctrl + O"],
    ["Close a dialog", "Esc or click outside it"],
  ]],
];
const sections = () => [...new Set(Object.values(data.schema).map((s) => s.section)), KEYS_SECTION];
const changedKeys = () => Object.keys(draft).filter((k) => draft[k] !== data.defaults[k]);
// labels start with a capital letter; units (kB, MB, GB) and codec names (zstd, lz4) stay as they are
const AS_IS = new Set(["zstd", "lz4"]);
const cap = (t) => (t === t.toLowerCase() && !AS_IS.has(t) ? t.charAt(0).toUpperCase() + t.slice(1) : t);
const show = (v) => (typeof v === "boolean" ? (v ? "On" : "Off") : typeof v === "string" ? cap(v) : String(v));

function renderNav() {
  const nav = $("settings-nav");
  nav.replaceChildren();
  const changed = new Set(changedKeys().map((k) => data.schema[k].section));
  for (const sec of sections()) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = sec === section ? "on" : "";
    b.innerHTML = "<span></span>";
    b.firstChild.textContent = sec;
    if (changed.has(sec)) {
      const d = document.createElement("i");
      d.className = "dot";
      d.title = "Contains values that differ from the default";
      b.append(d);
    }
    b.addEventListener("click", () => { section = sec; render(); });
    nav.append(b);
  }
}

function control(key, s) {
  const wrap = document.createElement("div");
  wrap.className = "set-control";
  const setVal = (v) => { draft[key] = v; render(); preview(); };
  if (s.type === "choice") {
    const seg = document.createElement("div");
    seg.className = "segmented";
    for (const c of s.choices) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = cap(c);
      b.classList.toggle("on", draft[key] === c);
      b.addEventListener("click", () => setVal(c));
      seg.append(b);
    }
    wrap.append(seg);
  } else if (s.type === "bool") {
    const l = document.createElement("label");
    l.className = "switch";
    l.innerHTML = '<input type="checkbox"><span></span>';
    l.firstChild.checked = draft[key];
    l.firstChild.addEventListener("change", (e) => setVal(e.target.checked));
    wrap.append(l);
  } else {
    const inp = document.createElement("input");
    inp.type = "text";
    inp.value = draft[key];
    if (s.type !== "str") inp.className = "num";
    inp.addEventListener("change", () => {
      let v = inp.value.trim();
      if (s.type !== "str") {
        v = Number(v.replace(",", "."));
        const bad = !isFinite(v) || (s.type === "int" && !Number.isInteger(v)) || (s.min != null && v < s.min) || (s.max != null && v > s.max);
        if (bad) {
          inp.classList.add("invalid");
          inp.title = `Number${s.min != null ? ` ≥ ${s.min}` : ""}${s.max != null ? ` and ≤ ${s.max}` : ""}`;
          return;
        }
      }
      setVal(v);
    });
    wrap.append(inp);
    const u = document.createElement("span");
    u.className = "unit";
    u.textContent = s.unit || "";
    wrap.append(u);
  }
  return wrap;
}

function renderKeys(rows) {
  for (const [group, list] of KEYS) {
    const h = document.createElement("h3");
    h.className = "keys-group";
    h.textContent = group;
    rows.append(h);
    for (const [what, how] of list) {
      const row = document.createElement("div");
      row.className = "set-row keys-row";
      row.innerHTML = '<div class="set-name"></div><div class="keys"></div>';
      row.children[0].textContent = what;
      // "Ctrl + Z" → key caps; words like "Click …" stay text
      for (const part of how.split(/(\s+\+\s+|\s+\/\s+|\s+or\s+|,\s+)/)) {
        const keyLike = /^(Ctrl|Shift|Alt|Esc|Z|Y|O|←|→)$/.test(part.trim());
        const el = document.createElement(keyLike ? "kbd" : "span");
        el.textContent = keyLike ? part.trim() : part;
        row.children[1].append(el);
      }
      rows.append(row);
    }
  }
}

function render() {
  renderNav();
  $("settings-title").textContent = section;
  const rows = $("settings-rows");
  rows.replaceChildren();
  $("settings-reset").hidden = section === KEYS_SECTION;
  if (section === KEYS_SECTION) {
    renderKeys(rows);
    $("settings-file").textContent = "Key combinations of the browser UI (not changeable)";
    return;
  }
  for (const [key, s] of Object.entries(data.schema)) {
    if (s.section !== section) continue;
    const row = document.createElement("div");
    row.className = "set-row";
    const left = document.createElement("div");
    left.innerHTML = '<div class="set-name"></div><div class="set-help"></div>';
    left.children[0].textContent = s.label;
    left.children[1].textContent = s.help;
    if (draft[key] !== data.defaults[key]) {
      const d = document.createElement("div");
      d.className = "set-default";
      d.innerHTML = '<span>Default: <b></b></span><button type="button" class="icon-btn sm" title="Back to the default"><svg viewBox="0 0 24 24" class="icon"><path d="M4 12a8 8 0 1 0 2.3-5.6M4 4v4h4"/></svg></button>';
      d.querySelector("b").textContent = show(data.defaults[key]);
      d.querySelector("button").addEventListener("click", () => { draft[key] = data.defaults[key]; render(); preview(); });
      left.append(d);
    }
    row.append(left, control(key, s));
    rows.append(row);
  }
  $("settings-file").textContent = `Your settings: ${data.file} (only values that differ from the defaults)`;
}

const preview = () => applyLook(draft);          // accent and theme change live while the dialog is open

export async function openSettings(startSection = null) {
  try {
    data = await api.settings();
  } catch (e) {
    return toast(e.message, true);
  }
  draft = { ...data.values };
  section = startSection || section || sections()[0];
  render();
  $("settings-dialog").showModal();
}

function close(save) {
  $("settings-dialog").close();
  if (save) {
    saveSettings(draft).then(() => toast("Settings saved")).catch((e) => { applyLook(data.values); toast(e.message, true); });
  } else {
    applyLook(data.values);                       // undo the live preview
  }
}

$("settings-cancel").addEventListener("click", () => close(false));
$("settings-save").addEventListener("click", () => close(true));
$("settings-reset").addEventListener("click", () => { draft = { ...data.defaults }; render(); preview(); });
$("settings-dialog").addEventListener("cancel", () => applyLook(data.values));
// a click on the dimmed background (the dialog element itself, outside its content) closes it like Cancel
$("settings-dialog").addEventListener("click", (ev) => {
  if (ev.target === $("settings-dialog")) close(false);
});
