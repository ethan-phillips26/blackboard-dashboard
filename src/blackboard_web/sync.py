"""Fetch from Blackboard, remember everything, and only ask again when stale.

Every read goes through the cache. A cold start pulls the full picture; after
that the dashboard reads from disk and re-fetches only the things that actually
go out of date (assignments, grades, announcements), while the expensive derived
knowledge — a syllabus's grade weighting — is computed once and kept.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from blackboard_mcp import paths
from blackboard_mcp.client import BlackboardClient, BlackboardError, ForbiddenError
from blackboard_mcp.server import (
    _GENERIC_TITLES, _client, _course_label, _course_title, _display_title,
    list_due_dates,
)
from blackboard_mcp.client import (
    content_instructions, extract_embedded_files, html_to_text,
    is_assignment_column,
    launch_url,
)

from .cache import Cache
from . import edits as E
from . import grades as G
from . import llm, textextract

SYLLABUS_HINTS = ("syllabus", "course info", "course information", "overview", "greensheet")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------- raw fetching


async def _course_bundle(bb: BlackboardClient, course_id: str, uid: str
                         ) -> dict[str, Any]:
    """Categories, columns and this user's grades for one course."""
    cats, cols, gr = await asyncio.gather(
        bb.gradebook_categories(course_id),
        bb.gradebook_columns(course_id),
        bb.gradebook_grades(course_id, uid),
        return_exceptions=True,
    )
    forbidden = isinstance(cols, BaseException)
    return {
        "categories": [] if isinstance(cats, BaseException) else cats,
        "columns": [] if forbidden else cols,
        "grades": {} if isinstance(gr, BaseException) else gr,
        "accessible": not forbidden,
        # Specifically a 403: the instructor has hidden the gradebook from
        # students. Any other failure — a timeout, a dropped connection — also
        # leaves no columns, but it is not an answer about the course, and a
        # course must not disappear from the dashboard because one request had
        # a bad afternoon.
        "hidden": isinstance(cols, ForbiddenError),
        "error": str(cols) if forbidden else None,
    }


async def refresh(cache: Cache, force: bool = False) -> dict[str, Any]:
    """Bring the cache up to date and report what changed."""
    stale = force or not cache.fresh("assignments", "assignments")
    if not stale and cache.read("courses"):
        return {"refreshed": False, "reason": "cache still fresh"}

    async with _client() as bb:
        uid = await bb.user_id()
        me = await bb.me()
        courses = await bb.courses(current_only=True)
        cache.write("me", {"id": uid, "name": me.get("name"),
                           "username": me.get("userName")})

        bundles = await asyncio.gather(
            *(_course_bundle(bb, c.id, uid) for c in courses)
        )
        # A course whose gradebook the instructor hides from students is dropped
        # here rather than filtered on every screen: nothing downstream — the
        # sidebar, the tiles, the grade list, the settings screen, the colour
        # order — has to know the rule, because the course is simply not in the
        # list they all read.
        dropped = [_course_label(c) for c, b in zip(courses, bundles) if b["hidden"]]
        kept = [(c, b) for c, b in zip(courses, bundles) if not b["hidden"]]
        courses = [c for c, _b in kept]

        course_rows = [{
            "course_id": c.id, "code": c.course_id, "label": _course_label(c),
            "title": _course_title(c), "name": c.name, "term": c.term,
            # Blackboard's own address for the course, as Blackboard reports it.
            "web_url": c.external_url,
        } for c in courses]
        cache.write("courses", course_rows)
        for c, bundle in kept:
            cache.write(f"course_{c.id}", bundle)

        anns = await asyncio.gather(
            *(bb.announcements(c.id) for c in courses), return_exceptions=True
        )
        rows: list[dict[str, Any]] = []
        for c, res in zip(courses, anns):
            if isinstance(res, BaseException):
                continue
            for a in res:
                rows.append({
                    "id": a.get("id"), "course_id": c.id,
                    "course": _course_label(c), "title": a.get("title"),
                    "posted": a.get("created"),
                    "body": _plain(a.get("body")),
                })
        rows.sort(key=lambda r: r.get("posted") or "", reverse=True)
        cache.write("announcements", rows)

    due = await list_due_dates(days_ahead=60, days_back=3)
    cache.write("assignments", due)
    changes = _track_new(cache, due, rows)
    # Not rendered anywhere — the point is that these courses are gone — but a
    # course vanishing should be explicable, so the sync says which and why.
    return {"refreshed": True, "at": _now(),
            "hidden_gradebooks": dropped, **changes}


def _plain(html: str | None) -> str:
    # The dashboard renders this, so keep the anchors the author wrote rather
    # than the bare URLs an agent would want.
    return html_to_text(html, links="markdown")[:4000]


def _track_new(cache: Cache, due: dict[str, Any],
               announcements: list[dict[str, Any]]) -> dict[str, Any]:
    """Record first-seen times so the UI can mark things that are actually new."""
    seen = cache.get_data("seen", {}) or {}
    assignments_seen = seen.get("assignments", {})
    ann_seen = seen.get("announcements", {})
    new_assignments, new_announcements = [], []

    for item in due.get("items", []):
        key = item.get("column_id") or item.get("content_id")
        if not key:
            continue
        if key not in assignments_seen:
            assignments_seen[key] = _now()
            new_assignments.append(item.get("title"))
    for a in announcements:
        key = a.get("id")
        if key and key not in ann_seen:
            ann_seen[key] = _now()
            new_announcements.append(a.get("title"))

    cache.write("seen", {"assignments": assignments_seen, "announcements": ann_seen})
    return {"new_assignments": new_assignments, "new_announcements": new_announcements}


# ------------------------------------------------------------------ syllabus


def _syllabus_score(filename: str, title: str, ancestor_matched: bool) -> int:
    """How much a candidate file looks like the course syllabus.

    The filename is the strongest signal — "Fall_2026_Syllabus_Information.docx"
    is unambiguous, while the item holding it is often called something generic
    like "ultraDocumentBody".
    """
    name = filename.lower()
    score = 0
    if any(h in name for h in SYLLABUS_HINTS):
        score += 3
    if any(h in (title or "").lower() for h in SYLLABUS_HINTS):
        score += 2
    if ancestor_matched:
        score += 1
    return score


async def find_syllabus(course_id: str, dest: Path, budget: int = 30
                        ) -> dict[str, Any] | None:
    """Find and download the course syllabus, or return None if there isn't one.

    Walks the course tree collecting every document-like attachment, scores each
    on how much it looks like a syllabus, and downloads only the best. Scoring
    beats first-match: the first document in a course is usually assignment one.
    """
    doc_suffixes = (".docx", ".pdf", ".doc", ".txt", ".rtf", ".odt")
    candidates: list[tuple[int, dict[str, Any]]] = []
    spent = 0

    async with _client() as bb:
        async def walk(parent: str | None, depth: int, ancestor_matched: bool) -> None:
            nonlocal spent
            if depth > 2 or spent >= budget:
                return
            spent += 1
            try:
                items = await bb.contents(course_id, parent)
            except BlackboardError:
                return
            for item in items:
                title = item.get("title") or ""
                matched = ancestor_matched or any(
                    h in title.lower() for h in SYLLABUS_HINTS)
                handler_obj = item.get("contentHandler") or {}

                # Ultra embeds files in the item's own HTML.
                for f in extract_embedded_files(content_instructions(item)):
                    if Path(f.filename).suffix.lower() not in doc_suffixes:
                        continue
                    score = _syllabus_score(f.filename, title, ancestor_matched)
                    if score > 0:
                        candidates.append((score, {
                            "kind": "inline", "url": f.url, "filename": f.filename,
                            "content_id": item.get("id"), "item_title": title,
                        }))

                # A plain "file" item instead exposes it through /attachments.
                if handler_obj.get("file") or handler_obj.get("id") == "resource/x-bb-file":
                    for a in await bb.attachments(course_id, item.get("id") or ""):
                        name = a.get("fileName") or ""
                        if Path(name).suffix.lower() not in doc_suffixes:
                            continue
                        score = _syllabus_score(name, title, ancestor_matched)
                        if score > 0:
                            candidates.append((score, {
                                "kind": "attachment", "attachment_id": a.get("id"),
                                "filename": name, "content_id": item.get("id"),
                                "item_title": title,
                            }))
                handler = str((item.get("contentHandler") or {}).get("id", ""))
                if item.get("hasChildren") or "folder" in handler or "lesson" in handler:
                    await walk(item.get("id"), depth + 1, matched)

        await walk(None, 0, False)
        if not candidates:
            return None
        score, best = max(candidates, key=lambda c: c[0])
        if best["kind"] == "attachment":
            data, _ = await bb.download_attachment(
                course_id, best["content_id"], best["attachment_id"])
        else:
            data, _ = await bb.download_url(best["url"])
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / best["filename"]
        path.write_bytes(data)
        return {"path": str(path), "filename": best["filename"],
                "content_id": best["content_id"], "title": best["item_title"],
                "match_score": score, "considered": len(candidates)}


# ------------------------------------------------------------ course content

# Blackboard names every kind of content item with a `contentHandler` id. Only
# the distinctions a student cares about are kept: is it a folder to open, a
# thing to hand in, or something to read.
FOLDER_TYPES = ("folder", "lesson")
ASSIGNMENT_TYPES = ("assignment", "asmt-test-link")

# How deep to open folders, and the hard ceiling on requests for one course.
# A course tree is a handful of weeks each holding a handful of items; a budget
# stops a pathologically nested course from turning one page load into hundreds
# of round trips.
CONTENT_DEPTH = 4
CONTENT_BUDGET = 80
# The shape of a cached content tree. Bump it when a row grows a field, so a
# tree cached under the old shape is re-walked instead of served incomplete.
TREE_SCHEMA = 4

# Enough of a document to read on the page. The whole thing is still one click
# away in the item's own drawer. The markup is kept a little longer than the text
# it renders down to, since tags cost characters that never reach the reader.
BODY_CHARS = 8000
BODY_HTML_CHARS = 24000


def _node(item: dict[str, Any], origin: str = "") -> dict[str, Any]:
    """One row of the course tree, described by what a student can do with it."""
    handler = item.get("contentHandler") or {}
    kind = str(handler.get("id") or "").replace("resource/x-bb-", "") or "item"
    body = content_instructions(item)

    # Ultra carries its files as links inside the item's own HTML; a classic
    # "file" item names its one file on the content handler. Both are free —
    # neither costs another request — so the row can list its files before
    # anyone opens it.
    files = [{"filename": f.filename, "bytes": f.size,
              "mime_type": f.mime_type, "source": "inline"}
             for f in extract_embedded_files(body)]
    attached = handler.get("file")
    if isinstance(attached, dict) and attached.get("fileName"):
        files.append({"filename": attached["fileName"], "bytes": None,
                      "mime_type": None, "source": "attachment"})

    return {
        "content_id": item.get("id"),
        "title": (item.get("title") or "").strip() or "(untitled)",
        "type": kind,
        "is_folder": kind in FOLDER_TYPES or bool(item.get("hasChildren")),
        "is_assignment": kind in ASSIGNMENT_TYPES,
        # The markup is what gets stored; see `_rendered` for why.
        "body_html": body[:BODY_HTML_CHARS],
        # An external link's target is the whole point of the item. An LTI item
        # also carries a URL — its tool's launch endpoint — but that one is not
        # a place a person can go: the tool only answers a signed handoff that
        # Blackboard performs, so following it directly lands on an error. Those
        # items get an "open in Blackboard" link instead, which is the way in.
        "url": handler.get("url") if kind == "externallink" else None,
        "target_id": handler.get("targetId"),
        # What ties a row here to a gradebook column, and so to a score.
        "grade_column_id": handler.get("gradeColumnId"),
        "files": files,
        "position": item.get("position"),
        "modified": item.get("modified"),
        "created": item.get("created"),
        # Blackboard's own link to this row, for reading it over there instead.
        "web_url": launch_url(item, origin) if origin else None,
        "children": [],
    }


def _summarise_tree(nodes: list[dict[str, Any]]) -> dict[str, int]:
    """How much is in a tree. Read every field defensively: this now runs over
    whatever is in the cache, which may have been written by a version that did
    not record all of these."""
    counts = {"folders": 0, "assignments": 0, "files": 0, "links": 0, "items": 0}

    def walk(rows: list[dict[str, Any]]) -> None:
        for n in rows:
            if n.get("is_folder"):
                counts["folders"] += 1
            else:
                counts["items"] += 1
                if n.get("is_assignment"):
                    counts["assignments"] += 1
                if n.get("url"):
                    counts["links"] += 1
            counts["files"] += len(n.get("files") or [])
            walk(n.get("children") or [])

    walk(nodes)
    return counts


# The two shapes an anchor can arrive in: the bare URL the agent tools produce,
# and the markdown link the dashboard renders.
_BRACKET_URL = re.compile(r"\[\s*https?://[^\]\s]+\s*\]")
_MD_LINK = re.compile(r"\[((?:[^\]\\]|\\.)*)\]\(\s*(https?://[^)\s]+)\s*\)")
_URLISH = re.compile(r"^\s*https?://")

# Blackboard serves course files from bbcswebdav, under a signed, expiring query
# string. Those URLs are not links worth keeping: they go stale, and whatever
# they point at is already offered as a download.
_FILE_URL_MARKS = ("/bbcswebdav/", "/xid-")


def _is_attachment_link(label: str, url: str, names: set[str]) -> bool:
    return (any(mark in url for mark in _FILE_URL_MARKS)
            or label.strip().lower() in names)


def strip_attachment_links(text: str | None, filenames: Any = ()) -> str:
    """Take the file links out of course text that already offers the files.

    Ultra puts an item's handouts in its body as anchors, so the text renders
    the same file the page is already showing a download button for — and often
    as a bare signed URL, because Ultra leaves the anchor's text empty. Neither
    is worth a link: the signature expires, while the download button re-fetches
    through the session.

    A line that was nothing but such an anchor goes entirely. One with prose
    around it keeps the prose and loses only the link. Links the instructor
    actually wrote — a reading, a video — are left exactly as they are.
    """
    names = {n.strip().lower() for n in filenames if n}

    def unlink(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2)
        if not _is_attachment_link(label, url, names):
            return match.group(0)
        # An anchor Ultra left empty carries its own URL as the label; showing
        # that as text is no better than showing it as a link.
        return "" if _URLISH.match(label) else label

    out = []
    for line in (text or "").splitlines():
        # A line is only an attachment anchor if nothing but the file's name is
        # left once every link is removed. Prose that happens to contain one
        # keeps the prose.
        bare = _BRACKET_URL.sub("", _MD_LINK.sub("", line)).strip()
        out.append("" if not bare or bare.lower() in names
                   else _MD_LINK.sub(unlink, line).rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _generic(title: str | None) -> bool:
    return (title or "").strip() in _GENERIC_TITLES


def _folded(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge Ultra's wrapper folders into the documents they wrap.

    Writing a page in Ultra produces a folder carrying the name the instructor
    typed — "Slides Week 1" — holding exactly one child titled
    "ultraDocumentBody", which is where the text and the files actually live.
    Drawn faithfully that is a folder you must open to reach a row named after
    Blackboard's internals. It is not really a folder at all, so the name and
    the content are put back together as one row.

    The child's id is what survives, because that is what the rest of the app
    fetches a body and its handouts with; the folder's is only a wrapper.
    """
    out = []
    for node in nodes:
        children = _folded(node.get("children") or [])
        only = children[0] if len(children) == 1 else None
        if (node.get("is_folder") and only and not only.get("is_folder")
                and _generic(only.get("title"))):
            out.append({
                **only,
                "title": node.get("title") or only.get("title"),
                "position": node.get("position"),
                # A folder can carry a description of its own. Keeping it in
                # front of the document's own text loses nothing.
                "body_html": (node.get("body_html") or "") + (only.get("body_html") or ""),
            })
            continue
        out.append({**node, "children": children})
    return out


def _named(node: dict[str, Any]) -> str:
    """A title worth showing for a document Blackboard never named.

    Almost every one of these is folded into its parent above. What is left is a
    stray — a wrapper with siblings, or one whose folder is unnamed too — and
    the page still cannot show "ultraDocumentBody". Its own first line is what
    the reader would have called it anyway, since that is the heading at the top
    of the document.
    """
    title = (node.get("title") or "").strip()
    if not _generic(title):
        return title
    first = next((ln.strip(" -\t") for ln in (node.get("body") or "").splitlines()
                  if ln.strip(" -\t")), "")
    if 0 < len(first) <= 80:
        return first
    files = node.get("files") or []
    if len(files) == 1 and files[0].get("filename"):
        return Path(files[0]["filename"]).stem
    return "Document"


def _rendered(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten each body for the page, on the way out rather than on the way in.

    The cache keeps the markup Blackboard sent, so changing how course text is
    presented — an anchor becoming a real link, say — takes effect on the next
    page load instead of waiting six hours for the tree to go stale. The markup
    itself never leaves the server: it is course-authored, and the page renders
    the text.

    A node cached before this existed has no markup to render, so its stored
    text is passed through as it is.
    """
    out = []
    for node in nodes:
        markup = node.get("body_html")
        rendered = {k: v for k, v in node.items() if k != "body_html"}
        if markup is not None:
            rendered["body"] = strip_attachment_links(
                html_to_text(markup, links="markdown"),
                [f.get("filename") for f in node.get("files") or []],
            )[:BODY_CHARS]
        rendered.setdefault("body", "")
        # After the body, because a stray wrapper is named from its own text.
        rendered["title"] = _named(rendered)
        rendered["children"] = _rendered(node.get("children") or [])
        out.append(rendered)
    return out


def _presented(nodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]],
                                                     dict[str, int]]:
    """The tree as the page should see it, with counts that describe that tree.

    Everything here is derived on the way out, so a cache written before any of
    these rules existed still comes back with them applied.
    """
    folded = _folded(nodes)
    return _rendered(folded), _summarise_tree(folded)


async def course_content(cache: Cache, course_id: str, force: bool = False
                         ) -> dict[str, Any]:
    """The course's whole content tree — folders, documents, links, assignments.

    This is what Blackboard draws as the course menu, fetched once and kept. It
    is the half of a course that the gradebook knows nothing about: the lecture
    slides, the readings, the instructions for work that has no grade column
    yet.
    """
    key = f"content_{course_id}"
    stored = cache.get_data(key) or {}
    # Bumped whenever a row grows a field: a tree cached under an older shape is
    # re-walked rather than served without it for the rest of its six hours.
    if not force and cache.fresh(key, "content") and stored.get("schema") == TREE_SCHEMA:
        nodes, counts = _presented(stored.get("nodes") or [])
        return {**stored, "nodes": nodes, "counts": counts, "cached": True}

    spent = 0
    truncated = False

    async with _client() as bb:
        async def children(parent: str | None) -> list[dict[str, Any]]:
            nonlocal spent, truncated
            if spent >= CONTENT_BUDGET:
                truncated = True
                return []
            spent += 1
            try:
                return await bb.contents(course_id, parent)
            except ForbiddenError:
                return []
            except BlackboardError:
                return []

        async def level(parent: str | None, depth: int) -> list[dict[str, Any]]:
            nodes = [_node(item, bb.origin) for item in await children(parent)]
            # Blackboard hands rows back in menu order, but not always; the
            # position it stores is what the instructor actually arranged.
            nodes.sort(key=lambda n: (n["position"] if isinstance(n["position"], int)
                                      else 10**6))
            openable = [n for n in nodes
                        if n["is_folder"] and n["content_id"] and depth < CONTENT_DEPTH]
            if openable:
                fetched = await asyncio.gather(
                    *(level(n["content_id"], depth + 1) for n in openable)
                )
                for node, kids in zip(openable, fetched):
                    node["children"] = kids
            return nodes

        try:
            tree = await level(None, 0)
        except ForbiddenError:
            entry = {"course_id": course_id, "accessible": False,
                     "reason": "This course's content is not visible to students.",
                     "nodes": [], "counts": {}, "fetched_at": _now(),
                     "schema": TREE_SCHEMA}
            cache.write(key, entry)
            return {**entry, "cached": False}

    entry = {
        "course_id": course_id,
        "accessible": True,
        "nodes": tree,
        "truncated": truncated,
        "fetched_at": _now(),
        "schema": TREE_SCHEMA,
    }
    cache.write(key, entry)
    nodes, counts = _presented(tree)
    return {**entry, "nodes": nodes, "counts": counts, "cached": False}


WEIGHT_SCHEMA = {
    "type": "object",
    "properties": {
        "categories": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "syllabus_label": {"type": "string"},
                "blackboard_category": {"type": ["string", "null"]},
                "weight_pct": {"type": "number"},
            },
            "required": ["syllabus_label", "blackboard_category", "weight_pct"],
        }},
        "letter_scale": {"type": "object", "additionalProperties": {"type": "number"}},
        "late_policy": {"type": ["string", "null"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "notes": {"type": "string"},
    },
    "required": ["categories", "letter_scale", "late_policy", "confidence", "notes"],
}

WEIGHT_INSTR = """You are reading a university course syllabus to find how the final grade is weighted.

Return the grade weighting as JSON.

Rules:
- `categories`: one entry per weighted component. weight_pct is out of 100, and they should sum to 100 when the syllabus is complete.
- `blackboard_category`: match each component to EXACTLY ONE `title` from the BLACKBOARD CATEGORIES below, copied verbatim, or null when none is a reasonable match. Match on the ACTUAL COLUMNS each category contains, not on its name — a category called "Test" holding "Quiz 1".."Quiz 8" is where the syllabus's quizzes live, and an empty category with a matching name is the wrong answer. Never map two components to the same category; leave the weaker match null.
- `letter_scale`: letter -> minimum percentage. Empty object if not stated.
- `late_policy`: one short sentence, or null.
- `confidence`: "high" only when weights are stated explicitly as percentages.
- `notes`: brief; what a student should know that the numbers miss.
Never invent a weight that is not in the document."""


def _populated_counts(bundle: dict[str, Any]) -> dict[str, int]:
    """How many real graded columns sit in each category."""
    from blackboard_mcp.client import is_assignment_column
    counts: dict[str, int] = {}
    for c in bundle.get("columns", []):
        if is_assignment_column(c) and c.get("gradebookCategoryId"):
            counts[c["gradebookCategoryId"]] = counts.get(c["gradebookCategoryId"], 0) + 1
    return counts


def _category_digest(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Describe each category by what it actually holds.

    Category names alone are misleading — an instructor's quizzes routinely sit in
    a category called "Test" while a "Quiz" category sits empty. Showing the model
    the real column names stops it matching on the label and silently dropping a
    chunk of the grade.
    """
    from blackboard_mcp.client import is_assignment_column
    columns = [c for c in bundle.get("columns", []) if is_assignment_column(c)]
    out = []
    for cat in bundle.get("categories", []):
        names = [c.get("name") for c in columns
                 if c.get("gradebookCategoryId") == cat.get("id")]
        out.append({
            "title": cat.get("title"),
            "column_count": len(names),
            "columns": names[:12],
            "points_possible": round(sum(
                float((c.get("score") or {}).get("possible") or 0)
                for c in columns
                if c.get("gradebookCategoryId") == cat.get("id")), 1),
        })
    return out


def resolve_weights(extracted: dict[str, Any], categories: list[dict[str, Any]],
                    overrides: dict[str, str] | None = None,
                    populated: dict[str, int] | None = None) -> dict[str, Any]:
    """Turn syllabus labels into {category_id: fraction}.

    `overrides` maps a syllabus label to a Blackboard category id, which is how a
    student fixes a component the model could not place (a "Projects" bucket with
    no matching category, say).
    """
    overrides = overrides or {}
    by_title = {(c.get("title") or "").strip().lower(): c.get("id")
                for c in categories}
    mapping: dict[str, float] = {}
    unmapped: list[dict[str, Any]] = []
    for row in extracted.get("categories", []):
        label = row.get("syllabus_label") or ""
        pct = float(row.get("weight_pct") or 0.0)
        cat_id = overrides.get(label)
        if not cat_id:
            title = (row.get("blackboard_category") or "").strip().lower()
            cat_id = by_title.get(title)
        if cat_id:
            mapping[cat_id] = mapping.get(cat_id, 0.0) + pct / 100.0
        else:
            unmapped.append({"syllabus_label": label, "weight_pct": pct})
    suspect = []
    if populated is not None:
        for row in extracted.get("categories", []):
            label = row.get("syllabus_label") or ""
            cat_id = overrides.get(label) or by_title.get(
                (row.get("blackboard_category") or "").strip().lower())
            if cat_id and not populated.get(cat_id):
                suspect.append({
                    "syllabus_label": label,
                    "weight_pct": row.get("weight_pct"),
                    "category_id": cat_id,
                    "reason": "mapped to a category that contains no graded columns",
                })
    return {
        "mapping": mapping,
        "unmapped": unmapped,
        "suspect": suspect,
        "mapped_pct": round(100.0 * sum(mapping.values()), 1),
    }


# ------------------------------------------------- inferring an item's category

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "n": {"type": "integer"},
                "syllabus_label": {"type": ["string", "null"]},
            },
            "required": ["n", "syllabus_label"],
        }},
    },
    "required": ["items"],
}

CLASSIFY_INSTR = """You are sorting a university gradebook's items into the weighted components of its syllabus.

You are given COMPONENTS (the syllabus's weighted parts) and ITEMS (the gradebook's rows, each with a number `n`, a name, and its points).

For every item, return the one component it belongs to, judged ONLY from the item's own name and points.

Rules:
- Return exactly one entry per item, using the item's `n`.
- `syllabus_label` must be copied verbatim from a COMPONENTS label, or be null.
- Judge by what the NAME says the item is. "Quiz 3" is a quiz, "Lab 1" is a lab, "Exam 2" is an exam, "Reading Response 4" is a reading response — regardless of what anyone filed it under.
- Points are a hint: a 200-point item among 20-point ones is likelier a project or final than a homework.
- Use null only when no component plausibly covers the item. Do not force a bad fit, and do not invent a component that is not listed.
- A component may take many items, or none."""


def _gradeable_columns(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """The gradebook rows that can actually move the grade, in a stable order.

    A zero-point row — a survey, an attendance placeholder — carries no weight
    however it is filed, so there is nothing to gain from sorting it and no
    reason to report it as unsorted.
    """
    out = []
    for c in bundle.get("columns", []):
        if not is_assignment_column(c):
            continue
        try:
            possible = float((c.get("score") or {}).get("possible") or 0)
        except (TypeError, ValueError):
            possible = 0.0
        if possible > 0:
            out.append(c)
    return out


def _components(stored: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The syllabus's weighted parts, as extracted earlier."""
    rows = ((stored or {}).get("extracted") or {}).get("categories") or []
    return [r for r in rows if (r.get("syllabus_label") or "").strip()]


def syllabus_categories(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Synthetic categories, one per syllabus component.

    Weighting hangs off the syllabus, so once items are placed by name the
    syllabus's own components are the right buckets — Blackboard's categories
    stop being part of the calculation at all.
    """
    return [{"id": f"syl:{r['syllabus_label']}", "title": r["syllabus_label"]}
            for r in components]


def syllabus_weights(components: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in components:
        key = f"syl:{r['syllabus_label']}"
        out[key] = out.get(key, 0.0) + float(r.get("weight_pct") or 0.0) / 100.0
    return out


def classify_columns(cache: Cache, course_id: str, stored: dict[str, Any] | None,
                     bundle: dict[str, Any], force: bool = False) -> dict[str, Any]:
    """Place every gradebook item into a syllabus component, by its name.

    The category an instructor set on a column is routinely wrong — quizzes filed
    under "Test", half a course left "Uncategorised" — and it is the one input
    here that nobody checks. The item's name is the honest signal, so that is what
    gets read.
    """
    components = _components(stored)
    columns = _gradeable_columns(bundle)
    labels = {r["syllabus_label"] for r in components}
    if not components or not columns:
        return {"map": {}, "labels": sorted(labels), "unclassified": [],
                "status": "nothing_to_classify"}

    items = [{"n": i + 1, "name": c.get("name"),
              "points": (c.get("score") or {}).get("possible")}
             for i, c in enumerate(columns)]
    payload = json.dumps({
        "components": [{"label": r["syllabus_label"],
                        "weight_pct": r.get("weight_pct")} for r in components],
        "items": items,
    }, indent=2)

    result = llm.cached(cache, f"colcat_{course_id}", CLASSIFY_INSTR, payload,
                        CLASSIFY_SCHEMA, force=force)
    placed: dict[str, str] = {}
    for row in (result["data"] or {}).get("items", []):
        n = row.get("n")
        label = (row.get("syllabus_label") or "").strip()
        # A label the syllabus does not have is a hallucination, not a category.
        if not isinstance(n, int) or not (1 <= n <= len(columns)) or label not in labels:
            continue
        col_id = columns[n - 1].get("id")
        if col_id:
            placed[col_id] = f"syl:{label}"

    unclassified = [c.get("name") for c in columns if c.get("id") not in placed]
    entry = {
        "map": placed,
        "labels": sorted(labels),
        "unclassified": unclassified,
        "status": "ok",
        "classified_at": _now(),
    }
    cache.write(f"colmap_{course_id}", entry)
    return entry


async def get_weights(cache: Cache, course_id: str, force: bool = False
                      ) -> dict[str, Any]:
    """The stored grade weighting for a course, extracting it once if needed."""
    key = f"weights_{course_id}"
    stored = cache.get_data(key)
    overrides = (stored or {}).get("overrides", {})
    bundle = cache.get_data(f"course_{course_id}", {}) or {}
    categories = bundle.get("categories", [])

    if stored and not force:
        resolved = resolve_weights(stored.get("extracted", {}), categories, overrides,
                                   _populated_counts(bundle))
        return {**stored, **resolved, "cached": True}

    files_dir = paths.download_dir() / "syllabi" / course_id
    found = await find_syllabus(course_id, files_dir)
    if not found:
        entry = {"course_id": course_id, "status": "no_syllabus_found",
                 "extracted": {"categories": []}, "overrides": overrides}
        cache.write(key, entry)
        return {**entry, "mapping": {}, "unmapped": [], "mapped_pct": 0.0}

    text = textextract.extract(found["path"])
    if not text.strip():
        entry = {"course_id": course_id, "status": "syllabus_unreadable",
                 "syllabus": found, "extracted": {"categories": []},
                 "overrides": overrides}
        cache.write(key, entry)
        return {**entry, "mapping": {}, "unmapped": [], "mapped_pct": 0.0}

    data = (f"BLACKBOARD CATEGORIES: {json.dumps(_category_digest(bundle), indent=2)}"
            f"\n\n=== SYLLABUS ===\n{text[:llm.max_document_chars()]}")
    extracted = llm.run(WEIGHT_INSTR, data, WEIGHT_SCHEMA)
    entry = {
        "course_id": course_id, "status": "ok", "syllabus": found,
        "extracted": extracted, "overrides": overrides, "extracted_at": _now(),
    }
    cache.write(key, entry)
    resolved = resolve_weights(extracted, categories, overrides,
                               _populated_counts(bundle))
    # Weights are useless until the gradebook's rows are sorted into them, so do
    # both in the one pass the student asked for.
    try:
        classified = classify_columns(cache, course_id, entry, bundle, force=force)
    except llm.LLMError:
        classified = {"status": "classification_failed"}
    return {**entry, **resolved, "classified": classified, "cached": False}


# -------------------------------------------------------------- assembly


async def course_standing(cache: Cache, course_id: str) -> dict[str, Any]:
    bundle = cache.get_data(f"course_{course_id}", {}) or {}
    if not bundle.get("accessible", True):
        return {"course_id": course_id, "accessible": False,
                "reason": "This course's gradebook is hidden from students."}
    stored = cache.get_data(f"weights_{course_id}") or {}
    categories = bundle.get("categories", [])
    columns = _gradeable_columns(bundle)
    grades = bundle.get("grades", {})
    scale = (stored.get("extracted") or {}).get("letter_scale") or None

    components = _components(stored)
    # A weighting the student corrected by hand outranks the one read off the
    # syllabus. Only the percentages move: the labels are what the gradebook's
    # rows were sorted into, so renaming one would orphan everything under it.
    manual = E.weights_for(cache, course_id)
    if manual:
        components = [{**r, "weight_pct": manual.get(r["syllabus_label"],
                                                     r.get("weight_pct"))}
                      for r in components]
    colmap = cache.get_data(f"colmap_{course_id}") or {}
    placed = {cid: cat for cid, cat in (colmap.get("map") or {}).items()
              if any(c.get("id") == cid for c in columns)}

    extra: dict[str, Any] = {}
    if components and placed:
        # Items were sorted by what they are called, so the syllabus's own
        # components are the buckets and Blackboard's categories play no part.
        cats = syllabus_categories(components)
        breakdown = G.build_breakdown(cats, columns, grades,
                                      syllabus_weights(components), assign=placed)
        titles_with_items = {b["title"] for b in breakdown}
        extra = {
            "grouped_by": "inferred",
            "unclassified": [c.get("name") for c in columns
                             if c.get("id") not in placed],
            "empty_components": [r["syllabus_label"] for r in components
                                 if r["syllabus_label"] not in titles_with_items],
            "unmapped": [],
            "suspect": [],
            "mapped_pct": round(100.0 * sum(syllabus_weights(components).values()), 1),
        }
    else:
        # No syllabus, or the gradebook has not been sorted yet: fall back to the
        # categories the instructor set, weighted by points if nothing is known.
        resolved = resolve_weights(stored.get("extracted", {}), categories,
                                   stored.get("overrides", {}),
                                   _populated_counts(bundle))
        breakdown = G.build_breakdown(categories, columns, grades,
                                      resolved["mapping"])
        extra = {
            "grouped_by": "blackboard",
            "unclassified": [],
            "empty_components": [],
            "unmapped": resolved["unmapped"],
            "suspect": resolved.get("suspect", []),
            "mapped_pct": resolved["mapped_pct"],
        }

    return {
        "course_id": course_id, "accessible": True,
        **G.summarise(breakdown, scale),
        **extra,
        "syllabus": stored.get("syllabus"),
        "weights_edited": bool(manual),
        "components": [{"syllabus_label": r["syllabus_label"],
                        "weight_pct": r.get("weight_pct")} for r in components],
        "weight_status": stored.get("status", "not_extracted"),
        "late_policy": (stored.get("extracted") or {}).get("late_policy"),
        "available_categories": [{"id": c.get("id"), "title": c.get("title")}
                                 for c in categories],
    }
