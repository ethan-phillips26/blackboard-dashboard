import { useEffect, useMemo, useState } from "react";
import { courseSlot, dateTime, relative } from "../lib/format.js";

/** The hero figure: how long is left, in the largest unit that is still honest. */
function countdown(due, now) {
  const ms = due - now;
  if (ms < 0) {
    const hours = -ms / 3600000;
    return hours < 24
      ? { value: Math.round(hours), unit: "hours ago", late: true }
      : { value: Math.round(hours / 24), unit: "days ago", late: true };
  }
  const hours = ms / 3600000;
  if (hours < 1) return { value: Math.max(1, Math.round(ms / 60000)), unit: "minutes left" };
  if (hours < 48) return { value: Math.round(hours), unit: "hours left" };
  return { value: Math.round(hours / 24), unit: "days left" };
}

export default function StatRow({ assignments, courses, order }) {
  // The headline figure is a countdown, so it has to move. Once a minute is
  // enough for a card that reads in hours and days, and costs nothing.
  const [tick, setTick] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setTick(Date.now()), 60000);
    return () => clearInterval(id);
  }, []);
  const now = useMemo(() => new Date(tick), [tick]);

  const stats = useMemo(() => {
    const dated = assignments
      .filter((a) => a.due_local)
      .map((a) => ({ ...a, due: new Date(a.due_local) }))
      .sort((a, b) => a.due - b.due);

    const week = new Date(now.getTime() + 7 * 86400000);
    const upcoming = dated.filter((a) => a.due >= now);
    const thisWeek = upcoming.filter((a) => a.due <= week);
    return {
      next: upcoming[0] ?? null,
      thisWeek: thisWeek.length,
      overdue: dated.filter((a) => a.due < now).length,
    };
  }, [assignments, now]);

  const next = stats.next;
  const clock = next ? countdown(next.due, now) : null;

  return (
    <section className="stats">
      {/* The figure and the detail are grouped so that when this card has to
          span the whole row — too narrow for five across — they can sit beside
          each other instead of stacking in the corner of a very wide box. */}
      <div className={"hero" + (clock?.late ? " late" : "")}>
        <div className="label">Next deadline</div>
        {next ? (
          <div className="hero-body">
            <div className="hero-num">
              {clock.value}
              <span className="hero-unit">{clock.unit}</span>
            </div>
            <div className="hero-detail">
              <div className={"hero-what s" + courseSlot(next.course, order)}>
                <i className="dot" />
                <b>{next.title}</b>
                <span className="course">{next.course}</span>
              </div>
              <div className="note">{dateTime(next.due)} · {relative(next.due, now)}</div>
            </div>
          </div>
        ) : (
          <div className="hero-body">
            <div className="hero-num">
              0<span className="hero-unit">outstanding</span>
            </div>
            <div className="hero-detail">
              <div className="note">Nothing is due in the sync window.</div>
            </div>
          </div>
        )}
      </div>

      <Tile label="Due this week" value={stats.thisWeek} sub="next 7 days" />
      <Tile
        label="Overdue"
        value={stats.overdue}
        sub={stats.overdue ? "past the deadline" : "nothing missed"}
        tone={stats.overdue ? "bad" : "ok"}
      />
      <Tile label="Courses" value={courses.length} sub="this term" />
    </section>
  );
}

function Tile({ label, value, sub, tone }) {
  return (
    <div className={"tile" + (tone ? ` ${tone}` : "")}>
      <div className="label">{label}</div>
      <div className="tile-num">{value}</div>
      <div className="note dim">{sub}</div>
    </div>
  );
}
