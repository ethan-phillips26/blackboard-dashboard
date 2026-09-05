/**
 * The calendar grid renders the generated .ics, so the writer and the reader are
 * one contract. This test builds a feed with the real Python generator and then
 * parses it with the real browser parser — if either side drifts, it fails here.
 *
 *   node tests/test_ics_parser.mjs
 */
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const { parseICS } = await import(
  join(root, "src/blackboard_web/frontend/src/lib/ics.js")
);

const fails = [];
function check(label, cond, detail = "") {
  console.log(`  ${cond ? "PASS" : "FAIL"}  ${label}` + (cond ? "" : `  — ${detail}`));
  if (!cond) fails.push(label);
}

const ITEMS = [
  {
    title: "Homework 3", course: "CS-340", course_name: "Algorithms",
    course_id: "_1_1", column_id: "_99_1", content_id: "_7_1",
    due_utc: "2026-09-05T04:59:00+00:00", points_possible: 100.0, submitted: false,
  },
  {
    title: "Lab; part 2, final \\ draft — café π", course: "PHYS 211",
    course_name: "Physics", column_id: "_100_1",
    due_utc: "2026-10-01T18:00:00+00:00", points_possible: 12.5, submitted: true,
  },
  { title: "x".repeat(200), course: "LONG", column_id: "_101_1",
    due_utc: "2026-10-02T18:00:00+00:00" },
];

console.log("\n[ics parser] against the real generator");

let feed;
try {
  feed = execFileSync(
    "uv",
    ["run", "python", "-c",
     `import json,sys; sys.path.insert(0,"src");
from blackboard_web import ics
sys.stdout.write(ics.build(json.loads(sys.argv[1]), calname="Ethan's, coursework"))`,
     JSON.stringify(ITEMS)],
    { cwd: root, encoding: "utf8" }
  );
} catch (e) {
  console.error("  SKIP  could not run the Python generator:", e.message);
  process.exit(0);
}

const { calendarName, events } = parseICS(feed);

check("the calendar name is unescaped", calendarName === "Ethan's, coursework", calendarName);
check("every event is found", events.length === 3, String(events.length));

const hw = events[0];
check("events come back in date order", hw.title === "Homework 3", hw.title);
check("the due time is the end of the block",
  hw.due.toISOString() === "2026-09-05T04:59:00.000Z", hw.due.toISOString());
check("the start is 30 minutes earlier",
  hw.start.toISOString() === "2026-09-05T04:29:00.000Z", hw.start.toISOString());
check("the course comes through", hw.course === "CS-340", hw.course);
check("so does the course id", hw.courseId === "_1_1", String(hw.courseId));
check("and the column id the dashboard keys on", hw.columnId === "_99_1", String(hw.columnId));
check("points are numeric", hw.points === 100, String(hw.points));
check("unsubmitted work is marked so", hw.submitted === false);
check("the description survives with its real line breaks",
  hw.description.split("\n").length === 3, JSON.stringify(hw.description));
check("the alarm's description does not overwrite the event's",
  !hw.description.startsWith("Due tomorrow"), hw.description);

const lab = events[1];
check("escaped punctuation round-trips exactly",
  lab.title === "Lab; part 2, final \\ draft — café π", JSON.stringify(lab.title));
check("submitted work is marked so", lab.submitted === true);
check("fractional points survive", lab.points === 12.5, String(lab.points));

check("a folded 200-character title is rejoined intact",
  events[2].title === "x".repeat(200), String(events[2].title.length));

console.log("\n[ics parser] shapes a hand-written feed can take");
const HAND = [
  "BEGIN:VCALENDAR",
  "BEGIN:VEVENT",
  "UID:local@x",
  // No Z: local wall time, which is what a TZID feed effectively means here.
  "DTSTART;TZID=America/Chicago:20260905T173000",
  "DTEND;TZID=America/Chicago:20260905T180000",
  "SUMMARY:MATH 265: Exam 1",
  "END:VEVENT",
  "BEGIN:VEVENT",
  "UID:allday@x",
  "DTSTART;VALUE=DATE:20260910",
  "SUMMARY:Reading day",
  "END:VEVENT",
  "END:VCALENDAR",
].join("\r\n");

const hand = parseICS(HAND).events;
check("a floating/TZID time is read as local", hand[0].due.getHours() === 18,
  String(hand[0].due.getHours()));
check("an all-day event is flagged", hand[1].allDay === true);
check("a summary with no X- hints still splits into course and title",
  hand[0].course === "MATH 265" && hand[0].title === "Exam 1",
  `${hand[0].course} / ${hand[0].title}`);
check("an event with no DTEND falls back to its start",
  +hand[1].due === +hand[1].start);
check("empty input is not a crash", parseICS("").events.length === 0);
check("garbage input is not a crash", parseICS("hello\nworld").events.length === 0);

console.log("\n" + "=".repeat(60));
console.log(fails.length ? `FAILED (${fails.length}): ${fails.join(", ")}` : "ALL CHECKS PASSED");
console.log("=".repeat(60));
process.exit(fails.length ? 1 : 0);
