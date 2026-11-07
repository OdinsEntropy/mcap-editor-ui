// Small DOM helpers shared by the panels: element lookup, toast, debounce, number formatting.

export const $ = (id) => document.getElementById(id);

let toastTimer;
/** Show a message at the bottom centre; errors stay longer. */
export function toast(text, error = false) {
  const el = $("toast");
  el.textContent = text;
  el.classList.toggle("error", error);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), error ? 9000 : 4500);
}

/** Call fn only after ms without further calls (typing, dragging). */
export function debounce(fn, ms = 300) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

/** Seconds with up to 3 decimals, without trailing zeros. */
export const fmtS = (s) => String(Math.round(s * 1000) / 1000);

/** Duration as "1 m 05 s" style text for elapsed / remaining times. */
export function fmtDuration(s) {
  if (s == null || !isFinite(s)) return "–";
  s = Math.round(s);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${String(s % 60).padStart(2, "0")} s`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

/** Parse a seconds input; empty → null, invalid → NaN. */
export function parseSeconds(text) {
  const t = text.trim().replace(",", ".");
  if (!t) return null;
  const v = Number(t);
  return isFinite(v) ? v : NaN;
}
