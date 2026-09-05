// Every top-level screen has its own URL, so the back button, a bookmark and a
// reload all land where you left off. The dashboard is the bare hash, which
// keeps it the thing you get by simply opening the app.

import { useEffect, useState } from "react";

export const HOME = "#/";

/** "#/course/_123_1/materials" -> { name: "course", id: "_123_1", tab: "materials" } */
export function parse(hash) {
  const path = (hash || "").replace(/^#\/?/, "");
  const [head, ...rest] = path.split("/").map(decodeURIComponent);
  const id = rest[0] || null;
  switch (head) {
    case "grades":
    case "announcements":
      return { name: head, id: null, tab: null };
    // A course without an id is not a screen; fall through to the dashboard.
    // The segment after the id is which of its tabs to open, so a bookmark
    // lands on the tab you were reading rather than the course's front page.
    case "course":
      return id
        ? { name: "course", id, tab: rest[1] || null }
        : { name: "overview", id: null, tab: null };
    // "#/settings" is the index; "#/settings/<id>" is one course's own page.
    case "settings":
      return { name: "settings", id, tab: null };
    default:
      return { name: "overview", id: null, tab: null };
  }
}

export function useRoute() {
  const [route, setRoute] = useState(() => parse(window.location.hash));
  useEffect(() => {
    const onHash = () => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return route;
}

export function go(hash) {
  window.location.hash = hash;
}

export const href = {
  overview: HOME,
  grades: "#/grades",
  announcements: "#/announcements",
  settings: "#/settings",
  course: (id, tab) =>
    `#/course/${encodeURIComponent(id)}${tab ? `/${tab}` : ""}`,
};
