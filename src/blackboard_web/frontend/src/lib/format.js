/** Shared date, colour and number helpers for the dashboard. */

/**
 * Course identity colour.
 *
 * Eight validated categorical slots, assigned in a fixed order by sorted course
 * name so a course keeps its colour between renders and never gets repainted
 * because a filter changed how many courses are on screen. Colour is always a
 * supplement here — every chip and legend row also carries the course's name.
 */
export const COURSE_SLOTS = 8;

export function courseSlot(course, order) {
  if (!course) return 0;
  const at = order?.indexOf(course);
  if (at != null && at >= 0) return at % COURSE_SLOTS;
  // A course the order does not know about still needs a stable colour.
  let hash = 0;
  for (let i = 0; i < course.length; i++) hash = (hash * 31 + course.charCodeAt(i)) | 0;
  return Math.abs(hash) % COURSE_SLOTS;
}

export function courseOrder(names) {
  return [...new Set(names.filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

export const dayKey = (date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
    date.getDate()
  ).padStart(2, "0")}`;

export const startOfDay = (date) =>
  new Date(date.getFullYear(), date.getMonth(), date.getDate());

export const sameDay = (a, b) => dayKey(a) === dayKey(b);

export function addMonths(date, n) {
  return new Date(date.getFullYear(), date.getMonth() + n, 1);
}

/** The 42 cells of a month grid, Sunday-first, including the neighbouring days. */
export function monthGrid(month) {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const start = new Date(first);
  start.setDate(1 - first.getDay());
  return Array.from({ length: 42 }, (_, i) => {
    const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    return { date: d, inMonth: d.getMonth() === month.getMonth(), key: dayKey(d) };
  });
}

export const MONTHS = ["January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];
export const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export const time = (date) =>
  date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

export const longDate = (date) =>
  date.toLocaleDateString(undefined, {
    weekday: "long", month: "long", day: "numeric",
  });

export const dateTime = (date) =>
  date.toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit",
  });

/** "in 3 days" / "tomorrow" / "in 5 hours" / "2d overdue" — from a real date. */
export function relative(date, now = new Date()) {
  if (!date) return "—";
  const ms = date - now;
  const hours = ms / 3600000;
  if (ms < 0) {
    const past = -hours;
    if (past < 1) return "just passed";
    if (past < 24) return `${Math.round(past)}h overdue`;
    return `${Math.round(past / 24)}d overdue`;
  }
  if (hours < 1) return `in ${Math.max(1, Math.round(ms / 60000))} min`;
  if (hours < 24 && startOfDay(date).getTime() === startOfDay(now).getTime())
    return `in ${Math.round(hours)}h`;
  const days = Math.round((startOfDay(date) - startOfDay(now)) / 86400000);
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days < 7) return `in ${days} days`;
  if (days < 14) return "next week";
  return `in ${Math.round(days / 7)} weeks`;
}

/** "3d ago" — for things that have already happened, where `relative` would
 *  say "overdue". A deadline looks forward; a post looks back. */
export function ago(date, now = new Date()) {
  if (!date) return "";
  const ms = now - date;
  if (ms < 0) return relative(date, now);
  const mins = ms / 60000;
  if (mins < 1) return "just now";
  if (mins < 60) return `${Math.round(mins)}m ago`;
  const hours = mins / 60;
  if (hours < 24) return `${Math.round(hours)}h ago`;
  const days = Math.round(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days}d ago`;
  if (days < 14) return "last week";
  if (days < 60) return `${Math.round(days / 7)}w ago`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** "16 KB" — the one thing about a handout worth showing next to its name. */
export function filesize(bytes) {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return null;
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${Math.round(kb)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

export function pct(value) {
  return value === null || value === undefined ? "—" : `${value.toFixed(1)}%`;
}

export function points(value) {
  if (value === null || value === undefined) return null;
  return `${Number(value) % 1 === 0 ? value : Number(value).toFixed(1)} pt`;
}
