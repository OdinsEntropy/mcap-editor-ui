// Time window: start / end inputs and a bar with two draggable handles (and a draggable range).
// Values are seconds after the first message; end = null means "until the end".
//
// The bar always shows the whole recording. The topic timelines show a view [v0, v1], marked on the bar as a
// rectangle: the wheel on the bar (or Ctrl+wheel on a topic timeline) zooms around the mouse, Shift+wheel or a
// sideways touchpad scroll pans, the zoom-out button resets. While dragging with Alt held, the handles stick to the cursor
// when they come within SNAP_PX of it. Click and drag are told apart on
// mouse-up (< 4 px movement = click): a click places the cursor.

import { $, fmtS, parseSeconds } from "./ui.js";

const SNAP_PX = 8;
let duration = 0;
let start = 0;
let end = null;
let onChange = () => {};
let v0 = 0, v1 = 0;                        // visible range of the bar (s)
let viewListeners = [];
let cursorTime = () => null;               // cursor position (s) for snapping, provided by the cursor module
let cursorHandler = { down() {}, move() {}, up() {} };

const endValue = () => (end == null ? duration : end);
const span = () => Math.max(v1 - v0, 1e-9);
const pct = (t) => (duration > 0 ? (t / duration) * 100 : 0);      // position on the (unzoomed) bar

/** Visible range of the bar and whether it is zoomed in. */
export const getView = () => ({ v0, v1, zoomed: v1 - v0 < duration * 0.999 });
export function onViewChange(f) {
  viewListeners.push(f);
}

function draw() {
  const a = pct(start), b = pct(endValue());
  const clip = (x) => Math.min(Math.max(x, 0), 100);
  $("tl-h0").style.left = `${a}%`;
  $("tl-h1").style.left = `${b}%`;
  $("tl-range").style.left = `${clip(a)}%`;
  $("tl-range").style.width = `${Math.max(clip(b) - clip(a), 0)}%`;
  $("window-chip").textContent = `${fmtS(endValue() - start)} s of ${fmtS(duration)} s`;
  const z = getView();
  $("tl-view").hidden = !z.zoomed;
  $("zoom-reset").disabled = !z.zoomed;
  $("tl-view").style.left = `${pct(v0)}%`;
  $("tl-view").style.width = `${pct(v1) - pct(v0)}%`;
  $("zoom-chip").textContent = `${(duration / span()).toFixed(1).replace(/\.0$/, "")}×`;
  $("zoom-chip").title = `Topic timelines show ${fmtS(v0)}–${fmtS(v1)} s. Wheel on the bar or Ctrl+wheel on a timeline: zoom; Shift+wheel or drag the rectangle on the bar: move`;
}

function ticks() {
  const el = $("tl-ticks");
  el.replaceChildren();
  for (const [v, label] of tickValues(0, duration)) {
    const s = document.createElement("span");
    s.style.left = `${pct(v)}%`;
    s.textContent = label;
    el.append(s);
  }
}

/** Tick positions and labels for the range [a, b]: about 8 round steps plus both ends. */
export function tickValues(a, b) {
  if (!(b > a)) return [];
  const raw = (b - a) / 8, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((x) => x >= raw);
  const digits = Math.max(0, -Math.floor(Math.log10(step)));
  const out = [[a, `${fmtS(a)} s`]];
  for (let v = Math.ceil(a / step + 0.4) * step; v < b - step * 0.4; v += step) out.push([v, `${v.toFixed(digits)} s`]);
  out.push([b, `${fmtS(b)} s`]);
  return out;
}

function setView(a, b) {
  const minSpan = Math.min(duration, Math.max(duration / 20000, 1e-3));
  let w = Math.min(Math.max(b - a, minSpan), duration);
  a = Math.min(Math.max(a, 0), duration - w);
  v0 = a;
  v1 = a + w;
  draw();
  viewListeners.forEach((f) => f(getView()));
}

function set(s, e, fromInput = false) {
  s = Math.min(Math.max(0, s), duration);
  e = e == null ? null : Math.min(Math.max(s, e), duration);
  start = s;
  end = e != null && e >= duration ? null : e;
  if (!fromInput) {
    $("win-start").value = fmtS(start);
    $("win-end").value = end == null ? "" : fmtS(end);
    $("win-start").classList.remove("invalid");
    $("win-end").classList.remove("invalid");
  }
  draw();
  onChange({ start, end });
}

/** Set up for a recording of ``dur`` seconds; the window starts as the whole recording, fully visible. */
export function setDuration(dur, callback) {
  duration = dur;
  onChange = callback;
  $("win-end").placeholder = fmtS(dur);
  v0 = 0;
  v1 = dur;
  ticks();
  setView(0, dur);
  set(0, null);
}

export const getWindow = () => ({ start, end });
export const getDuration = () => duration;

/** Set the window from code (undo / redo); reported like a user change, the caller decides what to record. */
export function setWindow(s, e) {
  set(s, e);
}

export function resetWindow() {
  set(0, null);
}

/** Handlers for the cursor (down / move / up with time in s and the pointer event) and its position for snapping. */
export function setCursorHandler(h, position) {
  cursorHandler = h;
  cursorTime = position;
}

/** Seconds after the first message at the horizontal position ``clientX`` of the bar (whole recording). */
export function timeAt(clientX) {
  const box = $("timeline").getBoundingClientRect();
  return Math.min(Math.max((clientX - box.left) / box.width, 0), 1) * duration;
}

/** Horizontal position (fraction 0–1) of time ``t`` on the bar. */
export const fractionOf = (t) => (duration > 0 ? t / duration : 0);

/** Zoom the view by ``f`` (< 1: in) around time ``t``; pan it by ``dt`` seconds. */
export function zoomView(t, f) {
  setView(t - (t - v0) * f, t + (v1 - t) * f);
}
export function panView(dt) {
  setView(v0 + dt, v1 + dt);
}

// typed values: checked, applied when valid, marked red otherwise
function fromInputs() {
  const s = parseSeconds($("win-start").value) ?? 0;
  const e = parseSeconds($("win-end").value);
  const okS = !isNaN(s) && s >= 0 && s < duration;
  const okE = e === null || (!isNaN(e) && e > s);
  $("win-start").classList.toggle("invalid", !okS);
  $("win-end").classList.toggle("invalid", !okE);
  if (okS && okE) set(s, e, true);
}
$("win-start").addEventListener("input", fromInputs);
$("win-end").addEventListener("input", fromInputs);
for (const id of ["win-start", "win-end"]) $(id).addEventListener("change", () => set(start, end));

/** ``t`` moved onto the cursor if it is within SNAP_PX of it (not with Alt). */
// snapping, only while Alt is held: onto the cursor and the ends of the window and of the zoom, within SNAP_PX on
// screen; never onto the ends of the thing being moved (``own``: "start", "end", "v0", "v1")
function snapPoints(own = []) {
  const zoomed = getView().zoomed;
  const pts = { cursor: cursorTime(), start, end: endValue(), v0: zoomed ? v0 : null, v1: zoomed ? v1 : null };
  return Object.entries(pts).filter(([k, v]) => v != null && !own.includes(k)).map(([, v]) => v);
}
const barSecPerPx = () => duration / $("timeline").getBoundingClientRect().width;
/** ``t`` moved onto the nearest snap point if Alt is held and one is within SNAP_PX (``secPerPx``: s per pixel). */
function stick(t, ev, own = [], secPerPx = barSecPerPx()) {
  if (!ev.altKey) return t;
  let best = t, bestD = SNAP_PX;
  for (const p of snapPoints(own)) {
    const d = Math.abs(p - t) / secPerPx;
    if (d <= bestD) [best, bestD] = [p, d];
  }
  return best;
}

// dragging: handles move one end, the range moves both (its nearer edge sticks to the cursor)
const picking = (ev) => ev.ctrlKey || ev.metaKey || ev.shiftKey;   // Ctrl / Shift press: range picking on the bar

function drag(kind) {
  return (ev) => {
    if (picking(ev) || pick?.pending) return;        // handled by the bar: zoom / window range
    ev.preventDefault();
    ev.stopPropagation();
    const box = $("timeline").getBoundingClientRect();
    const x0 = ev.clientX, s0 = start, e0 = endValue();
    const target = ev.currentTarget;
    let moved = false;
    try { target.setPointerCapture(ev.pointerId); } catch { /* synthetic event */ }
    target.classList.add("drag");
    const move = (m) => {
      if (!moved && Math.abs(m.clientX - x0) < 4) return;
      moved = true;
      const dt = ((m.clientX - x0) / box.width) * duration;
      const minLen = duration / 1e6;
      if (kind === "start") set(Math.min(stick(s0 + dt, m, ["start"]), e0 - minLen), e0);
      else if (kind === "end") set(s0, Math.max(stick(e0 + dt, m, ["end"]), s0 + minLen));
      else {
        let d = Math.min(Math.max(dt, -s0), duration - e0);
        const a = stick(s0 + d, m, ["start", "end"]), b = stick(e0 + d, m, ["start", "end"]);
        if (a !== s0 + d) d = a - s0;
        else if (b !== e0 + d) d = b - e0;
        set(s0 + d, e0 + d);
      }
    };
    const up = (u) => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
      target.classList.remove("drag");
      if (!moved && kind === "range") {          // a click inside the selected range places the cursor
        cursorHandler.down(timeAt(u.clientX), u);
        cursorHandler.up(timeAt(u.clientX), u);
      }
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", up);
  };
}
$("tl-h0").addEventListener("pointerdown", drag("start"));
$("tl-h1").addEventListener("pointerdown", drag("end"));
$("tl-range").addEventListener("pointerdown", drag("range"));

// press in the zoom rectangle: drag moves the zoomed range (pans the topic timelines); a click places the cursor
$("tl-view").addEventListener("pointerdown", (ev) => {
  if (picking(ev) || pick?.pending) return;
  ev.preventDefault();
  ev.stopPropagation();
  const el = $("tl-view");
  const px = $("timeline").getBoundingClientRect().width;
  const x0 = ev.clientX, a0 = v0, b0 = v1;
  let moved = false;
  try { el.setPointerCapture(ev.pointerId); } catch { /* synthetic event */ }
  el.classList.add("drag");
  const move = (m) => {
    if (!moved && Math.abs(m.clientX - x0) < 4) return;
    moved = true;
    let d = ((m.clientX - x0) / px) * duration;
    const a = stick(a0 + d, m, ["v0", "v1"]), b = stick(b0 + d, m, ["v0", "v1"]);   // Alt: an edge snaps
    if (a !== a0 + d) d = a - a0;
    else if (b !== b0 + d) d = b - b0;
    setView(a0 + d, b0 + d);
  };
  const up = (u) => {
    el.removeEventListener("pointermove", move);
    el.removeEventListener("pointerup", up);
    el.classList.remove("drag");
    if (!moved) {
      cursorHandler.down(timeAt(u.clientX), u);
      cursorHandler.up(timeAt(u.clientX), u);
    }
  };
  el.addEventListener("pointermove", move);
  el.addEventListener("pointerup", up);
});

// edges of the zoom rectangle: drag to widen / narrow the zoomed range
for (const edge of document.querySelectorAll(".tl-view-edge")) {
  edge.addEventListener("pointerdown", (ev) => {
    if (picking(ev) || pick?.pending) return;
    ev.preventDefault();
    ev.stopPropagation();
    const left = edge.classList.contains("l");
    try { edge.setPointerCapture(ev.pointerId); } catch { /* synthetic event */ }
    const move = (m) => {
      const t = stick(timeAt(m.clientX), m, [left ? "v0" : "v1"]);
      if (left) setView(Math.min(t, v1 - duration / 20000), v1);
      else setView(v0, Math.max(t, v0 + duration / 20000));
    };
    const up = () => {
      edge.removeEventListener("pointermove", move);
      edge.removeEventListener("pointerup", up);
    };
    edge.addEventListener("pointermove", move);
    edge.addEventListener("pointerup", up);
  });
}

// range picking, on the bar and in the topic timelines: Ctrl = zoom range, Shift = new window. Press + drag +
// release sets it at once; a click sets one end, the preview follows the mouse, and the next click (no key needed)
// sets the other end. Esc or a click anywhere else cancels. With Alt the ends snap: onto the cursor and the ends of
// window and zoom, and in a topic timeline otherwise onto the nearest message of that topic.
let pick = null;                                   // {mode: "zoom" | "cut", a, b, pending}
const pickListeners = [];
let messageSnap = null;                            // async (topic, t) => time of the nearest message, or null
export const getPick = () => pick;
export function onPickChange(f) {
  pickListeners.push(f);
}
export function setMessageSnap(f) {
  messageSnap = f;
}
/** Picking context of the bar (time and scale of a position). */
export const barPickCtx = { timeOf: (x) => timeAt(x), secPerPx: barSecPerPx, topic: null };

function pickChanged() {
  const el = $("tl-pick");
  el.hidden = !pick;
  if (pick) {
    el.classList.toggle("cut", pick.mode === "cut");
    el.style.left = `${pct(Math.min(pick.a, pick.b))}%`;
    el.style.width = `${Math.abs(pct(pick.b) - pct(pick.a))}%`;
  }
  pickListeners.forEach((f) => f(pick));
}
function applyPick(mode, a, b) {
  const lo = Math.max(0, Math.min(a, b)), hi = Math.min(duration, Math.max(a, b));
  if (hi - lo < duration / 1e6) return;                // a range of (almost) nothing: ignore
  if (mode === "zoom") setView(lo, hi);
  else set(lo, hi >= duration ? null : hi);           // a window change like any other (undo)
}
/** Time at the press / move ``ev`` in ``ctx``, snapped if Alt is held. */
async function pickTime(ev, ctx) {
  const t = ctx.timeOf(ev.clientX);
  if (!ev.altKey) return t;
  const s = stick(t, ev, [], ctx.secPerPx());
  if (s !== t || !ctx.topic || !messageSnap) return s;
  return (await messageSnap(ctx.topic, t)) ?? t;
}
let endToken = 0;
async function setPickEnd(ev, ctx) {
  const token = ++endToken;
  const t = await pickTime(ev, ctx);
  if (token !== endToken || !pick) return;            // a newer move won
  pick.b = t;
  pickChanged();
}
/** Press ``ev`` on ``el`` (bar or topic timeline): finish a pending range, or start one (Ctrl / Shift held). */
export async function startPick(ev, el, ctx) {
  if (pick?.pending) {                                 // second click: the other end, no key needed
    const p = pick;
    const t = await pickTime(ev, ctx);
    if (pick !== p) return;
    pick = null;
    pickChanged();
    applyPick(p.mode, p.a, t);
    return;
  }
  const mode = ev.shiftKey ? "cut" : "zoom";
  const t0 = ctx.timeOf(ev.clientX);
  const p = { mode, a: t0, b: t0, pending: false };
  pick = p;
  pickChanged();
  pickTime(ev, ctx).then((a) => {                      // snapped start (Alt)
    if (pick === p) {
      if (p.b === p.a) p.b = a;
      p.a = a;
      pickChanged();
    }
  });
  try { el.setPointerCapture(ev.pointerId); } catch { /* synthetic event */ }
  const x0 = ev.clientX;
  let moved = false;
  const move = (m) => {
    if (!moved && Math.abs(m.clientX - x0) < 4) return;
    moved = true;
    setPickEnd(m, ctx);
  };
  const up = async (u) => {
    el.removeEventListener("pointermove", move);
    el.removeEventListener("pointerup", up);
    if (pick !== p) return;
    if (moved) {
      const b = await pickTime(u, ctx);
      if (pick !== p) return;
      pick = null;
      pickChanged();
      applyPick(mode, p.a, b);
    } else {
      p.pending = true;                                // wait for the second click
      pickChanged();
    }
  };
  el.addEventListener("pointermove", move);
  el.addEventListener("pointerup", up);
}
/** Mouse move ``ev`` over the bar or a timeline (``ctx``): a pending range follows it. */
export function pickHover(ev, ctx) {
  if (pick?.pending) setPickEnd(ev, ctx);
}
export function cancelPick() {
  if (pick) {
    pick = null;
    pickChanged();
  }
}
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && pick) cancelPick();
});
// a press anywhere outside the bar and the topic timelines cancels a pending range
document.addEventListener("pointerdown", (ev) => {
  if (pick?.pending && !(ev.target instanceof Element && ev.target.closest("#timeline, #topic-rows tr.detail canvas"))) cancelPick();
}, true);

// press on the bar outside handles, range and zoom rectangle: place the cursor and drag it
$("timeline").addEventListener("pointerdown", (ev) => {
  if (duration <= 0) return;
  ev.preventDefault();
  if (picking(ev) || pick?.pending) {
    startPick(ev, $("timeline"), barPickCtx);
    return;
  }
  cancelPick();
  const el = $("timeline");
  try { el.setPointerCapture(ev.pointerId); } catch { /* synthetic event: the click still places the cursor */ }
  cursorHandler.down(timeAt(ev.clientX), ev);
  const move = (m) => cursorHandler.move(timeAt(m.clientX), m);
  const up = (u) => {
    el.removeEventListener("pointermove", move);
    el.removeEventListener("pointerup", up);
    cursorHandler.up(timeAt(u.clientX), u);
  };
  el.addEventListener("pointermove", move);
  el.addEventListener("pointerup", up);
});

// zoom and pan the topic timelines with the wheel on the bar (around the mouse position on the bar)
$("timeline").addEventListener("wheel", (ev) => {
  if (duration <= 0) return;
  ev.preventDefault();
  const horizontal = ev.shiftKey || Math.abs(ev.deltaX) > Math.abs(ev.deltaY);
  const px = $("timeline").getBoundingClientRect().width;
  if (horizontal) {
    const d = (ev.shiftKey ? ev.deltaY : ev.deltaX) || ev.deltaY;
    panView((d / px) * span());
    return;
  }
  zoomView(timeAt(ev.clientX), wheelFactor(ev));
}, { passive: false });

/** Zoom factor of one wheel event (< 1 zooms in). */
export const wheelFactor = (ev) => Math.exp(Math.max(-1, Math.min(1, ev.deltaY * (ev.deltaMode === 1 ? 0.05 : 0.0015))));
$("zoom-reset").addEventListener("click", () => setView(0, duration));
