// Export: start it on the server, then poll its state and show steps, bar, numbers and the result.

import { api } from "./api.js";
import { $, fmtDuration, toast } from "./ui.js";

const STEPS = ["Preparing", "Copying", "Done"];
let onFinished = () => {};

function showSteps(stage) {
  const i = STEPS.indexOf(stage);
  for (const el of $("steps").children) {
    const j = STEPS.indexOf(el.dataset.step);
    el.classList.toggle("current", j === i);
    el.classList.toggle("done", i >= 0 && j < i);
  }
}

function render(s) {
  showSteps(s.stage);
  const fill = $("bar-fill");
  fill.classList.toggle("indeterminate", s.state === "running" && !(s.done > 0));
  fill.style.width = `${Math.round((s.done || 0) * 100)}%`;
  const running = s.state === "running";
  $("cancel-btn").hidden = !running;
  $("close-progress").hidden = running;
  if (running) {
    $("prog-detail").textContent = s.done > 0 ? `${Math.round(s.done * 100)} % · ${s.written} messages written` : "Reading the index";
    $("prog-time").textContent = `${fmtDuration(s.elapsed)}` + (s.eta != null ? ` · ~${fmtDuration(s.eta)} left` : "");
    return;
  }
  $("prog-time").textContent = fmtDuration(s.elapsed);
  const res = $("result");
  res.replaceChildren();
  res.hidden = false;
  if (s.state === "done") {
    const n = s.outputs.length;
    $("prog-detail").textContent = `Written ${s.out_size_text}${n > 1 ? ` in ${n} parts` : ""}, read ${s.read_text} of the input`;
    for (const [t, n] of Object.entries(s.kept)) {
      const row = document.createElement("div");
      row.className = "kv";
      const extra = s.latched?.[t] ? ` + ${s.latched[t]} latched` : "";
      row.innerHTML = "<span></span><span></span>";
      row.children[0].textContent = t;
      row.children[1].textContent = `${n}${extra}`;
      res.append(row);
    }
    const files = s.outputs.length > 1
      ? s.outputs.map((o, i) => [`Part ${i}`, `${o.path} (${o.size_text})`])
      : [["Output", s.outputs[0].path]];
    for (const [label, path] of [...files, ["Job file", s.job]]) {
      const p = document.createElement("div");
      p.className = "path";
      p.textContent = `${label}: ${path}`;
      res.append(p);
    }
  } else if (s.state === "cancelled") {
    $("prog-detail").textContent = "Cancelled – no output written";
    res.hidden = true;
  } else {
    $("prog-detail").textContent = "Export failed – no output written";
    res.textContent = s.error || "";
    res.classList.add("danger-text");
  }
}

async function poll() {
  let s;
  try {
    s = await api.exportState();
  } catch (e) {
    toast(e.message, true);
    setTimeout(poll, 1000);
    return;
  }
  render(s);
  if (s.state === "running") setTimeout(poll, 300);
  else onFinished(s);
}

/** Start an export with ``body`` (see the server API); ``finished(state)`` runs when it ends. */
export async function startExport(body, finished) {
  onFinished = finished;
  try {
    await api.exportStart(body);
  } catch (e) {
    toast(e.message, true);
    return;
  }
  $("result").hidden = true;
  $("result").classList.remove("danger-text");
  $("progress").hidden = false;
  poll();
}

$("cancel-btn").addEventListener("click", () => api.cancel().catch((e) => toast(e.message, true)));
$("close-progress").addEventListener("click", () => ($("progress").hidden = true));
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !$("progress").hidden && !$("close-progress").hidden) $("progress").hidden = true;
});
