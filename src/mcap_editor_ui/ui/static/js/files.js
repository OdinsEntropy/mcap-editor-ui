// File dialog: browses folders on the server (the browser cannot give a page real file paths).
// Mode "open" picks an .mcap file; mode "save" picks a folder and a file name.

import { api } from "./api.js";
import { $, toast } from "./ui.js";

const dlg = $("file-dialog");
let mode = "open";
let current = null;          // folder shown
let selected = null;         // chosen file path (open mode)
let resolver = null;

async function show(path) {
  let data;
  try {
    data = await api.ls(path);
  } catch (e) {
    toast(e.message, true);
    return;
  }
  current = data.path;
  $("fd-path").value = data.path;
  $("fd-up").disabled = !data.parent;
  $("fd-up").dataset.parent = data.parent || "";
  const list = $("fd-list");
  list.replaceChildren();
  const entries = data.entries;
  if (!entries.length) {
    const p = document.createElement("div");
    p.className = "fd-empty";
    p.textContent = "No folders or .mcap files here";
    list.append(p);
  }
  for (const e of entries) {
    const row = document.createElement("div");
    row.className = "fd-entry" + (e.dir ? " dir" : "");
    row.innerHTML = `<span></span><span class="muted">${e.dir ? "" : e.size_text}</span>`;
    row.firstChild.textContent = e.name;
    row.title = e.path;
    row.addEventListener("click", () => {
      if (e.dir) return show(e.path);
      if (mode === "save") {
        $("fd-name").value = e.name;     // exists – the server will refuse it, but it is a useful start
        updateOk();
        return;
      }
      selected = e.path;
      for (const r of list.children) r.classList.toggle("sel", r === row);
      updateOk();
    });
    if (!e.dir && mode === "open") row.addEventListener("dblclick", () => finish(e.path));
    list.append(row);
  }
  selected = null;
  updateOk();
}

function updateOk() {
  $("fd-ok").disabled = mode === "open" ? !selected : !$("fd-name").value.trim().endsWith(".mcap");
}

function finish(value) {
  dlg.close();
  resolver?.(value);
  resolver = null;
}

/** Open the dialog; resolves with the chosen path, or null when cancelled. */
export function chooseFile({ title = "Open recording", start = null, save = false, name = "" } = {}) {
  mode = save ? "save" : "open";
  $("fd-title").textContent = title;
  $("fd-ok").textContent = save ? "Select" : "Open";
  $("fd-name-row").hidden = !save;
  $("fd-name").value = name;
  dlg.showModal();
  show(start);
  return new Promise((res) => (resolver = res));
}

$("fd-up").addEventListener("click", () => $("fd-up").dataset.parent && show($("fd-up").dataset.parent));
$("fd-path").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    ev.preventDefault();
    const v = $("fd-path").value.trim();
    if (mode === "open" && v.endsWith(".mcap")) finish(v);     // pasted file path: open it directly
    else show(v);
  }
});
$("fd-name").addEventListener("input", updateOk);
$("fd-cancel").addEventListener("click", () => finish(null));
$("fd-ok").addEventListener("click", () => finish(mode === "open" ? selected : `${current.replace(/\/$/, "")}/${$("fd-name").value.trim()}`));
dlg.addEventListener("click", (ev) => {        // click on the dimmed background: like Cancel
  if (ev.target === dlg) finish(null);
});
dlg.addEventListener("cancel", () => {
  resolver?.(null);
  resolver = null;
});
