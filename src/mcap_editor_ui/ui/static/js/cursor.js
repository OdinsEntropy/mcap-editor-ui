// Cursor, bin width and the topic timelines.
// The cursor is a time (ns after the first message), shown on the big bar and in every open topic timeline and as
// a number in the Cursor field (type a value to place it exactly). A click places it exactly there. Only Alt+click
// in a topic timeline snaps it to that topic's nearest message; while Alt is held over a timeline the preview line
// already shows that message. ← / → step through the messages of the last used topic (highlighted in the table). A grey preview line (one position for the bar and all timelines) shows where a click would
// put it. On the big bar the cursor is placed by a click; pressing in the zoom rectangle and dragging pans the view.
// Topic timelines (open rows of the topic table) show messages per bin over the zoomed view. The bin width is set
// automatically (Auto, the default: about 400 bins across the view, following the zoom); a typed width is kept
// while zooming, so zooming in makes the bars wider; the reset returns to whole recording / 400.

import { api } from "./api.js";
import { cancelPick, fractionOf, getDuration, getPick, getView, onPickChange, onViewChange, panView, pickHover, setCursorHandler, startPick,
  tickValues, timeAt, wheelFactor, zoomView } from "./timeline.js";
import { onDetailRender, redrawDetails, setFocus } from "./topics.js";
import { $, debounce, fmtS, parseSeconds, toast } from "./ui.js";

const BINS = 400;                    // bins across the view in Auto mode and when a file opens
const MAX_BINS = 4000;               // more bins than this across the view are merged (too thin to see anyway)
let info = null;
let cursorNs = null;                 // cursor time, ns after the first message
let focus = null;                    // cursor topic
let binW = 1;                        // bin width (s)
let auto = false;
let data = null;                     // {key, t0, w, counts: {topic: [...]}} for the view
let hover = null;                    // preview time (s) under the mouse, shared by the bar and all timelines
let details = [];                    // [{topic, canvas}] of the open rows

const durNs = () => Math.round(getDuration() * 1e9);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** New recording: no cursor, no cursor topic, bin width from the whole recording. */
export function resetCursor(newInfo) {
  info = newInfo;
  cursorNs = null;
  focus = null;
  data = null;
  hover = null;
  auto = true;                                     // default: the bin width follows the zoom
  setBinWidth(getDuration() / BINS);
  drawCursor();
}

// ---- cursor on the bar and in the Cursor field ----
function place(el, t) {
  const f = fractionOf(t);
  el.hidden = !(f >= 0 && f <= 1);
  el.style.left = `${f * 100}%`;
  return f;
}

function drawCursor() {
  const has = cursorNs != null && info;
  if (document.activeElement !== $("cur-time")) $("cur-time").value = has ? fmtS(cursorNs / 1e9) : "";
  if (!has) {
    $("cursor").hidden = true;
    redrawDetails();
    return;
  }
  const t = cursorNs / 1e9;
  const f = place($("cursor"), t);
  $("cursor-label").textContent = `${fmtS(t)} s`;
  $("cursor-label").classList.toggle("flip", f > 0.85);          // keep the label inside the bar at the right end
  redrawDetails();
}

function setCursor(ns) {
  cursorNs = Math.min(Math.max(Math.round(ns), 0), durNs());
  drawCursor();
}

let snapToken = 0;
/** Move the cursor onto the previous (-1) / next (1) / nearest (0) message of ``topic`` (default: cursor topic). */
async function snap(ns, dir, free = false, topic = focus) {
  if (free || !topic || !info) {
    setCursor(dir === 0 ? ns : ns + dir * binW * 1e9);
    return;
  }
  const token = ++snapToken;
  try {
    const r = await api.step(info.path, topic, Math.round(ns), dir);
    if (token !== snapToken) return;                             // a newer move won
    if (r.t != null) setCursor(r.t);
    else if (dir === 0) setCursor(ns);                           // no index: no snapping
  } catch (e) {
    toast(e.message, true);
  }
}

setCursorHandler({
  down: (t) => setCursor(t * 1e9),
  move: (t) => setCursor(t * 1e9),
  up: (t) => setCursor(t * 1e9),                                 // the big bar never snaps
}, () => (cursorNs == null ? null : cursorNs / 1e9));

// typed cursor time: placed exactly there (no snapping)
$("cur-time").addEventListener("input", () => {
  const v = parseSeconds($("cur-time").value);
  const ok = v === null || (!isNaN(v) && v >= 0 && v <= getDuration());
  $("cur-time").classList.toggle("invalid", !ok);
  if (ok && v !== null) setCursor(v * 1e9);
});
$("cur-time").addEventListener("change", drawCursor);

// ← / → step through the messages of the cursor topic (not while typing in a field or a dialog is open)
document.addEventListener("keydown", (ev) => {
  if (cursorNs == null || document.querySelector("dialog[open]") || ev.ctrlKey || ev.metaKey) return;
  if (ev.target instanceof Element && ev.target.matches("input[type=text], input[type=search]")) return;
  if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") {
    ev.preventDefault();
    snap(cursorNs, ev.key === "ArrowLeft" ? -1 : 1);
  }
});

// grey preview line with its time: one position for the bar and all topic timelines (hovering one shows it in all)
function setHover(t) {
  hover = t;
  const el = $("preview");
  if (t == null || !info) {
    el.hidden = true;
  } else {
    const f = place(el, t);
    $("preview-label").textContent = `${fmtS(t)} s`;
    $("preview-label").classList.toggle("flip", f > 0.85);
  }
  redrawDetails();
}
$("timeline-col").addEventListener("pointermove", (ev) => {
  const box = $("timeline").getBoundingClientRect();
  const inside = info && ev.clientX >= box.left && ev.clientX <= box.right;
  setHover(inside ? timeAt(ev.clientX) : null);
  if (inside) pickHover(timeAt(ev.clientX));
});
$("timeline-col").addEventListener("pointerleave", () => setHover(null));

// ---- bin width ----
function setBinWidth(w) {
  binW = Math.max(w, 1e-6);
  if (document.activeElement !== $("bin-width")) $("bin-width").value = String(Number(binW.toPrecision(3)));
  $("bin-auto").classList.toggle("on", auto);
  loadData();
}
$("bin-width").addEventListener("input", () => {
  const v = parseSeconds($("bin-width").value);
  const ok = v > 0;
  $("bin-width").classList.toggle("invalid", !ok);
  if (!ok) return;
  auto = false;
  setBinWidth(v);
});
$("bin-width").addEventListener("change", () => setBinWidth(binW));
$("bin-auto").addEventListener("click", () => {
  auto = !auto;
  const { v0, v1 } = getView();
  setBinWidth(auto ? (v1 - v0) / BINS : binW);
});
$("bin-reset").addEventListener("click", () => {     // starting value (whole recording / 400), Auto off
  auto = false;
  setBinWidth(getDuration() / BINS);
});

// ---- topic timelines ----
/** Bin grid for the view: aligned to multiples of the bin width; merged bins if there would be too many. */
function grid() {
  const { v0, v1 } = getView();
  let w = binW;
  if ((v1 - v0) / w > MAX_BINS) w = (v1 - v0) / MAX_BINS;
  const t0 = Math.floor(v0 / w) * w;
  return { t0, w, n: Math.max(1, Math.ceil((v1 - t0) / w - 1e-9)) };   // epsilon: 400 × (d / 400) must stay 400 bins
}

const loadData = debounce(async () => {
  if (!info?.activity) return;
  const topics = details.map((d) => d.topic);
  const g = grid();
  const key = `${g.t0}|${g.w}|${g.n}|${topics.join(",")}`;
  if (data?.key === key) return;
  const whole = Math.abs(g.w - getDuration() / BINS) < 1e-12 && g.t0 === 0 && g.n === BINS;
  if (whole) {                                     // the histogram from opening the file fits exactly
    data = { key, t0: 0, w: g.w, counts: info.activity };
    redrawDetails();
    return;
  }
  if (!topics.length) return;
  try {
    const counts = await api.activity({ path: info.path, topics, t0: Math.round(g.t0 * 1e9),
                                        t1: Math.round((g.t0 + g.n * g.w) * 1e9), bins: g.n });
    data = { key, t0: g.t0, w: g.w, counts };
    redrawDetails();
  } catch (e) {
    toast(e.message, true);
  }
}, 120);

function drawDetail({ topic, canvas }) {
  const w = canvas.clientWidth;
  if (!w) return;
  const H = 44, BAR = 24;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(H * dpr);
  const g = canvas.getContext("2d");
  g.scale(dpr, dpr);
  const { v0, v1 } = getView();
  const x = (t) => ((t - v0) / (v1 - v0)) * w;
  g.fillStyle = css("--border");
  g.fillRect(0, BAR, w, 1);                                    // baseline: an empty bin shows as a gap
  if (!info?.activity) {
    g.fillStyle = css("--muted");
    g.font = `12px ${getComputedStyle(document.body).fontFamily}`;
    g.fillText("No timeline: the file has no message index", 0, 16);
  }
  const counts = data?.counts?.[topic];
  if (counts) {
    const max = Math.max(1, ...counts);
    g.fillStyle = topic === focus ? css("--accent") : css("--muted");
    g.globalAlpha = topic === focus ? 0.9 : 0.6;
    counts.forEach((c, i) => {
      if (!c) return;
      const a = x(data.t0 + i * data.w), b = x(data.t0 + (i + 1) * data.w);
      if (b < 0 || a > w) return;
      const h = Math.max(2, (c / max) * (BAR - 4));
      g.fillRect(a, BAR - h, Math.max(b - a - 0.4, 0.8), h);
    });
    g.globalAlpha = 1;
  }
  // ticks of the view
  g.fillStyle = css("--muted");
  g.font = `11px ${getComputedStyle(document.body).fontFamily}`;
  const ticks = tickValues(v0, v1);
  ticks.forEach(([t, label], i) => {
    const tx = x(t);
    g.fillRect(Math.min(Math.max(tx, 0), w - 1), BAR + 1, 1, 3);
    g.textAlign = i === 0 ? "left" : i === ticks.length - 1 ? "right" : "center";
    g.fillText(label, Math.min(Math.max(tx, 0), w), H - 4);
  });
  g.textAlign = "left";
  // range being picked (Ctrl: zoom, dashed; Shift: new window, solid)
  const pk = getPick();
  if (pk) {
    const a = Math.max(0, x(Math.min(pk.a, pk.b))), b = Math.min(w, x(Math.max(pk.a, pk.b)));
    if (b >= a && pk.mode === "zoom") {                       // zoom: dashed frame like the zoom rectangle
      g.fillStyle = css("--accent-soft");
      g.fillRect(a, 0, b - a, BAR + 4);
      g.strokeStyle = css("--accent");
      g.lineWidth = 1.5;
      g.setLineDash([4, 3]);
      g.strokeRect(a + 0.75, 0.75, Math.max(b - a - 1.5, 0), BAR + 2.5);
      g.setLineDash([]);
    } else if (b >= a) {                                       // new window: band with start / end handle markers
      g.globalAlpha = 0.8;
      g.fillStyle = css("--accent-soft");
      g.fillRect(a, 0, b - a, BAR + 4);
      const handle = (hx) => {                                 // pill like the handles on the bar
        if (hx < -6 || hx > w + 6) return;
        const hw = 10, hh = 18, hy = (BAR + 4 - hh) / 2;
        g.beginPath();
        g.roundRect(hx - hw / 2, hy, hw, hh, hw / 2);
        g.fillStyle = css("--surface");
        g.fill();
        g.lineWidth = 2;
        g.strokeStyle = css("--accent");
        g.stroke();
      };
      g.fillStyle = css("--accent");
      g.fillRect(a, 0, 1.5, BAR + 4);
      g.fillRect(b - 1.5, 0, 1.5, BAR + 4);
      handle(x(Math.min(pk.a, pk.b)));
      handle(x(Math.max(pk.a, pk.b)));
      g.globalAlpha = 1;
    }
  }
  // preview and cursor lines
  if (hover != null) {
    g.fillStyle = css("--muted");
    g.globalAlpha = 0.7;
    g.fillRect(x(hover), 0, 1.5, BAR + 4);
    g.globalAlpha = 1;
  }
  if (cursorNs != null) {
    const cx = x(cursorNs / 1e9);
    if (cx >= 0 && cx <= w) {
      g.fillStyle = css("--accent");
      g.fillRect(cx, 0, 1.5, BAR + 4);
    }
  }
}

onDetailRender((list) => {
  const before = details.map((d) => d.topic).join(",");
  details = list;
  list.forEach(drawDetail);
  for (const d of list) wireDetail(d);
  if (details.map((d) => d.topic).join(",") !== before) loadData();
});

// preview over a topic timeline: the mouse time, or with Alt held the message the cursor would snap to
let over = null;                     // {topic, t} under the mouse in a topic timeline
let busy = false, again = false;        // at most one snap request at a time while the mouse moves
async function hoverAt(alt) {
  if (!over) return;
  if (!alt || !info) {
    setHover(over.t);
    return;
  }
  if (busy) {
    again = true;
    return;
  }
  busy = true;
  const { topic, t } = over;
  try {
    const r = await api.step(info.path, topic, Math.round(t * 1e9), 0);
    if (over && !again) setHover(r.t != null ? r.t / 1e9 : t);
  } catch {
    setHover(t);
  } finally {
    busy = false;
    if (again) {
      again = false;
      hoverAt(true);
    }
  }
}
for (const type of ["keydown", "keyup"]) {
  document.addEventListener(type, (ev) => {
    if (ev.key !== "Alt" || !info) return;
    ev.preventDefault();                       // Alt is the snap key here; Firefox would show its menu bar on release
    if (over) hoverAt(type === "keydown");
  });
}

/** Mouse handling of one topic timeline (canvases are rebuilt with the table, so they are wired each time). */
function wireDetail({ topic, canvas }) {
  if (canvas.dataset.wired) return;
  canvas.dataset.wired = "1";
  const timeIn = (clientX) => {
    const b = canvas.getBoundingClientRect();
    const { v0, v1 } = getView();
    return v0 + Math.min(Math.max((clientX - b.left) / b.width, 0), 1) * (v1 - v0);
  };
  canvas.addEventListener("click", (ev) => ev.stopPropagation());
  canvas.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    if (ev.ctrlKey || ev.metaKey || ev.shiftKey) {               // Ctrl: zoom range, Shift: new window
      startPick(ev, canvas, timeIn);
      return;
    }
    cancelPick();
    try { canvas.setPointerCapture(ev.pointerId); } catch { /* synthetic event */ }
    setCursor(timeIn(ev.clientX) * 1e9);
    const move = (m) => setCursor(timeIn(m.clientX) * 1e9);
    const up = (u) => {
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up);
      const t = timeIn(u.clientX) * 1e9;                        // before setFocus: it rebuilds the table and this canvas
      focus = topic;                                             // this topic becomes the cursor topic
      setFocus(topic);
      if (u.altKey) snap(t, 0, false, topic);                    // Alt: onto the nearest message of this topic
      else setCursor(t);
    };
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up);
  });
  canvas.addEventListener("pointermove", (ev) => {
    over = { topic, t: timeIn(ev.clientX) };
    hoverAt(ev.altKey);
    pickHover(over.t);
  });
  canvas.addEventListener("pointerleave", () => {
    over = null;
    setHover(null);
  });
  // Ctrl+wheel zooms, Shift+wheel / sideways scroll pans; a plain wheel scrolls the topic list
  canvas.addEventListener("wheel", (ev) => {
    const horizontal = ev.shiftKey || Math.abs(ev.deltaX) > Math.abs(ev.deltaY);
    if (!ev.ctrlKey && !horizontal) return;
    ev.preventDefault();
    const { v0, v1 } = getView();
    if (horizontal) {
      const d = (ev.shiftKey ? ev.deltaY : ev.deltaX) || ev.deltaY;
      panView((d / canvas.getBoundingClientRect().width) * (v1 - v0));
    } else {
      zoomView(timeIn(ev.clientX), wheelFactor(ev));
    }
  }, { passive: false });
}

onPickChange(() => redrawDetails());

onViewChange(({ v0, v1 }) => {
  if (auto) setBinWidth((v1 - v0) / BINS);
  else loadData();
  redrawDetails();
});

new MutationObserver(() => redrawDetails()).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-accent"] });
