"""A stub Blackboard Learn instance for testing, mimicking real API quirks:
paging, calculated gradebook columns, 302-to-storage downloads, and the
HTML-login-page-with-200 response an expired session produces.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

NOW = datetime.now(timezone.utc)
VALID_COOKIE = "BbRouter=expires:1234,id:abcd,signature:xyz"


def _bbtime(days: float) -> str:
    return (NOW + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


COURSES = [
    {"id": "_11_1", "courseId": "CS-340-001", "name": "Algorithms",
     "availability": {"available": "Yes"}, "term": {"name": "Fall 2026"}},
    {"id": "_22_1", "courseId": "ENG-210", "name": "Technical Writing",
     "availability": {"available": "Yes"}, "term": {"name": "Fall 2026"}},
    {"id": "_33_1", "courseId": "HIST-101", "name": "Ancient History",
     "availability": {"available": "No"}, "term": {"name": "Spring 2026"}},
    {"id": "_44_1", "courseId": "ORG-1", "name": "Student Union",
     "availability": {"available": "Yes"}, "organization": True},
]

COLUMNS = {
    "_11_1": [
        {"id": "_c1_1", "name": "Homework 3", "contentId": "_501_1",
         "score": {"possible": 100}, "grading": {"due": _bbtime(5), "type": "Attempts"}},
        {"id": "_c2_1", "name": "Quiz 1", "contentId": "_502_1",
         "score": {"possible": 50}, "grading": {"due": _bbtime(40), "type": "Attempts"}},
        {"id": "_c3_1", "name": "Lab 2", "contentId": "_503_1",
         "score": {"possible": 25}, "grading": {"due": _bbtime(2), "type": "Attempts"}},
        {"id": "_c4_1", "name": "Weighted Total",
         "scoreProviderHandle": "resource/x-bb-calculatedgrade",
         "score": {"possible": 100}, "grading": {}},
        {"id": "_c5_1", "name": "Participation", "score": {"possible": 10},
         "grading": {"type": "Manual"}},
    ],
    "_22_1": [
        {"id": "_c9_1", "name": "Essay Draft", "contentId": "_601_1",
         "score": {"possible": 40}, "grading": {"due": _bbtime(10), "type": "Attempts"}},
    ],
}

# Lab 2 is already graded; Homework 3 and Essay Draft are outstanding.
ATTEMPTS = {
    ("_11_1", "_c3_1"): {"status": "Graded", "score": 24.0},
    ("_11_1", "_c1_1"): {"status": "NotAttempted", "score": None},
    ("_22_1", "_c9_1"): {"status": "NotAttempted", "score": None},
}

CONTENT = {
    "_501_1": {"id": "_501_1", "title": "Homework 3",
               "body": "<div><p>Implement <b>Dijkstra&#39;s</b> algorithm.</p>"
                       "<ul><li>Part A: adjacency list</li><li>Part B: priority queue</li></ul>"
                       "<script>tracking()</script>"
                       "<a href='https://example.edu/spec.pdf'>spec</a></div>",
               "created": _bbtime(-14), "modified": _bbtime(-2)},
    "_601_1": {"id": "_601_1", "title": "Essay Draft", "body": "<p>Three pages, MLA.</p>"},
}

ULTRA_ID = "_701_1"
CONTENT[ULTRA_ID] = {
    "id": ULTRA_ID, "title": "Ultra Assignment", "body": "",
    "contentHandler": {
        "id": "resource/x-bb-asmt-test-link",
        "assessmentId": "_900_1", "gradeColumnId": "_c9_1",
        "instructions": (
            "<p>Read the handout.</p>"
            "<a href=\"https://bb.test/bbcswebdav/pid-1/xid-1?sig=abc&amp;t=2\" "
            "data-bbtype=\"attachment\" "
            "data-bbfile=\"{&quot;fileName&quot;:&quot;Handout.docx&quot;,"
            "&quot;mimeType&quot;:&quot;application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document&quot;,&quot;fileSize&quot;:1234}\">Handout</a>"
        ),
    },
}

DOC_ID = "_702_1"
DOC_PARENT = "_703_1"
CONTENT[DOC_PARENT] = {"id": DOC_PARENT, "title": "Course Syllabus",
                       "contentHandler": {"id": "resource/x-bb-folder"}}
CONTENT[DOC_ID] = {
    "id": DOC_ID, "title": "ultraDocumentBody", "parentId": DOC_PARENT,
    "contentHandler": {"id": "resource/x-bb-document"},
    # No href and no data-bbtype: the URL lives in the data-bbfile JSON.
    "body": (
        "<p>[Insert Course Name] Course Syllabus</p>"
        "<a data-bbid=\"bbml-editor-id_807af\" data-bbfile=\"{"
        "&quot;linkName&quot;:&quot;Fall_2026_Syllabus.docx&quot;,"
        "&quot;displayName&quot;:&quot;Fall_2026_Syllabus.docx&quot;,"
        "&quot;mimeType&quot;:&quot;application/vnd.openxmlformats-officedocument"
        ".wordprocessingml.document&quot;,&quot;render&quot;:&quot;inline&quot;,"
        "&quot;resourceUrl&quot;:&quot;__BASE__/storage/_a1_1?sig=abc&amp;t=9&quot;,"
        "&quot;viewerUrl&quot;:&quot;__BASE__/storage/_a1_1?view=1&quot;}\"></a>"
    ),
}

ATTACHMENTS = {
    "_501_1": [
        {"id": "_a1_1", "fileName": "hw3.pdf", "mimeType": "application/pdf"},
        # A hostile filename: the server must not let this escape the download dir.
        {"id": "_a2_1", "fileName": "../../../../etc/passwd.txt", "mimeType": "text/plain"},
    ],
    "_601_1": [],
}

FILES = {"_a1_1": b"%PDF-1.4 fake homework sheet", "_a2_1": b"not actually passwd"}

TOP_CONTENT = {
    "_11_1": [
        {"id": "_400_1", "title": "Week 1", "contentHandler": {"id": "resource/x-bb-folder"}},
        {"id": "_501_1", "title": "Homework 3",
         "contentHandler": {"id": "resource/x-bb-assignment"}, "body": "<p>x</p>"},
        # Writing a page in Ultra produces exactly this: a folder carrying the
        # name, holding one child called "ultraDocumentBody".
        {"id": DOC_PARENT, "title": "Course Syllabus", "hasChildren": True,
         "contentHandler": {"id": "resource/x-bb-folder"}},
    ],
}

# Children of "Week 1", deliberately out of order so a caller has to honour
# `position` rather than the order the API happens to return.
CHILD_CONTENT = {
    "_400_1": [
        {"id": "_401_1", "title": "Lecture slides", "position": 1,
         "contentHandler": {"id": "resource/x-bb-file",
                            "file": {"fileName": "week1.pdf"}}},
        {"id": "_402_1", "title": "Readings", "position": 0, "hasChildren": True,
         "contentHandler": {"id": "resource/x-bb-folder"}},
        {"id": "_403_1", "title": "Course website", "position": 2,
         "contentHandler": {"id": "resource/x-bb-externallink",
                            "url": "https://example.edu/algorithms"}},
    ],
    DOC_PARENT: [CONTENT[DOC_ID]],
    "_402_1": [
        {"id": "_404_1", "title": "Chapter 1", "position": 0,
         "contentHandler": {"id": "resource/x-bb-document"},
         "body": ("<p>Read pages 1&ndash;40 before Tuesday.</p>"
                  "<p>Stuck? <a href=\"https://career-advising.ndsu.edu/bisonadvise/\">"
                  "Bison Advise - Your Advising Resource</a> can help.</p>")},
    ],
}

# A real instance serves any content id from /contents/{id}, wherever it sits in
# the tree, and a classic "file" item has an attachment record behind its name.
for _kids in CHILD_CONTENT.values():
    for _item in _kids:
        CONTENT.setdefault(_item["id"], _item)
ATTACHMENTS["_401_1"] = [
    {"id": "_a4_1", "fileName": "week1.pdf", "mimeType": "application/pdf"},
]
FILES["_a4_1"] = b"%PDF-1.4 fake lecture slides"

ANNOUNCEMENTS = {
    "_11_1": [{"id": "_an1", "title": "Deadline moved", "created": _bbtime(-1),
               "body": "<p>Homework 3 is now due Friday.</p>"}],
    "_22_1": [],
}


class State:
    """Lets a test flip the stub into failure modes."""
    expired = False
    require_cookie = True
    # When set, attachment _a3_1 redirects here instead of to local /storage.
    external_storage_url: str | None = None
    # When on, every response reissues BbRouter with a fresh expiry.
    roll_cookie = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep test output clean
        pass

    def _send(self, code: int, payload, ctype="application/json", headers=None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        if State.roll_cookie:
            fresh = int((NOW + timedelta(hours=3)).timestamp())
            self.send_header(
                "Set-Cookie",
                f"BbRouter=expires:{fresh},id:ROLLED,timeout:10800,v:2; Path=/")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        path, qs = url.path, parse_qs(url.query)

        cookie_hdr = self.headers.get("Cookie") or ""
        if State.roll_cookie:
            if "BbRouter=" not in cookie_hdr:
                return self._send(401, {"status": 401, "message": "no session"})
        elif State.require_cookie and VALID_COOKIE not in cookie_hdr:
            return self._send(401, {"status": 401, "message": "no session"})

        if State.expired:
            # Exactly what a real expired session does: 200 with the login page.
            return self._send(200, b"<html><body>Please log in</body></html>", "text/html")

        if path == "/learn/api/public/v1/users/me":
            return self._send(200, {"id": "_101_1", "userName": "ethanp",
                                    "name": {"given": "Ethan", "family": "Phillips"}})

        if m := re.fullmatch(r"/learn/api/public/v1/users/([^/]+)/courses", path):
            # Paged deliberately, one membership per page, to exercise nextPage.
            offset = int(qs.get("offset", ["0"])[0])
            memberships = [{"courseId": c["id"], "userId": "_101_1", "course": c}
                           for c in COURSES]
            page = memberships[offset:offset + 1]
            body = {"results": page}
            if offset + 1 < len(memberships):
                body["paging"] = {"nextPage": f"{path}?offset={offset + 1}&expand=course"}
            return self._send(200, body)

        if m := re.fullmatch(r"/learn/api/public/v2/courses/([^/]+)/gradebook/columns", path):
            return self._send(200, {"results": COLUMNS.get(m.group(1), [])})

        if m := re.fullmatch(
                r"/learn/api/public/v2/courses/([^/]+)/gradebook/users/([^/]+)", path):
            rows = [{"userId": m.group(2), "columnId": col,
                     "displayGrade": {"score": row["score"], "possible": 25.0},
                     "status": row["status"]}
                    for (cid, col), row in ATTEMPTS.items()
                    if cid == m.group(1) and row.get("score") is not None]
            return self._send(200, {"results": rows})

        if m := re.fullmatch(
                r"/learn/api/public/v2/courses/([^/]+)/gradebook/columns/([^/]+)/users/([^/]+)",
                path):
            row = ATTEMPTS.get((m.group(1), m.group(2)))
            if row is None:
                return self._send(404, {"status": 404})
            return self._send(200, row)

        if m := re.fullmatch(r"/learn/api/public/v1/courses/([^/]+)/contents", path):
            return self._send(200, {"results": TOP_CONTENT.get(m.group(1), [])})

        if m := re.fullmatch(
                r"/learn/api/public/v1/courses/([^/]+)/contents/([^/]+)/children", path):
            return self._send(200, {"results": CHILD_CONTENT.get(m.group(2), [])})

        if m := re.fullmatch(
                r"/learn/api/public/v1/courses/([^/]+)/contents/([^/]+)/attachments", path):
            if m.group(2) == ULTRA_ID:
                # Real Ultra items reject this endpoint outright.
                return self._send(400, {"status": 400, "message": "not supported"})
            return self._send(200, {"results": ATTACHMENTS.get(m.group(2), [])})

        if m := re.fullmatch(
                r"/learn/api/public/v1/courses/([^/]+)/contents/([^/]+)"
                r"/attachments/([^/]+)/download", path):
            aid = m.group(3)
            if aid == "_a3_1" and State.external_storage_url:
                return self._send(302, b"", "text/plain",
                                  {"Location": State.external_storage_url})
            if aid == "_a2_1":
                # Real Blackboard 302s to signed storage for some attachments.
                return self._send(302, b"", "text/plain",
                                  {"Location": f"/storage/{aid}"})
            return self._send(200, FILES[aid], "application/pdf",
                              {"Content-Disposition": 'attachment; filename="hw3.pdf"'})

        if m := re.fullmatch(r"/storage/([^/]+)", path):
            return self._send(200, FILES[m.group(1)], "text/plain")

        if m := re.fullmatch(r"/learn/api/public/v1/courses/([^/]+)/contents/([^/]+)", path):
            item = CONTENT.get(m.group(2))
            return self._send(200, item) if item else self._send(404, {"status": 404})

        if m := re.fullmatch(r"/learn/api/public/v1/courses/([^/]+)/announcements", path):
            return self._send(200, {"results": ANNOUNCEMENTS.get(m.group(1), [])})

        return self._send(404, {"status": 404, "message": f"no route {path}"})


def start() -> tuple[str, ThreadingHTTPServer]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}", srv
