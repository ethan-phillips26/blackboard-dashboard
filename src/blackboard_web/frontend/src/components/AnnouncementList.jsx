import { useState } from "react";
import { ago, courseSlot } from "../lib/format.js";
import { arrivedAt } from "../lib/read.js";
import RichText, { clamp, visibleLength } from "./RichText.jsx";

const CLAMP = 220;

/**
 * Announcements, newest first, each one expandable.
 *
 * `order` is the course colour order. On a single course's page there is only
 * one course, so it is left out and the colour swatch and course chip go with
 * it — repeating the course name on every row of its own page says nothing.
 */
export default function AnnouncementList({ announcements, firstSeen, since,
                                           order }) {
  const [open, setOpen] = useState(() => new Set());

  function toggle(id) {
    setOpen((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  return announcements.map((a, i) => {
    const id = a.id ?? `${a.course}-${i}`;
    const body = (a.body ?? "").trim();
    const long = visibleLength(body) > CLAMP;
    const isOpen = open.has(id);
    const isNew = since != null && arrivedAt(a, firstSeen) > since;
    return (
      <div
        className={"ann" + (order ? ` s${courseSlot(a.course, order)}` : "")}
        key={id}
      >
        {order && <i className="dot" />}
        <div className="ann-body">
          <div className="ann-top">
            <b>{a.title || "(untitled)"}</b>
            {isNew && <span className="badge-new">new</span>}
            {order && <span className="course">{a.course}</span>}
            {a.posted && (
              <span className="note dim" title={new Date(a.posted).toLocaleString()}>
                {ago(new Date(a.posted))}
              </span>
            )}

          </div>
          {body && (
            <p className="ann-text">
              <RichText text={isOpen || !long ? body : `${clamp(body, CLAMP)}…`} />
            </p>
          )}
          {long && (
            <button className="linkish" onClick={() => toggle(id)}>
              {isOpen ? "Show less" : "Read more"}
            </button>
          )}
        </div>
      </div>
    );
  });
}
