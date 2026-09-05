import { useEffect } from "react";
import CourseCard from "./CourseCard.jsx";

/**
 * Every course's standing, on its own screen.
 *
 * Instructors can hide a gradebook from students. There is nothing to put on a
 * card for those courses, so they are left off this screen entirely.
 */
export default function Grades({ courses, standings, order }) {
  useEffect(() => {
    window.scrollTo(0, 0);
  }, []);

  const graded = courses.filter((c) => standings[c.course_id]?.accessible);

  return (
    <>
      <header className="page-head">
        <div>
          <h1>Grades</h1>
          <p className="note dim">
            {graded.length} {graded.length === 1 ? "course" : "courses"}
          </p>
        </div>
      </header>

      {graded.length === 0 ? (
        <section className="panel">
          <p className="empty">
            None of your courses have a gradebook visible to students.
          </p>
        </section>
      ) : (
        <div className="grid cards">
          {graded.map((c) => (
            <CourseCard
              key={c.course_id}
              course={c}
              standing={standings[c.course_id]}
              order={order}
            />
          ))}
        </div>
      )}
    </>
  );
}
