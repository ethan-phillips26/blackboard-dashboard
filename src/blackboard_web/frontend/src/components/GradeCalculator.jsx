import { useMemo, useState } from "react";
import { api } from "../api.js";

export default function GradeCalculator({ courseId, standing }) {
  const ungraded = useMemo(
    () =>
      standing.categories.flatMap((c) =>
        c.columns.filter((col) => !col.graded).map((col) => ({ ...col, cat: c.title }))
      ),
    [standing]
  );

  const [columnId, setColumnId] = useState(ungraded[0]?.column_id ?? "");
  const [target, setTarget] = useState(90);
  const [rate, setRate] = useState(100);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  if (!ungraded.length) {
    return (
      <div className="calc">
        <p className="note">Nothing ungraded left to calculate against.</p>
      </div>
    );
  }

  async function run() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.needed(courseId, columnId, Number(target), Number(rate) / 100));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="calc">
      <div className="controls">
        <label className="field">
          <span>On this</span>
          <select value={columnId} onChange={(e) => setColumnId(e.target.value)}>
            {ungraded.map((c) => (
              <option key={c.column_id} value={c.column_id}>
                {c.name} ({c.possible} pt)
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Target %</span>
          <input
            type="number" min="0" max="100" value={target}
            onChange={(e) => setTarget(e.target.value)}
          />
        </label>
        <label
          className="field"
          title="What you assume you'll score on everything else still outstanding"
        >
          <span>Rest at %</span>
          <input
            type="number" min="0" max="100" value={rate}
            onChange={(e) => setRate(e.target.value)}
          />
        </label>
        <button className="primary" onClick={run} disabled={busy}>
          {busy ? <><span className="spin" /> Working</> : "Calculate"}
        </button>
      </div>

      {error && <p className="err">{error}</p>}

      {result && (
        <div
          className={`result ${
            result.already_secured ? "good" : result.achievable ? "" : "bad"
          }`}
        >
          {result.already_graded ? (
            <>
              Already graded: <strong>{result.score}</strong> / {result.possible}
            </>
          ) : result.already_secured ? (
            <>
              <div className="num">Already secured</div>
              You reach {result.target}% even with 0 on {result.name}, assuming the rest
              comes in at {Math.round(result.assumed_rate * 100)}%.
            </>
          ) : (
            <>
              <div className="num">
                {result.needed_points} / {result.possible}
                <span className="dim"> ({result.needed_pct}%)</span>
              </div>
              on <strong>{result.name}</strong> to finish at {result.target}%, assuming
              everything else outstanding comes in at {Math.round(result.assumed_rate * 100)}%.
              {!result.achievable && (
                <div className="foot-note">
                  <strong>Not reachable on this assignment alone</strong> — it needs more
                  than full marks. Lower the target or raise your assumption for the rest.
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
