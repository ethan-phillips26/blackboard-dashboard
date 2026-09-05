// What the browser tab says.
//
// The tab is the only part of the app visible when the app is not: a row of
// pinned tabs, a window switcher, a bookmark taken while reading something.
// So it names what is on screen rather than the product — "Tools Assignment 1 ·
// CSCI 413 · Whiteboard" — narrowest thing first, because that is the end a
// squeezed tab keeps.
//
// Screens are not the only claimants. A drawer or a document reader covers the
// screen underneath it and is what the reader is actually looking at, so it
// should own the title while it is open and hand it back on close. Mount order
// cannot decide that — React runs a child's effect before its parent's, so the
// page would overwrite the overlay it contains. Each claim carries a level
// instead, and the highest one wins; ties go to whichever claimed last.

import { useEffect } from "react";

export const BRAND = "Whiteboard";

// Claim levels, coarse on purpose: a screen, something laid over it, something
// laid over that.
export const SCREEN = 0;
export const OVERLAY = 10;
export const READER = 20;

const claims = new Map();
let nextId = 1;

/** Tabs are narrow; a segment that runs on says nothing the tab can show. */
function clean(part) {
  const text = String(part ?? "").replace(/\s+/g, " ").trim();
  return text.length > 64 ? text.slice(0, 63).trimEnd() + "…" : text;
}

function render() {
  let best = null;
  for (const claim of claims.values()) {
    if (!best || claim.level > best.level ||
        (claim.level === best.level && claim.id > best.id)) {
      best = claim;
    }
  }
  const parts = (best?.parts ?? []).map(clean).filter(Boolean);
  // The brand is the last segment and never repeats: the dashboard's own name
  // *is* the brand, so it claims nothing and gets "Whiteboard" on its own.
  if (parts[parts.length - 1] !== BRAND) parts.push(BRAND);
  document.title = parts.join(" · ");
}

/**
 * Name the tab for as long as this component is mounted.
 *
 * @param {Array<string|null|undefined|false>} parts Narrowest first; empty
 *   entries drop out, so a value still loading simply shortens the title.
 * @param {number} level Which claim wins when several are mounted at once.
 */
export function useTitle(parts, level = SCREEN) {
  // Callers build this array inline, so it is a new array every render; the
  // contents are what actually changed.
  const key = JSON.stringify((parts ?? []).map((p) => p || ""));
  useEffect(() => {
    const id = nextId++;
    claims.set(id, { id, level, parts: JSON.parse(key) });
    render();
    return () => {
      claims.delete(id);
      render();
    };
  }, [key, level]);
}
