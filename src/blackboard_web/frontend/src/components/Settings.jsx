import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import GoogleSetup from "./GoogleSetup.jsx";
import { THEMES, resolve } from "../lib/theme.js";
import { dateTime, filesize, points } from "../lib/format.js";

const THEME_LABELS = { light: "Light", dark: "Dark", system: "Match system" };

// The choice lives in localStorage rather than on the server: it belongs to the
// screen you are reading on, not to the account, so a phone and a desktop can
// disagree about it and both be right.

/** An <input type="datetime-local"> wants a naive local string, not an ISO one. */
function toLocalInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function Appearance({ theme, onTheme }) {
  return (
    <section className="panel" id="appearance">
      <div className="panel-head">
        <h2>Appearance</h2>
        <span className="note dim">
          {theme === "system" ? `following this device — ${resolve("system")}` : "set by you"}
        </span>
      </div>
      <div className="seg">
        {THEMES.map((t) => (
          <button
            key={t}
            className={theme === t ? "on" : ""}
            onClick={() => onTheme(t)}
          >
            {THEME_LABELS[t]}
          </button>
        ))}
      </div>
    </section>
  );
}

// Only the percentage is editable. The labels are the buckets every gradebook
// row was sorted into, so renaming one here would orphan everything under it —
// and these percentages drive every grade the dashboard calculates.
function WeightRow({ course, standing, onSaved, showName = true }) {
  const components = standing?.components ?? [];
  const [draft, setDraft] = useState(() =>
    Object.fromEntries(components.map((c) => [c.syllabus_label, c.weight_pct ?? 0]))
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    setDraft(Object.fromEntries(
      components.map((c) => [c.syllabus_label, c.weight_pct ?? 0])
    ));
  }, [standing]); // eslint-disable-line react-hooks/exhaustive-deps

  const total = Object.values(draft).reduce((a, b) => a + (Number(b) || 0), 0);
  const dirty = components.some(
    (c) => Number(draft[c.syllabus_label]) !== Number(c.weight_pct ?? 0)
  );

  if (!components.length) {
    return (
      <div className="set-course">
        <div className="set-course-head">
          {showName && <b>{course.label}</b>}
          <span className="note dim">
            {standing?.accessible === false
              ? "gradebook hidden by the instructor"
              : "no syllabus weighting read yet — open the course and extract it first"}
          </span>
        </div>
      </div>
    );
  }

  async function save() {
    setBusy(true); setErr(null);
    try {
      await api.editWeights(course.course_id,
        Object.fromEntries(Object.entries(draft).map(([k, v]) => [k, Number(v) || 0])));
      await onSaved();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  async function reset() {
    setBusy(true); setErr(null);
    try {
      await api.resetWeights(course.course_id);
      await onSaved();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  return (
    <div className="set-course">
      <div className="set-course-head">
        {showName && <b>{course.label}</b>}
        {standing?.weights_edited && <span className="flag">edited</span>}
        <span className="spacer" />
        <span className={"note " + (Math.round(total) === 100 ? "dim" : "warn-text")}>
          {total.toFixed(0)}% of 100
        </span>
      </div>
      {components.map((c) => (
        <label className="set-weight" key={c.syllabus_label}>
          <span className="set-weight-label">{c.syllabus_label}</span>
          <input
            type="number" min="0" max="100" step="0.5"
            value={draft[c.syllabus_label] ?? 0}
            onChange={(e) =>
              setDraft((d) => ({ ...d, [c.syllabus_label]: e.target.value }))}
          />
          <span className="note dim">%</span>
        </label>
      ))}
      {err && <p className="err">{err}</p>}
      <div className="set-actions">
        <button className="primary" onClick={save} disabled={busy || !dirty}>
          {busy ? <><span className="spin" /> Saving</> : "Save"}
        </button>
        {standing?.weights_edited && (
          <button onClick={reset} disabled={busy}>Use the syllabus again</button>
        )}
      </div>
    </div>
  );
}

// An edit is stored apart from the fetched data and re-applied on read, so a
// sync cannot undo it, and a changed due date carries through to the calendar
// grid and the .ics feed as well as this list.
function DeadlineRow({ item, edit, onSaved, showCourse = true }) {
  const key = item.column_id ?? item.content_id;
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState(item.title ?? "");
  const [due, setDue] = useState(toLocalInput(item.due_local));
  const [pts, setPts] = useState(item.points_possible ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    setTitle(item.title ?? "");
    setDue(toLocalInput(item.due_local));
    setPts(item.points_possible ?? "");
  }, [item]);

  const edited = !!edit;

  async function save() {
    setBusy(true); setErr(null);
    try {
      await api.editAssignment(key, {
        title: title.trim() || null,
        // The picker gives a naive local time; the server stores UTC.
        due_utc: due ? new Date(due).toISOString() : null,
        points_possible: pts === "" ? null : Number(pts),
      });
      await onSaved();
      setOpen(false);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  async function hide() {
    setBusy(true); setErr(null);
    try {
      await api.editAssignment(key, { hidden: true });
      await onSaved();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  async function reset() {
    setBusy(true); setErr(null);
    try {
      await api.resetAssignment(key);
      await onSaved();
      setOpen(false);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  return (
    <div className={"set-item" + (open ? " open" : "")}>
      <button className="set-item-head" onClick={() => setOpen((v) => !v)}>
        <div className="set-item-id">
          <b>{item.title}</b>
          {edited && <span className="flag">edited</span>}
          {item.hidden && <span className="flag warn">hidden</span>}
          <div className="note dim">
            {showCourse ? `${item.course} · ` : ""}
            {item.due_local ? dateTime(new Date(item.due_local)) : "no due date"}
            {points(item.points_possible) ? ` · ${points(item.points_possible)}` : ""}
          </div>
        </div>
        <span className="note dim">{open ? "Close" : "Edit"}</span>
      </button>

      {open && (
        <div className="set-item-body">
          <label className="field">
            <span>Title</span>
            <input type="text" value={title}
                   onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label className="field">
            <span>Due</span>
            <input type="datetime-local" value={due}
                   onChange={(e) => setDue(e.target.value)} />
          </label>
          <label className="field">
            <span>Points</span>
            <input type="number" min="0" step="1" value={pts}
                   onChange={(e) => setPts(e.target.value)} />
          </label>
          {err && <p className="err">{err}</p>}
          <div className="set-actions">
            <button className="primary" onClick={save} disabled={busy}>
              {busy ? <><span className="spin" /> Saving</> : "Save"}
            </button>
            {!item.hidden && (
              <button onClick={hide} disabled={busy}>Hide from dashboard</button>
            )}
            {edited && (
              <button onClick={reset} disabled={busy}>Reset to Blackboard</button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** What is worth saying about a course before you open it. */
function courseSummary(course, data) {
  const id = course.course_id;
  const due = data.assignments.filter((a) => a.course_id === id).length;
  const hidden = (data.hidden_assignments ?? [])
    .filter((a) => a.course_id === id).length;
  const standing = data.standings[id];
  const edits = data.edits?.assignments ?? {};
  const changed = [...data.assignments, ...(data.hidden_assignments ?? [])]
    .filter((a) => a.course_id === id &&
                   edits[a.column_id ?? a.content_id]).length;
  return { due, hidden, changed, standing };
}

function CourseButton({ course, data }) {
  const { due, hidden, changed, standing } = courseSummary(course, data);
  const weights = standing?.components?.length ?? 0;
  return (
    <a className="ccard set-card" href={`#/settings/${course.course_id}`}>
      <div className="ccard-head">
        <div>
          <h3>{course.label}</h3>
          <div className="code">
            {course.title && course.title !== course.label ? course.title : "\u00a0"}
          </div>
        </div>
      </div>
      <div className="set-card-meta note dim">
        <div>
          {weights ? `${weights} weighted parts` : "no syllabus weighting yet"}
        </div>
        <div>
          {due} {due === 1 ? "deadline" : "deadlines"}
          {hidden ? ` · ${hidden} hidden` : ""}
        </div>
      </div>
      <div className="ccard-foot">
        {standing?.weights_edited && <span className="flag">weights edited</span>}
        {changed > 0 && <span className="flag">{changed} edited</span>}
        <span className="go" aria-hidden="true">→</span>
      </div>
    </a>
  );
}

/** Everything editable about one course, and nothing about any other. */
function CourseSettings({ course, data, onReload, onBack }) {
  const [query, setQuery] = useState("");
  const id = course.course_id;
  const edits = data.edits ?? { assignments: {}, weights: {} };
  const mine = useMemo(
    () => data.assignments.filter((a) => a.course_id === id),
    [data.assignments, id]
  );
  const hidden = useMemo(
    () => (data.hidden_assignments ?? []).filter((a) => a.course_id === id),
    [data.hidden_assignments, id]
  );
  const items = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? mine.filter((a) => a.title.toLowerCase().includes(q)) : mine;
  }, [mine, query]);

  return (
    <>
      <header className="topbar">
        <div>
          <h1>{course.label}</h1>
          <p className="note dim">
            {course.title && course.title !== course.label
              ? course.title : "Course settings"}
          </p>
        </div>
        <div className="spacer" />
        <button onClick={onBack}>Back to settings</button>
      </header>

      <section className="panel" id="weights">
        <div className="panel-head">
          <h2>Grade weighting</h2>
        </div>
        <WeightRow course={course} standing={data.standings[id]}
                   onSaved={onReload} showName={false} />
      </section>

      <section className="panel" id="deadlines-edit">
        <div className="panel-head">
          <h2>Deadlines</h2>
          <span className="note dim">
            {mine.length} outstanding{hidden.length ? ` · ${hidden.length} hidden` : ""}
          </span>
        </div>
        {mine.length > 6 && (
          <input
            className="set-search"
            type="search"
            placeholder="Filter by title"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        )}
        {items.map((a) => (
          <DeadlineRow
            key={a.column_id ?? a.content_id ?? a.title}
            item={a}
            edit={edits.assignments?.[a.column_id ?? a.content_id]}
            onSaved={onReload}
            showCourse={false}
          />
        ))}
        {!items.length && (
          <p className="empty">
            {mine.length ? "Nothing matches that." : "Nothing outstanding for this course."}
          </p>
        )}

        {hidden.length > 0 && <HiddenList items={hidden} onSaved={onReload} />}
      </section>
    </>
  );
}

export default function Settings({ data, courseId, theme, onTheme, onReload,
                                   onBack, onOpenCourse }) {
  const course = courseId
    ? data.courses.find((c) => c.course_id === courseId)
    : null;

  if (courseId && course) {
    return (
      <CourseSettings course={course} data={data} onReload={onReload}
                      onBack={onOpenCourse} />
    );
  }

  return (
    <>
      <header className="topbar">
        <div>
          <h1>Settings</h1>
          <p className="note dim">
            Appearance, connected accounts, and corrections to what Blackboard
            reports.
          </p>
        </div>
        <div className="spacer" />
        <button onClick={onBack}>Back to dashboard</button>
      </header>

      {courseId && !course && (
        <p className="err banner">That course is not in this term.</p>
      )}

      <Appearance theme={theme} onTheme={onTheme} />

      <GoogleSetup />

      <section className="section" id="courses">
        <div className="panel-head">
          <h2>Courses</h2>
          <span className="note dim">
            {data.courses.length} enrolled — open one to correct its weighting
            or its deadlines
          </span>
        </div>
        <div className="grid cards">
          {data.courses.map((c) => (
            <CourseButton key={c.course_id} course={c} data={data} />
          ))}
        </div>
      </section>

      <StoredData onReload={onReload} />
    </>
  );
}

/**
 * The way back to a clean slate.
 *
 * Everything this deletes is re-fetchable — that is the whole reason it is safe
 * to offer. What it keeps is what is not: the Blackboard login costs an SSO round
 * trip and a Duo push to re-establish, and the Google connection costs a trip
 * through a consent screen. Neither is data about your courses, so neither is
 * what "clear my data" means.
 *
 * The button arms before it fires. 400MB of handouts and a term of corrections
 * are worth one deliberate second, and there is nothing to undo it with.
 */
function StoredData({ onReload }) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(null);
  const [err, setErr] = useState(null);

  async function clear() {
    setBusy(true); setErr(null);
    try {
      const result = await api.resetData();
      setDone(result);
      setArmed(false);
      // The cache is empty now, so this reload is a full re-sync rather than a
      // read — the screen fills back in instead of going blank.
      await onReload();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" id="stored-data">
      <div className="panel-head">
        <h2>Stored data</h2>
        <span className="note dim">everything here comes back on the next sync</span>
      </div>
      <p className="note">
        Clears the cached courses, deadlines, announcements and content trees, the
        grade weightings read from your syllabi, your local corrections to
        deadlines and weights, which announcements have been popped up, and every
        handout downloaded to this machine.
      </p>
      <p className="note dim">
        Your Blackboard login and your Google Docs connection are left alone.
      </p>

      {err && <p className="err">{err}</p>}
      {done && !armed && (
        <p className="note">
          Cleared {done.cache_entries} cached {done.cache_entries === 1 ? "entry" : "entries"}
          {done.files > 0 && <> and {done.files} {done.files === 1 ? "file" : "files"}</>}
          {done.bytes > 0 && <> — {filesize(done.bytes)} freed</>}.
        </p>
      )}

      <div className="set-actions">
        {armed ? (
          <>
            <button className="danger" onClick={clear} disabled={busy}>
              {busy ? <><span className="spin" /> Clearing</> : "Delete it all"}
            </button>
            <button onClick={() => setArmed(false)} disabled={busy}>Cancel</button>
            <span className="note dim">This cannot be undone.</span>
          </>
        ) : (
          <button onClick={() => { setArmed(true); setDone(null); }}>
            Clear stored data
          </button>
        )}
      </div>
    </section>
  );
}

function HiddenList({ items, onSaved }) {
  return (
    <div className="set-hidden">
      <div className="label">Hidden</div>
      {items.map((item) => {
        const key = item.column_id ?? item.content_id;
        return (
          <div className="set-hidden-row" key={key}>
            <span className="note">{item.title}</span>
            <span className="spacer" />
            <button onClick={async () => {
              await api.resetAssignment(key);
              await onSaved();
            }}>Show again</button>
          </div>
        );
      })}
    </div>
  );
}
