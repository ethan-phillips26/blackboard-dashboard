import { courseSlot, pct } from "../lib/format.js";
import { href } from "../lib/route.js";

/**
 * One course at a glance — a fixed shape so a row of them reads as a row.
 *
 * Everything that varies in length (category lists, syllabus warnings, the
 * calculator) lives on the course's own page; the card carries only the parts
 * every course has, so they all come out the same size.
 */
export default function CourseCard({ course, standing, order }) {
  const cats = standing?.categories ?? [];
  const columns = cats.flatMap((c) => c.columns ?? []);
  const graded = columns.filter((c) => c.graded).length;

  // Anything the student would want to fix, counted once.
  const attention =
    (standing?.unclassified?.length ?? 0) +
    (standing?.unmapped?.length ?? 0) +
    (standing?.suspect?.length ?? 0);
  const noSyllabus = standing?.weighted_by !== "syllabus";

  return (
    <a
      className={"ccard s" + courseSlot(course.label, order)}
      href={href.course(course.course_id, "grades")}
    >
      <div className="ccard-head">
        <i className="dot" />
        <div>
          <h3>{course.label}</h3>
          {/* The subtitle is dropped when the label already is the course's name. */}
          <div className="code">
            {course.title && course.title !== course.label ? course.title : "\u00a0"}
          </div>
        </div>
      </div>

      <div className="ccard-grade">
        {standing?.current_pct == null ? (
          <span className="big none">Nothing graded yet</span>
        ) : (
          <span className="big">
            {pct(standing.current_pct)}
            {standing.current_letter && (
              <span className="letter">{standing.current_letter}</span>
            )}
          </span>
        )}
      </div>

      <div className="ccard-meta note dim">
        {graded} of {columns.length} {columns.length === 1 ? "item" : "items"} graded
      </div>

      <div className="ccard-foot">
        {attention > 0 ? (
          <span className="flag warn">{attention} to check</span>
        ) : noSyllabus ? (
          <span className="flag">no syllabus weighting</span>
        ) : (
          <span className="flag ok">weighted by syllabus</span>
        )}
        <span className="go" aria-hidden="true">→</span>
      </div>
    </a>
  );
}
