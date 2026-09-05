import { useEffect, useRef } from "react";
import { arrivedAt } from "../lib/read.js";
import AnnouncementList from "./AnnouncementList.jsx";

/**
 * Every announcement, newest first.
 *
 * This is a screen you arrive at on purpose, so the list is whole — there is no
 * "show all" to press. Opening it is what marks the sidebar badge read.
 */
export default function Announcements({ announcements, firstSeen, readAt,
                                        onRead, order }) {
  // Freeze the read mark at the moment the screen opened: marking it read first
  // would erase the very flags you came here to look at.
  const since = useRef(readAt).current;
  useEffect(() => {
    window.scrollTo(0, 0);
    onRead();
  }, [onRead]);

  const unread = announcements.filter(
    (a) => arrivedAt(a, firstSeen) > since
  ).length;

  return (
    <>
      <header className="page-head">
        <div>
          <h1>Announcements</h1>
          <p className="note dim">
            {announcements.length} recent
            {unread > 0 && <> · {unread} new since you last looked</>}
          </p>
        </div>
      </header>

      {announcements.length === 0 ? (
        <section className="panel reading">
          <p className="empty">Nothing posted in your courses.</p>
        </section>
      ) : (
        <section className="panel reading">
          <AnnouncementList
            announcements={announcements}
            firstSeen={firstSeen}
            since={since}
            order={order}
          />
        </section>
      )}
    </>
  );
}
