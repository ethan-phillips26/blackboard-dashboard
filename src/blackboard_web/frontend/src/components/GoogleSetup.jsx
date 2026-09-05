import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";

// Google has no API for creating an OAuth client, so these five console pages
// are irreducibly manual. Linking each step straight to the page it happens on
// is the difference between a five-minute job and giving up.
const STEPS = [
  {
    href: "https://console.cloud.google.com/projectcreate",
    title: "Create a Google Cloud project",
    note: "Any name. It exists only to own the credentials.",
  },
  {
    href: "https://console.cloud.google.com/apis/library/drive.googleapis.com",
    title: "Enable the Google Drive API",
    note: "Press Enable on that page, with the new project selected.",
  },
  {
    href: "https://console.cloud.google.com/auth/branding",
    title: "Fill in the consent screen, and save it",
    note: "Three fields are required: app name, user support email, and — at " +
          "the bottom of the page, below the optional logo and domain boxes — " +
          "developer contact email. Choose External when it asks; Internal " +
          "exists only on a work or school account and restricts the app to " +
          "that organisation. Finish this before the next step: until it is " +
          "saved, Google calls the app incomplete and there is nothing to " +
          "publish.",
  },
  {
    href: "https://console.cloud.google.com/auth/audience",
    title: "Publish the app",
    note: "Audience → Publishing status → Publish app. While it says Testing, " +
          "Google expires the connection after 7 days and you would reconnect " +
          "every week. No publishing status on the page means either step 3 " +
          "is not saved yet, or you chose Internal — Internal apps have no " +
          "Testing mode and the 7-day limit does not apply to them.",
  },
  {
    href: "https://console.cloud.google.com/auth/clients",
    title: "Create client — type Desktop app",
    note: "Desktop, not Web: this signs in over a loopback address.",
  },
];

function Status({ status }) {
  const state = !status?.configured
    ? { tone: "flag", text: "Not set up" }
    : status.authorised
      ? { tone: "flag ok", text: "Connected" }
      : { tone: "flag warn", text: "Client saved — not connected" };
  return <span className={state.tone}>{state.text}</span>;
}

export default function GoogleSetup() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [manual, setManual] = useState(false);
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [consentUrl, setConsentUrl] = useState(null);
  const fileRef = useRef(null);
  const pollRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const next = await api.googleStatus();
      setStatus(next);
      return next;
    } catch (e) {
      setError(e.message);
      return null;
    }
  }, []);

  useEffect(() => {
    refresh();
    return () => clearInterval(pollRef.current);
  }, [refresh]);

  // Consent happens in another tab, so the only way to know it landed is to ask.
  const watch = useCallback(() => {
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const next = await refresh();
      if (!next || !next.pending) {
        clearInterval(pollRef.current);
        if (next?.authorised) setConsentUrl(null);
        if (next?.error) setError(next.error);
      }
    }, 2000);
  }, [refresh]);

  async function save(body) {
    setBusy(true);
    setError(null);
    try {
      setStatus(await api.googleSaveCredentials(body));
      setManual(false);
      setClientId("");
      setSecret("");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function onFile(e) {
    const file = e.target.files?.[0];
    if (file) await save({ client_json: await file.text() });
    if (fileRef.current) fileRef.current.value = "";
  }

  async function connect() {
    // The tab is opened synchronously off the click, before any await, or the
    // popup blocker eats it.
    const tab = window.open("about:blank", "_blank", "noopener");
    setBusy(true);
    setError(null);
    try {
      const res = await api.googleAuthorise();
      setStatus(res);
      setConsentUrl(res.url);
      if (tab) tab.location = res.url;
      watch();
    } catch (e) {
      if (tab) tab.close();
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function run(fn) {
    setBusy(true);
    setError(null);
    try {
      setStatus(await fn());
      setConsentUrl(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" id="google">
      <div className="panel-head">
        <h2>Google Docs</h2>
        <Status status={status} />
        <div className="spacer" />
        {status?.authorised && (
          <button onClick={() => run(api.googleDisconnect)} disabled={busy}>
            Disconnect
          </button>
        )}
        {status?.configured && (
          <button onClick={() => run(api.googleForget)} disabled={busy}>
            Forget client
          </button>
        )}
      </div>

      <p className="note dim gsetup-intro">
        Opens an assignment's handout as an editable Google Doc.
      </p>

      {error && <p className="err">{error}</p>}

      {!status ? (
        <p className="empty"><span className="spin" /> Checking…</p>
      ) : !status.configured ? (
        <>
          <ol className="gsteps">
            {STEPS.map((s, i) => (
              <li key={s.href}>
                <div className="gstep-n">{i + 1}</div>
                <div>
                  <a href={s.href} target="_blank" rel="noreferrer noopener">
                    {s.title}
                  </a>
                  <div className="note dim">{s.note}</div>
                </div>
              </li>
            ))}
          </ol>

          <div className="gdrop">
            <div>
              <b>Last step: upload the JSON</b>
              <div className="note dim">
                The download button beside the client you just created gives a{" "}
                <code>client_secret….json</code>. Drop it in — nothing to retype.
              </div>
            </div>
            <div className="spacer" />
            <input
              ref={fileRef}
              type="file"
              accept="application/json,.json"
              onChange={onFile}
              disabled={busy}
            />
          </div>

          {manual ? (
            <div className="gmanual">
              <label className="field">
                <span>Client ID</span>
                <input type="text" value={clientId}
                       onChange={(e) => setClientId(e.target.value)}
                       placeholder="….apps.googleusercontent.com" />
              </label>
              <label className="field">
                <span>Client secret</span>
                <input value={secret} type="password"
                       onChange={(e) => setSecret(e.target.value)} />
              </label>
              <button
                className="primary"
                disabled={busy || !clientId.trim() || !secret.trim()}
                onClick={() => save({ client_id: clientId, client_secret: secret })}
              >
                Save
              </button>
            </div>
          ) : (
            <button className="linkish" onClick={() => setManual(true)}>
              Or paste the two values by hand
            </button>
          )}
        </>
      ) : !status.authorised ? (
        <div className="gconnect">
          <div>
            <b>Connect your Google account</b>
            <div className="note dim">
              Opens Google's consent screen in a new tab. Sign in with the
              account whose Drive should hold the documents.
            </div>
            {status.pending && (
              <div className="note">
                <span className="spin" /> Waiting for consent…
                {consentUrl && (
                  <>
                    {" "}
                    <a className="link" href={consentUrl} target="_blank"
                       rel="noreferrer noopener">
                      reopen that tab
                    </a>
                  </>
                )}
              </div>
            )}
          </div>
          <div className="spacer" />
          <button className="primary" onClick={connect} disabled={busy}>
            {busy ? <span className="spin" /> : "Connect Google"}
          </button>
        </div>
      ) : (
        <p className="note dim">
          Ready — a handout now carries an <b>Open in Docs</b> button in the
          panel that shows its assignment
          {status.browser ? `, and opens in ${status.browser}` : ""}.
        </p>
      )}

      {status?.client_id && (
        <div className="note dim gclient">Client {status.client_id}</div>
      )}
    </section>
  );
}
