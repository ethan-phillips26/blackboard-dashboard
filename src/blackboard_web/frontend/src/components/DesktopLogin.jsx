import { useState } from "react";

/**
 * Signing in through a real browser window, which the desktop app can open.
 *
 * Every other route into Blackboard has to imitate a person: find the username
 * box, find the submit button, anticipate the second factor. That only ever
 * works at the institutions somebody thought to test, and there is no way to
 * test the rest. Here the person signs in exactly as they would in their own
 * browser — whatever the provider asks for, in whatever order — and the app
 * only reads the session that comes out of it.
 *
 * So there is nothing to ask for but the address of the school's Blackboard.
 */
export default function DesktopLogin({ auth, waiting, error, onOpen }) {
  const known = (auth?.host ?? "").replace(/^https?:\/\//, "");
  const [host, setHost] = useState(known);

  function submit(event) {
    event.preventDefault();
    const value = host.trim();
    if (value) onOpen(value);
  }

  return (
    <div className="boot">
      <div className="panel login-panel">
        <div className="login-brand">
          <span className="brand-mark" aria-hidden="true" />
          <h2>{waiting ? "Waiting for your sign-in" : "Sign in to Blackboard"}</h2>
        </div>

        {waiting ? (
          <>
            <p className="note dim">
              <span className="spin" /> A sign-in window is open. Finish signing
              in there — including anything on your phone — and this picks up
              from wherever it lands.
            </p>
            <p className="note dim">
              Closing that window cancels, and brings you back here.
            </p>
          </>
        ) : (
          <form className="login-form" onSubmit={submit}>
            <p className="note dim">
              Your school's Blackboard address. Everything after this happens in
              an ordinary browser window, so whatever your university asks for
              will work.
            </p>
            <label className="field">
              <span>Blackboard address</span>
              <input
                type="text"
                value={host}
                onChange={(e) => setHost(e.target.value)}
                placeholder="blackboard.university.edu"
                autoFocus
                spellCheck={false}
                autoCapitalize="off"
                autoCorrect="off"
              />
            </label>
            <button type="submit" className="primary login-submit"
                    disabled={!host.trim()}>
              Open the sign-in window
            </button>
          </form>
        )}

        {error && <p className="note error">{error}</p>}
      </div>
    </div>
  );
}
