import { useEffect, useRef } from "react";
import { go, href } from "../lib/route.js";
import AnnouncementList from "./AnnouncementList.jsx";

/**
 * What was posted while you were away, said once.
 *
 * The sidebar badge is a good ambient signal and a bad urgent one — a course
 * cancelled tomorrow's lecture at 6am and the badge says "1" as quietly as it
 * says everything else. So a post that has never been shown gets one modal on
 * load, and is then marked on the server as shown: the record is why this does
 * not become a thing you dismiss every morning, and why it stays dismissed on
 * the laptop as well as the desktop.
 *
 * Marking happens when it opens, not when it is closed. "Shown" is the honest
 * condition — a modal read and then abandoned by closing the tab has done its
 * job, and re-showing it on the next load would be the exact nag this is meant
 * to avoid.
 */
export default function NewAnnouncements({ announcements, order, onShown,
                                           onClose }) {
  const closeRef = useRef(null);
  const marked = useRef(false);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Strict mode mounts effects twice in development; the ref keeps that from
  // becoming two writes.
  useEffect(() => {
    if (marked.current) return;
    marked.current = true;
    onShown(announcements.map((a) => a.id));
  }, [announcements, onShown]);

  const many = announcements.length > 1;

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="modal ann-modal" role="dialog" aria-modal="true"
           aria-label={many ? "New announcements" : "New announcement"}>
        <div className="modal-head">
          <h3>{many ? `${announcements.length} new announcements` : "New announcement"}</h3>
          <div className="spacer" />
          <button ref={closeRef} className="primary" onClick={onClose}>
            Got it
          </button>
        </div>
        <div className="modal-body reading">
          {/* No `since`: everything in here is new by construction, so a "new"
              flag on every row would only be noise. */}
          <AnnouncementList announcements={announcements} order={order} />
        </div>
        <div className="modal-foot">
          <button
            className="linkish"
            onClick={() => { onClose(); go(href.announcements); }}
          >
            Open announcements
          </button>
        </div>
      </div>
    </>
  );
}
