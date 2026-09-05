// Announcements moved off the dashboard, so the sidebar badge is the only thing
// that says something new was posted. It has to mean "you have not looked at
// this yet" — a badge that shows the same number forever is a badge you stop
// reading.
//
// Read state is a browser preference like the theme: it belongs to the screen
// you read on, not to the account.

const KEY = "announcements-read-at";

export function readAt() {
  try {
    const at = Number(localStorage.getItem(KEY));
    return Number.isFinite(at) ? at : 0;
  } catch {
    return 0;
  }
}

export function markRead(at = Date.now()) {
  try {
    localStorage.setItem(KEY, String(at));
  } catch {
    // Storage blocked: the badge clears for this session and comes back later.
  }
  return at;
}

/**
 * When an announcement arrived *here*.
 *
 * The first sync that saw it is the honest answer — a course that posts its
 * welcome message in August should not still be shouting in November just
 * because you connected the dashboard today. Its posted date is the fallback
 * for anything the server has not recorded.
 */
export function arrivedAt(announcement, firstSeen) {
  const at = Date.parse(
    firstSeen?.[announcement.id] ?? announcement.posted ?? ""
  );
  return Number.isFinite(at) ? at : 0;
}

export function countUnread(announcements, firstSeen, since) {
  return announcements.filter((a) => arrivedAt(a, firstSeen) > since).length;
}
