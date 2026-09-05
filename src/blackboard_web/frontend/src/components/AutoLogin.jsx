import { useEffect, useRef, useState } from "react";

/**
 * Signing back in, without asking again for something already known.
 *
 * A Blackboard session ends on its own — an idle timeout, or the hard daily cap
 * an SSO puts on it however active you were — and the old sign-in form met that
 * with three empty boxes, two of which the server could already fill in. This
 * screen fills them in and runs the same login, so an expiry is a few seconds of
 * waiting rather than a form.
 *
 * The other account is always one press away: this is a convenience, not a lock.
 */
export default function AutoLogin({ auth, state, error, stage, onStart, onRetry,
                                    onManual }) {
  const started = useRef(false);
  const [seconds, setSeconds] = useState(0);
  const running = state === "running";

  // Fire once when the screen appears. A failure hands the retry to the reader
  // rather than looping at the login endpoint on its own.
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    onStart();
  }, [onStart]);

  useEffect(() => {
    if (!running) return setSeconds(0);
    const id = setInterval(() => setSeconds((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [running]);

  const host = (auth?.host ?? "").replace(/^https?:\/\//, "");

  return (
    <div className="boot">
      <div className="panel login-panel auto-login">
        <div className="login-brand">
          <span className="brand-mark" aria-hidden="true" />
          <h2>{running ? "Signing you back in" : "Could not sign you back in"}</h2>
        </div>

        <p className="note dim">
          {running
            ? "Your Blackboard session expired. Whiteboard is signing in again " +
              "with the credentials you already gave it."
            : "The saved credentials did not get through this time. You can try " +
              "again, or sign in as someone else."}
        </p>

        {(auth?.username || host) && (
          <div className="auto-who">
            {auth?.username && <b>{auth.username}</b>}
            {host && <span className="note dim">{host}</span>}
          </div>
        )}

        {running ? (
          <div className={"auto-status" + (stage?.waiting ? " waiting" : "")}
               role="status">
            <span className="spin" />
            <span>
              {stage?.label ?? `Signing in…${seconds > 3 ? ` ${seconds}s` : ""}`}
              {/* Until the server has said which step it is on, the old advice
                  is still the useful thing to show a reader who has been
                  watching a spinner for ten seconds. */}
              {!stage && seconds > 8 && (
                <span className="note dim auto-duo">
                  If your university asks for it, approve the Duo prompt on your
                  phone — this screen is waiting for it.
                </span>
              )}
            </span>
          </div>
        ) : (
          error && <p className="err">{error}</p>
        )}

        <div className="auto-actions">
          {!running && (
            <button className="primary" onClick={onRetry}>Try again</button>
          )}
          <button className="linkish" onClick={onManual}>
            Sign in with a different account
          </button>
        </div>
      </div>
    </div>
  );
}
