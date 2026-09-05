import { courseSlot, dateTime, points, relative } from "../lib/format.js";
import { Sep } from "./Sep.jsx";

/**
 * Everything outstanding, soonest first.
 *
 * The list is never cut short. Beside the pinned month it scrolls inside its own
 * panel; on a window too small for that layout the page scrolls and the list
 * runs its full length. Either way there is nothing to press to see the rest.
 */
export default function DueList({ assignments, firstSeen, onOpenAssignment, order }) {
  if (!assignments.length) {
    return (
      <section className="panel" id="deadlines">
        <div className="panel-head"><h2>Deadlines</h2></div>
        <p className="empty">Nothing outstanding in the next 60 days.</p>
      </section>
    );
  }

  // Anything first seen in the last day is worth marking as new.
  const cutoff = Date.now() - 24 * 3600 * 1000;
  const now = new Date();

  return (
    <section className="panel" id="deadlines">
      <div className="panel-head">
        <h2>Deadlines</h2>
        <span className="note">{assignments.length} outstanding</span>
      </div>
      <div className="due-list">
        {assignments.map((a) => {
          const seen = firstSeen?.[a.column_id ?? a.content_id];
          const isNew = seen && new Date(seen).getTime() > cutoff;
          const due = a.due_local ? new Date(a.due_local) : null;
          const overdue = due && due < now;
          const urgent = (a.days_until ?? 99) <= 3;
          return (
            <button
              className={"due s" + courseSlot(a.course, order)}
              key={a.column_id ?? a.content_id ?? a.title}
              onClick={() =>
                onOpenAssignment({
                  courseId: a.course_id, contentId: a.content_id,
                  title: a.title, course: a.course, points: a.points_possible,
                  due,
                })
              }
            >
              <i className="dot" />
              <div className="due-body">
                <div className="due-top">
                  <b>{a.title}</b>
                  {isNew && <span className="badge-new">new</span>}
                </div>
                <div className="note dim">
                  {due ? dateTime(due) : "no due date"}
                  {points(a.points_possible)
                    ? <><Sep />{points(a.points_possible)}</> : null}
                </div>
                <span className="course">{a.course}</span>
              </div>
              <span
                className={"when " + (overdue ? "bad" : urgent ? "soon" : "later")}
              >
                {relative(due, now)}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
