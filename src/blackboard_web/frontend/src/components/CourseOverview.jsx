import { ago, dateTime, points, relative } from "../lib/format.js";
import { Sep } from "./Sep.jsx";
import RichText, { clamp, visibleLength } from "./RichText.jsx";

const PREVIEW = 6;
const ANN_CLAMP = 260;

/**
 * The course's front page: what is due, what was said, and where the grade
 * stands — the three things a student opens a course to find out.
 *
 * Blackboard scatters these across an announcements page, a content tree and a
 * gradebook. Everything here is already in memory from the dashboard's own
 * sync, so this screen costs nothing to draw.
 */
export default function CourseOverview({ standing, assignments, announcements,
                                         firstSeen, onOpenAssignment, onTab }) {
  const now = new Date();
  const upcoming = assignments.filter((a) => !a.submitted);
  const next = upcoming.slice(0, PREVIEW);
  const recent = announcements.slice(0, 3);

  const columns = (standing?.categories ?? []).flatMap((c) => c.columns ?? []);
  const graded = columns.filter((c) => c.graded).length;
  const cutoff = Date.now() - 24 * 3600 * 1000;

  return (
    <>
      <div className="split">
        <section className="panel">
          <div className="panel-head">
            <h2>Up next</h2>
            <span className="note dim">
              {upcoming.length} outstanding
            </span>
          </div>

          {next.length === 0 ? (
            <p className="empty">Nothing outstanding in this course.</p>
          ) : (
            <div className="due-list">
              {next.map((a) => {
                const seen = firstSeen?.[a.column_id ?? a.content_id];
                const isNew = seen && new Date(seen).getTime() > cutoff;
                const due = a.due_local ? new Date(a.due_local) : null;
                const overdue = due && due < now;
                return (
                  <button
                    className="due"
                    key={a.column_id ?? a.content_id ?? a.title}
                    onClick={() =>
                      onOpenAssignment({
                        courseId: a.course_id, contentId: a.content_id,
                        title: a.title, course: a.course,
                        points: a.points_possible, due,
                      })
                    }
                  >
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
                    </div>
                    <span
                      className={
                        "when " + (overdue ? "bad"
                          : (a.days_until ?? 99) <= 3 ? "soon" : "later")
                      }
                    >
                      {relative(due, now)}
                    </span>
                  </button>
                );
              })}
            </div>
          )}

          {upcoming.length > next.length && (
            <button className="due-more" onClick={() => onTab("materials")}>
              See all {upcoming.length} in this course's material →
            </button>
          )}
        </section>

        <section className="panel">
          <div className="panel-head"><h2>At a glance</h2></div>
          <dl className="facts">
            <div>
              <dt>Graded so far</dt>
              <dd>
                {columns.length
                  ? `${graded} of ${columns.length} items`
                  : "no gradebook items"}
              </dd>
            </div>
            <div>
              <dt>Weighting</dt>
              <dd>
                {standing?.weighted_by === "syllabus"
                  ? "read from the syllabus"
                  : "by points — no syllabus weighting yet"}
              </dd>
            </div>
            {standing?.late_policy && (
              <div>
                <dt>Late work</dt>
                <dd>{standing.late_policy}</dd>
              </div>
            )}
            {standing?.syllabus?.filename && (
              <div>
                <dt>Syllabus</dt>
                <dd>{standing.syllabus.filename}</dd>
              </div>
            )}
          </dl>
        </section>
      </div>

      <section className="panel">
        <div className="panel-head">
          <h2>Announcements</h2>
          <span className="note dim">{announcements.length} posted</span>
          <div className="spacer" />
          {announcements.length > recent.length && (
            <button onClick={() => onTab("announcements")}>See all</button>
          )}
        </div>

        {recent.length === 0 ? (
          <p className="empty">Nothing posted in this course.</p>
        ) : (
          recent.map((a, i) => {
            const body = (a.body ?? "").trim();
            return (
              <div className="ann" key={a.id ?? i}>
                <div className="ann-body">
                  <div className="ann-top">
                    <b>{a.title || "(untitled)"}</b>
                    {a.posted && (
                      <span className="note dim"
                            title={new Date(a.posted).toLocaleString()}>
                        {ago(new Date(a.posted))}
                      </span>
                    )}
                  </div>
                  {body && (
                    <p className="ann-text">
                      <RichText text={visibleLength(body) > ANN_CLAMP
                        ? `${clamp(body, ANN_CLAMP)}…`
                        : body} />
                    </p>
                  )}
                </div>
              </div>
            );
          })
        )}
      </section>
    </>
  );
}
