/**
 * The course page, rendered.
 *
 * Every tab is drawn from a realistic snapshot of what the dashboard holds, so
 * a bad prop, a missing guard or a crash in one tab is caught here rather than
 * on screen. The components are bundled with esbuild first because they are
 * JSX; React itself stays external and is resolved at import time.
 */
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = join(HERE, "..", "src", "blackboard_web", "frontend");
const SRC = join(FRONTEND, "src");

// The frontend has its own node_modules; this file lives outside it, so every
// dependency is resolved from the frontend's package rather than from here.
const req = createRequire(join(FRONTEND, "package.json"));
const { build } = req("esbuild");
const { renderToStaticMarkup } = req("react-dom/server");
const { createElement: h } = req("react");

const fails = [];
function check(label, cond, detail = "") {
  console.log(`  ${cond ? "PASS" : "FAIL"}  ${label}` + (cond ? "" : `  — ${detail}`));
  if (!cond) fails.push(label);
}

// --- bundle the components under test ---------------------------------------

// React stays external so the bundle and this file share one copy of it —
// two copies would each keep their own hook dispatcher and nothing would
// render. That means the bundle has to sit where "react" resolves, so it is
// written inside the frontend's own node_modules.
const outDir = join(FRONTEND, "node_modules", ".cache");
mkdirSync(outDir, { recursive: true });
const out = join(outDir, "course-page-test-bundle.mjs");
await build({
  stdin: {
    contents: `
      export { default as CoursePage } from "./components/CoursePage.jsx";
      export { MaterialTree, filterTree, countLeaves }
        from "./components/CourseMaterials.jsx";
      export { default as RichText, clamp, visibleLength }
        from "./components/RichText.jsx";
      export { default as StatRow } from "./components/StatRow.jsx";
    `,
    resolveDir: SRC,
    loader: "jsx",
  },
  outfile: out,
  bundle: true,
  format: "esm",
  platform: "node",
  jsx: "automatic",
  logLevel: "silent",
  external: ["react", "react-dom", "react/jsx-runtime"],
});
const { CoursePage, MaterialTree, filterTree, countLeaves, RichText, clamp,
        visibleLength, StatRow } = await import(pathToFileURL(out).href);

const html = (element) => renderToStaticMarkup(element);

// --- a realistic snapshot ----------------------------------------------------

const soon = new Date(Date.now() + 3 * 86400000);

const COURSE = { course_id: "_11_1", label: "CSCI 450", title: "Cloud Computing" };

const STANDING = {
  course_id: "_11_1", accessible: true, current_pct: 88.4, current_letter: "B",
  projected_pct: 94.1, weighted_by: "syllabus", grouped_by: "inferred",
  late_policy: "Ten percent a day, three days maximum.",
  syllabus: { filename: "Fall_2026_Syllabus.docx" },
  weight_status: "ok", weights_edited: false,
  categories: [{
    category_id: "syl:Quizzes", title: "Quizzes", weight: 0.25,
    effective_weight: 0.25, pct: 88,
    columns: [
      { column_id: "_c1_1", name: "Quiz 2", possible: 21, score: 19, graded: true },
      { column_id: "_c2_1", name: "Quiz 3", possible: 21, score: null, graded: false },
    ],
  }],
  empty_components: [], unclassified: [], unmapped: [], suspect: [],
  available_categories: [{ id: "_cat1", title: "Quizzes" }],
  components: [{ syllabus_label: "Quizzes", weight_pct: 25 }],
};

const ASSIGNMENTS = [
  { course_id: "_11_1", column_id: "_c2_1", content_id: "_501_1", title: "Quiz 3",
    course: "CSCI 450", due_local: soon.toISOString(), days_until: 3,
    points_possible: 21, submitted: false },
  { course_id: "_99_1", column_id: "_zz_1", content_id: "_777_1", title: "Elsewhere",
    course: "PLSC 307", due_local: soon.toISOString(), days_until: 3,
    points_possible: 10, submitted: false },
];

const ANNOUNCEMENTS = [
  { id: "_an1", course_id: "_11_1", course: "CSCI 450", title: "Quiz 3 moved",
    posted: new Date(Date.now() - 3600000).toISOString(),
    body: "Quiz 3 is now open until Friday at midnight." },
  { id: "_an2", course_id: "_99_1", course: "PLSC 307", title: "Other course",
    posted: new Date().toISOString(), body: "Not this course." },
];

function page(tab) {
  return html(h(CoursePage, {
    course: COURSE, standing: STANDING, tab,
    assignments: ASSIGNMENTS, announcements: ANNOUNCEMENTS, firstSeen: {},
    onOpenAssignment: () => {}, onChange: () => {}, onBack: () => {},
  }));
}

// --- the tabs ----------------------------------------------------------------

console.log("\n[course page] tabs");
const overview = page(undefined);
check("an unknown tab falls back to the overview",
      page("nonsense").includes("Up next"));
check("the overview leads with what is due", overview.includes("Up next"));
check("the course's own work is listed", overview.includes("Quiz 3"));
check("another course's work is not",
      !overview.includes("Elsewhere"), overview.slice(0, 400));
check("the course's announcements are shown",
      overview.includes("Quiz 3 moved") && !overview.includes("Not this course."));
check("the grade sits in the header", overview.includes("88.4%"));
check("every tab is offered",
      ["Overview", "Materials", "Grades", "Announcements"]
        .every((t) => overview.includes(t)));

const grades = page("grades");
check("the grades tab shows the breakdown", grades.includes("Breakdown"));
check("and the calculator", grades.includes("What do I need?"));
check("and every gradebook row",
      grades.includes("Quiz 2") && grades.includes("19 / 21"));
check("and the syllabus it was weighted by",
      grades.includes("Fall_2026_Syllabus.docx"));

const anns = page("announcements");
check("the announcements tab lists this course only",
      anns.includes("Quiz 3 moved") && !anns.includes("Not this course."));

check("the materials tab loads rather than crashing",
      page("materials").includes("Reading the course"));

console.log("\n[course page] a gradebook the instructor hid");
const hidden = html(h(CoursePage, {
  course: COURSE,
  standing: { course_id: "_11_1", accessible: false, reason: "Hidden from students." },
  tab: "grades", assignments: ASSIGNMENTS, announcements: ANNOUNCEMENTS,
  firstSeen: {}, onOpenAssignment: () => {}, onChange: () => {}, onBack: () => {},
}));
check("says so instead of showing an empty breakdown",
      hidden.includes("Hidden from students.") && !hidden.includes("Breakdown"));
check("and shows no percentage in the header", !hidden.includes("88.4%"));

const missing = html(h(CoursePage, {
  course: null, tab: "overview", assignments: [], announcements: [],
  onOpenAssignment: () => {}, onChange: () => {}, onBack: () => {},
}));
check("a course that is not in the term is handled",
      missing.includes("Course not found"));

// --- the material tree -------------------------------------------------------

const TREE = [
  { content_id: "_400_1", title: "Week 1", type: "folder", is_folder: true,
    is_assignment: false, body: "", files: [], children: [
      { content_id: "_501_1", title: "Quiz 3", type: "assignment", is_folder: false,
        is_assignment: true, grade_column_id: "_c2_1", body: "Covers chapters 4-6.",
        files: [{ filename: "study-guide.pdf", bytes: 20480 }], children: [] },
      { content_id: "_402_1", title: "Quiz 2", type: "assignment", is_folder: false,
        is_assignment: true, grade_column_id: "_c1_1", body: "", files: [],
        children: [] },
      { content_id: "_403_1", title: "Course website", type: "externallink",
        is_folder: false, is_assignment: false, url: "https://example.edu",
        body: "", files: [], children: [] },
    ] },
];

const byContent = new Map(ASSIGNMENTS.map((a) => [a.content_id, a]));
const byColumn = new Map(
  STANDING.categories.flatMap((c) => c.columns.map((col) => [col.column_id, col]))
);

console.log("\n[materials] what a row knows about itself");
const tree = html(h(MaterialTree, {
  nodes: TREE, depth: 0, expanded: new Set(["_400_1"]), onToggle: () => {},
  byContent, byColumn, forceOpen: false, onOpen: () => {},
}));
check("the folder is drawn with its item count",
      tree.includes("Week 1") && tree.includes("3 items"), tree.slice(0, 300));
check("an ungraded item shows its deadline instead of a score",
      tree.includes("in 3 days"), tree);
check("a graded item shows the score Blackboard has",
      tree.includes("19 / 21"), tree);
check("an item's handouts are named before anything is downloaded",
      tree.includes("study-guide.pdf") && tree.includes("20 KB"));
check("and each one is a control you can take the file from, not a label",
      /<button[^>]*class="mat-file"[^>]*title="Download study-guide\.pdf"/.test(tree),
      tree.slice(tree.indexOf("mat-files") - 40));
check("an external link points at its target",
      tree.includes('href="https://example.edu"'));
check("each row says what kind of thing it is",
      tree.includes("Assignment") && tree.includes("Link"));

const shut = html(h(MaterialTree, {
  nodes: TREE, depth: 0, expanded: new Set(), onToggle: () => {},
  byContent, byColumn, forceOpen: false, onOpen: () => {},
}));
check("a folder nobody has opened starts shut",
      shut.includes("Week 1") && !shut.includes("Course website"), shut);
check("and still says how much is inside it", shut.includes("3 items"));
check("but a search opens it anyway",
      html(h(MaterialTree, {
        nodes: TREE, depth: 0, expanded: new Set(),
        onToggle: () => {}, byContent, byColumn, forceOpen: true, onOpen: () => {},
      })).includes("Course website"));

console.log("\n[materials] search");
const hits = filterTree(TREE, "chapters 4");
check("a body match keeps the folder above it",
      hits.length === 1 && hits[0].title === "Week 1", JSON.stringify(hits));
check("and only the item that matched",
      hits[0].children.length === 1 && hits[0].children[0].title === "Quiz 3");
check("the matched item is flagged", hits[0].children[0].matched === true);
check("the folder it is in is not", hits[0].matched === false);
check("a file name is searchable too",
      filterTree(TREE, "study-guide")[0].children.length === 1);
check("a title match works", filterTree(TREE, "website")[0].children.length === 1);
check("nothing matching gives nothing back",
      filterTree(TREE, "zzzz").length === 0);
check("leaves are counted through folders", countLeaves(TREE) === 3);
check("an empty tree counts zero", countLeaves([]) === 0);

console.log("\n[dashboard] the figures across the top");
const stats = html(h(StatRow, {
  assignments: [ASSIGNMENTS[0]], courses: [{ course_id: "_11_1" }],
}));
check("the next deadline is the hero figure",
      stats.includes("Next deadline") && stats.includes("days left"), stats);
check("with the work it belongs to",
      stats.includes("Quiz 3") && stats.includes("CSCI 450"));
check("the figure and its detail are grouped, so they can sit side by side",
      stats.includes('class="hero-body"') && stats.includes('class="hero-detail"'),
      stats);
check("and the figures come with it",
      ["Due this week", "Overdue", "Courses"].every((t) => stats.includes(t)));
check("points at stake is not among them",
      !stats.toLowerCase().includes("at stake"));
const quiet = html(h(StatRow, { assignments: [], courses: [] }));
check("with nothing due it still draws, rather than blanking",
      quiet.includes("Nothing is due") && quiet.includes('class="hero-body"'), quiet);

console.log("\n[links] course text becomes clickable");
const ADVISE = "https://career-advising.ndsu.edu/bisonadvise/";
const SENTENCE = `See [Bison Advise - Your Advising Resource](${ADVISE}) for help.`;
const rendered = html(h(RichText, { text: SENTENCE }));
check("the anchor is real and points where the author sent it",
      rendered.includes(`<a class="link" href="${ADVISE}"`), rendered);
check("the reader sees the words, not the url",
      rendered.includes(">Bison Advise - Your Advising Resource</a>")
      && !rendered.includes(`[${ADVISE}]`), rendered);
check("the prose around it is untouched",
      rendered.startsWith("See ") && rendered.endsWith(" for help."), rendered);
check("it opens away from the dashboard, without handing over the referrer",
      rendered.includes('target="_blank"')
      && rendered.includes('rel="noreferrer noopener"'), rendered);
check("an escaped bracket in the label is shown as one bracket",
      html(h(RichText, { text: String.raw`[a \] b](https://x.edu)` }))
        .includes(">a ] b</a>"),
      html(h(RichText, { text: String.raw`[a \] b](https://x.edu)` })));
check("text with no links renders as itself",
      html(h(RichText, { text: "Nothing to click." })) === "Nothing to click.");
check("course text is never treated as markup",
      html(h(RichText, { text: "<script>x()</script> & co" }))
        .includes("&lt;script&gt;"),
      html(h(RichText, { text: "<script>x()</script> & co" })));
check("a bare url with no label is left as plain text",
      html(h(RichText, { text: "go to https://x.edu now" }))
      === "go to https://x.edu now");

console.log("\n[links] clamping around them");
check("a body is measured as it reads, not as it is stored",
      visibleLength(SENTENCE) === "See Bison Advise - Your Advising Resource for help.".length,
      String(visibleLength(SENTENCE)));
// The cut lands inside the link's URL, which would leave half of one on screen.
const cut = clamp(SENTENCE, 30);
check("a cut that would split a link moves back before it",
      cut === "See" && !cut.includes("http"), JSON.stringify(cut));
check("a cut clear of a link happens where asked",
      clamp("abcdefghij", 4) === "abcd", clamp("abcdefghij", 4));
check("text shorter than the cut is returned whole",
      clamp("short", 99) === "short");
check("a link that ends before the cut survives it",
      clamp(`${SENTENCE} And more text after.`, SENTENCE.length + 4)
        .includes(`](${ADVISE})`));

console.log("\n" + "=".repeat(60));
console.log(fails.length ? `${fails.length} CHECK(S) FAILED: ${fails.join(", ")}`
                         : "ALL CHECKS PASSED");
console.log("=".repeat(60));
process.exit(fails.length ? 1 : 0);
