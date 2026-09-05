"""Cache behaviour and syllabus-weight resolution."""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from blackboard_web.cache import Cache, digest  # noqa: E402
from blackboard_web import ics  # noqa: E402
from blackboard_web import grades as G  # noqa: E402
from blackboard_web import sync  # noqa: E402
from blackboard_web import notices as N  # noqa: E402
from blackboard_web.sync import (  # noqa: E402
    _category_digest, _components, _folded, _gradeable_columns, _named,
    _populated_counts, _syllabus_score, classify_columns, resolve_weights,
    strip_attachment_links, syllabus_categories, syllabus_weights,
)

FAILS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  — {detail}"))
    if not cond:
        FAILS.append(label)


print("\n[cache]")
with tempfile.TemporaryDirectory() as td:
    c = Cache(Path(td))
    c.write("assignments", {"items": [1, 2]})
    check("round-trips data", c.get_data("assignments") == {"items": [1, 2]})
    check("fresh right after writing", c.fresh("assignments", "assignments"))
    check("a tiny ttl expires it", not c.fresh("assignments", ttl=0.0001) or time.sleep(0.01) is None)
    check("missing key is not fresh", not c.fresh("nope", "assignments"))
    check("missing key returns the default", c.get_data("nope", "fallback") == "fallback")
    check("age is reported", (c.age("assignments") or 0) >= 0)
    check("age of a missing key is None", c.age("nope") is None)
    check("digest is stable across key order",
          digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1}))
    check("digest changes with content", digest({"a": 1}) != digest({"a": 2}))
    c.drop("assignments")
    check("drop removes it", c.get_data("assignments") is None)
    c.write("a/b", {"x": 1})
    check("keys with slashes are made safe", "a_b" in c.keys(), str(c.keys()))

print("\n[syllabus scoring]")
check("filename match scores highest",
      _syllabus_score("Fall_2026_Syllabus.docx", "ultraDocumentBody", False) == 3)
check("item title match counts",
      _syllabus_score("doc.docx", "Course Syllabus", False) == 2)
check("ancestor match counts a little",
      _syllabus_score("doc.docx", "ultraDocumentBody", True) == 1)
check("an unrelated file scores zero",
      _syllabus_score("Assignment1.docx", "Assignment 1", False) == 0)
check("signals add up",
      _syllabus_score("Syllabus.pdf", "Course Syllabus", True) == 6)

BUNDLE = {
    "categories": [{"id": "cq", "title": "Quiz"}, {"id": "ct", "title": "Test"},
                   {"id": "ca", "title": "Assignment"}],
    "columns": [
        {"id": "1", "name": "Quiz 1", "gradebookCategoryId": "ct",
         "score": {"possible": 20}, "grading": {"type": "Attempts"}},
        {"id": "2", "name": "Quiz 2", "gradebookCategoryId": "ct",
         "score": {"possible": 20}, "grading": {"type": "Attempts"}},
        {"id": "3", "name": "Lab 1", "gradebookCategoryId": "ca",
         "score": {"possible": 20}, "grading": {"type": "Attempts"}},
        # Blackboard's running total must never look like coursework.
        {"id": "9", "name": "Overall Grade", "score": {"possible": 315},
         "grading": {"type": "Calculated"}},
    ],
}

print("\n[category digest] what the model is shown")
dg = _category_digest(BUNDLE)
test_cat = next(d for d in dg if d["title"] == "Test")
quiz_cat = next(d for d in dg if d["title"] == "Quiz")
check("the Test category reveals it holds the quizzes",
      test_cat["columns"] == ["Quiz 1", "Quiz 2"], str(test_cat))
check("its points are totalled", test_cat["points_possible"] == 40.0)
check("the empty Quiz category is shown as empty",
      quiz_cat["column_count"] == 0, str(quiz_cat))
check("the calculated column is excluded from every category",
      all("Overall Grade" not in d["columns"] for d in dg), str(dg))
check("populated counts skip calculated columns",
      _populated_counts(BUNDLE) == {"ct": 2, "ca": 1}, str(_populated_counts(BUNDLE)))

print("\n[weight resolution]")
extracted = {"categories": [
    {"syllabus_label": "Quizzes", "blackboard_category": "Test", "weight_pct": 40},
    {"syllabus_label": "LABs", "blackboard_category": "Assignment", "weight_pct": 50},
    {"syllabus_label": "Final Project", "blackboard_category": None, "weight_pct": 10},
]}
r = resolve_weights(extracted, BUNDLE["categories"], {}, _populated_counts(BUNDLE))
check("titles resolve to category ids", r["mapping"] == {"ct": 0.4, "ca": 0.5}, str(r["mapping"]))
check("mapped percentage reported", r["mapped_pct"] == 90.0, str(r["mapped_pct"]))
check("a component with no match is listed unmapped",
      [u["syllabus_label"] for u in r["unmapped"]] == ["Final Project"], str(r["unmapped"]))
check("nothing suspect when mappings hit real columns", r["suspect"] == [], str(r["suspect"]))

# The bug this guards against: matching "Quizzes" to the empty "Quiz" category.
bad = {"categories": [
    {"syllabus_label": "Quizzes", "blackboard_category": "Quiz", "weight_pct": 40}]}
rb = resolve_weights(bad, BUNDLE["categories"], {}, _populated_counts(BUNDLE))
check("mapping onto an empty category is flagged suspect",
      [s["syllabus_label"] for s in rb["suspect"]] == ["Quizzes"], str(rb["suspect"]))

print("\n[manual override]")
ro = resolve_weights(extracted, BUNDLE["categories"], {"Final Project": "cq"},
                     _populated_counts(BUNDLE))
check("an override places the unmapped component", ro["mapping"].get("cq") == 0.1, str(ro["mapping"]))
check("and it is no longer unmapped", ro["unmapped"] == [], str(ro["unmapped"]))
check("full weighting now accounted for", ro["mapped_pct"] == 100.0)
check("but an override onto an empty category is still flagged",
      [s["syllabus_label"] for s in ro["suspect"]] == ["Final Project"], str(ro["suspect"]))
check("case-insensitive title matching",
      resolve_weights({"categories": [{"syllabus_label": "Q", "blackboard_category": "  test ",
                                       "weight_pct": 10}]},
                      BUNDLE["categories"])["mapping"] == {"ct": 0.1})
check("two components on one category sum",
      resolve_weights({"categories": [
          {"syllabus_label": "A", "blackboard_category": "Test", "weight_pct": 30},
          {"syllabus_label": "B", "blackboard_category": "Test", "weight_pct": 20}]},
          BUNDLE["categories"])["mapping"] == {"ct": 0.5})

print("\n[icalendar feed]")


def unfold(text: str) -> list[str]:
    """The reader's half of RFC 5545 folding — what a calendar client does."""
    lines: list[str] = []
    for raw in text.split("\r\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


ITEMS = [
    {"title": "Homework 3", "course": "CS-340", "course_name": "Algorithms",
     "course_id": "_1_1", "column_id": "_99_1", "content_id": "_7_1",
     "due_utc": "2026-09-05T04:59:00+00:00", "due_local": "2026-09-04T23:59:00-05:00",
     "points_possible": 100.0, "submitted": False},
    # A comma, a semicolon and a backslash all have to survive the round trip.
    {"title": "Lab; part 2, final \\ draft", "course": "PHYS 211",
     "course_name": "Physics", "column_id": "_100_1",
     "due_utc": "2026-10-01T18:00:00+00:00", "points_possible": None,
     "submitted": True},
    # No due date — there is nothing to place on a calendar.
    {"title": "Undated", "course": "X", "column_id": "_101_1", "due_utc": None},
]

FEED = ics.build(ITEMS, calname="Test, calendar", description="d")
LINES = unfold(FEED)

check("wrapped in a VCALENDAR", LINES[0] == "BEGIN:VCALENDAR" and LINES[-1] == "END:VCALENDAR",
      f"{LINES[0]!r} .. {LINES[-1]!r}")
check("CRLF line endings, with a trailing break", FEED.endswith("\r\n") and "\n" not in FEED.replace("\r\n", ""))
check("every folded line fits 75 octets",
      all(len(l.encode()) <= 75 for l in FEED.split("\r\n")),
      str([l for l in FEED.split("\r\n") if len(l.encode()) > 75][:1]))
check("only dated assignments become events", FEED.count("BEGIN:VEVENT") == 2,
      str(FEED.count("BEGIN:VEVENT")))
check("every VEVENT is closed", FEED.count("END:VEVENT") == 2)
check("the calendar is named", "X-WR-CALNAME:Test\\, calendar" in LINES)

check("the deadline is the end of the block", "DTEND:20260905T045900Z" in LINES)
check("and the block starts 30 minutes earlier", "DTSTART:20260905T042900Z" in LINES)
check("times are emitted in UTC", all(
    l.split(":")[1].endswith("Z") for l in LINES if l.startswith(("DTSTART", "DTEND", "DTSTAMP"))))
check("the uid is stable and derived from the column", "UID:bb-_99_1@blackboard-dashboard.local" in LINES)
check("the summary carries course and title", "SUMMARY:CS-340: Homework 3" in LINES)
check("points reach the description", any("100 points" in l for l in LINES))
check("a day-ahead alarm is attached", LINES.count("TRIGGER:-P1D") == 2)
check("the course id survives for the dashboard", "X-BB-COURSE-ID:_1_1" in LINES)
check("submitted state is recorded", LINES.count("X-BB-SUBMITTED:TRUE") == 1
      and LINES.count("X-BB-SUBMITTED:FALSE") == 1)

# The escaping is only correct if unfolding then unescaping gives the original.
summary = next(l for l in LINES if l.startswith("SUMMARY:PHYS 211"))
check("comma, semicolon and backslash are escaped",
      summary == "SUMMARY:PHYS 211: Lab\\; part 2\\, final \\\\ draft", repr(summary))
desc = next(l for l in LINES if l.startswith("DESCRIPTION:Algorithms"))
check("a newline in the description is escaped, never literal",
      desc == "DESCRIPTION:Algorithms\\nDue Fri Sep 04\\, 11:59 PM\\n100 points"
      or desc.count("\\n") == 2, repr(desc))
check("and unescaping it recovers the three real lines",
      len(desc.split(":", 1)[1].replace("\\\\n", "\x00").split("\\n")) == 3, repr(desc))

check("no due date at all yields an empty but valid calendar",
      ics.build([]).count("BEGIN:VEVENT") == 0)
check("an untitled item still gets an event",
      ics.build([{"due_utc": "2026-09-05T04:59:00+00:00"}]).count("BEGIN:VEVENT") == 1)
check("a naive timestamp is treated as UTC rather than dropped",
      "DTEND:20260905T045900Z" in unfold(ics.build([{"due_utc": "2026-09-05T04:59:00"}])))
check("an unparseable timestamp is skipped, not crashed on",
      ics.build([{"due_utc": "not a date"}]).count("BEGIN:VEVENT") == 0)

long_title = "x" * 300
folded = ics.build([{"title": long_title, "course": "C",
                     "due_utc": "2026-09-05T04:59:00+00:00"}])
check("a very long summary folds and unfolds back intact",
      f"SUMMARY:C: {long_title}" in unfold(folded))

utf8 = ics.build([{"title": "Café — naïve π", "course": "C",
                   "due_utc": "2026-09-05T04:59:00+00:00"}])
check("folding never splits a multi-byte character",
      "SUMMARY:C: Café — naïve π" in unfold(utf8))


print("\n[inferring a category from the item name]")

STORED = {"extracted": {"categories": [
    {"syllabus_label": "Quizzes", "weight_pct": 40},
    {"syllabus_label": "Labs", "weight_pct": 50},
    {"syllabus_label": "Participation", "weight_pct": 10},
]}}

check("components come off the stored extraction",
      [r["syllabus_label"] for r in _components(STORED)] == ["Quizzes", "Labs", "Participation"])
check("a component with a blank label is ignored",
      _components({"extracted": {"categories": [{"syllabus_label": "  ", "weight_pct": 5}]}}) == [])
check("synthetic categories are one per component",
      [c["id"] for c in syllabus_categories(_components(STORED))]
      == ["syl:Quizzes", "syl:Labs", "syl:Participation"])
check("weights are fractions keyed the same way",
      syllabus_weights(_components(STORED)) == {"syl:Quizzes": 0.4, "syl:Labs": 0.5,
                                                "syl:Participation": 0.1})
check("two components with one label sum",
      syllabus_weights([{"syllabus_label": "X", "weight_pct": 30},
                        {"syllabus_label": "X", "weight_pct": 20}]) == {"syl:X": 0.5})
check("only gradeable rows are offered for sorting",
      [c["name"] for c in _gradeable_columns(BUNDLE)] == ["Quiz 1", "Quiz 2", "Lab 1"])
check("a zero-point row cannot move the grade, so it is not sorted at all",
      [c["name"] for c in _gradeable_columns({"columns": BUNDLE["columns"] + [
          {"id": "z", "name": "Class Survey", "score": {"possible": 0},
           "grading": {"type": "Attempts"}}]})] == ["Quiz 1", "Quiz 2", "Lab 1"])
check("a row with no points at all is treated the same way",
      _gradeable_columns({"columns": [
          {"id": "z", "name": "Placeholder", "grading": {"type": "Attempts"}}]}) == [])


class FakeLLM:
    """Stands in for the model so the sorting logic can be tested exactly."""

    def __init__(self, items): self.items, self.calls = items, 0

    def cached(self, cache, key, instr, data, schema, force=False):
        self.calls += 1
        self.last_payload = data
        return {"data": {"items": self.items}, "cached": False, "generated_at": "now"}


def sort_with(items, bundle=BUNDLE, stored=STORED):
    import blackboard_web.sync as S
    fake = FakeLLM(items)
    real, S.llm = S.llm, fake
    try:
        with tempfile.TemporaryDirectory() as td:
            return classify_columns(Cache(Path(td)), "_c_", stored, bundle), fake
    finally:
        S.llm = real


# The whole point: "Quiz 1" and "Quiz 2" sit in Blackboard's "Test" category and
# "Lab 1" in "Assignment". The names say otherwise, and the names win.
res, fake = sort_with([{"n": 1, "syllabus_label": "Quizzes"},
                       {"n": 2, "syllabus_label": "Quizzes"},
                       {"n": 3, "syllabus_label": "Labs"}])
check("items are placed by name, not by the category set on them",
      res["map"] == {"1": "syl:Quizzes", "2": "syl:Quizzes", "3": "syl:Labs"}, str(res["map"]))
check("nothing is left over", res["unclassified"] == [])
check("the model is shown the item names and points",
      '"Quiz 1"' in fake.last_payload and '"points": 20' in fake.last_payload)
check("the model is shown the components to sort into",
      '"Quizzes"' in fake.last_payload and '"weight_pct": 40' in fake.last_payload)
check("the model is never shown the instructor's categories",
      "Test" not in fake.last_payload and "gradebookCategoryId" not in fake.last_payload,
      fake.last_payload[:200])

res, _ = sort_with([{"n": 1, "syllabus_label": "Quizzes"},
                    {"n": 2, "syllabus_label": None},
                    {"n": 3, "syllabus_label": "Labs"}])
check("an item the model could not place is reported, not guessed",
      res["unclassified"] == ["Quiz 2"], str(res["unclassified"]))

res, _ = sort_with([{"n": 1, "syllabus_label": "Homework"},
                    {"n": 2, "syllabus_label": "Quizzes"}])
check("a label the syllabus does not have is discarded",
      res["map"] == {"2": "syl:Quizzes"}, str(res["map"]))
check("and that item counts as unsorted",
      res["unclassified"] == ["Quiz 1", "Lab 1"], str(res["unclassified"]))

res, _ = sort_with([{"n": 9, "syllabus_label": "Quizzes"},
                    {"n": 0, "syllabus_label": "Labs"},
                    {"n": "2", "syllabus_label": "Quizzes"}])
check("an out-of-range or non-integer item number is ignored",
      res["map"] == {}, str(res["map"]))

res, fake = sort_with([], stored={"extracted": {"categories": []}})
check("with no syllabus components the model is never called", fake.calls == 0)
check("and that is reported rather than silently empty",
      res["status"] == "nothing_to_classify", res["status"])

print("\n[weighting on the inferred buckets]")
GRADES = {"1": {"displayGrade": {"score": 18}}, "2": {"displayGrade": {"score": 16}},
          "3": {"displayGrade": {"score": 20}}}
cats = syllabus_categories(_components(STORED))
placed = {"1": "syl:Quizzes", "2": "syl:Quizzes", "3": "syl:Labs"}
bd = G.build_breakdown(cats, _gradeable_columns(BUNDLE), GRADES,
                       syllabus_weights(_components(STORED)), assign=placed)
by_title = {b["title"]: b for b in bd}
check("quizzes are pooled into one bucket",
      by_title["Quizzes"]["total_possible"] == 40.0 and by_title["Quizzes"]["earned"] == 34.0,
      str(by_title["Quizzes"]))
check("the lab lands in its own bucket", by_title["Labs"]["earned"] == 20.0)
check("a component with no items produces no bucket", "Participation" not in by_title)
check("the weighted standing uses the syllabus percentages",
      # 85% of 40 + 100% of 50, renormalised over the 90 that is graded
      round(G.current_grade(bd), 2) == round((0.4 * 85 + 0.5 * 100) / 0.9, 2),
      str(G.current_grade(bd)))
check("without an assignment map Blackboard's own categories still apply",
      {b["title"] for b in G.build_breakdown(BUNDLE["categories"],
                                             _gradeable_columns(BUNDLE), GRADES)}
      == {"Test", "Assignment"})


print("\n[assignment instructions]")
import os  # noqa: E402

# app.py resolves the download root and the cache at import time, so point both
# somewhere safe before importing it — a test must never read or write the
# cache the running dashboard is using.
_DL = tempfile.mkdtemp()
os.environ["BB_DOWNLOAD_DIR"] = _DL
os.environ["BB_CACHE_DIR"] = tempfile.mkdtemp()
from blackboard_web import app as webapp  # noqa: E402
from blackboard_mcp import paths  # noqa: E402

URL = "https://bb.test/bbcswebdav/pid-1/xid-2?token=abc%3D"
OUTSIDE = "https://web.archive.org/web/2021/scrum-card"
clean = strip_attachment_links

check("an anchor that is only the attachment is dropped",
      clean(f"[{URL}] Handout.docx", ["Handout.docx"]) == "", repr(clean(f"[{URL}] Handout.docx", ["Handout.docx"])))
check("the credentialed url never survives that case",
      "token" not in clean(f"[{URL}] Handout.docx", ["Handout.docx"]))
check("real prose around an anchor is kept",
      clean(f"Read ch 3.\n\n[{URL}] Handout.docx\n\nAnswer Q1.", ["Handout.docx"])
      == "Read ch 3.\n\nAnswer Q1.")
check("a link inside a sentence is left alone",
      clean(f"See [{URL}] the rubric.", ["Other.docx"]) == f"See [{URL}] the rubric.")

# The dashboard re-reads instructions with the anchors kept, so the same rules
# have to hold for a markdown link as for a bare bracketed URL.
check("a markdown attachment anchor is dropped whole",
      clean(f"[Handout.docx]({URL})", ["Handout.docx"]) == "",
      repr(clean(f"[Handout.docx]({URL})", ["Handout.docx"])))
check("its credentialed url goes with it",
      "token" not in clean(f"[Handout.docx]({URL})", ["Handout.docx"]))
# A file the page already offers a download button for is not worth a link:
# the URL is signed and expires, while the button re-fetches through the session.
check("a link to a course file loses the link but keeps the words",
      clean(f"Download the [syllabus]({URL}) before Friday.", ["syllabus.docx"])
      == "Download the syllabus before Friday.",
      clean(f"Download the [syllabus]({URL}) before Friday.", ["syllabus.docx"]))
check("an anchor Ultra left empty goes entirely, rather than showing its url",
      clean(f"Slides\n\n[{URL}]({URL})", ["a.pptx"]) == "Slides",
      repr(clean(f"Slides\n\n[{URL}]({URL})", ["a.pptx"])))
check("a link named after one of the files goes too, wherever it points",
      clean("Here: [Handout.docx](https://elsewhere.test/h) now.", ["Handout.docx"])
      == "Here: Handout.docx now.")
check("a link the instructor actually wrote is left alone",
      clean(f"See [the reading]({OUTSIDE}) first.", ["Other.docx"])
      == f"See [the reading]({OUTSIDE}) first.")
check("even on a line that also carried a file link",
      clean(f"[Slides.pptx]({URL}) and [the reading]({OUTSIDE}).", ["Slides.pptx"])
      == f"Slides.pptx and [the reading]({OUTSIDE}).",
      clean(f"[Slides.pptx]({URL}) and [the reading]({OUTSIDE}).", ["Slides.pptx"]))
check("prose around a markdown anchor is kept",
      clean(f"Read ch 3.\n\n[Handout.docx]({URL})\n\nAnswer Q1.", ["Handout.docx"])
      == "Read ch 3.\n\nAnswer Q1.")
check("a bare link line with no filename is dropped", clean(f"[{URL}]", []) == "")
check("filename matching ignores case and padding",
      clean(f"[{URL}]  handout.DOCX ", ["Handout.docx"]) == "")
check("ordinary instructions are untouched",
      clean("Submit a PDF.", []) == "Submit a PDF.")
check("no instructions at all is an empty string, not None",
      clean(None, []) == "" and clean("", []) == "")
check("runs of blank lines collapse", clean("a\n\n\n\n\nb", []) == "a\n\nb")

print("\n[downloaded file route]")
from fastapi.testclient import TestClient  # noqa: E402

root = Path(_DL)
(root / "Homework 3").mkdir(parents=True, exist_ok=True)
(root / "Homework 3" / "sheet.pdf").write_bytes(b"%PDF-1.4 fake")
(root.parent / "secret.txt").write_text("do not serve me")

client = TestClient(webapp.app)
ok = client.get("/api/files/Homework 3/sheet.pdf")
check("a downloaded file is served back", ok.status_code == 200 and ok.content == b"%PDF-1.4 fake",
      f"{ok.status_code}")
check("it is offered under its own filename",
      "sheet.pdf" in ok.headers.get("content-disposition", ""), ok.headers.get("content-disposition", ""))
# HTTP clients normalise "../" out of a URL before it is ever sent, so calling
# the handler directly is the only way to actually exercise the guard.
import asyncio  # noqa: E402

from fastapi import HTTPException  # noqa: E402

for attack in ("../secret.txt", "../../etc/passwd", "Homework 3/../../secret.txt",
               "/etc/passwd"):
    try:
        asyncio.run(webapp.saved_file(attack))
        refused = False
    except HTTPException as e:
        refused = e.status_code == 404
    except Exception:
        refused = False
    check(f"traversal is refused: {attack}", refused)

# And whatever a normalising client turns those into, the secret never comes back.
for attack in ("../secret.txt", "%2e%2e%2fsecret.txt", "..%2Fsecret.txt"):
    r = client.get(f"/api/files/{attack}")
    check(f"the file outside the root is never served: {attack}",
          b"do not serve me" not in r.content, str(r.status_code))

check("a missing file is a 404, not a stack trace",
      client.get("/api/files/nope.pdf").status_code == 404)


print("\n[ultra wrapper folders]")

def _doc(title, **kw):
    return {"content_id": kw.pop("cid", "_c_1"), "title": title, "type": "document",
            "is_folder": False, "is_assignment": False, "files": [], "children": [],
            "body_html": "", "position": None, **kw}


def _dir(title, children, **kw):
    return {"content_id": kw.pop("cid", "_d_1"), "title": title, "type": "folder",
            "is_folder": True, "is_assignment": False, "files": [],
            "children": children, "body_html": "", "position": 3, **kw}


wrapped = _folded([_dir("Slides Week 1",
                        [_doc("ultraDocumentBody", cid="_c_1",
                              files=[{"filename": "a.pptx"}],
                              body_html="<p>Read these.</p>")], cid="_d_1")])
check("a folder holding one unnamed document becomes that document",
      len(wrapped) == 1 and not wrapped[0]["is_folder"], str(wrapped))
check("under the name the instructor typed", wrapped[0]["title"] == "Slides Week 1")
check("keeping the document's id, so its files stay reachable",
      wrapped[0]["content_id"] == "_c_1")
check("and its files", [f["filename"] for f in wrapped[0]["files"]] == ["a.pptx"])
check("and its text", wrapped[0]["body_html"] == "<p>Read these.</p>")
check("it keeps the folder's place in the menu", wrapped[0]["position"] == 3)

both = _folded([_dir("Notes", [_doc("ultraDocumentBody", body_html="<p>doc</p>")],
                     body_html="<p>folder blurb</p>")])
check("a description on the folder is kept in front of the document's own text",
      both[0]["body_html"] == "<p>folder blurb</p><p>doc</p>", both[0]["body_html"])

check("a folder holding a named document is left alone",
      _folded([_dir("Week 1", [_doc("Chapter 1")])])[0]["is_folder"])
check("a folder holding two things is left alone",
      _folded([_dir("Week 1", [_doc("ultraDocumentBody"), _doc("Chapter 1")])
               ])[0]["is_folder"])
check("a folder holding one folder is left alone",
      _folded([_dir("Week 1", [_dir("Readings", [])])])[0]["is_folder"])
check("an empty folder is left alone",
      _folded([_dir("Week 1", [])])[0]["is_folder"])
check("the fold reaches all the way down",
      _folded([_dir("Term", [_dir("Week 1", [_doc("ultraDocumentBody")])])]
              )[0]["children"][0]["title"] == "Week 1")

print("\n[naming a document blackboard never named]")
check("a real title is left alone", _named(_doc("Chapter 1")) == "Chapter 1")
check("a stray wrapper is named from its own first line",
      _named(_doc("ultraDocumentBody",
                  body="Helpful Resources for NDSU Students\n\nWe want to…"))
      == "Helpful Resources for NDSU Students")
check("a bullet marker is not part of the name",
      _named(_doc("ultraDocumentBody", body="- Welcome\n\nmore"))  == "Welcome")
check("a first line too long to be a title is not used",
      _named(_doc("ultraDocumentBody", body="x" * 200,
                  files=[{"filename": "Syllabus.docx"}])) == "Syllabus")
check("with nothing to go on it falls back to its one file",
      _named(_doc("ultraDocumentBody", files=[{"filename": "Slides.pptx"}]))
      == "Slides")
check("two files are no help, so it says what it is",
      _named(_doc("ultraDocumentBody",
                  files=[{"filename": "a.pptx"}, {"filename": "b.pptx"}]))
      == "Document")
check("and an empty item still gets a name", _named(_doc("")) == "Document")

print("\n[links in course text]")
from blackboard_mcp.client import html_to_text as _h2t  # noqa: E402

_A = ('<p>See <a href="https://career-advising.ndsu.edu/bisonadvise/">'
      'Bison Advise - Your Advising Resource</a> for help.</p>')
check("an agent still gets the url it can act on",
      _h2t(_A) == "See [https://career-advising.ndsu.edu/bisonadvise/] "
                  "Bison Advise - Your Advising Resource for help.", _h2t(_A))
check("the page gets the anchor the author wrote",
      _h2t(_A, links="markdown")
      == "See [Bison Advise - Your Advising Resource]"
         "(https://career-advising.ndsu.edu/bisonadvise/) for help.",
      _h2t(_A, links="markdown"))
check("an anchor with no text falls back to its url",
      _h2t('<a href="https://x.edu"><img src="y"></a>', links="markdown")
      == "[https://x.edu](https://x.edu)")
check("an unclosed anchor still gives its text back",
      _h2t('<a href="https://x.edu">dangling', links="markdown")
      == "[dangling](https://x.edu)")
check("a bracket in the label is escaped, not left to break the link",
      _h2t('<a href="https://x.edu">a ] b</a>', links="markdown")
      == r"[a \] b](https://x.edu)",
      _h2t('<a href="https://x.edu">a ] b</a>', links="markdown"))
check("an anchor broken across block tags keeps one label",
      _h2t('<a href="https://x.edu"><div>two</div><div>lines</div></a>',
           links="markdown") == "[two lines](https://x.edu)",
      _h2t('<a href="https://x.edu"><div>two</div><div>lines</div></a>',
           links="markdown"))
check("in-page and script anchors are still ignored",
      _h2t('<a href="#top">up</a> <a href="javascript:x()">go</a>',
           links="markdown") == "up go")
check("text with no links is untouched either way",
      _h2t("<p>Plain.</p>") == _h2t("<p>Plain.</p>", links="markdown") == "Plain.")

print("\n[assignment instructions, rendered on the way out]")
_U = "https://career-advising.ndsu.edu/bisonadvise/"
_fresh = webapp._present({
    "instructions_html": f'<p>See <a href="{_U}">Bison Advise</a>.</p>',
    "instructions": f"See [{_U}] Bison Advise.", "attachments": [],
})
check("the anchor is rebuilt from the markup the cache kept",
      _fresh["instructions"] == f"See [Bison Advise]({_U}).",
      _fresh["instructions"])
check("and the markup itself is not handed to the page",
      "instructions_html" not in _fresh, str(sorted(_fresh)))
_legacy = webapp._present({
    "instructions": f"See [{_U}] Bison Advise.", "attachments": [],
})
check("an entry cached before the markup was kept still reads",
      _legacy["instructions"] == f"See [{_U}] Bison Advise.",
      _legacy["instructions"])
check("an attachment anchor is still stripped when read this way",
      webapp._present({
          "instructions_html": '<a href="https://bb.test/x?t=1">Handout.docx</a>',
          "attachments": [{"filename": "Handout.docx"}],
      })["instructions"] == "")

print("\n[course content route]")
webapp.cache.write("content__11_1", {
    "course_id": "_11_1", "accessible": True, "counts": {"items": 2},
    "nodes": [{"title": "Week 1", "is_folder": True, "children": []}],
    "schema": sync.TREE_SCHEMA,
})
served = TestClient(webapp.app).get("/api/courses/_11_1/content")
check("a cached tree is served without touching Blackboard",
      served.status_code == 200 and served.json()["cached"] is True,
      f"{served.status_code} {served.text[:200]}")
check("the tree comes back whole",
      served.json()["nodes"][0]["title"] == "Week 1", served.text[:200])

# Written under an older shape — no link field on its rows — it has to be
# re-walked rather than served, so the row's Blackboard link is not missing for
# the six hours the entry would otherwise stay fresh. There is no session here,
# so the re-walk fails; failing to serve the stale copy is the point.
webapp.cache.write("content__12_1", {
    "course_id": "_12_1", "accessible": True,
    "nodes": [{"title": "Week 1", "is_folder": True, "children": []}],
})
stale = TestClient(webapp.app).get("/api/courses/_12_1/content")
check("a tree cached before the link field is not served from cache",
      not (stale.status_code == 200 and stale.json().get("cached") is True),
      f"{stale.status_code} {stale.text[:120]}")

print("\n[blackboard web auth]")
saved_env = {
    k: os.environ.get(k)
    for k in ("BB_HOST", "BB_USERNAME", "BB_PASSWORD", "BB_COOKIE", "BB_SESSION_FILE")
}
old_env_path, old_profile, old_cache = webapp.ENV_PATH, webapp.PROFILE_DIR, webapp.cache
try:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        webapp.ENV_PATH = base / ".env"
        webapp.PROFILE_DIR = base / ".bb_browser"
        cache_dir = base / "cache"
        cache_dir.mkdir()
        webapp.cache = Cache(cache_dir)
        os.environ["BB_SESSION_FILE"] = str(base / ".bb_session.json")
        for k in ("BB_HOST", "BB_USERNAME", "BB_PASSWORD", "BB_COOKIE"):
            os.environ.pop(k, None)

        cookie = "BbRouter=expires:4102444800,id:abcd,signature:xyz"
        webapp._set_env_values({
            "BB_HOST": "https://bb.test",
            "BB_USERNAME": "student",
            "BB_PASSWORD": "secret with spaces",
            "BB_COOKIE": cookie,
        })
        status = webapp._auth_status()
        check("auth status recognises saved credentials",
              status["configured"] and status["host"] == "https://bb.test",
              str(status))
        check("auth status reports the saved session",
              status["logged_in"] and status["cookie_source"] == "env", str(status))
        check("auth status never returns the password",
              "password" not in status and status["has_password"] is True, str(status))

        webapp.cache.write("assignments", {"items": [1]})
        webapp.PROFILE_DIR.mkdir()
        (webapp.PROFILE_DIR / "cookie").write_text("browser cookie")
        asyncio.run(webapp.auth_logout())
        logged_out = webapp._auth_status()
        check("logout clears the active cookie", logged_out["logged_in"] is False,
              str(logged_out))
        check("logout clears cached coursework", webapp.cache.keys() == [],
              str(webapp.cache.keys()))
        check("logout removes the saved browser profile",
              not webapp.PROFILE_DIR.exists(), str(webapp.PROFILE_DIR))
finally:
    webapp.ENV_PATH, webapp.PROFILE_DIR, webapp.cache = old_env_path, old_profile, old_cache
    for key, value in saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


print("\n[google docs]")
from blackboard_web import gdocs  # noqa: E402

os.environ["BB_GOOGLE_TOKEN_FILE"] = str(Path(_DL) / "tok.json")
os.environ.pop("BB_GOOGLE_CLIENT_ID", None)
os.environ.pop("BB_GOOGLE_CLIENT_SECRET", None)

check("without credentials it reports unconfigured", gdocs.configured() is False)
try:
    gdocs.credentials()
    told = ""
except gdocs.GoogleError as e:
    told = str(e)
# The step-by-step instructions live in the settings screen now, so the error
# has one job: say where to go. Repeating them here would be a second copy to
# drift out of date.
check("and points at the setup screen", "Settings" in told, told)

os.environ["BB_GOOGLE_CLIENT_ID"] = "cid.apps.googleusercontent.com"
os.environ["BB_GOOGLE_CLIENT_SECRET"] = "secret"
check("with both set it is configured", gdocs.configured() is True)
check("but not yet authorised", gdocs.authorised() is False)

gdocs._save_tokens({"refresh_token": "rt"})
check("a stored refresh token counts as authorised", gdocs.authorised() is True)
check("the token file is user-only readable",
      (Path(_DL) / "tok.json").stat().st_mode & 0o077 == 0, "mode leaks to group/other")
check("the client secret is never written to the token file",
      "secret" not in (Path(_DL) / "tok.json").read_text())

check("the status route publishes what drive can convert",
      set(asyncio.run(webapp.google_status())["convertible"]) == set(gdocs.CONVERTIBLE),
      str(asyncio.run(webapp.google_status()).get("convertible")))
check("and that list is word-processing formats, not decks or archives",
      ".docx" in gdocs.CONVERTIBLE and ".pptx" not in gdocs.CONVERTIBLE
      and ".zip" not in gdocs.CONVERTIBLE and ".xlsx" not in gdocs.CONVERTIBLE,
      str(sorted(gdocs.CONVERTIBLE)))


print("\n[client secret file]")
DESKTOP = ('{"installed": {"client_id": "cid.apps.googleusercontent.com",'
           ' "client_secret": "s3cret", "redirect_uris": ["http://localhost"]}}')
check("a desktop client file gives up its two values",
      gdocs.parse_client_json(DESKTOP) == ("cid.apps.googleusercontent.com", "s3cret"))
for label, payload, expect in [
    ("a web client is refused with the reason", '{"web": {"client_id": "a", "client_secret": "b"}}', "Desktop"),
    ("a file that is not JSON is refused", "not json at all", "JSON"),
    ("a JSON file missing the fields is refused", '{"installed": {}}', "client_id"),
]:
    try:
        gdocs.parse_client_json(payload)
        told = ""
    except gdocs.GoogleError as e:
        told = str(e)
    check(label, expect in told, told or "no error raised")

print("\n[consent flow]")
check("nothing is pending before it starts", gdocs.flow_state()["pending"] is False)
consent = gdocs.begin(timeout=5)
check("the consent url carries the narrow scope", "drive.file" in consent, consent)
check("and asks for offline access, or there is no refresh token",
      "access_type=offline" in consent and "prompt=consent" in consent)
check("and redirects to a loopback port", "127.0.0.1" in consent)
check("the flow is now pending", gdocs.flow_state()["pending"] is True)
gdocs.cancel()
check("cancelling ends it", gdocs.flow_state()["pending"] is False)

check("a converted upload opens in the Docs editor",
      gdocs.doc_url("abc", gdocs.DOC_MIME) == "https://docs.google.com/document/d/abc/edit")
check("anything else opens in the Drive viewer",
      gdocs.doc_url("abc", "application/zip") == "https://drive.google.com/file/d/abc/view")
check("word documents are converted", ".docx" in gdocs.CONVERTIBLE)
check("an archive is not", ".zip" not in gdocs.CONVERTIBLE)

check("the scope is the narrow create-only one",
      gdocs.SCOPE.endswith("/auth/drive.file"), gdocs.SCOPE)

os.environ["BB_BROWSER"] = "librewolf"
check("the browser is configurable", gdocs.browser_command() == ["librewolf"])
os.environ.pop("BB_BROWSER")
check("and otherwise discovered", gdocs.browser_command() is None
      or Path(gdocs.browser_command()[0]).name in ("librewolf", "firefox", "xdg-open"),
      str(gdocs.browser_command()))

missing = Path(_DL) / "not-here.docx"
try:
    gdocs.upload_as_doc(missing)
    refused = False
except gdocs.GoogleError:
    refused = True
check("uploading a file that is not on disk is refused, not attempted", refused)

print("\n[docs route guards]")
# Each guard needs its own state; the checks above left a token behind.
Path(_DL, "tok.json").unlink()
os.environ.pop("BB_GOOGLE_CLIENT_ID")
os.environ.pop("BB_GOOGLE_CLIENT_SECRET")
res = client.post("/api/assignments/_c_/_i_/gdocs", json={"filename": "x.docx"})
check("with no credentials the route explains the setup", res.status_code == 503,
      str(res.status_code))
check("and never reaches Google", "Settings" in res.json()["detail"])

os.environ["BB_GOOGLE_CLIENT_ID"] = "cid.apps.googleusercontent.com"
os.environ["BB_GOOGLE_CLIENT_SECRET"] = "secret"
res = client.post("/api/assignments/_c_/_i_/gdocs", json={"filename": "x.docx"})
check("configured but not connected asks to connect first", res.status_code == 428,
      str(res.status_code))

# _find_downloaded must not treat the filename as a glob.
(root / "Homework 3" / "notes.txt").write_text("hi")
check("an existing handout is found by exact name",
      webapp._find_downloaded("notes.txt").name == "notes.txt")
check("a glob pattern matches nothing", webapp._find_downloaded("*.txt") is None)
check("a path is reduced to its basename",
      webapp._find_downloaded("../../notes.txt").name == "notes.txt")
check("an empty name finds nothing", webapp._find_downloaded("") is None)


print("\n[courses with a hidden gradebook]")
from blackboard_mcp.client import BlackboardError, ForbiddenError  # noqa: E402


class _Bb:
    """Just enough client for _course_bundle: each call either answers or raises."""

    def __init__(self, columns_raises=None):
        self._raise = columns_raises

    async def gradebook_categories(self, _cid):
        return [{"id": "_c1_"}]

    async def gradebook_columns(self, _cid):
        if self._raise:
            raise self._raise
        return [{"id": "_col_"}]

    async def gradebook_grades(self, _cid, _uid):
        return {}


bundle = asyncio.run(sync._course_bundle(_Bb(), "_1_", "_u_"))
check("an ordinary course is accessible and not hidden",
      bundle["accessible"] and not bundle["hidden"], str(bundle))

bundle = asyncio.run(sync._course_bundle(
    _Bb(ForbiddenError("403 Forbidden")), "_1_", "_u_"))
check("a 403 on the gradebook marks the course hidden",
      bundle["hidden"] and not bundle["accessible"], str(bundle))

# The distinction that matters: a course must not vanish from the dashboard
# because one request timed out.
bundle = asyncio.run(sync._course_bundle(
    _Bb(BlackboardError("connection reset")), "_1_", "_u_"))
check("any other failure leaves the course listed",
      not bundle["hidden"], str(bundle))
check("even though it has no grade to show", not bundle["accessible"], str(bundle))


print("\n[links back to blackboard]")
from blackboard_mcp.client import (  # noqa: E402
    Course, launch_url, launches_in_blackboard, web_link,
)

ITEM = {"id": "_1_", "links": [{"href": "/ultra/redirect?courseId=_2_&contentId=_1_",
                                "rel": "alternate", "type": "text/html"}]}
check("a relative link is joined to the origin",
      web_link(ITEM, "https://bb.test") ==
      "https://bb.test/ultra/redirect?courseId=_2_&contentId=_1_",
      web_link(ITEM, "https://bb.test"))
check("a trailing slash on the origin does not double up",
      web_link(ITEM, "https://bb.test/").startswith("https://bb.test/ultra"))
check("an absolute link is passed through",
      web_link({"links": [{"href": "https://bb.test/x", "rel": "alternate"}]}, "https://o")
      == "https://bb.test/x")
check("an item with no links has no url", web_link({"id": "_1_"}, "https://bb.test") is None)
check("an empty links list has no url", web_link({"links": []}, "https://bb.test") is None)
check("a link of another kind is not followed",
      web_link({"links": [{"href": "/x", "rel": "edit"}]}, "https://bb.test") is None)
check("a link with no href is skipped",
      web_link({"links": [{"rel": "alternate"}]}, "https://bb.test") is None)

def _item(kind, links=True):
    item = {"id": "_1_", "contentHandler": {"id": kind}}
    if links:
        item["links"] = [{"href": "/ultra/redirect?contentId=_1_", "rel": "alternate"}]
    return item


# What Blackboard runs rather than stores — the link is the only way in.
for kind in ("resource/x-bb-blti-link",
             "resource/x-bb-bltiplacement-zoomlti_13_ndsu",
             "resource/x-bb-bltiplacement-SmartEvals_Course_Tool",
             "resource/x-bb-asmt-test-link",
             "resource/x-bb-assignment",
             "resource/x-bb-courselink"):
    check(f"{kind.replace('resource/x-bb-', '')} is worth opening there",
          launches_in_blackboard(_item(kind)))

# What this dashboard already shows in full, and what leaves Blackboard anyway.
for kind in ("resource/x-bb-file", "resource/x-bb-document", "resource/x-bb-folder",
             "resource/x-bb-lesson", "resource/x-bb-externallink"):
    check(f"{kind.replace('resource/x-bb-', '')} is not",
          not launches_in_blackboard(_item(kind)))

check("an item with no handler is not", not launches_in_blackboard({"id": "_1_"}))

# An LTI item's own URL is its tool's launch endpoint, which only answers a
# signed handoff from Blackboard — offering it as a link sends people to an
# error page.
lti = sync._node({"id": "_1_", "title": "Class Participation",
                  "contentHandler": {"id": "resource/x-bb-blti-link",
                                     "url": "https://vevox.example/lti/v1p3/launch/x"}})
check("an lti item does not offer its tool's launch endpoint", lti["url"] is None,
      str(lti["url"]))
ext = sync._node({"id": "_2_", "title": "GitLab",
                  "contentHandler": {"id": "resource/x-bb-externallink",
                                     "url": "https://gitlab.example/"}})
check("an external link still offers where it goes",
      ext["url"] == "https://gitlab.example/", str(ext["url"]))
check("a launchable item gets the url",
      launch_url(_item("resource/x-bb-blti-link"), "https://bb.test") ==
      "https://bb.test/ultra/redirect?contentId=_1_")
check("a file gets none even though blackboard offers a link",
      launch_url(_item("resource/x-bb-file"), "https://bb.test") is None)
check("and a launchable item with no link still gets none",
      launch_url(_item("resource/x-bb-blti-link", links=False), "https://bb.test") is None)

course = Course.from_api({"id": "_2_", "courseId": "C1", "name": "n",
                          "externalAccessUrl": "https://bb.test/ultra/courses/_2_/outline"})
check("a course keeps the url blackboard reports for it",
      course.external_url == "https://bb.test/ultra/courses/_2_/outline")
check("and tolerates one that does not report it",
      Course.from_api({"id": "_2_", "name": "n"}).external_url is None)

with tempfile.TemporaryDirectory() as td:
    c = Cache(Path(td))
    # A tree cached under the previous shape has to be re-walked, not served.
    c.write("content__1_", {"nodes": [{"content_id": "_9_"}], "accessible": True})
    stored = c.get_data("content__1_")
    check("a tree cached before the link field is not served from cache",
          stored.get("schema") != sync.TREE_SCHEMA, str(stored.get("schema")))
    c.write("content__1_", {"nodes": [], "schema": sync.TREE_SCHEMA})
    check("one written now is", c.get_data("content__1_")["schema"] == sync.TREE_SCHEMA)


print("\n[login failures and progress]")
from blackboard_mcp.login import LoginError  # noqa: E402

check("a login error carries a reason",
      LoginError("nope", reason="credentials").reason == "credentials")
check("and defaults to a generic one", LoginError("nope").reason == "failed")

from blackboard_mcp.login import _rejected_text  # noqa: E402

check("a provider that says the password is incorrect is a rejection",
      _rejected_text("the password you entered is incorrect. please try again."))
check("so is an invalid user id",
      _rejected_text("invalid user id or password"))
check("so is a failed authentication",
      _rejected_text("authentication failed — check your username"))
# The words alone are not enough: a login page's own help text carries them
# before anything has gone wrong.
check("but the help text on a fresh login page is not",
      not _rejected_text("trouble logging in? your browser may be out of date."))
check("nor an unrelated error",
      not _rejected_text("invalid request — the page has expired"))

old_env_path = webapp.ENV_PATH
try:
    with tempfile.TemporaryDirectory() as td:
        webapp.ENV_PATH = Path(td) / ".env"
        os.environ["BB_PASSWORD"] = "wrong one"
        exc = webapp._login_failure(
            LoginError("That username or password was not accepted.",
                       reason="credentials"), "fallback")
        check("a refused password answers 401", exc.status_code == 401,
              str(exc.status_code))
        check("it names the reason so the form can re-ask",
              exc.detail["reason"] == "credentials", str(exc.detail))
        check("it keeps the message human",
              exc.detail["message"] == "That username or password was not accepted.")
        check("and it drops the stored password",
              os.environ["BB_PASSWORD"] == "", repr(os.environ["BB_PASSWORD"]))

        os.environ["BB_PASSWORD"] = "still good"
        exc = webapp._login_failure(LoginError("timed out", reason="timeout"), "x")
        check("any other failure is a 502", exc.status_code == 502, str(exc.status_code))
        check("which keeps the password", os.environ["BB_PASSWORD"] == "still good")
finally:
    webapp.ENV_PATH = old_env_path
    os.environ.pop("BB_PASSWORD", None)

webapp._stage("duo", active=True)
progress = asyncio.run(webapp.auth_progress())
check("progress reports the stage it is on",
      progress["stage"] == "duo" and progress["active"] is True, str(progress))
check("and how long it has been there", progress["seconds"] is not None)
webapp._stage(None, active=False)
progress = asyncio.run(webapp.auth_progress())
check("an idle server reports no stage",
      progress["stage"] is None and progress["active"] is False, str(progress))

check("the login body no longer accepts a headed browser",
      "headed" not in webapp.CredentialsBody.model_fields,
      str(list(webapp.CredentialsBody.model_fields)))
check("nor debug screenshots",
      "debug" not in webapp.CredentialsBody.model_fields)


print("\n[clearing stored data]")
old_downloads, old_cache_obj = webapp.DOWNLOADS, webapp.cache
try:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cache_dir = base / "cache"
        cache_dir.mkdir()
        webapp.cache = Cache(cache_dir)
        webapp.cache.write("assignments", {"items": [1]})
        webapp.cache.write("announced", {"shown": {"_a_": "now"}})
        (cache_dir / "coursework.ics").write_text("BEGIN:VCALENDAR\r\n")

        downloads = base / "blackboard-files"
        (downloads / "Homework 3" / "deep").mkdir(parents=True)
        (downloads / "Homework 3" / "sheet.pdf").write_bytes(b"x" * 2048)
        (downloads / "Homework 3" / "deep" / "notes.txt").write_text("hi")
        webapp.DOWNLOADS = downloads

        # The things a reset must not touch.
        keep = {
            ".env": "BB_COOKIE=abc",
            ".bb_session.json": "{}",
            ".bb_google.json": '{"refresh_token": "keep me"}',
        }
        for name, text in keep.items():
            (base / name).write_text(text)

        result = asyncio.run(webapp.reset_data())
        check("it reports what it removed",
              result["cache_entries"] == 3 and result["files"] == 2, str(result))
        check("it reports the space freed", result["bytes"] > 2048, str(result))
        check("the cache is empty", webapp.cache.keys() == [], str(webapp.cache.keys()))
        check("the .ics goes with it", not (cache_dir / "coursework.ics").exists())
        check("every downloaded file is gone",
              [p for p in downloads.rglob("*") if p.is_file()] == [])
        check("their folders go too", list(downloads.iterdir()) == [],
              str(list(downloads.iterdir())))
        check("the downloads folder itself stays", downloads.is_dir())
        check("credentials and the Google token are untouched",
              all((base / name).read_text() == text for name, text in keep.items()))

        # A mistyped BB_DOWNLOAD_DIR must not take the surrounding files with it.
        webapp.DOWNLOADS = Path.home()
        check("it refuses to empty a home directory", webapp._wipe_downloads() == (0, 0))
        webapp.DOWNLOADS = paths.state_dir()
        check("it refuses to empty the state directory",
              webapp._wipe_downloads() == (0, 0))
finally:
    webapp.DOWNLOADS, webapp.cache = old_downloads, old_cache_obj


print("\n[announcement popup record]")
with tempfile.TemporaryDirectory() as td:
    c = Cache(Path(td))
    posts = [{"id": "_a_"}, {"id": "_b_"}]
    check("the first run baselines instead of popping up", N.pending(c, posts) == [])
    check("the baseline names every post present", set(N.load(c)) == {"_a_", "_b_"})
    check("nothing new stays quiet", N.pending(c, posts) == [])

    later = [{"id": "_c_"}, *posts]
    check("a post that arrives after the baseline pops up",
          N.pending(c, later) == ["_c_"], str(N.pending(c, later)))
    N.mark(c, ["_c_"])
    check("once shown it does not pop up again", N.pending(c, later) == [])

    first = N.load(c)["_a_"]
    N.mark(c, ["_a_", "_a_"])
    check("re-marking keeps the original time", N.load(c)["_a_"] == first)
    check("an id with no post behind it is harmless",
          N.pending(c, [{"id": "_c_"}]) == [])
    check("a post with no id is skipped", N.pending(c, [{"title": "no id"}]) == [])
    # Ageing out of the cached window is the same as never having been posted.
    check("a post that has aged out of the list cannot pop up",
          N.pending(c, [{"id": "_a_"}]) == [])


print("\n" + "=" * 60)
print("ALL CHECKS PASSED" if not FAILS else f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
print("=" * 60)
sys.exit(1 if FAILS else 0)
