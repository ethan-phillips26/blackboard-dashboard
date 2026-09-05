import { useEffect, useMemo } from "react";
import { go, href } from "../lib/route.js";
import { pct, relative } from "../lib/format.js";
import AnnouncementList from "./AnnouncementList.jsx";
import BbLink from "./BbLink.jsx";
import CourseGrades from "./CourseGrades.jsx";
import CourseMaterials from "./CourseMaterials.jsx";
import CourseOverview from "./CourseOverview.jsx";

export const TABS = [
  { id: "overview", label: "Overview" },
  { id: "materials", label: "Materials" },
  { id: "grades", label: "Grades" },
  { id: "announcements", label: "Announcements" },
];

/**
 * One course, whole.
 *
 * This used to be the grade page for a course, which is only ever a third of
 * the question — the material and the announcements sat on other screens or
 * back in Blackboard itself. The tabs are separate URLs, so a bookmark and the
 * back button both land on the one you were reading.
 */
export default function CoursePage({ course, standing, tab, assignments,
                                     announcements, firstSeen,
                                     onOpenAssignment, onChange, onBack }) {
  const courseId = course?.course_id;
  const active = TABS.some((t) => t.id === tab) ? tab : "overview";

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [courseId, active]);

  // The dashboard already holds every course's work and posts; this page is
  // the same data narrowed, so switching to it costs nothing.
  const mine = useMemo(
    () => (assignments ?? []).filter((a) => a.course_id === courseId),
    [assignments, courseId]
  );
  const posts = useMemo(
    () => (announcements ?? []).filter((a) => a.course_id === courseId),
    [announcements, courseId]
  );

  if (!course) {
    return (
      <>
        <header className="page-head">
          <div>
            <h1>Course not found</h1>
            <p className="note dim">That course is not in the current term.</p>
          </div>
        </header>
        <section className="panel">
          <button onClick={onBack}>Back to dashboard</button>
        </section>
      </>
    );
  }

  const next = mine.find((a) => !a.submitted);
  const openTab = (id) => go(href.course(courseId, id === "overview" ? null : id));

  return (
    <>
      <a className="back" href="#" onClick={(e) => { e.preventDefault(); onBack(); }}>
        ← Dashboard
      </a>

      <header className="course-head">
        <div className="course-id">
          <h1>{course.label}</h1>
          {course.title && course.title !== course.label && (
            <p className="note dim">{course.title}</p>
          )}
          {/* Under the name, where it reads as this course's own address rather
              than as one more control in the row of them. */}
          <BbLink href={course.web_url} className="bblink course-bblink" />
        </div>
        <div className="spacer" />
        {standing?.accessible && (
          <div className="course-score">
            <div className="label">Grade so far</div>
            <div className="big">
              {pct(standing?.current_pct)}
              {standing?.current_letter && (
                <span className="letter">{standing.current_letter}</span>
              )}
            </div>
            {standing?.projected_pct != null && (
              <div className="note dim">
                {pct(standing.projected_pct)} if you ace the rest
              </div>
            )}
          </div>
        )}
      </header>

      <div className="tabbar">
        <div className="seg">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={t.id === active ? "on" : ""}
              onClick={() => openTab(t.id)}
              aria-current={t.id === active ? "page" : undefined}
            >
              {t.label}
              {t.id === "announcements" && posts.length > 0 && (
                <span className="tab-count">{posts.length}</span>
              )}
              {t.id === "overview" && mine.some((a) => !a.submitted) && (
                <span className="tab-count">
                  {mine.filter((a) => !a.submitted).length}
                </span>
              )}
            </button>
          ))}
        </div>
        <div className="spacer" />
        {next?.due_local && (
          <span className="note dim tab-next">
            Next: <b>{next.title}</b> · {relative(new Date(next.due_local))}
          </span>
        )}
      </div>

      {active === "overview" && (
        <CourseOverview
          standing={standing}
          assignments={mine}
          announcements={posts}
          firstSeen={firstSeen}
          onOpenAssignment={onOpenAssignment}
          onTab={openTab}
        />
      )}

      {active === "materials" && (
        <CourseMaterials
          courseId={courseId}
          courseLabel={course.label}
          assignments={mine}
          standing={standing}
          onOpenAssignment={onOpenAssignment}
        />
      )}

      {active === "grades" && (
        standing?.accessible ? (
          <CourseGrades course={course} standing={standing} onChange={onChange} />
        ) : (
          <section className="panel">
            <p className="note">
              {standing?.reason ?? "This course's gradebook is not available."}
            </p>
          </section>
        )
      )}

      {active === "announcements" && (
        <section className="panel">
          <div className="panel-head">
            <h2>Announcements</h2>
            <span className="note dim">{posts.length} posted</span>
          </div>
          {posts.length === 0 ? (
            <p className="empty">Nothing posted in this course.</p>
          ) : (
            <AnnouncementList announcements={posts} />
          )}
        </section>
      )}
    </>
  );
}
