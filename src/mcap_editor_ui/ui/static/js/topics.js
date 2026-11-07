// Topic table: one row per topic with the same numbers as `mcap-editor info`.
// Export selection: the row checkbox, the header checkbox (all / none of the shown rows) and Invert.
// Row selection (to act on several rows): Ctrl+click adds / removes a row, Shift+click selects the range from the
// last clicked row, Esc clears it. A checkbox or chevron click on a selected row applies to all selected rows, and
// dragging one of them moves all of them (combined in their current order). A plain click opens / closes a row:
// a detail row below shows the topic's timeline (drawn by cursor.js); opening does not change the active topic.
// Filters (chips) and the search only hide rows. Sorting in the header has two layers: an optional grouping
// (selected first / open first, ▲ first, ▼ last, a third click turns it off) and always exactly one order (own
// order, topic, messages, rate, size, share, schema; a click on the active one turns its direction around; own
// order has no direction). Search: plain text matches anywhere; * stands for any characters (no regex).

import { $ } from "./ui.js";

const WIDTH_KEY = "mcap-editor-col-widths-v3";       // only the columns the user dragged
let topics = [];
let selected = new Set();                  // export selection
let rowSel = new Set();                    // row selection (Ctrl / Shift + click)
let anchor = null;                         // last clicked row: start of a Shift range
let onChange = () => {};
const filters = { latched: false, open: false, selected: false };
let expanded = new Set();                  // opened topics: their timeline is shown below the row
let onDetail = () => {};                   // draws the timelines: callback([{topic, canvas}])
let focusTopic = null;                     // active topic (last clicked timeline), highlighted in the table

/** Search text → test function; "*" = any characters, matched anywhere in topic or schema, case-insensitive. */
function matcher(text) {
  const q = text.trim().toLowerCase();
  if (!q) return () => true;
  if (!q.includes("*")) return (s) => s.toLowerCase().includes(q);
  const esc = (part) => part.replace(/[.+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(q.split("*").map(esc).join(".*"));
  return (s) => re.test(s.toLowerCase());
}

const GROUP_KEYS = new Set(["selected", "open"]);
let group = null;                          // optional grouping {key, dir}
let order = { key: "topic", dir: 1 };      // always one order {key, dir}
let manualOrder = null;                    // own order (dragged by hand), saved per recording
let recPath = null;
const orderKey = () => `mcap-editor-order:${recPath}`;
const SORT = {
  topic: (t) => t.topic, schema: (t) => t.schema, count: (t) => t.count ?? -1, rate: (t) => t.rate ?? -1,
  size: (t) => t.size ?? -1, share: (t) => t.size ?? -1,
  open: (t) => (expanded.has(t.topic) ? 0 : 1), selected: (t) => (selected.has(t.topic) ? 0 : 1),
  manual: (t) => manualIndex.get(t.topic) ?? 1e9,
};
let manualIndex = new Map();

function shown() {
  const m = matcher($("topic-search").value);
  const list = topics.filter((t) => (!filters.latched || t.latched) && (!filters.open || expanded.has(t.topic))
    && (!filters.selected || selected.has(t.topic)) && (m(t.topic) || m(t.schema)));
  manualIndex = new Map((manualOrder || []).map((t, i) => [t, i]));
  const keys = [...(group ? [group] : []), order];
  list.sort((a, b) => {
    for (const { key, dir } of keys) {
      const x = SORT[key](a), y = SORT[key](b);
      const c = typeof x === "string" ? x.localeCompare(y) : x - y;
      if (c) return dir * c;
    }
    return 0;
  });
  return list;
}

/** Header click on column ``k``: grouping columns cycle ▲ → ▼ → off; order columns become the order or turn their
 * direction around (own order: only becomes the order). */
function sortBy(k) {
  if (GROUP_KEYS.has(k)) {
    if (group?.key !== k) group = { key: k, dir: 1 };
    else if (group.dir === 1) group.dir = -1;
    else group = null;
  } else if (order.key !== k) {
    order = { key: k, dir: 1 };
  } else if (k !== "manual") {
    order.dir = -order.dir;
  }
  sortChanged();
}

for (const th of document.querySelectorAll("#topic-table th[data-sort]")) {
  th.addEventListener("click", (ev) => {
    if (ev.target.closest(".head-check, .head-chev")) return;   // select-all / open-all are not sort clicks
    if (th.dataset.sort === "manual" && !manualOrder) return;
    sortBy(th.dataset.sort);
  });
}

function sortChanged() {
  for (const h of document.querySelectorAll("#topic-table th[data-sort]")) {
    const k = h.dataset.sort;
    const a = h.querySelector(".arrow");
    if (group?.key === k) a.textContent = group.dir === 1 ? "▲" : "▼";
    else if (order.key === k) a.textContent = k === "manual" ? "●" : order.dir === 1 ? "▲" : "▼";
    else a.textContent = "";
  }
  for (const [k, on] of Object.entries(filters)) $(`filter-${k}`).classList.toggle("on", on);
  render();
}

for (const k of Object.keys(filters)) {
  $(`filter-${k}`).addEventListener("click", () => {
    filters[k] = !filters[k];
    sortChanged();
  });
}
// reset of filters, search and sorting; the selection stays
$("list-reset").addEventListener("click", () => {
  for (const k of Object.keys(filters)) filters[k] = false;
  $("topic-search").value = "";
  group = null;
  order = { key: "topic", dir: 1 };
  sortChanged();
});
// selection of the shown rows: header checkbox (all / none) and Invert
$("head-check").addEventListener("click", (ev) => {
  ev.stopPropagation();
  const list = shown().map((t) => t.topic);
  const all = list.length > 0 && list.every((t) => selected.has(t));
  list.forEach((t) => (all ? selected.delete(t) : selected.add(t)));
  changed();
});
// header chevron: open all shown topics, or close them all if they are all open
$("head-chev").addEventListener("click", (ev) => {
  ev.stopPropagation();
  const list = shown().map((t) => t.topic);
  const allOpen = list.length > 0 && list.every((t) => expanded.has(t));
  list.forEach((t) => (allOpen ? expanded.delete(t) : expanded.add(t)));
  render();
});
$("sel-invert").addEventListener("click", () => {
  shown().forEach((t) => (selected.has(t.topic) ? selected.delete(t.topic) : selected.add(t.topic)));
  changed();
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && rowSel.size && !document.querySelector("dialog[open]")) {
    rowSel.clear();
    render();
  }
});

function render() {
  const body = $("topic-rows");
  body.replaceChildren();
  const list = shown();
  const details = [];
  for (const t of list) {
    const tr = document.createElement("tr");
    const open = expanded.has(t.topic);
    tr.classList.toggle("on", selected.has(t.topic));
    tr.classList.toggle("focus", t.topic === focusTopic);
    tr.classList.toggle("open", open);
    tr.classList.toggle("rowsel", rowSel.has(t.topic));
    const rate = t.rate == null ? "" : `${t.rate.toFixed(2)} Hz`;
    tr.dataset.topic = t.topic;
    tr.innerHTML = `<td class="c-grip"><span class="drag-grip" title="Drag to change the order (own order)"><svg viewBox="0 0 10 16" class="grip-dots"><circle cx="3" cy="3" r="1.3"/><circle cx="7" cy="3" r="1.3"/><circle cx="3" cy="8" r="1.3"/><circle cx="7" cy="8" r="1.3"/><circle cx="3" cy="13" r="1.3"/><circle cx="7" cy="13" r="1.3"/></svg></span></td>
      <td class="c-check" title="Select for the export (on a selected row: for all selected rows)"><div class="check"></div></td>
      <td class="c-open"><svg viewBox="0 0 12 12" class="chev"><path d="M4.5 2.5 8 6l-3.5 3.5"/></svg></td>
      <td class="name"><span class="name-in"><span class="tname"></span></span></td>
      <td class="r">${t.count ?? "?"}</td><td class="r">${rate}</td><td class="r">${t.size_text}</td>
      <td class="r">${t.share_text}</td><td class="schema"></td>`;
    const name = tr.querySelector(".tname");
    name.textContent = t.topic;
    tr.querySelector(".name").title = `${t.topic} – click: ${open ? "close" : "open"} its timeline; Ctrl+click: select the row, Shift+click: range`
      + (t.topic === focusTopic ? "\nActive topic: ← / → step the cursor through its messages" : "");
    if (t.latched) {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = "Latched";
      tag.title = "Published with transient local durability (ROS 2) or latching (ROS 1), e.g. /tf_static";
      tr.querySelector(".name-in").append(tag);
    }
    tr.querySelector(".schema").textContent = t.schema;
    tr.querySelector(".schema").title = t.schema;
    tr.querySelector(".c-check").addEventListener("click", (ev) => { ev.stopPropagation(); check(t.topic); });
    tr.addEventListener("click", (ev) => rowClick(t.topic, ev));
    tr.querySelector(".drag-grip").addEventListener("pointerdown", (ev) => dragRow(ev, t.topic));
    tr.querySelector(".drag-grip").addEventListener("click", (ev) => ev.stopPropagation());
    body.append(tr);
    if (open) {
      const dr = document.createElement("tr");
      dr.className = "detail";
      dr.dataset.topic = t.topic;
      dr.innerHTML = `<td colspan="${cols.length}"><div class="detail-box"><canvas></canvas></div></td>`;
      body.append(dr);
      details.push({ topic: t.topic, canvas: dr.querySelector("canvas") });
    }
  }
  $("topics-chip").textContent = `${selected.size} of ${topics.length} selected` + (list.length < topics.length ? ` · ${list.length} shown` : "");
  const n = list.filter((t) => selected.has(t.topic)).length;
  $("head-check").classList.toggle("on", n > 0 && n === list.length);
  $("head-check").classList.toggle("some", n > 0 && n < list.length);
  $("head-check").title = n === list.length && n ? "Deselect the shown topics" : "Select all shown topics";
  const allOpen = list.length > 0 && list.every((t) => expanded.has(t.topic));
  $("head-chev").classList.toggle("open", allOpen);
  $("head-chev").title = allOpen ? "Close all shown topics" : "Open all shown topics";
  sizeDetails();
  onDetail(details);
}

/** Timelines span the visible width of the table pane (they stay in view when the table scrolls sideways). */
function sizeDetails() {
  for (const box of document.querySelectorAll(".detail-box")) box.style.width = `${wrap.clientWidth}px`;
}

// own order: drag a row by its grip. The row (with its open timeline) lifts and follows the pointer, the other rows
// slide aside to show where it will go, and on release it settles into its new place.
/** Bottom edge of the table header as seen: the header cells stick to the top of the pane (the thead box itself
 * scrolls away with the list, so it is not the right reference). */
const headerBottom = () => table.querySelector("th").getBoundingClientRect().bottom;

function dragRow(ev, topic) {
  ev.preventDefault();
  ev.stopPropagation();
  const grip = ev.currentTarget;
  try { grip.setPointerCapture(ev.pointerId); } catch { /* synthetic event */ }
  const s0 = wrap.scrollTop;
  // positions in list coordinates (viewport y + scroll), independent of scrolling
  const groups = [...document.querySelectorAll("#topic-rows tr:not(.detail)")].map((tr) => {
    const els = [tr];
    if (tr.nextElementSibling?.classList.contains("detail")) els.push(tr.nextElementSibling);
    const top = tr.getBoundingClientRect().top, bottom = els[els.length - 1].getBoundingClientRect().bottom;
    return { topic: tr.dataset.topic, els, top: top + s0, h: bottom - top };
  });
  // the block: all selected rows if the grabbed one is among several, in their current order
  const moving = new Set(targets(topic));
  const block = groups.filter((g) => moving.has(g.topic));
  const others = groups.filter((g) => !moving.has(g.topic));
  const blockH = block.reduce((a, g) => a + g.h, 0);
  const listTop = groups[0].top;
  const grabbed = block.find((g) => g.topic === topic);
  const grabOffset = block.slice(0, block.indexOf(grabbed)).reduce((a, g) => a + g.h, 0);   // grabbed row inside the block
  const float = liftCopy(block.flatMap((g) => g.els));
  const top0 = grabbed.top - s0 - grabOffset, y0 = ev.clientY;    // copy placed so the grabbed row stays under the pointer
  // compact layout of the other rows (block taken out): their middles decide the target
  const compactTop = [];
  others.reduce((y, g) => (compactTop.push(y), y + g.h), listTop);
  const startIndex = others.filter((g) => g.top < grabbed.top).length;
  let lastY = ev.clientY, target = startIndex, dragging = true;
  document.body.classList.add("row-dragging");
  block.forEach((g) => g.els.forEach((el) => el.classList.add("placeholder")));
  others.forEach((g) => g.els.forEach((el) => el.classList.add("sliding")));

  const update = () => {
    const box = wrap.getBoundingClientRect();
    // the copy follows the pointer, but stays between the bottom of the table header and the bottom of the pane
    const top = Math.min(Math.max(top0 + lastY - y0, headerBottom()), box.bottom - Math.min(blockH, box.height));
    float.style.top = `${top}px`;
    // the block starts in the slot of the grabbed row between the other rows; the drag distance moves it from
    // there. The leading edge decides: moving up its top, moving down its bottom; a row makes room as soon as that
    // edge passes the row's middle (rows are measured with the block in its starting slot)
    const d = top + wrap.scrollTop - (top0 + s0);
    const slot = startIndex < others.length ? compactTop[startIndex] : listTop + others.reduce((a, g) => a + g.h, 0);
    const edge = d < 0 ? slot + d : slot + d + blockH;
    target = others.filter((g, i) => compactTop[i] + (i >= startIndex ? blockH : 0) + g.h / 2 < edge).length;
    // every other row goes to its place in the compact layout with a gap of the block's height at the target
    others.forEach((g, i) => {
      const shift = compactTop[i] + (i >= target ? blockH : 0) - g.top;
      g.els.forEach((el) => (el.style.transform = Math.abs(shift) > 0.5 ? `translateY(${shift}px)` : ""));
    });
  };
  update();                                        // scattered selected rows close up at once
  // near the top (below the header) or bottom edge the list scrolls along
  const autoScroll = () => {
    if (!dragging) return;
    const box = wrap.getBoundingClientRect();
    const edge = 36, hb = headerBottom();
    let v = 0;
    if (lastY < hb + edge) v = -Math.ceil((hb + edge - lastY) / 4);
    else if (lastY > box.bottom - edge) v = Math.ceil((lastY - box.bottom + edge) / 4);
    if (v) {
      const before = wrap.scrollTop;
      wrap.scrollTop += v;
      if (wrap.scrollTop !== before) update();
    }
    requestAnimationFrame(autoScroll);
  };
  requestAnimationFrame(autoScroll);
  const move = (m) => {
    lastY = m.clientY;
    update();
  };
  const up = () => {
    dragging = false;
    grip.removeEventListener("pointermove", move);
    grip.removeEventListener("pointerup", up);
    document.body.classList.remove("row-dragging");
    const names = groups.map((g) => g.topic);
    const newNames = others.map((g) => g.topic);
    newNames.splice(target, 0, ...block.map((g) => g.topic));
    let rerender = render;
    if (newNames.join("\n") !== names.join("\n")) {
      const rest = (manualOrder || topics.map((t) => t.topic)).filter((n) => !newNames.includes(n));
      manualOrder = [...newNames, ...rest];                         // topics hidden by filters keep their place at the end
      localStorage.setItem(orderKey(), JSON.stringify(manualOrder));
      group = null;                                                 // the dragged order is exactly what is shown now
      order = { key: "manual", dir: 1 };
      rerender = sortChanged;
    }
    settle(rerender, moving);
    // the copy glides into the new place of the block, then the real rows show again
    const placed = [...document.querySelectorAll("#topic-rows tr")].filter((el) => moving.has(el.dataset.topic));
    placed.forEach((el) => el.classList.add("placeholder"));
    float.classList.add("landing");
    float.style.top = `${placed[0].getBoundingClientRect().top}px`;
    setTimeout(() => {
      float.remove();
      placed.forEach((el) => el.classList.remove("placeholder"));
    }, 190);
  };
  grip.addEventListener("pointermove", move);
  grip.addEventListener("pointerup", up);
}

/** A floating copy of the rows ``els`` (row and open timeline) at their place, with one shadow: the picked-up row. */
function liftCopy(els) {
  const r = els[0].getBoundingClientRect();
  const box = document.createElement("div");
  box.className = "drag-float";
  const t = document.createElement("table");
  t.className = "topics";
  t.style.width = table.style.width || `${table.getBoundingClientRect().width}px`;
  t.append(table.querySelector("colgroup").cloneNode(true));
  const body = document.createElement("tbody");
  for (const el of els) {
    const c = el.cloneNode(true);
    c.classList.remove("placeholder", "sliding");
    c.style.transform = "";
    const src = el.querySelector("canvas"), dst = c.querySelector("canvas");
    if (src && dst) {                                // canvases are copied empty: copy their picture
      dst.width = src.width;
      dst.height = src.height;
      dst.getContext("2d").drawImage(src, 0, 0);
    }
    body.append(c);
  }
  t.append(body);
  box.append(t);
  const wb = wrap.getBoundingClientRect();
  Object.assign(box.style, { left: `${wb.left}px`, top: `${r.top}px`, width: `${wrap.clientWidth}px` });
  t.style.marginLeft = `${-wrap.scrollLeft}px`;
  document.body.append(box);
  return box;
}

/** Run ``update`` (which re-renders the rows) and let every row glide from where it is seen now to its new place;
 * the rows of the topics in ``skip`` (the dragged ones, carried by their floating copy) are left out. */
function settle(update, skip = new Set()) {
  const key = (el) => (el.classList.contains("detail") ? "d:" : "") + el.dataset.topic;
  const seen = new Map();
  for (const el of document.querySelectorAll("#topic-rows tr")) {
    if (skip.has(el.dataset.topic)) continue;
    seen.set(key(el), el.getBoundingClientRect().top);         // includes the drag / slide shift
  }
  update();                                                     // new rows, without any shift
  for (const el of document.querySelectorAll("#topic-rows tr")) {
    if (!seen.has(key(el))) continue;
    const delta = seen.get(key(el)) - el.getBoundingClientRect().top;
    if (Math.abs(delta) < 1) continue;
    el.style.transition = "none";
    el.style.transform = `translateY(${delta}px)`;
    el.getBoundingClientRect();                                 // apply the start position before animating
    el.style.transition = "transform 180ms ease-out";
    el.style.transform = "";
    el.addEventListener("transitionend", () => { el.style.transition = ""; }, { once: true });
  }
}

/** The rows an action on ``topic`` applies to: all selected rows if it is one of several, else just it. */
const targets = (topic) => (rowSel.has(topic) && rowSel.size > 1 ? [...rowSel] : [topic]);

function check(topic) {
  const on = !selected.has(topic);
  for (const t of targets(topic)) on ? selected.add(t) : selected.delete(t);
  changed();
}

/** Row click: Ctrl adds / removes the row from the row selection, Shift selects the range from the last clicked
 * row; a plain click opens / closes the row (all selected rows if it is one of them). */
function rowClick(topic, ev) {
  if (ev.ctrlKey || ev.metaKey) {
    rowSel.has(topic) ? rowSel.delete(topic) : rowSel.add(topic);
    anchor = topic;
  } else if (ev.shiftKey && anchor != null) {
    const list = shown().map((t) => t.topic);
    const a = list.indexOf(anchor), b = list.indexOf(topic);
    if (a >= 0) list.slice(Math.min(a, b), Math.max(a, b) + 1).forEach((t) => rowSel.add(t));
  } else {
    const open = !expanded.has(topic);
    const ts = targets(topic);
    if (ts.length === 1) rowSel.clear();
    for (const t of ts) open ? expanded.add(t) : expanded.delete(t);
    anchor = topic;
  }
  render();                                         // the active topic stays: only a click in a timeline changes it
}

function changed() {
  render();
  onChange(new Set(selected));
}

/** Fill the table for a newly opened recording; all topics start unselected. */
export function setTopics(list, callback, path = null) {
  topics = list;
  recPath = path;
  const stored = JSON.parse(localStorage.getItem(orderKey()) || "null");
  if (stored) {                            // own order of this recording from last time; new topics at the end
    const known = new Set(list.map((t) => t.topic));
    manualOrder = [...stored.filter((t) => known.has(t)), ...list.map((t) => t.topic).filter((t) => !stored.includes(t))];
  } else {
    manualOrder = null;
  }
  group = null;
  order = manualOrder ? { key: "manual", dir: 1 } : { key: "topic", dir: 1 };
  selected = new Set();
  rowSel = new Set();
  anchor = null;
  focusTopic = null;
  expanded = new Set();
  onChange = callback;
  $("topic-search").value = "";
  sortChanged();
  changed();
  measure();
}

export const getSelected = () => [...selected];

/** Mark ``topic`` as the active topic (from a click in its timeline); highlighted in the table. */
export function setFocus(topic) {
  focusTopic = topic;
  render();
}

/** Callback that draws the timelines of the opened topics after each table render. */
export function onDetailRender(callback) {
  onDetail = callback;
}

/** Redraw only the timelines (cursor, zoom, data changed), without rebuilding the table. */
export function redrawDetails() {
  const details = [...document.querySelectorAll("#topic-rows tr.detail")].map((dr) => ({
    topic: dr.previousElementSibling.querySelector(".tname").textContent, canvas: dr.querySelector("canvas"),
  }));
  onDetail(details);
}

/** Set the selection without reporting it as a user change (undo / redo). */
export function setSelection(list) {
  selected = new Set(list);
  render();
}

/** New numbers for the same topics (e.g. another size unit); keeps selection, search and scroll. */
export function updateTopics(list) {
  topics = list;
  render();
  measure();
}

$("topic-search").addEventListener("input", render);

// column widths: every column is as wide as its widest entry (measured once per recording), the topic column
// gets the rest of the pane. Dragging a grip fixes that column and the topic column at their widths, so the edge
// follows the mouse and the columns right of it move along; if the columns get wider than the pane, only the
// pane scrolls sideways. Widths are remembered in the browser; Reset returns to the automatic layout.
const table = $("topic-table");
const wrap = table.parentElement;
const cols = [...table.querySelectorAll("col")];
const MIN_TOPIC = 160;
let natural = {};                          // measured content widths
let minW = {};                             // smallest widths: the header label (with sort arrow) stays readable
let user = JSON.parse(localStorage.getItem(WIDTH_KEY) || "{}");

function measure() {
  table.classList.add("measure");
  const ths = [...table.querySelectorAll("th")];
  natural = Object.fromEntries(cols.map((c, i) => [c.dataset.col, Math.ceil(ths[i].getBoundingClientRect().width) + 1]));
  natural.check = 50;                       // checkbox + sort arrow
  natural.grip = 34;
  natural.open = 40;                        // chevron + sort arrow
  // header label width as rendered, with the sort arrow shown (it must stay visible when the column is sorted)
  ths.forEach((th, i) => {
    const arrow = th.querySelector(".arrow");
    const label = th.firstChild?.nodeType === Node.TEXT_NODE ? th.firstChild : null;
    if (!label || !arrow) return;
    const old = arrow.textContent;
    arrow.textContent = "▼";
    const range = document.createRange();
    range.selectNodeContents(label);
    const cs = getComputedStyle(th), ca = getComputedStyle(arrow);
    const w = range.getBoundingClientRect().width + arrow.getBoundingClientRect().width + parseFloat(ca.marginLeft)
      + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
    arrow.textContent = old;
    minW[cols[i].dataset.col] = Math.ceil(w) + 2;
  });
  table.classList.remove("measure");
  layout();
}

function layout() {
  const w = {};
  for (const c of cols) {
    const k = c.dataset.col;
    w[k] = Math.max(minW[k] ?? 0, user[k] ?? natural[k] ?? 80);
  }
  if (user.topic == null) {
    const others = Object.entries(w).filter(([k]) => k !== "topic").reduce((a, [, v]) => a + v, 0);
    w.topic = Math.max(MIN_TOPIC, natural.topic ?? 0, wrap.clientWidth - others);   // never cut names in auto mode
  }
  cols.forEach((c) => (c.style.width = `${w[c.dataset.col]}px`));
  table.style.width = `${Object.values(w).reduce((a, b) => a + b, 0)}px`;
  $("cols-reset").hidden = Object.keys(user).length === 0;
}

new ResizeObserver(() => { layout(); sizeDetails(); redrawDetails(); }).observe(wrap);

table.querySelectorAll(".col-grip").forEach((grip) => {
  grip.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    const th = grip.parentElement;
    const key = cols[[...th.parentElement.children].indexOf(th)].dataset.col;
    const x0 = ev.clientX, w0 = th.getBoundingClientRect().width;
    // the topic column stops filling the rest: otherwise widening a column right of it would take the space from
    // the topic column, its right edge (the grip) would stay put and the column would grow to the left
    if (user.topic == null) user.topic = Math.round(table.querySelector("th[data-sort=topic]").getBoundingClientRect().width);
    grip.setPointerCapture(ev.pointerId);
    grip.classList.add("drag");
    table.classList.add("resizing");
    const move = (m) => {
      user[key] = Math.max(minW[key] ?? 48, Math.round(w0 + m.clientX - x0));
      layout();
    };
    const up = () => {
      grip.removeEventListener("pointermove", move);
      grip.removeEventListener("pointerup", up);
      grip.classList.remove("drag");
      table.classList.remove("resizing");
      localStorage.setItem(WIDTH_KEY, JSON.stringify(user));
    };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", up);
  });
  grip.addEventListener("click", (ev) => ev.stopPropagation());   // a resize is not a sort
});
$("cols-reset").addEventListener("click", () => {
  user = {};
  localStorage.removeItem(WIDTH_KEY);
  layout();
});
