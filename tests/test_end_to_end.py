"""End-to-end exercise of every MCP tool against the stub Blackboard instance."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import stub_blackboard as stub

BASE, SRV = stub.start()
os.environ["BB_HOST"] = BASE
os.environ["BB_COOKIE"] = "BbRouter=expires:1234,id:abcd,signature:xyz"
import tempfile as _tf
SESSION_FILE = str(Path(_tf.mkdtemp()) / "bb_session.json")
os.environ["BB_SESSION_FILE"] = SESSION_FILE
os.environ["BB_KEEPALIVE_MINUTES"] = "45"
# The web app pins its download root at import time; keep it out of the way.
os.environ["BB_DOWNLOAD_DIR"] = str(Path(_tf.mkdtemp()) / "files")
os.environ["BB_CACHE_DIR"] = _tf.mkdtemp()

from blackboard_mcp import server as S            # noqa: E402
from blackboard_mcp.client import (               # noqa: E402
    html_to_text, normalize_cookie, normalize_host,
)

FAILS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


async def main() -> None:
    print("\n[unit] cookie / host / html normalisation")
    check("bare BbRouter pair kept",
          normalize_cookie("BbRouter=expires:1,id:2") == "BbRouter=expires:1,id:2")
    check("full cookie dump filtered to relevant pairs",
          normalize_cookie("_ga=GA1.2.99; BbRouter=expires:1,id:2; _fbp=fb.1.x; JSESSIONID=ABC")
          == "BbRouter=expires:1,id:2; JSESSIONID=ABC")
    check("bare value gets a name", normalize_cookie("expires:1,id:2") == "BbRouter=expires:1,id:2")
    try:
        normalize_cookie("_ga=GA1.2.99; _fbp=fb.1.x")
        check("cookie without BbRouter rejected", False)
    except Exception as e:
        check("cookie without BbRouter rejected", "BbRouter" in str(e))
    try:
        normalize_cookie("")
        check("empty cookie rejected", False)
    except Exception:
        check("empty cookie rejected", True)
    # Whichever DevTools pane the cookie is copied from, it must normalise the
    # same way: request Cookie header, response Set-Cookie (attributes and all),
    # or the bare Value cell in the Application > Cookies panel.
    _v = "expires:1788157763,id:3E11ABC,timeout:10800,v:2"
    forms = {
        "request header": f"_ga=GA1.2.9; BbRouter={_v}; AWSALB=xyz",
        "set-cookie": f"BbRouter={_v}; Path=/; Secure; HttpOnly; SameSite=None",
        "bare value": _v,
        "set-cookie with Expires": f"BbRouter={_v}; Expires=Sun, 31 Aug 2026 06:29:23 GMT",
    }
    normalised = {k: normalize_cookie(v) for k, v in forms.items()}
    for k, out in normalised.items():
        check(f"{k} yields a clean BbRouter",
              out.split(";")[0] == f"BbRouter={_v}", out)
    check("cookie attributes stripped, not treated as cookies",
          not any(x in o for o in normalised.values()
                  for x in ("Path=", "Secure", "HttpOnly", "SameSite", "Expires=")),
          str(normalised))

    check("host gets https", normalize_host("bb.edu") == "https://bb.edu")
    check("deep link trimmed to origin",
          normalize_host("https://bb.edu/ultra/course/_1_1/outline") == "https://bb.edu")
    text = html_to_text(stub.CONTENT["_501_1"]["body"])
    check("html entities decoded", "Dijkstra's" in text, text)
    check("script contents dropped", "tracking()" not in text, text)
    check("list items become bullets", "- Part A: adjacency list" in text, text)
    check("link href preserved", "https://example.edu/spec.pdf" in text, text)

    print("\n[tool] check_connection")
    r = await S.check_connection()
    check("connected", r.get("connected") is True, str(r))
    check("identifies user", r["user"]["name"] == "Ethan Phillips", str(r.get("user")))
    check("counts active courses (excludes unavailable + org)",
          r["active_course_count"] == 2, str(r))

    print("\n[tool] list_courses")
    r = await S.list_courses()
    check("2 active courses via paging", r["count"] == 2, str([c["code"] for c in r["courses"]]))
    check("organization excluded", "ORG-1" not in [c["code"] for c in r["courses"]])
    check("unavailable excluded", "HIST-101" not in [c["code"] for c in r["courses"]])
    r_all = await S.list_courses(include_unavailable=True)
    check("include_unavailable surfaces past term",
          "HIST-101" in [c["code"] for c in r_all["courses"]], str(r_all["count"]))

    print("\n[tool] list_due_dates")
    r = await S.list_due_dates(days_ahead=30)
    titles = [i["title"] for i in r["items"]]
    check("Homework 3 (due +5d) included", "Homework 3" in titles, str(titles))
    check("Essay Draft (due +10d) included", "Essay Draft" in titles, str(titles))
    check("Quiz 1 (due +40d) outside window", "Quiz 1" not in titles, str(titles))
    check("graded Lab 2 filtered out", "Lab 2" not in titles, str(titles))
    check("calculated column filtered out", "Weighted Total" not in titles, str(titles))
    check("undated Participation excluded by default", "Participation" not in titles, str(titles))
    check("sorted by due date", titles == sorted(titles, key=lambda t: {"Homework 3": 0, "Essay Draft": 1}[t]), str(titles))

    hw = next(i for i in r["items"] if i["title"] == "Homework 3")
    # The label comes from the course's NAME, never the institutional id — a
    # student recognises "Algorithms", not "CS-340-001".
    check("course label uses the course name", hw["course"] == "Algorithms", hw["course"])
    check("content_id present for download", hw["content_id"] == "_501_1", str(hw))
    check("points carried", hw["points_possible"] == 100, str(hw))
    check("days_until ~5", 4.5 < hw["days_until"] < 5.5, str(hw["days_until"]))
    ev = hw["calendar_event"]
    check("calendar summary is course + title",
          ev["summary"] == "Algorithms: Homework 3", ev["summary"])
    check("calendar event ends at the deadline", ev["end"] == hw["due_local"], str(ev))
    check("calendar event has a start before the end", ev["start"] < ev["end"], str(ev))
    check("due_local is tz-aware", "+" in hw["due_local"] or "-" in hw["due_local"][10:],
          hw["due_local"])

    r2 = await S.list_due_dates(days_ahead=30, include_submitted=True)
    check("include_submitted brings back Lab 2",
          "Lab 2" in [i["title"] for i in r2["items"]], str([i["title"] for i in r2["items"]]))
    check("Lab 2 marked submitted",
          next(i for i in r2["items"] if i["title"] == "Lab 2")["submitted"] is True)
    r3 = await S.list_due_dates(days_ahead=60)
    check("wider window catches Quiz 1", "Quiz 1" in [i["title"] for i in r3["items"]])
    r4 = await S.list_due_dates(days_ahead=30, include_undated=True)
    check("include_undated surfaces Participation",
          "Participation" in [i["title"] for i in r4["items"]])
    r5 = await S.list_due_dates(days_ahead=30, course_id="ENG")
    check("course filter by name substring",
          [i["title"] for i in r5["items"]] == ["Essay Draft"], str(r5["items"]))
    r6 = await S.list_due_dates(days_ahead=30, course_id="CS-340-001")
    check("course filter by code", [i["title"] for i in r6["items"]] == ["Homework 3"])
    try:
        await S.list_due_dates(course_id="NOPE-999")
        check("unknown course raises with suggestions", False)
    except Exception as e:
        check("unknown course raises with suggestions", "CS-340-001" in str(e), str(e))

    print("\n[tool] get_assignment")
    r = await S.get_assignment("_11_1", "_501_1")
    check("title", r["title"] == "Homework 3")
    check("instructions flattened to text", "Dijkstra's" in r["instructions"])
    check("two attachments listed", len(r["attachments"]) == 2, str(r["attachments"]))
    check("next_step points at download", "download_assignment_files" in r["next_step"])
    r_noatt = await S.get_assignment("_22_1", "_601_1")
    check("no-attachment case explained", "whole assignment" in r_noatt["next_step"])

    print("\n[tool] download_assignment_files")
    with tempfile.TemporaryDirectory() as td:
        r = await S.download_assignment_files("_11_1", "_501_1", dest_dir=td)
        names = sorted(f["filename"] for f in r["downloaded"])
        check("both files downloaded", len(r["downloaded"]) == 2, str(r))
        check("pdf saved", "hw3.pdf" in names, str(names))
        check("path traversal neutralised",
              all(".." not in n and "/" not in n for n in names), str(names))
        check("traversal file landed inside dest dir",
              all(Path(f["path"]).resolve().is_relative_to(Path(td).resolve())
                  for f in r["downloaded"]), str(r["downloaded"]))
        check("302-to-storage download followed",
              any(f["bytes"] == len(stub.FILES["_a2_1"]) for f in r["downloaded"]), str(r))
        check("bytes written to disk match",
              (Path(r["directory"]) / "hw3.pdf").read_bytes() == stub.FILES["_a1_1"])
        check("per-assignment subfolder created",
              Path(r["directory"]).name == "Homework 3", r["directory"])
        # Second run must not clobber the first.
        r_again = await S.download_assignment_files("_11_1", "_501_1", dest_dir=td)
        check("re-download does not overwrite",
              any("(2)" in f["filename"] for f in r_again["downloaded"]),
              str([f["filename"] for f in r_again["downloaded"]]))
        r_empty = await S.download_assignment_files("_22_1", "_601_1", dest_dir=td)
        check("no-attachment download explained", r_empty["downloaded"] == []
              and "no attached files" in r_empty["note"])

    print("\n[ultra] instructions + inline attachments")
    from blackboard_mcp.client import content_instructions, extract_embedded_files
    handler = stub.CONTENT[stub.ULTRA_ID]["contentHandler"]
    check("instructions read from contentHandler when body is empty",
          "Read the handout." in content_instructions(stub.CONTENT[stub.ULTRA_ID]))
    files = extract_embedded_files(handler["instructions"])
    check("inline attachment found", len(files) == 1, str(files))
    check("real filename from data-bbfile",
          files[0].filename == "Handout.docx", str(files[0]))
    check("mime + size parsed", files[0].size == 1234 and "wordprocessing" in
          (files[0].mime_type or ""), str(files[0]))
    check("href entities decoded", "&amp;" not in files[0].url, files[0].url)
    check("plain html yields no inline files",
          extract_embedded_files("<p>no files here</p>") == [])

    ua = await S.get_assignment("_11_1", stub.ULTRA_ID)
    check("Ultra assignment survives the 400 on /attachments",
          len(ua["attachments"]) == 1, str(ua["attachments"]))
    check("inline attachment marked as such",
          ua["attachments"][0]["source"] == "inline", str(ua["attachments"]))
    check("Ultra type reported", ua["type"] == "asmt-test-link", str(ua["type"]))
    check("grade column linked", ua["grade_column_id"] == "_c9_1", str(ua))

    # Point the inline link at the stub so the download path is exercised too.
    stub.CONTENT[stub.ULTRA_ID]["contentHandler"]["instructions"] = (
        stub.CONTENT[stub.ULTRA_ID]["contentHandler"]["instructions"]
        .replace("https://bb.test/bbcswebdav/pid-1/xid-1?sig=abc&amp;t=2",
                 f"{BASE}/storage/_a1_1?sig=abc&amp;t=2"))
    with tempfile.TemporaryDirectory() as td:
        du = await S.download_assignment_files("_11_1", stub.ULTRA_ID, dest_dir=td)
        check("inline Ultra attachment downloaded",
              len(du["downloaded"]) == 1, str(du))
        check("saved under its real filename",
              du["downloaded"][0]["filename"] == "Handout.docx", str(du["downloaded"]))
        check("inline file contents written",
              (Path(du["directory"]) / "Handout.docx").read_bytes() == stub.FILES["_a1_1"])

    print("\n[ultra] document bodies (no href, no data-bbtype)")
    stub.CONTENT[stub.DOC_ID]["body"] = (
        stub.CONTENT[stub.DOC_ID]["body"].replace("__BASE__", BASE))
    docfiles = extract_embedded_files(stub.CONTENT[stub.DOC_ID]["body"])
    check("file found with no href and no data-bbtype",
          len(docfiles) == 1, str(docfiles))
    check("url taken from data-bbfile.resourceUrl",
          docfiles[0].url.startswith(BASE) and "sig=abc" in docfiles[0].url,
          docfiles[0].url)
    check("filename from linkName", docfiles[0].filename == "Fall_2026_Syllabus.docx",
          docfiles[0].filename)
    check("resourceUrl and viewerUrl dedupe to one file", len(docfiles) == 1)

    da = await S.get_assignment("_11_1", stub.DOC_ID)
    check("generic ultraDocumentBody title replaced by parent folder",
          da["title"] == "Course Syllabus", f"{da['title']} / {da['raw_title']}")
    check("raw title still reported", da["raw_title"] == "ultraDocumentBody")
    check("document attachment surfaced", len(da["attachments"]) == 1, str(da["attachments"]))
    check("next_step no longer claims there are no files",
          "download_assignment_files" in da["next_step"], da["next_step"])
    with tempfile.TemporaryDirectory() as td:
        dd = await S.download_assignment_files("_11_1", stub.DOC_ID, dest_dir=td)
        check("document file downloaded", len(dd["downloaded"]) == 1, str(dd))
        check("saved under the parent folder name",
              Path(dd["directory"]).name == "Course Syllabus", dd["directory"])
        check("document bytes written",
              (Path(dd["directory"]) / "Fall_2026_Syllabus.docx").read_bytes()
              == stub.FILES["_a1_1"])

    print("\n[tool] browse_course_content / list_announcements")
    r = await S.browse_course_content("CS-340-001")
    check("folder flagged", any(e["is_folder"] for e in r["entries"]), str(r["entries"]))
    check("assignment flagged", any(e["is_assignment"] for e in r["entries"]))
    check("type label cleaned", any(e["type"] == "folder" for e in r["entries"]), str(r["entries"]))
    r = await S.list_announcements()
    check("announcement returned", r["count"] == 1, str(r))
    check("announcement body is plain text",
          "Homework 3 is now due Friday." in r["announcements"][0]["body"], str(r))

    print("\n[web] the course content tree")
    import tempfile as _tmp
    from blackboard_web.cache import Cache as _Cache
    from blackboard_web import sync as _sync
    with _tmp.TemporaryDirectory() as td:
        cache = _Cache(Path(td))
        tree = await _sync.course_content(cache, "_11_1")
        titles = [n["title"] for n in tree["nodes"]]
        check("top level walked",
              titles == ["Week 1", "Homework 3", "Course Syllabus"], str(titles))
        week1 = tree["nodes"][0]
        check("folders are opened, not just listed", len(week1["children"]) == 3,
              str(week1["children"]))
        kids = [n["title"] for n in week1["children"]]
        check("children come back in the instructor's order",
              kids == ["Readings", "Lecture slides", "Course website"], str(kids))
        readings = week1["children"][0]
        check("nesting goes deeper than one level",
              [n["title"] for n in readings["children"]] == ["Chapter 1"],
              str(readings))
        chapter = readings["children"][0]
        check("a document's body is plain text",
              "Read pages 1" in chapter["body"] and "<p>" not in chapter["body"],
              chapter["body"])
        # The page renders this, so a link has to arrive as something it can
        # draw an anchor from — not as a URL dumped in front of its own words.
        check("a link in the body keeps the words the author wrote",
              "[Bison Advise - Your Advising Resource]"
              "(https://career-advising.ndsu.edu/bisonadvise/)" in chapter["body"],
              chapter["body"])
        check("and the bare url is not left sitting in the sentence",
              "] Bison Advise" not in chapter["body"], chapter["body"])
        slides = week1["children"][1]
        check("a classic file item names its file without a second request",
              [f["filename"] for f in slides["files"]] == ["week1.pdf"],
              str(slides["files"]))
        link = week1["children"][2]
        check("an external link carries its target",
              link["url"] == "https://example.edu/algorithms", str(link))
        check("assignments are flagged",
              tree["nodes"][1]["is_assignment"], str(tree["nodes"][1]))
        check("counts describe the tree",
              tree["counts"] == {"folders": 2, "assignments": 1, "files": 2,
                                 "links": 1, "items": 5},
              str(tree["counts"]))

        # Ultra names a document's content item after its own internals. The
        # folder around it carries the name a student would recognise, so the
        # two are one row on the page rather than a click and a puzzle.
        syllabus = tree["nodes"][2]
        check("an Ultra wrapper folder is folded into the document it wraps",
              not syllabus["is_folder"] and syllabus["children"] == [],
              str(syllabus))
        check("the folder's name is the one that survives",
              syllabus["title"] == "Course Syllabus", syllabus["title"])
        check("\"ultraDocumentBody\" never reaches the page",
              "ultraDocumentBody" not in json.dumps(tree["nodes"]))
        check("the document's own id survives, so its files can still be fetched",
              syllabus["content_id"] == stub.DOC_ID, syllabus["content_id"])
        check("and its files come with it",
              [f["filename"] for f in syllabus["files"]]
              == ["Fall_2026_Syllabus.docx"], str(syllabus["files"]))
        check("as does its text",
              "Course Syllabus" in syllabus["body"], syllabus["body"][:120])
        # The file is already a download button on that row, and its URL is
        # signed and expiring, so it is not also a link in the prose.
        check("its handout is not repeated as a link in the text",
              "bbcswebdav" not in syllabus["body"]
              and "](" not in syllabus["body"], syllabus["body"][:160])
        check("nothing was truncated", tree["truncated"] is False, str(tree))
        check("first read is not from cache", tree["cached"] is False)
        again = await _sync.course_content(cache, "_11_1")
        check("a second read is served from the cache", again["cached"] is True)
        check("and is rendered the same way as the first",
              again["nodes"][0]["children"][0]["children"][0]["body"]
              == chapter["body"])
        check("the course's markup never reaches the page",
              "body_html" not in chapter and "<p>" not in json.dumps(again["nodes"]),
              str(sorted(chapter.keys())))

        # The cache holds what Blackboard sent, not what the page drew from it,
        # so a change to how course text is presented lands on the next read
        # rather than six hours later.
        stored = cache.get_data("content__11_1")
        held = stored["nodes"][0]["children"][0]["children"][0]
        check("the markup is what was kept", "<p>" in (held.get("body_html") or ""),
              str(sorted(held.keys())))
        cache.write("content__11_1", {**stored, "nodes": [{
            "content_id": "_x_1", "title": "Legacy", "type": "document",
            "is_folder": False, "is_assignment": False, "files": [],
            "body": "written before the markup was kept", "children": [],
        }]})
        legacy = await _sync.course_content(cache, "_11_1")
        check("an entry cached before that still renders its stored text",
              legacy["nodes"][0]["body"] == "written before the markup was kept",
              str(legacy["nodes"]))

    print("\n[web] downloading a handout straight from a materials row")
    from blackboard_web import app as _webapp
    from fastapi.testclient import TestClient as _TestClient
    # "Lecture slides" is a plain file item deep in the tree, with no gradebook
    # column and no due date — exactly the kind of thing the old page could only
    # name. Its file has to come back like any assignment's.
    got = await _webapp.download_assignment_files_route("_11_1", "_401_1")
    rows = got.get("downloaded", [])
    check("the file is fetched", [r["filename"] for r in rows] == ["week1.pdf"],
          str(got))
    check("and offered back through a url the page can use",
          rows and rows[0].get("url", "").startswith("/api/files/"), str(rows))
    served = _TestClient(_webapp.app).get(rows[0]["url"])
    check("that url serves the bytes Blackboard had",
          served.status_code == 200 and served.content == stub.FILES["_a4_1"],
          f"{served.status_code} {served.content[:60]!r}")
    check("as an attachment rather than something to render",
          "attachment" in served.headers.get("content-disposition", ""),
          served.headers.get("content-disposition", ""))

    print("\n[failure modes] expired session")
    stub.State.expired = True
    r = await S.check_connection()
    check("expired session reported, not crashed", r.get("connected") is False, str(r))
    check("diagnosed as auth", r.get("problem") == "auth", str(r))
    check("fix instructions included", "DevTools" in r.get("fix", ""), str(r))
    stub.State.expired = False

    os.environ["BB_COOKIE"] = "BbRouter=bogus"
    stub.State.require_cookie = True
    r = await S.check_connection()
    check("401 surfaces as auth problem",
          r.get("connected") is False and r.get("problem") == "auth", str(r))
    os.environ["BB_COOKIE"] = "BbRouter=expires:1234,id:abcd,signature:xyz"

    os.environ["BB_HOST"] = ""
    r = await S.check_connection()
    check("missing host reported clearly", r.get("connected") is False, str(r))
    os.environ["BB_HOST"] = BASE

    print("\n[security] off-origin download redirect")
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen: dict[str, str | None] = {}

    class Storage(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            seen["cookie"] = self.headers.get("Cookie")
            body = b"signed-storage-bytes"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    store = ThreadingHTTPServer(("127.0.0.1", 0), Storage)
    threading.Thread(target=store.serve_forever, daemon=True).start()
    # localhost vs 127.0.0.1 is an origin change to httpx, which is the condition
    # under test, without needing real DNS.
    stub.State.external_storage_url = f"http://localhost:{store.server_port}/signed/abc"
    stub.ATTACHMENTS["_501_1"].append(
        {"id": "_a3_1", "fileName": "remote.bin", "mimeType": "application/octet-stream"})
    from blackboard_mcp.client import BlackboardClient
    async with BlackboardClient(BASE, os.environ["BB_COOKIE"]) as bb:
        data, _ = await bb.download_attachment("_11_1", "_501_1", "_a3_1")
    check("off-origin redirect followed", data == b"signed-storage-bytes", str(data))
    check("session cookie NOT forwarded off-origin", not seen.get("cookie"),
          f"storage host saw: {seen.get('cookie')!r}")
    store.shutdown()
    stub.State.external_storage_url = None
    stub.ATTACHMENTS["_501_1"] = [a for a in stub.ATTACHMENTS["_501_1"] if a["id"] != "_a3_1"]

    print("\n[session] cookie auto-renewal")
    from blackboard_mcp.session import (
        SessionStore, best_cookie, cookie_expiry, parse_bbrouter, seconds_remaining)

    real = ("expires:1788157763,id:3E117DB9270E92714FCBE7A8196AB8FF,timeout:10800,"
            "v:2,xsrf:cb7e7b32-3bda-4f17-a9a7-bf1c8f2af7e9")
    fields = parse_bbrouter(real)
    check("BbRouter fields parsed", fields["id"] == "3E117DB9270E92714FCBE7A8196AB8FF"
          and fields["timeout"] == "10800", str(fields))
    check("expiry decoded", cookie_expiry(real).year == 2026, str(cookie_expiry(real)))
    check("prefixed form parses the same",
          parse_bbrouter(f"BbRouter={real}") == fields)
    check("no-expires cookie yields None", seconds_remaining("id:abc") is None)

    stub.State.roll_cookie = True
    S.STORE = SessionStore(Path(SESSION_FILE))
    before = S.STORE.load()
    r = await S.check_connection()
    after = S.STORE.load()
    check("renewal persisted after a tool call", after is not None and after != before,
          f"{before!r} -> {after!r}")
    check("stored cookie is the reissued one", "ROLLED" in (after or ""), str(after))
    left = seconds_remaining(after or "")
    check("renewed cookie has ~3h left", left and 2.5 * 3600 < left < 3.1 * 3600, str(left))
    check("stored file is user-only readable",
          oct(Path(SESSION_FILE).stat().st_mode)[-3:] == "600",
          oct(Path(SESSION_FILE).stat().st_mode)[-3:])

    S.STORE.save(S.STORE.load() or "", BASE)  # tag the stored cookie with its host
    other = best_cookie(None, S.STORE, "https://someone-elses-school.edu")
    check("stored cookie is NOT replayed against a different host",
          other[0] is None, str(other))
    check("stored cookie still used for its own host",
          best_cookie(None, S.STORE, BASE)[0] is not None)

    chosen, source = best_cookie(os.environ["BB_COOKIE"], S.STORE, BASE)
    check("fresher stored cookie beats stale .env seed", "ROLLED" in chosen, source)
    future = "BbRouter=expires:4102444800,id:FROMENV"
    chosen2, _ = best_cookie(future, S.STORE, BASE)
    check("freshly pasted .env cookie beats stored", "FROMENV" in chosen2, chosen2[:40])

    st = await S.session_status()
    check("session_status reports alive", st["configured"] and not st["expired"], str(st))
    check("session_status shows hours left",
          2.5 < st["hours_remaining"] < 3.1, str(st["hours_remaining"]))
    check("session_status names the source", "session file" in st["cookie_source"], str(st))
    check("session_status reports keepalive on", "on, every" in st["keepalive"], str(st))
    stub.State.roll_cookie = False

    print("\n[mcp] tool registration")
    tools = await S.mcp.list_tools()
    names = sorted(t.name for t in tools)
    expected = sorted(["check_connection", "list_courses", "list_due_dates",
                       "get_assignment", "download_assignment_files",
                       "browse_course_content", "list_announcements",
                       "session_status"])
    check("all 8 tools registered", names == expected, str(names))
    dd = next(t for t in tools if t.name == "list_due_dates")
    props = dd.input_schema.get("properties", {})
    check("list_due_dates schema has documented params",
          {"days_ahead", "course_id", "include_submitted"} <= set(props), str(props.keys()))
    check("tool descriptions populated", all(t.description for t in tools))

    print("\n[course names]")
    _NS = type("C", (), {})


    def _course(name: str, code: str = "SIS-1-2-3"):
        c = _NS()
        c.id, c.course_id, c.name, c.term = "_1_1", code, name, None
        return c


    check("an ampersand gets air around it",
          S.format_course_name("Network&Parallel") == "Network & Parallel")
    check("underscores are word gaps",
          S.format_course_name("Intro_to_AI") == "Intro to AI")
    check("runs of whitespace collapse",
          S.format_course_name("  A   B  ") == "A B")
    check("a name that is already clean is untouched",
          S.format_course_name("Cloud Computing") == "Cloud Computing")
    check("no name at all is an empty string, not None",
          S.format_course_name(None) == "")

    check("the real offender formats",
          S._course_label(_course("Network&Parallel_Computation"))
          == "Network & Parallel Computation")
    check("a catalogue code in the title still wins",
          S._course_label(_course("CSCI 450: Cloud Computing - 33093 - F26")) == "CSCI 450")
    check("the title drops the code, section and term",
          S._course_title(_course("CSCI 450: Cloud Computing - 33093 - F26")) == "Cloud Computing")
    check("a dash-separated title works the same",
          S._course_title(_course("CSCI 413 - Principles of Software Engineering"))
          == "Principles of Software Engineering")
    check("a spelled-out term is stripped too",
          S._course_title(_course("INTRO_TO_AI - Spring 2027")) == "INTRO TO AI")
    check("a title with no code is just formatted",
          S._course_title(_course("Network&Parallel_Computation"))
          == "Network & Parallel Computation")
    check("stripping never eats the whole title",
          S._course_title(_course("F26")) == "F26")
    check("an empty name falls back to the institutional id",
          S._course_label(_course("", "NDSU1-2710-P-CSCI-32840")) == "NDSU1-2710-P-CSCI-32840")
    check("and with nothing at all it still returns something",
          S._course_label(_course("", "")) == "(untitled course)")

    print(f"\n{'=' * 62}")
    if FAILS:
        print(f"FAILED ({len(FAILS)}):")
        for f in FAILS:
            print(f"  - {f}")
    else:
        print("ALL CHECKS PASSED")
    print("=" * 62)


asyncio.run(main())
SRV.shutdown()
sys.exit(1 if FAILS else 0)
