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
function stick(t, ev) {
  const c = cursorTime();
  if (c == null || !ev.altKey) return t;                    // sticking only while Alt is held
  const px = $("timeline").getBoundingClientRect().width;
  return Math.abs(t - c) / duration * px <= SNAP_PX ? c : t;
}

// dragging: handles move one end, the range moves both (its nearer edge sticks to the cursor)
function drag(kind) {
  return (ev) => {
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
      if (kind === "start") set(Math.min(stick(s0 + dt, m), e0 - minLen), e0);
      else if (kind === "end") set(s0, Math.max(stick(e0 + dt, m), s0 + minLen));
      else {
        let d = Math.min(Math.max(dt, -s0), duration - e0);
        const a = stick(s0 + d, m), b = stick(e0 + d, m);
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
    const d = ((m.clientX - x0) / px) * duration;
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

// press on the bar outside handles, range and zoom rectangle: place the cursor and drag it
$("timeline").addEventListener("pointerdown", (ev) => {
  if (duration <= 0) return;
  ev.preventDefault();
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
