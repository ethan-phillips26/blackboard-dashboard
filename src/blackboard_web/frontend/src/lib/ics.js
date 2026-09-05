/**
 * A small iCalendar reader.
 *
 * The calendar grid renders whatever `/api/calendar.ics` serves rather than a
 * convenient JSON shape, so what is on screen and what lands in Google Calendar
 * come from one document. That is only worth doing if the parse is honest, so
 * this handles the parts of RFC 5545 a real feed uses: folded lines, escaped
 * text, property parameters, and all three DATE-TIME forms.
 */

/** Undo line folding: a line beginning with a space or tab continues the previous one. */
function unfold(text) {
  const out = [];
  for (const raw of text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n")) {
    if ((raw.startsWith(" ") || raw.startsWith("\t")) && out.length) {
      out[out.length - 1] += raw.slice(1);
    } else if (raw.length) {
      out.push(raw);
    }
  }
  return out;
}

/** Split `NAME;PARAM=v:value` — the first colon that is not inside a quoted param. */
function splitLine(line) {
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') quoted = !quoted;
    else if (ch === ":" && !quoted) {
      const head = line.slice(0, i);
      const [name, ...params] = head.split(";");
      return { name: name.toUpperCase(), params, value: line.slice(i + 1) };
    }
  }
  return { name: line.toUpperCase(), params: [], value: "" };
}

function unescapeText(value) {
  let out = "";
  for (let i = 0; i < value.length; i++) {
    if (value[i] !== "\\") {
      out += value[i];
      continue;
    }
    const next = value[++i];
    if (next === "n" || next === "N") out += "\n";
    else if (next === undefined) out += "\\";
    else out += next; // \\ \, \; and anything else stands for itself
  }
  return out;
}

/**
 * Parse a DATE-TIME or DATE value.
 *
 * `20260905T230000Z` is UTC, `20260905T180000` is local wall time (a TZID we
 * cannot resolve is treated the same way — the browser's zone is the student's
 * zone), and a bare `20260905` is an all-day date.
 */
function parseWhen(value, params) {
  const v = (value || "").trim();
  const dateOnly = /^(\d{4})(\d{2})(\d{2})$/.exec(v);
  if (dateOnly || params.some((p) => /^VALUE=DATE$/i.test(p))) {
    const m = dateOnly ?? /^(\d{4})(\d{2})(\d{2})/.exec(v);
    if (!m) return null;
    return { date: new Date(+m[1], +m[2] - 1, +m[3]), allDay: true };
  }
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z)?$/.exec(v);
  if (!m) return null;
  const [, y, mo, d, h, mi, s, z] = m;
  const date = z
    ? new Date(Date.UTC(+y, +mo - 1, +d, +h, +mi, +s))
    : new Date(+y, +mo - 1, +d, +h, +mi, +s);
  return { date, allDay: false };
}

const X = {
  "X-BB-COURSE": "course",
  "X-BB-COURSE-NAME": "courseName",
  "X-BB-COURSE-ID": "courseId",
  "X-BB-COLUMN-ID": "columnId",
  "X-BB-CONTENT-ID": "contentId",
  "X-BB-TITLE": "title",
};

/**
 * @returns {{ calendarName: string|null, events: Array }} events sorted by start,
 *   each with `{ uid, start, end, allDay, summary, description, course, title, points, submitted }`.
 */
export function parseICS(text) {
  const events = [];
  let calendarName = null;
  let current = null;
  let inAlarm = false;

  for (const line of unfold(text || "")) {
    const { name, params, value } = splitLine(line);

    if (name === "BEGIN" && value === "VEVENT") {
      current = { points: null, submitted: false };
      continue;
    }
    // An alarm carries its own DESCRIPTION; it must not overwrite the event's.
    if (name === "BEGIN" && value === "VALARM") {
      inAlarm = true;
      continue;
    }
    if (name === "END" && value === "VALARM") {
      inAlarm = false;
      continue;
    }
    if (name === "END" && value === "VEVENT") {
      if (current?.start) events.push(finish(current));
      current = null;
      continue;
    }
    if (!current) {
      if (name === "X-WR-CALNAME") calendarName = unescapeText(value);
      continue;
    }
    if (inAlarm) continue;

    switch (name) {
      case "UID":
        current.uid = value;
        break;
      case "SUMMARY":
        current.summary = unescapeText(value);
        break;
      case "DESCRIPTION":
        current.description = unescapeText(value);
        break;
      case "DTSTART": {
        const when = parseWhen(value, params);
        if (when) {
          current.start = when.date;
          current.allDay = when.allDay;
        }
        break;
      }
      case "DTEND": {
        const when = parseWhen(value, params);
        if (when) current.end = when.date;
        break;
      }
      case "CATEGORIES":
        current.categories = unescapeText(value).split(",").filter(Boolean);
        break;
      case "X-BB-POINTS": {
        const n = Number(value);
        current.points = Number.isFinite(n) ? n : null;
        break;
      }
      case "X-BB-SUBMITTED":
        current.submitted = value.trim().toUpperCase() === "TRUE";
        break;
      default:
        if (X[name]) current[X[name]] = unescapeText(value);
    }
  }

  events.sort((a, b) => a.start - b.start);
  return { calendarName, events };
}

function finish(event) {
  const end = event.end ?? event.start;
  // The deadline is the end of the block; everything the UI shows hangs off it.
  return {
    uid: event.uid ?? `${+event.start}-${event.summary ?? ""}`,
    start: event.start,
    end,
    due: end,
    allDay: !!event.allDay,
    summary: event.summary ?? "",
    description: event.description ?? "",
    // Fall back to splitting "COURSE: Title" when the X- hints are absent, so a
    // feed from some other producer still renders sensibly.
    course: event.course ?? event.categories?.[0] ?? splitSummary(event.summary).course,
    courseName: event.courseName ?? null,
    courseId: event.courseId ?? null,
    columnId: event.columnId ?? null,
    contentId: event.contentId ?? null,
    title: event.title ?? splitSummary(event.summary).title,
    points: event.points,
    submitted: event.submitted,
  };
}

function splitSummary(summary) {
  const s = summary ?? "";
  const at = s.indexOf(": ");
  return at > 0
    ? { course: s.slice(0, at), title: s.slice(at + 2) }
    : { course: "", title: s };
}
