import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CALENDAR_URL, api } from "./api.js";
import Announcements from "./components/Announcements.jsx";
import DesktopLogin from "./components/DesktopLogin.jsx";
import AssignmentDrawer from "./components/AssignmentDrawer.jsx";
import Calendar from "./components/Calendar.jsx";
import CoursePage, { TABS } from "./components/CoursePage.jsx";
import DueList from "./components/DueList.jsx";
import Grades from "./components/Grades.jsx";
import NewAnnouncements from "./components/NewAnnouncements.jsx";
import Settings from "./components/Settings.jsx";
import Sidebar from "./components/Sidebar.jsx";
import StatRow from "./components/StatRow.jsx";
import { courseOrder } from "./lib/format.js";
import { parseICS } from "./lib/ics.js";
import { countUnread, markRead, readAt as storedReadAt } from "./lib/read.js";
import { HOME, go, href, useRoute } from "./lib/route.js";
import { useTitle } from "./lib/title.js";
import { apply as applyTheme, stored as storedTheme, watch as watchTheme } from "./lib/theme.js";

export default function App() {
  const [auth, setAuth] = useState(null);
  const [desktopWaiting, setDesktopWaiting] = useState(false);
  const [authLoading, setAuthLoading] = useState(true);
  const [loginError, setLoginError] = useState(null);
  const [loggingOut, setLoggingOut] = useState(false);

  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [syncing, setSyncing] = useState(false);

  const [events, setEvents] = useState([]);
  const [calLoading, setCalLoading] = useState(true);
  const [calError, setCalError] = useState(null);

  const [drawer, setDrawer] = useState(null);
  // Announcements that have never been shown, held for one modal. The set is
  // what stops a background reload popping the same post up twice while the
  // server is still being told about the first time.
  const [popup, setPopup] = useState(null);
  const announced = useRef(new Set());
  const loadedAt = useRef(0);

  // Every screen has its own URL, so the back button and a bookmark both work.
  const route = useRoute();

  // Announcements live on their own screen now, so the sidebar badge is the
  // only thing that says one was posted. It counts what arrived after the last
  // time that screen was opened.
  const [annReadAt, setAnnReadAt] = useState(storedReadAt);
  const markAnnouncementsRead = useCallback(() => {
    setAnnReadAt(markRead());
  }, []);

  // The pre-paint script in index.html has already stamped the document; this
  // keeps React's idea of the choice in step and follows the OS while on
  // "system".
  const [theme, setTheme] = useState(storedTheme);
  useEffect(() => {
    applyTheme(theme);
    return watchTheme(theme, () => applyTheme("system"));
  }, [theme]);


  const loadAuth = useCallback(async () => {
    setAuthLoading(true);
    try {
      setAuth(await api.authStatus());
    } catch (e) {
      setLoginError(e.message);
      setAuth({ logged_in: false });
    } finally {
      setAuthLoading(false);
    }
  }, []);

  /** Re-read who the server thinks we are, without blanking the screen.
   *
   * `loadAuth` raises the "checking session" state, which unmounts the sign-in
   * form — and with it whatever the reader had typed. After a refused password
   * the form has to stay exactly where it is, so this updates the answer
   * underneath it instead. */
  const refreshAuth = useCallback(async () => {
    try {
      setAuth(await api.authStatus());
    } catch {
      // Keep what we had; the failure the reader is looking at is the real one.
    }
  }, []);

  // The calendar is rendered from the generated .ics rather than the JSON, so
  // the grid and the file you import into Google Calendar cannot disagree.
  const loadCalendar = useCallback(async (refresh = false) => {
    setCalLoading(true);
    setCalError(null);
    try {
      setEvents(parseICS(await api.calendar(refresh)).events);
    } catch (e) {
      setCalError(e.message);
    } finally {
      setCalLoading(false);
    }
  }, []);

  const load = useCallback(async (refresh = false) => {
    setError(null);
    if (refresh) setSyncing(true);
    try {
      setData(await api.state(refresh));
      loadedAt.current = Date.now();
    } catch (e) {
      setError(e.message);
      // A session that died while the tab was open shows up here first, as a
      // load that failed for no visible reason. Ask who we are before leaving
      // the reader staring at an error they cannot act on.
      const status = await api.authStatus().catch(() => null);
      if (status && !status.logged_in) {
        setAutoState("running");
        setAuth(status);
        setData(null);
      }
    } finally {
      setSyncing(false);
    }
  }, []);

  useEffect(() => {
    loadAuth();
  }, [loadAuth]);

  useEffect(() => {
    if (!auth?.logged_in) return;
    load(false);
    loadCalendar(false);
  }, [auth?.logged_in, load, loadCalendar]);

  // Coming back to a tab that has been open all afternoon should not show
  // this morning's deadlines. The server still serves from its own cache, so a
  // return visit inside the TTL costs nothing.
  useEffect(() => {
    const STALE_MS = 5 * 60 * 1000;
    function recheck() {
      if (!auth?.logged_in) return;
      if (document.visibilityState !== "visible") return;
      if (Date.now() - loadedAt.current < STALE_MS) return;
      load(false);
      loadCalendar(false);
    }
    window.addEventListener("focus", recheck);
    document.addEventListener("visibilitychange", recheck);
    return () => {
      window.removeEventListener("focus", recheck);
      document.removeEventListener("visibilitychange", recheck);
    };
  }, [auth?.logged_in, load, loadCalendar]);

  const markAnnounced = useCallback(async (ids) => {
    ids.forEach((id) => announced.current.add(id));
    try {
      await api.markAnnounced(ids);
    } catch {
      // The record is what makes this once-only; failing to write it means the
      // post is offered again next load, which is the harmless direction.
    }
  }, []);

  // Something posted while you were away is worth a modal once. It waits behind
  // an open drawer rather than stacking on top of it — two overlays would both
  // answer the same Escape.
  useEffect(() => {
    if (!data || drawer || popup) return;
    const fresh = (data.announcements ?? []).filter(
      (a) => (data.unannounced ?? []).includes(a.id) && !announced.current.has(a.id)
    );
    if (fresh.length) setPopup(fresh);
  }, [data, drawer, popup]);

  // A calendar event and a deadline row describe the same thing differently.
  const openAssignment = useCallback((source) => {
    setDrawer({
      courseId: source.courseId ?? source.course_id ?? null,
      contentId: source.contentId ?? source.content_id ?? null,
      title: source.title ?? source.summary ?? "",
      course: source.course ?? "",
      points: source.points ?? source.points_possible ?? null,
      due: source.due ?? (source.due_local ? new Date(source.due_local) : null),
    });
  }, []);

  async function syncNow() {
    await load(true);
    // The feed is already warm at this point; re-read it without a second fetch
    // of Blackboard itself.
    await loadCalendar(false);
  }

  /** Ask the shell for a sign-in window, then wait for the session it yields. */
  const openDesktopLogin = useCallback(async (host) => {
    setLoginError(null);
    try {
      await api.desktopLogin(host);
    } catch (e) {
      setLoginError(e.message);
      return;
    }
    setDesktopWaiting(true);
  }, []);

  // The shell owns the window and hands the cookie straight to the server, so
  // there is nothing to receive here — only a session to watch for. Polling
  // rather than a socket keeps the dashboard page free of any privileged
  // channel: it is served over loopback but it is still just a web page.
  useEffect(() => {
    if (!desktopWaiting) return undefined;
    const id = setInterval(async () => {
      const status = await api.authStatus().catch(() => null);
      if (status?.logged_in) {
        setDesktopWaiting(false);
        setAuth(status);
      }
    }, 2000);
    return () => clearInterval(id);
  }, [desktopWaiting]);

  async function logout() {
    setLoggingOut(true);
    try {
      const nextAuth = await api.logout();
      setAuth(nextAuth);
      // Logging out drops the saved password too, so there is nothing left to
      // sign back in with — but say so plainly rather than relying on that.
      setManualLogin(true);
      setData(null);
      setEvents([]);
      setError(null);
      setCalError(null);
      go(HOME);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoggingOut(false);
    }
  }

  // One colour order for the whole app, built from every enrolled course. Each
  // screen used to derive its own from whatever subset it held — the calendar
  // from events, the sidebar from assignments — so the same course came out a
  // different colour on each of them. Deriving it once, from the one list that
  // never changes shape, is what makes the swatch mean something.
  const order = useMemo(
    () => courseOrder((data?.courses ?? []).map((c) => c.label)),
    [data?.courses]
  );

  // The sidebar counts what is worth a glance from another screen: how many
  // courses actually total up, and how many announcements you have not read.
  const counts = useMemo(() => {
    if (!data) return {};
    return {
      grades: data.courses.filter(
        (c) => data.standings[c.course_id]?.accessible).length,
      announcements: countUnread(
        data.announcements, data.first_seen_announcements, annReadAt),
    };
  }, [data, annReadAt]);

  // The tab says where you are. The dashboard is the app itself, so it claims
  // nothing and the tab reads plain "Whiteboard"; everywhere else names the
  // screen, and a course names the course before the section of it you are in.
  const titleParts = useMemo(() => {
    if (!auth?.logged_in) return authLoading ? [] : ["Sign in"];
    if (!data) return [];
    const courseLabel = (id) =>
      data.courses.find((c) => c.course_id === id)?.label ?? null;
    switch (route.name) {
      case "grades":
        return ["Grades"];
      case "announcements":
        return ["Announcements"];
      case "settings":
        return route.id
          ? [courseLabel(route.id) ?? "Course", "Settings"]
          : ["Settings"];
      case "course": {
        const label = courseLabel(route.id);
        if (!label) return ["Course not found"];
        const tab = TABS.find((t) => t.id === route.tab && t.id !== "overview");
        return tab ? [tab.label, label] : [label];
      }
      default:
        return [];
    }
  }, [auth?.logged_in, authLoading, data, route]);
  useTitle(titleParts);

  if (authLoading) {
    return (
      <div className="boot">
        <p className="empty">
          <span className="spin" /> Checking Blackboard session…
        </p>
      </div>
    );
  }

  // One way in. The window sign-in needs nothing from us but the address, and
  // works at any institution, so there is no second path worth keeping.
  if (!auth?.logged_in) {
    return (
      <DesktopLogin
        auth={auth}
        waiting={desktopWaiting}
        error={loginError}
        onOpen={openDesktopLogin}
      />
    );
  }

  if (!data) {
    return (
      <div className="boot">
        <p className="empty">
          <span className="spin" /> Loading coursework…
        </p>
      </div>
    );
  }

  const name = data.me?.name?.given ?? "";

  // The dashboard is one screenful by design — the countdown, the month and the
  // deadline list are meant to be taken in together — so its shell is pinned to
  // the viewport and the month grid takes whatever height is left. Every other
  // screen is reference material of unbounded length and scrolls normally.
  return (
    <div className={"app" + (route.name === "overview" ? " fit" : "")}>
      <Sidebar
        me={data.me}
        courses={data.courses}
        counts={counts}
        route={route}
        order={order}
        onLogout={logout}
        loggingOut={loggingOut}
      />

      <main className="main">
        {route.name === "settings" ? (
          <Settings
            data={data}
            courseId={route.id}
            onOpenCourse={() => go(href.settings)}
            theme={theme}
            onTheme={setTheme}
            // An edited due date moves in the calendar too, and that grid is
            // drawn from the .ics rather than from this JSON, so both have to
            // be re-read or the two would disagree until the next reload.
            onReload={async () => { await load(false); await loadCalendar(false); }}
            onBack={() => go(HOME)}
          />
        ) : route.name === "course" ? (
          <CoursePage
            course={data.courses.find((c) => c.course_id === route.id)}
            standing={data.standings[route.id]}
            tab={route.tab}
            assignments={data.assignments}
            announcements={data.announcements}
            firstSeen={data.first_seen}
            onOpenAssignment={openAssignment}
            onChange={() => load(false)}
            onBack={() => go(HOME)}
          />
        ) : route.name === "grades" ? (
          <Grades courses={data.courses} standings={data.standings} order={order} />
        ) : route.name === "announcements" ? (
          <Announcements
            announcements={data.announcements}
            firstSeen={data.first_seen_announcements}
            readAt={annReadAt}
            onRead={markAnnouncementsRead}
            order={order}
          />
        ) : (
        /* The overview answers "what is due"; everything else is reference
           material you go and look up, so it lives on its own screen. */
        <>
        <header className="topbar">
          <div>
            <h1>{name ? `Welcome back, ${name}!` : "Whiteboard"}</h1>
            <p className="note dim">
              {new Date().toLocaleDateString(undefined, {
                weekday: "long", month: "long", day: "numeric",
              })}
              {data.cached_at && (
                <> · synced {new Date(data.cached_at).toLocaleTimeString()}</>
              )}
            </p>
          </div>
          <div className="spacer" />
          <div className="row actions">
            <button className="primary" onClick={syncNow} disabled={syncing}>
              {syncing ? <><span className="spin" /> Syncing</> : "Sync now"}
            </button>
            <a className="btn" href={`${CALENDAR_URL}?download=true`}
               download="coursework.ics">
              Export .ics
            </a>
          </div>
        </header>

        {error && <p className="err banner">{error}</p>}

        <StatRow assignments={data.assignments} courses={data.courses} order={order} />

        <div className="split">
          <Calendar
            events={events}
            loading={calLoading}
            error={calError}
            onReload={() => loadCalendar(false)}
            onOpenAssignment={openAssignment}
            order={order}
          />
          <DueList
            assignments={data.assignments}
            firstSeen={data.first_seen}
            onOpenAssignment={openAssignment}
            order={order}
          />
        </div>
        </>
        )}
      </main>

      {drawer && (
        <AssignmentDrawer target={drawer} onClose={() => setDrawer(null)} />
      )}

      {popup && (
        <NewAnnouncements
          announcements={popup}
          order={order}
          onShown={markAnnounced}
          onClose={() => setPopup(null)}
        />
      )}
    </div>
  );
}
