// Undo / redo of the editing state (topic selection and time window): one step per
// user action. Changes are recorded after a short quiet time, so a drag or a typed number becomes one step.

let past = [], future = [], current = null;
let limit = 20;
let applying = false;
let applyFn = () => {};
let onUpdate = () => {};

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

let pending = null, timer = null;

function flush() {
  clearTimeout(timer);
  const snap = pending;
  pending = null;
  if (!snap || (current && same(snap, current))) return;
  if (current) past.push(current);
  while (past.length > limit) past.shift();
  current = snap;
  future = [];
  onUpdate();
}

function commit(snap) {
  pending = snap;
  clearTimeout(timer);
  timer = setTimeout(flush, 400);
}

/** Start a new history (new recording) with ``snap`` as the first state. */
export function resetHistory(snap, apply, update) {
  clearTimeout(timer);
  pending = null;
  past = [];
  future = [];
  current = snap;
  applyFn = apply;
  onUpdate = update;
  onUpdate();
}

/** Record a user change (ignored while undo / redo applies a state). */
export function record(snap) {
  if (!applying) commit(snap);
}

export function setLimit(n) {
  limit = Math.max(1, n | 0);
  while (past.length > limit) past.shift();
  onUpdate();
}

function apply(snap) {
  applying = true;
  try {
    applyFn(snap);
  } finally {
    applying = false;
  }
  onUpdate();
}

export function undo() {
  flush();                                   // a change still waiting to be recorded counts first
  if (!past.length) return;
  future.push(current);
  current = past.pop();
  apply(current);
}

export function redo() {
  flush();
  if (!future.length) return;
  past.push(current);
  current = future.pop();
  apply(current);
}

export const canUndo = () => past.length > 0 || pending !== null;
export const canRedo = () => future.length > 0;
