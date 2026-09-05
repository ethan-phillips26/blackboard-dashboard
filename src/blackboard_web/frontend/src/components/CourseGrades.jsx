import { useState } from "react";
import { api } from "../api.js";
import GradeCalculator from "./GradeCalculator.jsx";

/**
 * Where the course's grade actually comes from: the weighting, the arithmetic,
 * and every row that feeds it.
 *
 * The syllabus panel lives here rather than on its own tab because it is only
 * ever read to answer a question about the numbers beside it.
 */
export default function CourseGrades({ course, standing, onChange }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function readSyllabus() {
    setBusy(true);
    setError(null);
    try {
      await api.weights(course.course_id, true);
      await onChange();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function mapCategory(label, categoryId) {
    setBusy(true);
    try {
      await api.setMapping(course.course_id, label, categoryId || null);
      await onChange();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const weighted = standing?.weighted_by === "syllabus";
  const inferred = standing?.grouped_by === "inferred";

  return (
    <>
      <div className="split">
        <section className="panel">
          <div className="panel-head">
            <h2>Breakdown</h2>
            <span className="note dim">
              weighted by {standing.weighted_by}
              {inferred && <> · items sorted by name</>}
            </span>
          </div>

          {standing.categories.map((c) => (
            <div className="catrow" key={c.category_id ?? c.title}>
              <span className="nm">{c.title}</span>
              <span className="w">
                {weighted && c.weight != null
                  ? `${Math.round(c.weight * 100)}%`
                  : `${Math.round(c.effective_weight * 100)}%*`}
              </span>
              <span className="bar">
                <i style={{ width: `${Math.max(0, Math.min(100, c.pct ?? 0))}%` }} />
              </span>
              <span className="w score">
                {c.pct === null ? "—" : `${c.pct}%`}
              </span>
            </div>
          ))}

          {!weighted && (
            <p className="note dim foot-note">
              * weighted by points — no syllabus weighting stored yet.
            </p>
          )}

          {standing.empty_components?.length > 0 && (
            <p className="note dim foot-note">
              Nothing in the gradebook yet for{" "}
              {standing.empty_components.join(", ")}.
            </p>
          )}
        </section>

        <section className="panel">
          <div className="panel-head"><h2>What do I need?</h2></div>
          <GradeCalculator courseId={course.course_id} standing={standing} />
        </section>
      </div>

      {standing.categories.some((c) => c.columns?.length) && (
        <section className="panel">
          <div className="panel-head"><h2>Items</h2></div>
          <table className="items">
            <tbody>
              {standing.categories.flatMap((c) =>
                (c.columns ?? []).map((col) => (
                  <tr key={col.column_id}>
                    <td className="it-name">{col.name}</td>
                    <td className="it-cat"><span className="course">{c.title}</span></td>
                    <td className="it-score">
                      {col.graded ? `${col.score} / ${col.possible}` : `— / ${col.possible}`}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </section>
      )}

      <section className="panel">
        <div className="panel-head">
          <h2>Syllabus</h2>
          <div className="spacer" />
          <button onClick={readSyllabus} disabled={busy}>
            {busy ? <><span className="spin" /> Reading</>
              : weighted ? "Re-read syllabus" : "Read syllabus & sort gradebook"}
          </button>
        </div>

        {standing.syllabus ? (
          <p className="note dim">Read from {standing.syllabus.filename}.</p>
        ) : standing.weight_status === "no_syllabus_found" ? (
          <p className="empty">No syllabus file found in this course.</p>
        ) : (
          /* A head over an empty box reads as something that failed to load. */
          <p className="empty">
            Nothing read yet — the syllabus is what turns a points total into
            the weighting your instructor actually grades on.
          </p>
        )}
        {standing.late_policy && (
          <p className="note foot-note">
            <strong>Late:</strong> {standing.late_policy}
          </p>
        )}
        {error && <p className="err">{error}</p>}

        {standing.unclassified?.length > 0 && (
          <div className="warn-box">
            <strong>Not sorted.</strong> These gradebook items don't match any
            component the syllabus weights, so they're excluded:
            <div className="note foot-note">
              {standing.unclassified.join(", ")}
            </div>
          </div>
        )}

        {standing.suspect?.length > 0 && (
          <div className="warn-box">
            <strong>Check this mapping.</strong>{" "}
            {standing.suspect.map((s) => (
              <div key={s.syllabus_label}>
                "{s.syllabus_label}" ({s.weight_pct}%) is {s.reason}.
              </div>
            ))}
          </div>
        )}

        {standing.unmapped?.length > 0 && (
          <div className="warn-box">
            <strong>Unmapped weighting.</strong> The syllabus counts these toward
            your grade, but they don't match a Blackboard category, so they're
            excluded:
            {standing.unmapped.map((u) => (
              <div className="row map-row" key={u.syllabus_label}>
                <span className="spacer">
                  {u.syllabus_label} ({u.weight_pct}%)
                </span>
                <select
                  defaultValue=""
                  disabled={busy}
                  onChange={(e) => mapCategory(u.syllabus_label, e.target.value)}
                >
                  <option value="">map to…</option>
                  {standing.available_categories.map((c) => (
                    <option key={c.id} value={c.id}>{c.title}</option>
                  ))}
                </select>
              </div>
            ))}
          </div>
        )}
      </section>
    </>
  );
}
