import { useEffect, useMemo, useRef, useState } from "react";
import {
  MONTHS, WEEKDAYS, courseOrder, courseSlot, dayKey, longDate, monthGrid,
  points, relative, sameDay, time,
} from "../lib/format.js";
import { Sep } from "./Sep.jsx";

const MAX_CHIPS = 3;

export default function Calendar({ events, loading, error, onReload,
                                   onOpenAssignment, order }) {
  const today = useMemo(() => new Date(), []);
  const [month, setMonth] = useState(
    () => new Date(today.getFullYear(), today.getMonth(), 1)
  );
  const [selected, setSelected] = useState(null);
  const [only, setOnly] = useState(null);
  const jumped = useRef(false);
  const closeRef = useRef(null);

  // A day opens as a modal, so it has to behave like one: Escape closes it, and
  // focus moves into it rather than being left behind on the grid.
  useEffect(() => {
    if (!selected) return;
    closeRef.current?.focus();
    const onKey = (e) => e.key === "Escape" && setSelected(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected]);

  // Open on the month that actually has work in it. Only once — after that the
  // month is the reader's to steer.
  useEffect(() => {
    if (jumped.current || !events.length) return;
    jumped.current = true;
    const next = events.find((e) => e.due >= today) ?? events[0];
    if (next.due.getMonth() !== today.getMonth() ||
        next.due.getFullYear() !== today.getFullYear()) {
      setMonth(new Date(next.due.getFullYear(), next.due.getMonth(), 1));
    }
  }, [events, today]);

  // The legend can only name courses that actually have work in the feed, but
  // their colours come from the app-wide order so a course is the same colour
  // here as it is in the sidebar and the deadline list.
  const present = useMemo(
    () => courseOrder(events.map((e) => e.course)),
    [events]
  );

  const visible = useMemo(
    () => (only ? events.filter((e) => e.course === only) : events),
    [events, only]
  );

  const byDay = useMemo(() => {
    const map = new Map();
    for (const e of visible) {
      const key = dayKey(e.due);
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(e);
    }
    for (const list of map.values()) list.sort((a, b) => a.due - b.due);
    return map;
  }, [visible]);

  const cells = useMemo(() => monthGrid(month), [month]);
  const monthCount = cells.reduce(
    (n, c) => n + (c.inMonth ? byDay.get(c.key)?.length ?? 0 : 0), 0
  );
  const selectedEvents = selected ? byDay.get(selected) ?? [] : [];

  function shift(n) {
    setMonth((m) => new Date(m.getFullYear(), m.getMonth() + n, 1));
    setSelected(null);
  }

  function goToday() {
    setMonth(new Date(today.getFullYear(), today.getMonth(), 1));
    setSelected(dayKey(today));
  }

  return (
    <section className="panel cal" id="calendar">
      <div className="panel-head">
        <h2>Calendar</h2>
        <span className="note">
          {loading
            ? "reading calendar…"
            : `${monthCount} due in ${MONTHS[month.getMonth()]}`}
        </span>
        <div className="spacer" />
        <div className="cal-nav">
          <button onClick={() => shift(-1)} aria-label="Previous month">‹</button>
          <button onClick={goToday}>Today</button>
          <button onClick={() => shift(1)} aria-label="Next month">›</button>
        </div>
      </div>

      {error && (
        <p className="err">
          {error} <button onClick={onReload}>Retry</button>
        </p>
      )}

      <div className="cal-title">
        <strong>{MONTHS[month.getMonth()]}</strong> {month.getFullYear()}
      </div>

      <div className="cal-grid">
        {WEEKDAYS.map((d) => (
          <div key={d} className="cal-wd" aria-hidden="true">{d}</div>
        ))}
        {cells.map((cell) => {
          const list = byDay.get(cell.key) ?? [];
          const isToday = sameDay(cell.date, today);
          const className =
            "cal-cell" +
            (cell.inMonth ? "" : " out") +
            (isToday ? " today" : "") +
            (selected === cell.key ? " sel" : "") +
            (list.length ? " has" : "");
          // A day is one target. The chips are a preview of what is in it, not
          // controls of their own — picking one assignment out of a 60px cell
          // was a small target next to a large one that did something else, and
          // the day it opens lists the same assignments at a size you can read.
          // With nothing interactive inside it, the cell can be the button.
          const chips = (
            <>
              <div className="cal-num">{cell.date.getDate()}</div>
              {list.slice(0, MAX_CHIPS).map((e) => (
                <span
                  key={e.uid}
                  className={"chip s" + courseSlot(e.course, order) +
                             (e.due < today ? " past" : "")}
                  title={`${e.summary}\n${time(e.due)}${
                    e.points ? ` · ${points(e.points)}` : ""}`}
                >
                  <i className="dot" />
                  <span className="chip-t">{e.title || e.summary}</span>
                </span>
              ))}
              {list.length > MAX_CHIPS && (
                <span className="cal-more">+{list.length - MAX_CHIPS} more</span>
              )}
              {/* Narrow screens have no room for the chips; a count keeps the
                  day legible without leaving colour as the only signal. */}
              {list.length > 0 && (
                <span className="cal-count">{list.length} due</span>
              )}
            </>
          );

          return list.length ? (
            <button
              key={cell.key}
              type="button"
              className={className}
              aria-label={`${longDate(cell.date)} — ${list.length} due`}
              onClick={() => setSelected(cell.key)}
            >
              {chips}
            </button>
          ) : (
            <div key={cell.key} className={className}>{chips}</div>
          );
        })}
      </div>

      {present.length > 0 && (
        <div className="legend">
          {present.map((name) => (
            <button
              key={name}
              className={"lg s" + courseSlot(name, order) + (only === name ? " on" : "")}
              aria-pressed={only === name}
              onClick={() => setOnly((cur) => (cur === name ? null : name))}
            >
              <i className="dot" />
              <span className="lg-name">{name}</span>
            </button>
          ))}
          {only && (
            <button className="linkish" onClick={() => setOnly(null)}>
              Show all courses
            </button>
          )}
        </div>
      )}

      {selected && (
        <>
          <div className="scrim" onClick={() => setSelected(null)} />
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label={longDate(new Date(`${selected}T12:00:00`))}
          >
            <div className="modal-head">
              <h3>{longDate(new Date(`${selected}T12:00:00`))}</h3>
              <div className="spacer" />
              <button ref={closeRef} onClick={() => setSelected(null)}>Close</button>
            </div>
            <div className="modal-body">
          {selectedEvents.map((e) => (
            <button
              key={e.uid}
              className={"detail s" + courseSlot(e.course, order)}
              onClick={() => { setSelected(null); onOpenAssignment(e); }}
            >
              <div className="detail-head">
                <i className="dot" />
                <b>{e.title || e.summary}</b>
                <span className="course">{e.course}</span>
              </div>
              <div className="note dim">
                Due {time(e.due)}<Sep />{relative(e.due)}
                {e.points ? <><Sep />{points(e.points)}</> : null}
                {e.submitted ? <><Sep />submitted</> : null}
              </div>
              {e.courseName && e.courseName !== e.course && (
                <div className="note dim">{e.courseName}</div>
              )}
            </button>
          ))}
            </div>
          </div>
        </>
      )}

    </section>
  );
}
