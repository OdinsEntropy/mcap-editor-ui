// Wiring of the page: open a recording, keep topic selection, window and output in sync, export.

import { api } from "./api.js";
import { startExport } from "./exporter.js";
import { chooseFile } from "./files.js";
import { canRedo, canUndo, record, redo, resetHistory, setLimit, undo } from "./history.js";
import { resetCursor } from "./cursor.js";
import { allSettings, loadSettings, openSettings, saveSettings, settingValue } from "./settings.js";
import { getWindow, resetWindow, setDuration, setWindow } from "./timeline.js";
import { getSelected, setSelection, setTopics, updateTopics } from "./topics.js";
import { $, debounce, fmtDuration, fmtS, parseSeconds, toast } from "./ui.js";

const RECENT_KEY = "mcap-editor-recent";
const state = { info: null, outEdited: false, outOk: false, startDir: null };

// theme icon: switches and saves the theme setting
$("theme-btn").addEventListener("click", () => {
  const t = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  saveSettings({ ...allSettings(), "interface.theme": t }).catch((e) => toast(e.message, true));
});
$("settings-btn").addEventListener("click", () => openSettings());
$("keys-btn").addEventListener("click", () => openSettings("Keys"));

// recent files (in this browser)
const recent = () => JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
function remember(path) {
  localStorage.setItem(RECENT_KEY, JSON.stringify([path, ...recent().filter((p) => p !== path)].slice(0, 8)));
}
function renderRecent() {
  const el = $("recent-list");
  el.replaceChildren();
  for (const p of recent()) {
    const b = document.createElement("button");
    const i = p.lastIndexOf("/");
    b.innerHTML = "<span></span><small></small>";
    b.children[0].textContent = p.slice(i + 1);
    b.children[1].textContent = p.slice(0, i);
    b.title = p;
    b.addEventListener("click", () => open(p));
    el.append(b);
  }
}

const dirname = (p) => p.slice(0, p.lastIndexOf("/")) || "/";
const basename = (p) => p.slice(p.lastIndexOf("/") + 1);

// opening runs on the server in the background; large files take a while (message indexes, or a full read
// without summary), so a card shows the stage, progress and elapsed time, with Stop
const STAGES = ["Summary", "Message indexes"];
let loadToken = 0;

async function open(path) {
  const token = ++loadToken;
  let s;
  try {
    s = await api.openStart(path);
  } catch (e) {
    toast(e.message, true);
    return;
  }
  $("load-name").textContent = s.name;
  $("load-size").textContent = s.size_text;
  $("loading").hidden = false;
  while (s.state === "running") {
    renderLoading(s);
    await new Promise((r) => setTimeout(r, 200));
    if (token !== loadToken) return;                 // another file was opened meanwhile
    try {
      s = await api.openState();
    } catch (e) {
      toast(e.message, true);
      break;
    }
  }
  $("loading").hidden = true;
  if (s.state === "done") show(s.info);
  else if (s.state === "cancelled") toast("Opening stopped");
  else if (s.state === "error") toast(s.error, true);
}

function renderLoading(s) {
  const i = STAGES.indexOf(s.stage);
  for (const el of $("load-steps").children) {
    const j = STAGES.indexOf(el.dataset.step);
    el.classList.toggle("current", j === i);
    el.classList.toggle("done", j < i);
  }
  const fill = $("load-fill");
  fill.classList.toggle("indeterminate", !(s.done > 0));
  fill.style.width = `${Math.round((s.done || 0) * 100)}%`;
  const pct = `${Math.round((s.done || 0) * 100)} %`;
  $("load-detail").textContent = s.stage === "Summary"
    ? (s.done > 0 ? `No summary in the file – reading all of it: ${pct}` : "Reading the summary")
    : `Reading the message indexes: ${pct}`;
  $("load-time").textContent = fmtDuration(s.elapsed);
}

$("load-stop").addEventListener("click", () => api.openCancel().catch((e) => toast(e.message, true)));

function show(info) {
  state.info = info;
  remember(info.path);
  $("empty-state").hidden = true;
  for (const id of ["window-card", "topics-card", "output-section", "recording-info"]) $(id).hidden = false;
  $("recording-empty").hidden = true;
  $("rec-name").textContent = info.name;
  $("rec-name").title = info.path;
  $("rec-size").textContent = info.size_text;
  $("rec-duration").textContent = `${fmtS(info.duration)} s`;
  $("rec-start").textContent = info.start_text;
  $("rec-compression").textContent = info.compression;
  $("rec-noindex").hidden = info.indexed;
  document.title = `${info.name} – MCAP Editor UI`;
  state.outEdited = false;
  setOutput(info.default_output);
  setTopics(info.topics, changed, info.path);
  setDuration(info.duration, changed);
  resetCursor(info);
  resetOptions();
  latchedOff.clear();
  resetHistory(snapshot(), applySnapshot, updateUndoButtons);
}

// undo / redo of selection and window
const snapshot = () => ({ selected: getSelected().sort(), ...getWindow() });
function applySnapshot(snap) {
  setSelection(snap.selected);
  setWindow(snap.start, snap.end);
  changed();
}
function updateUndoButtons() {
  $("undo-btn").disabled = !canUndo();
  $("redo-btn").disabled = !canRedo();
}
$("undo-btn").addEventListener("click", undo);
$("redo-btn").addEventListener("click", redo);
document.addEventListener("keydown", (ev) => {
  if (!(ev.ctrlKey || ev.metaKey) || !state.info || document.querySelector("dialog[open]")) return;
  if (ev.target instanceof Element && ev.target.matches("input[type=text], input[type=search]")) return;    // text fields keep their own undo
  const k = ev.key.toLowerCase();
  if (k === "z" && !ev.shiftKey) { ev.preventDefault(); undo(); }
  else if ((k === "z" && ev.shiftKey) || k === "y") { ev.preventDefault(); redo(); }
});

// export options: compression and split, defaults from the settings
function segValue(id) {
  return $(id).querySelector("button.on")?.dataset.v;
}
function setSeg(id, v) {
  for (const b of $(id).children) b.classList.toggle("on", b.dataset.v === v);
}
function resetOptions() {
  $("keep-latched").checked = settingValue("output.keep_latched");
  const comp = settingValue("output.compression");
  $("comp-on").checked = comp !== "keep";
  setSeg("comp-choice", comp === "keep" ? "zstd" : comp);
  const split = settingValue("output.split");
  $("split-on").checked = split !== "off";
  $("split-repeat").checked = settingValue("output.split_repeat_latched");
  setSplitMode(split === "size" ? "size" : "duration");
  showOptions();
}
function setSplitMode(mode) {
  setSeg("split-mode", mode);
  $("split-value").value = mode === "size" ? settingValue("output.split_size_gb") : settingValue("output.split_duration_s");
}
function showOptions() {
  $("comp-body").hidden = !$("comp-on").checked;
  $("split-body").hidden = $("split-repeat-row").hidden = !$("split-on").checked;
}
function exportOptions() {
  const split = $("split-on").checked ? segValue("split-mode") : "off";
  const v = parseSeconds($("split-value").value);
  return {
    compression: $("comp-on").checked ? segValue("comp-choice") : "keep",
    split,
    split_duration_s: split === "duration" ? v : null,
    split_size_gb: split === "size" ? v : null,
    split_repeat_latched: $("split-repeat").checked,
  };
}
$("comp-on").addEventListener("change", () => { showOptions(); changed(); });
$("split-on").addEventListener("change", () => { showOptions(); checkOutput(); });
for (const b of $("comp-choice").children) b.addEventListener("click", (e) => { e.preventDefault(); setSeg("comp-choice", b.dataset.v); });
for (const b of $("split-mode").children) b.addEventListener("click", (e) => { e.preventDefault(); setSplitMode(b.dataset.v); changed(); });
$("split-value").addEventListener("input", () => {
  const v = parseSeconds($("split-value").value);
  $("split-value").classList.toggle("invalid", !(v > 0));
  changed();
});

// summary, estimate, export button
const estimate = debounce(async () => {
  const sel = getSelected();
  if (!state.info || !sel.length) return ($("sum-size").textContent = "–");
  const { start, end } = getWindow();
  try {
    const r = await api.estimate({ path: state.info.path, topics: sel, start, end });
    $("sum-size").textContent = r.text === "?" ? "?" : `≈ ${r.text}`;
  } catch (e) {
    $("sum-size").textContent = "–";
  }
}, 250);

function changed() {
  if (!state.info) return;
  const sel = getSelected();
  const { start, end } = getWindow();
  $("sum-topics").textContent = `${sel.length} of ${state.info.topics.length}`;
  $("sum-window").textContent = `${fmtS(start)} – ${fmtS(end ?? state.info.duration)} s`;
  renderLatched(new Set(sel), start);
  record(snapshot());
  const splitBad = $("split-on").checked && !(parseSeconds($("split-value").value) > 0);
  $("export-btn").disabled = !sel.length || !state.outOk || splitBad;
  $("export-btn").title = !sel.length ? "Select at least one topic" : !state.outOk ? "Choose a valid output file"
    : splitBad ? "Enter the part length or size" : "";
  estimate();
}

// latched topics under the switch, as toggles: accent = included, outlined = selected but left out by the user,
// grey = not selected (nothing to include)
const latchedOff = new Set();
function renderLatched(sel, start) {
  const el = $("latched-list");
  el.replaceChildren();
  const latched = state.info.topics.filter((t) => t.latched);
  const note = (text) => {
    const n = document.createElement("span");
    n.className = "note";
    n.textContent = text;
    el.append(n);
  };
  if (!latched.length) return note("No latched topics in this recording");
  const on = $("keep-latched").checked;
  for (const t of latched) {
    const c = document.createElement("button");
    c.type = "button";
    const selectedT = sel.has(t.topic), off = latchedOff.has(t.topic);
    c.className = "chip latched-chip" + (!selectedT ? " unselected" : off || !on ? " off" : " included");
    c.textContent = t.topic;
    c.title = !selectedT ? `${t.topic}: select the topic to include its latched messages`
      : off ? `${t.topic}: left out – click to include its earlier messages`
      : `${t.topic}: its earlier messages are included – click to leave them out`;
    c.addEventListener("click", () => {
      if (!selectedT || !on) return;                 // nothing to include: the tooltip says why
      off ? latchedOff.delete(t.topic) : latchedOff.add(t.topic);
      changed();
    });
    el.append(c);
  }
  const included = latched.filter((t) => sel.has(t.topic) && !latchedOff.has(t.topic));
  if (!on) note("Switched off: none included");
  else if (!latched.some((t) => sel.has(t.topic))) note("None included: no latched topic is selected");
  else if (!included.length) note("None included: all left out");
  else if (start === 0) note("No effect while the start is 0 s");
}

// output path: checked on the server (exists? folder?), with a free name offered
const checkOutput = debounce(async () => {
  const path = $("out-path").value.trim();
  try {
    const r = await api.output(path, $("split-on").checked);
    state.outOk = r.ok;
    $("out-path").classList.toggle("invalid", !r.ok);
    $("out-problem").hidden = r.ok;
    $("out-problem-text").textContent = r.problem;
    $("out-alt").hidden = !r.alternative;
    if (r.alternative) {
      $("out-alt").textContent = `Use ${basename(r.alternative)}`;
      $("out-alt").dataset.path = r.alternative;
      $("out-alt").title = r.alternative;
    }
  } catch (e) {
    state.outOk = false;
  }
  changed();
}, 250);

function setOutput(path) {
  $("out-path").value = path;
  $("out-path").title = path;
  $("out-path").scrollLeft = $("out-path").scrollWidth;     // show the file name, not the start of the folder
  state.outOk = false;
  checkOutput();
}

$("out-path").addEventListener("input", () => {
  state.outEdited = true;
  state.outOk = false;
  $("export-btn").disabled = true;
  checkOutput();
});
$("out-alt").addEventListener("click", () => setOutput($("out-alt").dataset.path));
$("out-browse").addEventListener("click", async () => {
  const cur = $("out-path").value.trim();
  const p = await chooseFile({ title: "Save as", save: true, start: dirname(cur), name: basename(cur) });
  if (p) {
    state.outEdited = true;
    setOutput(p);
  }
});

$("keep-latched").addEventListener("change", changed);   // also redraws the chips (disabled while off)
$("window-reset").addEventListener("click", resetWindow);

$("export-btn").addEventListener("click", () => {
  const { start, end } = getWindow();
  startExport({ input: state.info.path, output: $("out-path").value.trim(), topics: getSelected(), start, end,
                keep_latched: $("keep-latched").checked, latched_off: [...latchedOff], ...exportOptions() }, async () => {
    // the old name is taken now: offer the next free default unless the user typed their own
    if (!state.outEdited) setOutput((await api.info(state.info.path)).default_output);
    else checkOutput();
  });
});

// opening
async function openDialog() {
  const p = await chooseFile({ start: state.info ? dirname(state.info.path) : state.startDir });
  if (p) open(p);
}
$("open-btn").addEventListener("click", openDialog);
document.addEventListener("keydown", (ev) => {
  if ((ev.ctrlKey || ev.metaKey) && ev.key === "o") {
    ev.preventDefault();
    openDialog();
  }
});

// settings saved: apply what affects the open recording
async function settingsSaved(values, before) {
  setLimit(values["editing.history_size"]);
  if (!state.info) return;
  if (values["sizes.unit"] !== before["sizes.unit"]) {
    const info = await api.info(state.info.path);
    state.info = info;
    updateTopics(info.topics);
    estimate();
  }
  if (values["output.suffix"] !== before["output.suffix"] && !state.outEdited) {
    setOutput((await api.info(state.info.path)).default_output);
  }
}

renderRecent();
api.start().then((r) => (state.startDir = r.start_dir));
loadSettings(settingsSaved).then((values) => {
  setLimit(values["editing.history_size"]);
  const initial = new URLSearchParams(location.search).get("open");     // mcap-editor <file>
  if (initial) open(initial);
}).catch((e) => toast(e.message, true));
