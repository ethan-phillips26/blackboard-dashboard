import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { extension, save } from "../lib/download.js";
import { dateTime, filesize, points, relative } from "../lib/format.js";
import { OVERLAY, useTitle } from "../lib/title.js";
import { canView } from "../lib/viewer.js";
import BbLink from "./BbLink.jsx";
import DocumentViewer from "./DocumentViewer.jsx";
import RichText from "./RichText.jsx";
import { Sep } from "./Sep.jsx";

/**
 * The assignment behind a deadline: what it actually says, and its handouts.
 *
 * `target` is whatever the calendar or the deadline list knows — course, title,
 * due date — so the drawer has something real to show from the first frame,
 * before the instructions arrive.
 */
export default function AssignmentDrawer({ target, onClose }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [files, setFiles] = useState(null);
  const [downloading, setDownloading] = useState(null);
  const [fileError, setFileError] = useState(null);
  const [viewing, setViewing] = useState(null);
  const [opening, setOpening] = useState(null);
  const [google, setGoogle] = useState(null);
  const [docsBusy, setDocsBusy] = useState(null);
  const [docs, setDocs] = useState({});
  const closeRef = useRef(null);

  const { courseId, contentId } = target;

  // The drawer covers the screen that opened it, so while it is up it is what
  // the tab should name — a bookmark or a pinned tab taken here says which
  // assignment was being read, not merely which course.
  useTitle([detail?.title ?? target.title, target.course], OVERLAY);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  // Escape belongs to whatever is on top. Both this and the viewer listen on the
  // window, and this one registered first, so it has to stand down itself rather
  // than rely on the viewer stopping the event — otherwise one press closes the
  // document you were reading *and* the assignment behind it.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape" && !viewing) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, viewing]);

  // Whether the Docs button can work is a property of the machine, not of this
  // assignment, so ask once when the drawer opens.
  useEffect(() => {
    let live = true;
    api.googleStatus()
      .then((g) => live && setGoogle(g))
      .catch(() => live && setGoogle({ configured: false }));
    return () => { live = false; };
  }, []);

  useEffect(() => {
    // A manually created gradebook column has no content item behind it, so
    // there are no instructions to fetch and nothing to download.
    if (!contentId || !courseId) {
      setLoading(false);
      return;
    }
    let live = true;
    setLoading(true);
    setError(null);
    setFiles(null);
    api
      .assignment(courseId, contentId)
      .then((d) => live && setDetail(d))
      .catch((e) => live && setError(e.message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [courseId, contentId]);

  /**
   * Bring this item's files down if they are not here yet, and hand back the one
   * that was asked for. One call fetches every attachment, so the second row you
   * press is served from what the first already got rather than from Blackboard
   * again.
   */
  async function fetched(filename, index) {
    const result = files ?? await api.fetchFiles(courseId, contentId);
    if (!files) setFiles(result);
    const row = result?.downloaded?.find((f) => f.filename === filename)
      ?? result?.downloaded?.[index];
    if (!row?.url) throw new Error(`Blackboard would not give up ${filename}.`);
    return row;
  }

  async function download(filename, index) {
    setDownloading(filename);
    setFileError(null);
    try {
      const row = await fetched(filename, index);
      save(row.url, row.filename);
    } catch (e) {
      setFileError(e.message);
    } finally {
      setDownloading(null);
    }
  }

  /** A file has to be here before it can be read, so opening it fetches it. */
  async function view(filename, index) {
    setOpening(filename);
    setFileError(null);
    try {
      setViewing(await fetched(filename, index));
    } catch (e) {
      setFileError(e.message);
    } finally {
      setOpening(null);
    }
  }

  async function openDocs(filename) {
    setDocsBusy(filename);
    setFileError(null);
    try {
      const res = await api.openInDocs(courseId, contentId, filename);
      setDocs((d) => ({ ...d, [filename]: res }));
    } catch (e) {
      setFileError(e.message);
    } finally {
      setDocsBusy(null);
    }
  }

  const attachments = detail?.attachments ?? [];
  const canDocs = !!google?.authorised;

  /** Can Drive turn this into an editable document?
   *
   * A .pptx or a .zip uploads fine but lands in the Drive viewer, so a button
   * saying "Open in Docs" on one is a promise the upload does not keep. The
   * list comes from the server rather than being repeated here, because it is
   * the same list the upload itself branches on.
   */
  const asDoc = (filename) => {
    const types = google?.convertible;
    if (!types?.length) return false;
    const dot = (filename ?? "").lastIndexOf(".");
    return dot > 0 && types.includes(filename.slice(dot).toLowerCase());
  };
  const needsSetup = !!google && !google.authorised;

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true"
             aria-label={target.title || "Assignment"}>
        <div className="drawer-head">
          <div className="drawer-title">
            <h3>{detail?.title || target.title}</h3>
            <div className="drawer-meta">
              {target.course && <span className="course">{target.course}</span>}
              <span className="note dim">
                {target.due
                  ? <>{dateTime(target.due)}<Sep />{relative(target.due)}</>
                  : "no due date"}
                {points(target.points) ? <><Sep />{points(target.points)}</> : null}
              </span>
            </div>
          </div>
          {detail?.web_url && <BbLink href={detail.web_url} className="btn bblink" />}
          <button ref={closeRef} onClick={onClose} aria-label="Close">Close</button>
        </div>

        <div className="drawer-body">
          {!contentId && (
            <p className="note">
              This is a manually created gradebook column — there is no content item
              behind it in Blackboard, so it has no instructions or files to fetch.
              Look for the material under the course's Materials tab.
            </p>
          )}

          {loading && <p className="empty"><span className="spin" /> Reading the assignment…</p>}
          {error && <p className="err">{error}</p>}

          {detail && (
            <>
              {detail.instructions?.trim() ? (
                <pre className="instructions">
                  <RichText text={detail.instructions.trim()} />
                </pre>
              ) : (
                <p className="empty">There is nothing written on this item.</p>
              )}

              <div className="drawer-files">
                <div className="panel-head">
                  <h2>Files</h2>
                  <span className="note dim">
                    {attachments.length || "no"} attached
                  </span>
                </div>

                {attachments.map((a, i) => {
                  // Once downloaded, the same file is servable by name.
                  const saved = files?.downloaded?.find(
                    (f) => f.filename === a.filename
                  ) ?? files?.downloaded?.[i];
                  // Once downloaded the size is measured rather than claimed.
                  const size = filesize(saved?.bytes ?? a.bytes);
                  const doc = docs[a.filename];
                  return (
                    <div className="filerow" key={a.filename ?? i}>
                      <span className="fileext" aria-hidden="true">
                        {extension(a.filename)}
                      </span>
                      <span className="fileid">
                        {canView(a.filename) ? (
                          <button
                            className="fn linkish"
                            title={`Read ${a.filename} here`}
                            onClick={() => view(a.filename, i)}
                          >
                            {a.filename}
                          </button>
                        ) : (
                          <span className="fn" title={a.filename}>{a.filename}</span>
                        )}
                        {size && <span className="fsize">{size}</span>}
                      </span>
                      <span className="filerow-actions">
                        {canView(a.filename) && (
                          <button
                            className="primary"
                            onClick={() => view(a.filename, i)}
                            disabled={opening === a.filename}
                          >
                            {opening === a.filename
                              ? <><span className="spin" /> Opening</>
                              : "View"}
                          </button>
                        )}
                        <button
                          onClick={() => download(a.filename, i)}
                          disabled={downloading === a.filename}
                        >
                          {downloading === a.filename
                            ? <><span className="spin" /> Fetching</>
                            : "Download"}
                        </button>
                        {canDocs && asDoc(a.filename) && (
                          doc ? (
                            <a className="btn" href={doc.url}
                               target="_blank" rel="noreferrer">
                              Reopen in Docs
                            </a>
                          ) : (
                            <button
                              onClick={() => openDocs(a.filename)}
                              disabled={docsBusy === a.filename}
                              title={`Upload to Google Docs and open in ${google?.browser ?? "your browser"}`}
                            >
                              {docsBusy === a.filename
                                ? <><span className="spin" /> Opening</>
                                : "Open in Docs"}
                            </button>
                          )
                        )}
                      </span>
                    </div>
                  );
                })}

                {fileError && <p className="err">{fileError}</p>}
                {files?.failed?.length > 0 && (
                  <p className="err">
                    {files.failed.length} file(s) could not be fetched:{" "}
                    {files.failed.map((f) => f.filename).join(", ")}
                  </p>
                )}
                {files?.directory && (
                  <p className="note dim">Saved to {files.directory}</p>
                )}
                {/* Only worth offering where it would change something: a
                    drawer holding one .zip has nothing to gain from Google. */}
                {needsSetup && attachments.some((a) => asDoc(a.filename)) && (
                  <p className="note dim">
                    <a className="link" href="#/settings">
                      {google.configured
                        ? "Connect Google in settings"
                        : "Set up Google Docs in settings"}
                    </a>{" "}
                    to open handouts as editable documents.
                  </p>
                )}
              </div>
            </>
          )}
        </div>
      </aside>

      {viewing && (
        <DocumentViewer
          file={viewing}
          onClose={() => setViewing(null)}
          onDocs={canDocs && asDoc(viewing.filename)
            ? () => openDocs(viewing.filename) : null}
          docsBusy={docsBusy === viewing.filename}
          docsUrl={docs[viewing.filename]?.url}
        />
      )}
    </>
  );
}
