async function request(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    let reason = null;
    try {
      const body = (await res.json()).detail;
      // A failure the caller has to tell apart from other failures — a refused
      // password, say — arrives as an object rather than a sentence.
      if (body && typeof body === "object") {
        detail = body.message ?? detail;
        reason = body.reason ?? null;
      } else if (body) {
        detail = body;
      }
    } catch {
      /* keep the status text */
    }
    const error = new Error(detail);
    error.reason = reason;
    error.status = res.status;
    throw error;
  }
  return res.json();
}

/** The calendar feed doubles as a subscription URL, so it is named once here. */
export const CALENDAR_URL = "/api/calendar.ics";

export const api = {
  /** The raw iCalendar document the calendar grid renders. */
  calendar: async (refresh = false) => {
    const res = await fetch(`${CALENDAR_URL}?refresh=${refresh}`, {
      headers: { Accept: "text/calendar" },
    });
    if (!res.ok) throw new Error(`Calendar feed failed (${res.status})`);
    return res.text();
  },
  state: (refresh = false) => request(`/api/state?refresh=${refresh}`),
  authStatus: () => request("/api/auth/status"),
  login: ({ host, username, password }) =>
    request("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ host, username, password }),
    }),
  /** Which step a sign-in in flight is on — "duo" is the one worth saying. */
  loginProgress: () => request("/api/auth/progress"),
  /** Sign back in with what the server already has — nothing typed. */
  relogin: () => request("/api/auth/relogin", { method: "POST", body: "{}" }),
  logout: () => request("/api/auth/logout", { method: "POST" }),
  assignment: (courseId, contentId) =>
    request(`/api/assignments/${courseId}/${contentId}`),
  fetchFiles: (courseId, contentId) =>
    request(`/api/assignments/${courseId}/${contentId}/files`, { method: "POST" }),
  googleStatus: () => request("/api/google/status"),
  googleSaveCredentials: (body) =>
    request("/api/google/credentials", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  googleForget: () => request("/api/google/credentials", { method: "DELETE" }),
  googleAuthorise: () => request("/api/google/authorise", { method: "POST" }),
  googleDisconnect: () => request("/api/google/disconnect", { method: "POST" }),
  openInDocs: (courseId, contentId, filename) =>
    request(`/api/assignments/${courseId}/${contentId}/gdocs`, {
      method: "POST",
      body: JSON.stringify({ filename }),
    }),
  refresh: () => request("/api/refresh", { method: "POST" }),
  /** Delete everything fetched, derived or downloaded. Logins are not touched. */
  resetData: () => request("/api/reset", { method: "POST" }),
  /** Record that these announcements have been popped up, so they are not again. */
  markAnnounced: (ids) =>
    request("/api/announcements/announced", {
      method: "POST",
      body: JSON.stringify({ ids }),
    }),
  courseContent: (courseId, refresh = false) =>
    request(`/api/courses/${courseId}/content?refresh=${refresh}`),
  weights: (courseId, extract = false) =>
    request(`/api/courses/${courseId}/weights?extract=${extract}`),
  setMapping: (courseId, syllabus_label, category_id) =>
    request(`/api/courses/${courseId}/mapping`, {
      method: "POST",
      body: JSON.stringify({ syllabus_label, category_id }),
    }),
  needed: (courseId, column_id, target, rate) =>
    request(`/api/courses/${courseId}/needed`, {
      method: "POST",
      body: JSON.stringify({ column_id, target, rate }),
    }),

  // Local corrections. These never reach Blackboard — they change what this
  // dashboard shows and what its calendar feed publishes.
  edits: () => request("/api/edits"),
  editAssignment: (key, patch) =>
    request(`/api/edits/assignments/${encodeURIComponent(key)}`, {
      method: "PUT",
      body: JSON.stringify(patch),
    }),
  resetAssignment: (key) =>
    request(`/api/edits/assignments/${encodeURIComponent(key)}`, {
      method: "DELETE",
    }),
  editWeights: (courseId, weights) =>
    request(`/api/edits/weights/${courseId}`, {
      method: "PUT",
      body: JSON.stringify({ weights }),
    }),
  resetWeights: (courseId) =>
    request(`/api/edits/weights/${courseId}`, { method: "DELETE" }),
};
