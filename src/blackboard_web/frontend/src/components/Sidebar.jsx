import { courseSlot } from "../lib/format.js";
import { href } from "../lib/route.js";

const SECTIONS = [
  { name: "overview", label: "Overview", to: href.overview },
  { name: "grades", label: "Grades", to: href.grades },
  { name: "announcements", label: "Announcements", to: href.announcements },
];

/** "Ethan Phillips" if Blackboard gave us both halves, else whatever it gave. */
function fullName(me) {
  const name = me?.name ?? {};
  const both = [name.given, name.family].filter(Boolean).join(" ");
  return both || name.preferredDisplayName || me?.username || "";
}

export default function Sidebar({ me, courses, counts, route, order,
                                  onLogout, loggingOut }) {
  // Every course the sync kept. Courses whose gradebook the instructor hides
  // from students are dropped at the source, so there is nothing to filter here.
  const listed = courses;

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true" />
        <div>
          <div className="brand-name">Whiteboard</div>
        </div>
      </div>

      {/* On a phone the sidebar lies down into one bar and this is the part
          that scrolls, so the mark stays put while the links slide under it.
          `display: contents` keeps it out of the way of the column layout. */}
      <div className="nav-strip">
      <nav>
        {SECTIONS.map((s) => (
          <a
            key={s.name}
            href={s.to}
            className={route.name === s.name ? "nav on" : "nav"}
            aria-current={route.name === s.name ? "page" : undefined}
          >
            {s.label}
            {counts[s.name] ? <span className="pill">{counts[s.name]}</span> : null}
          </a>
        ))}
      </nav>

      {listed.length > 0 && (
        <nav className="nav-group">
          <div className="nav-title">Courses</div>
          {listed.map((c) => (
            <a
              key={c.course_id}
              href={href.course(c.course_id)}
              className={
                (route.name === "course" && route.id === c.course_id
                  ? "nav on" : "nav") + " nav-course s" + courseSlot(c.label, order)
              }
              title={c.title && c.title !== c.label ? c.title : c.label}
            >
              <i className="dot" />
              <span className="nav-course-name">{c.label}</span>
            </a>
          ))}
        </nav>
      )}

      <nav className="nav-foot">
        <a
          href={href.settings}
          className={route.name === "settings" ? "nav on" : "nav"}
          aria-current={route.name === "settings" ? "page" : undefined}
        >
          Settings
        </a>
      </nav>
      </div>

      {/* Who is signed in, and the way out. The course count and the sync clock
          that used to live here were facts about the data rather than anything
          to act on, and both are said on the screen that uses them. */}
      <div className="side-foot">
        <div className="side-who" title={fullName(me)}>{fullName(me)}</div>
        <button className="side-out" onClick={onLogout} disabled={loggingOut}>
          {loggingOut ? <><span className="spin" /> Logging out</> : "Log out"}
        </button>
      </div>
    </aside>
  );
}
