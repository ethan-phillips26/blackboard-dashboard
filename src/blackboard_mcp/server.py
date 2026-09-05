"""MCP server exposing Blackboard coursework to an agent.

The intended pipeline is: list_due_dates -> pick something due soon ->
get_assignment for the instructions -> download_assignment_files for the
handout -> start working.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

from . import paths
from .session import SessionStore, best_cookie, cookie_expiry, seconds_remaining
from .client import (
    AuthError,
    ForbiddenError,
    BlackboardClient,
    BlackboardError,
    Course,
    gather_limited,
    content_instructions,
    extract_embedded_files,
    html_to_text,
    is_assignment_column,
    parse_bb_time,
    launch_url,
)

load_dotenv()

log = logging.getLogger("blackboard_mcp")
STORE = SessionStore()


def _client() -> BlackboardClient:
    host = os.environ.get("BB_HOST", "")
    cookie, _source = best_cookie(os.environ.get("BB_COOKIE"), STORE, host)
    return BlackboardClient(
        host=host,
        cookie=cookie or "",
        # Blackboard reissues the session cookie as you use it; save each new one
        # (tagged with its host) so the session stays alive instead of dying at
        # the seed's expiry.
        on_cookie_refresh=lambda c: STORE.save(c, host),
    )


def _keepalive_minutes() -> float:
    try:
        return float(os.environ.get("BB_KEEPALIVE_MINUTES", "45"))
    except ValueError:
        return 45.0


async def _keepalive_loop(minutes: float) -> None:
    """Touch Blackboard periodically so the idle timeout never elapses.

    One cheap request well inside the idle window, which is the same thing a
    parked browser tab does. Any failure is ignored — if the session has really
    expired, the next real tool call reports it properly.
    """
    while True:
        await asyncio.sleep(minutes * 60)
        try:
            async with _client() as bb:
                await bb.me()
            log.info("keepalive: session refreshed")
        except Exception as e:
            log.info("keepalive: skipped (%s)", e)


@asynccontextmanager
async def _lifespan(_server: MCPServer):
    minutes = _keepalive_minutes()
    task = asyncio.create_task(_keepalive_loop(minutes)) if minutes > 0 else None
    try:
        yield {}
    finally:
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


mcp = MCPServer(
    name="blackboard",
    lifespan=_lifespan,
    instructions=(
        "Read-only access to the user's Blackboard Learn courses: assignment due "
        "dates, assignment instructions, and course file downloads. Start with "
        "list_due_dates to find what is coming up; it returns a calendar_event "
        "block for each item that can be passed straight to a calendar tool, plus "
        "a content_id that download_assignment_files needs to fetch the handout."
    ),
)


def _local(dt: datetime | None) -> datetime | None:
    return dt.astimezone() if dt else None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _safe_filename(name: str, fallback: str = "file") -> str:
    """Make an untrusted Blackboard filename safe to join onto a directory."""
    name = unicodedata.normalize("NFKD", name or "")
    name = name.replace("\x00", "")
    name = os.path.basename(name.replace("\\", "/"))     # strip any path parts
    name = re.sub(r"[^\w\s.\-()\[\]]", "_", name).strip(" .")
    name = re.sub(r"\s+", " ", name)
    if not name or name in (".", ".."):
        name = fallback
    return name[:180]


def _filename_from_disposition(disposition: str | None) -> str | None:
    if not disposition:
        return None
    m = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I)
    if m:
        from urllib.parse import unquote
        return unquote(m.group(1)).strip('"')
    m = re.search(r'filename="?([^";]+)"?', disposition, re.I)
    return m.group(1).strip() if m else None


_CODE_RE = re.compile(r"^\s*([A-Z]{2,5})\s*[- ]?\s*(\d{3}[A-Z]?)\b")


# Ultra names every document body this internally; it is not a real title.
_GENERIC_TITLES = {"ultraDocumentBody", "ultraDocument", ""}


async def _display_title(bb: BlackboardClient, course_id: str,
                         item: dict[str, Any]) -> str:
    """A human title for a content item, falling back to its parent folder.

    Ultra document bodies are all titled "ultraDocumentBody", so the folder that
    contains them ("Course Syllabus") is what the user actually recognises.
    """
    title = (item.get("title") or "").strip()
    if title not in _GENERIC_TITLES:
        return title
    parent_id = item.get("parentId")
    if parent_id:
        try:
            parent = await bb.content(course_id, parent_id)
            parent_title = (parent.get("title") or "").strip()
            if parent_title and parent_title not in _GENERIC_TITLES:
                return parent_title
        except BlackboardError:
            pass
    return title or item.get("id", "content")


def _write(folder: Path, name: str, data: bytes, saved: list[dict[str, Any]]) -> None:
    path = folder / name
    if path.exists():
        stem, suffix = path.stem, path.suffix
        n = 2
        while path.exists():
            path = folder / f"{stem} ({n}){suffix}"
            n += 1
    path.write_bytes(data)
    saved.append({"filename": path.name, "path": str(path), "bytes": len(data)})


# Trailing "- 33093", "- F26", "- Fall 2026" — section and term noise that every
# institution staples onto the course title.
_TERM_TAIL_RE = re.compile(
    r"\s*[-\u2013\u2014]\s*(?:\d{4,6}|[A-Z]{1,3}\d{2}|"
    r"(?:Fall|Spring|Summer|Winter|FA|SP|SU|WI)\s*\d{2,4})\s*$",
    re.IGNORECASE,
)


def format_course_name(raw: str | None) -> str:
    """Make a Blackboard course name readable.

    Course titles are typed into an SIS by hand and arrive however they were
    stored — "Network&Parallel_Computation" is one real example. Underscores are
    word gaps, an ampersand wants air around it, and runs of space collapse.
    """
    text = (raw or "").replace("_", " ")
    text = re.sub(r"\s*&\s*", " & ", text)
    return re.sub(r"\s+", " ", text).strip()


def _course_title(course: Course) -> str:
    """The human title alone: no catalogue code, no section number, no term."""
    text = format_course_name(course.name)
    m = _CODE_RE.match(text)
    if m:
        text = text[m.end():]
    text = text.lstrip(" :-\u2013\u2014")
    # "- 33093 - F26" is two segments, so strip until nothing more comes off.
    previous = None
    while previous != text:
        previous = text
        text = _TERM_TAIL_RE.sub("", text).strip()
    return text or format_course_name(course.name)


def _course_label(course: Course) -> str:
    """A short label for calendars: "CSCI 450" beats "NDSU1-2710-CSCI450-33093"."""
    # Most institutions lead the course title with the catalogue code.
    m = _CODE_RE.match(format_course_name(course.name))
    if m:
        return f"{m.group(1)} {m.group(2)}"
    # No code in the title, so the title itself is the name worth showing — the
    # institutional id is never something a student would recognise.
    title = _course_title(course)
    if title:
        return title[:48]
    # Nothing usable in the title at all; the institutional id is all that is left.
    return (course.course_id or "").strip() or "(untitled course)"


async def _resolve_courses(bb: BlackboardClient, course_id: str | None,
                           include_unavailable: bool,
                           current_only: bool = False) -> list[Course]:
    courses = await bb.courses(include_unavailable=include_unavailable,
                               current_only=current_only)
    if course_id:
        wanted = course_id.strip().lower()
        # Exact id/code matches win outright; otherwise fall back to a substring
        # over both the code and the title, so "ENG", "ENG-210" and "writing"
        # all find the same course.
        exact = [c for c in courses
                 if c.id.lower() == wanted or c.course_id.lower() == wanted]
        matches = exact or [
            c for c in courses
            if wanted in c.course_id.lower() or wanted in c.name.lower()
        ]
        if not matches:
            known = ", ".join(f"{c.course_id or c.name} ({c.id})" for c in courses[:20])
            raise BlackboardError(
                f"No course matched {course_id!r}. Available courses: {known}"
            )
        return matches
    return courses


# --------------------------------------------------------------------- tools


@mcp.tool()
async def check_connection() -> dict[str, Any]:
    """Verify the Blackboard session cookie works and report who it belongs to.

    Run this first when anything else returns an auth error — it distinguishes a
    stale cookie from a wrong hostname or a genuinely missing item.
    """
    try:
        async with _client() as bb:
            me = await bb.me()
            all_courses = await bb.courses()
            current = [c for c in all_courses if c.is_current is not False]
            name = me.get("name") or {}
            return {
                "connected": True,
                "host": bb.origin,
                "user": {
                    "id": me.get("id"),
                    "username": me.get("userName"),
                    "name": " ".join(
                        p for p in (name.get("given"), name.get("family")) if p
                    ) or None,
                },
                "active_course_count": len(current),
                "all_terms_course_count": len(all_courses),
                "current_terms": sorted({c.term for c in current if c.term}),
            }
    except AuthError as e:
        return {
            "connected": False,
            "problem": "auth",
            "error": str(e),
            "fix": (
                "Log into Blackboard in your browser, open DevTools > Network, click "
                "any request to the Blackboard domain, copy the whole Cookie request "
                "header, and paste it as BB_COOKIE in .env."
            ),
        }
    except BlackboardError as e:
        return {"connected": False, "problem": "request", "error": str(e)}


@mcp.tool()
async def list_courses(all_terms: bool = False,
                       include_unavailable: bool = False) -> dict[str, Any]:
    """List the user's Blackboard courses, current term only by default.

    Args:
        all_terms: Include past and future terms. Institutions leave old courses
            flagged available for years, so the default filters by the term's
            actual date range.
        include_unavailable: Also return courses the institution has closed or
            hidden entirely.
    """
    async with _client() as bb:
        courses = await bb.courses(include_unavailable=include_unavailable,
                                   current_only=not all_terms)
        return {
            "count": len(courses),
            "filtered_to": "all terms" if all_terms else "current term",
            "courses": [
                {
                    "course_id": c.id,
                    "code": c.course_id,
                    "name": c.name,
                    "title": _course_title(c),
                    "label": _course_label(c),
                    "term": c.term,
                    "term_ends": _iso(c.term_end),
                    "current_term": c.is_current,
                    "last_accessed": _iso(c.last_accessed),
                }
                for c in courses
            ],
        }


@mcp.tool()
async def list_due_dates(
    days_ahead: int = 30,
    days_back: int = 0,
    course_id: str | None = None,
    include_submitted: bool = False,
    include_undated: bool = False,
    all_terms: bool = False,
) -> dict[str, Any]:
    """List upcoming assignment due dates across all courses.

    This is the main entry point. Each item carries a ready-to-use `calendar_event`
    block for a calendar tool, and a `content_id` that `download_assignment_files`
    and `get_assignment` accept.

    Args:
        days_ahead: How far forward to look. 30 covers a typical month of work.
        days_back: Also include items that were due this many days ago — useful for
            spotting something missed. 0 means upcoming only.
        course_id: Restrict to one course. Accepts the internal id, the course code
            like "CS-340-001", or any substring of the course name.
        include_submitted: Keep items already submitted or graded. Off by default so
            the list reflects outstanding work only.
        include_undated: Also list assignments with no due date set.
        all_terms: Scan past and future terms too. Off by default — it multiplies
            the number of requests and old courses have nothing due.
    """
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=max(days_back, 0))
    window_end = now + timedelta(days=max(days_ahead, 0))

    async with _client() as bb:
        courses = await _resolve_courses(bb, course_id, include_unavailable=False,
                                         current_only=not all_terms)
        if not courses:
            return {"count": 0, "items": [],
                    "note": "No current-term courses found. Try all_terms=true."}

        uid = await bb.user_id()
        results = await gather_limited(bb.gradebook_columns(c.id) for c in courses)

        candidates: list[tuple[Course, dict[str, Any], datetime | None]] = []
        errors: list[dict[str, str]] = []
        inaccessible: list[str] = []
        for course, res in zip(courses, results):
            if isinstance(res, BaseException):
                if isinstance(res, ForbiddenError):
                    inaccessible.append(course.name)
                else:
                    errors.append({"course": course.name, "error": str(res)})
                continue
            for col in res:
                grading = col.get("grading") or {}
                due = parse_bb_time(grading.get("due"))
                # Calculated columns (Overall Grade, Weighted Total) are not work.
                if not is_assignment_column(col):
                    continue
                if due is None:
                    if include_undated:
                        candidates.append((course, col, None))
                    continue
                if window_start <= due <= window_end:
                    candidates.append((course, col, due))

        # One bulk gradebook call per course returns every grade at once, which is
        # far cheaper than asking per assignment.
        statuses: dict[str, dict[str, Any]] = {}
        with_candidates = {c.id: c for c, _col, _d in candidates}
        if with_candidates:
            grade_res = await gather_limited(
                bb.gradebook_grades(cid, uid) for cid in with_candidates
            )
            for cid, grades in zip(with_candidates, grade_res):
                if isinstance(grades, BaseException):
                    continue
                for col_id, row in grades.items():
                    statuses[f"{cid}:{col_id}"] = row

        items: list[dict[str, Any]] = []
        for course, col, due in candidates:
            key = f"{course.id}:{col['id']}"
            st = statuses.get(key) or {}
            display = st.get("displayGrade") or {}
            status_text = st.get("status")
            score = display.get("score", st.get("score"))
            # A row exists only once there is something to report, but an ungraded
            # submission still has no score — so check both signals.
            submitted = bool(
                st.get("status") in ("Graded", "NeedsGrading", "Completed")
                or score is not None
                or st.get("exempt") is True
            )
            if submitted and not include_submitted:
                continue

            label = _course_label(course)
            title = col.get("name") or "(untitled assignment)"
            due_local = _local(due)
            item: dict[str, Any] = {
                "title": title,
                "course": label,
                "course_name": _course_title(course),
                "course_id": course.id,
                "column_id": col.get("id"),
                # content_id is the handle for instructions and file downloads;
                # it is absent for manually created gradebook columns.
                "content_id": col.get("contentId"),
                "due_utc": _iso(due),
                "due_local": _iso(due_local),
                "days_until": round((due - datetime.now(timezone.utc)).total_seconds() / 86400, 1)
                if due else None,
                "points_possible": (col.get("score") or {}).get("possible"),
                "submitted": submitted,
                "status": status_text,
                "score": score,
            }
            if due_local:
                item["calendar_event"] = {
                    "summary": f"{label}: {title}" if label else title,
                    # Due dates are instants, not spans; a 30-minute block reads
                    # sensibly in every calendar UI.
                    "start": _iso(due_local - timedelta(minutes=30)),
                    "end": _iso(due_local),
                    "description": (
                        f"{course.name}\nDue {due_local.strftime('%a %b %d, %I:%M %p')}"
                        + (f"\n{item['points_possible']} points"
                           if item["points_possible"] else "")
                    ),
                }
            items.append(item)

        items.sort(key=lambda i: (i["due_utc"] is None, i["due_utc"] or ""))
        out: dict[str, Any] = {
            "count": len(items),
            "window": {"from": _iso(window_start), "to": _iso(window_end)},
            "courses_checked": len(courses),
            "terms": sorted({c.term for c in courses if c.term}),
            "items": items,
        }
        if inaccessible:
            out["courses_without_gradebook_access"] = inaccessible
            out["note"] = (
                "Some courses returned 403 for their gradebook. The session is fine "
                "— those instructors have hidden the gradebook from students, so any "
                "deadlines there must be found with browse_course_content."
            )
        if errors:
            out["partial_failures"] = errors
        return out


@mcp.tool()
async def get_assignment(course_id: str, content_id: str) -> dict[str, Any]:
    """Fetch an assignment's full instructions and list its attached files.

    Args:
        course_id: The course's internal id, from list_due_dates or list_courses.
        content_id: The assignment's content id, from list_due_dates.
    """
    async with _client() as bb:
        item = await bb.content(course_id, content_id)
        title = await _display_title(bb, course_id, item)
        body = content_instructions(item)
        classic = await bb.attachments(course_id, content_id)
        embedded = extract_embedded_files(body)
        files = [
            {"attachment_id": a.get("id"), "filename": a.get("fileName"),
             "mime_type": a.get("mimeType"), "source": "attachment"}
            for a in classic
        ] + [
            {"attachment_id": None, "filename": f.filename, "mime_type": f.mime_type,
             "bytes": f.size, "source": "inline"}
            for f in embedded
        ]
        handler = item.get("contentHandler") or {}
        return {
            "title": title,
            "raw_title": item.get("title"),
            "course_id": course_id,
            "content_id": content_id,
            "instructions": html_to_text(body),
            "instructions_html": body,
            "created": item.get("created"),
            "modified": item.get("modified"),
            "type": (handler.get("id") or "").replace("resource/x-bb-", "") or None,
            "grade_column_id": handler.get("gradeColumnId"),
            # Where this item lives in Blackboard, for reading it there instead.
            "web_url": launch_url(item, bb.origin),
            "attachments": files,
            "next_step": (
                "Call download_assignment_files with this course_id and content_id "
                "to save the attachments locally."
                if files else
                "No attached files; the instructions above are the whole assignment."
            ),
        }


@mcp.tool()
async def download_assignment_files(
    course_id: str,
    content_id: str,
    dest_dir: str | None = None,
) -> dict[str, Any]:
    """Download an assignment's attached files (handouts, starter code, rubrics).

    Args:
        course_id: The course's internal id.
        content_id: The assignment's content id, from list_due_dates.
        dest_dir: Where to save. Defaults to $BB_DOWNLOAD_DIR, or the
            `blackboard-files` folder in the app's state directory.
            A subfolder is created per assignment.
    """
    async with _client() as bb:
        item = await bb.content(course_id, content_id)
        title = await _display_title(bb, course_id, item)
        body = content_instructions(item)
        attachments = await bb.attachments(course_id, content_id)
        embedded = extract_embedded_files(body)
        if not attachments and not embedded:
            return {
                "downloaded": [],
                "note": f"{title!r} has no attached files.",
            }

        base = (Path(dest_dir).expanduser().resolve() if dest_dir
                else paths.download_dir())
        folder = base / _safe_filename(title or content_id, content_id)
        folder.mkdir(parents=True, exist_ok=True)

        saved: list[dict[str, Any]] = []
        failed: list[dict[str, str]] = []
        for a in attachments:
            aid = a.get("id")
            if not aid:
                continue
            try:
                data, disposition = await bb.download_attachment(course_id, content_id, aid)
            except BlackboardError as e:
                failed.append({"filename": a.get("fileName") or aid, "error": str(e)})
                continue
            name = _safe_filename(
                a.get("fileName") or _filename_from_disposition(disposition) or aid,
                fallback=str(aid),
            )
            _write(folder, name, data, saved)

        # Ultra links its handouts inline in the instructions rather than exposing
        # them through /attachments, so fetch those by URL.
        for f in embedded:
            try:
                data, disposition = await bb.download_url(f.url)
            except BlackboardError as e:
                failed.append({"filename": f.filename, "error": str(e)})
                continue
            name = _safe_filename(
                f.filename or _filename_from_disposition(disposition) or "attachment",
                fallback="attachment",
            )
            _write(folder, name, data, saved)

        out: dict[str, Any] = {
            "assignment": title,
            "directory": str(folder),
            "downloaded": saved,
        }
        if failed:
            out["failed"] = failed
        return out


@mcp.tool()
async def browse_course_content(
    course_id: str,
    content_id: str | None = None,
) -> dict[str, Any]:
    """Browse a course's content tree — folders, documents, and assignments.

    Use this to find material that has no gradebook entry (lecture slides, syllabus,
    readings) or to locate an assignment list_due_dates could not link to content.

    Args:
        course_id: The course's internal id, code, or a substring of its name.
        content_id: A folder to open. Omit for the course's top level.
    """
    async with _client() as bb:
        courses = await _resolve_courses(bb, course_id, include_unavailable=True)
        course = courses[0]
        children = await bb.contents(course.id, content_id)
        entries = []
        for c in children:
            handler = (c.get("contentHandler") or {}).get("id", "")
            entries.append({
                "content_id": c.get("id"),
                "title": c.get("title"),
                "type": handler.replace("resource/x-bb-", "") or "unknown",
                "is_folder": handler in ("resource/x-bb-folder", "resource/x-bb-lesson"),
                "is_assignment": handler in (
                    "resource/x-bb-assignment", "resource/x-bb-asmt-test-link",
                ),
                "has_body": bool(c.get("body")),
                "web_url": launch_url(c, bb.origin),
            })
        return {
            "course_id": course.id,
            "course_name": _course_title(course),
            "parent_content_id": content_id,
            "count": len(entries),
            "entries": entries,
        }


@mcp.tool()
async def list_announcements(
    course_id: str | None = None,
    limit: int = 15,
) -> dict[str, Any]:
    """Read recent course announcements — often where deadline changes are posted.

    Args:
        course_id: Restrict to one course. Omit to check every active course.
        limit: Maximum announcements to return across all courses.
    """
    async with _client() as bb:
        courses = await _resolve_courses(bb, course_id, include_unavailable=False,
                                         current_only=True)
        results = await gather_limited(bb.announcements(c.id) for c in courses)
        rows: list[dict[str, Any]] = []
        for course, res in zip(courses, results):
            if isinstance(res, BaseException):
                continue
            for a in res:
                rows.append({
                    "course": _course_label(course),
                    "title": a.get("title"),
                    "posted": a.get("created"),
                    "body": html_to_text(a.get("body"))[:2000],
                })
        rows.sort(key=lambda r: r.get("posted") or "", reverse=True)
        return {"count": len(rows[:limit]), "announcements": rows[:limit]}


@mcp.tool()
async def session_status() -> dict[str, Any]:
    """Report how much life the Blackboard session cookie has left.

    Blackboard reissues the cookie as it gets used, and this server saves each
    new one, so an actively used session keeps extending itself. Use this to see
    whether a fresh cookie needs pasting.
    """
    env_cookie = os.environ.get("BB_COOKIE")
    cookie, source = best_cookie(env_cookie, STORE, os.environ.get("BB_HOST"))
    if not cookie:
        return {
            "configured": False,
            "fix": "Set BB_COOKIE in .env — see the README.",
        }
    left = seconds_remaining(cookie)
    exp = cookie_expiry(cookie)
    stored = STORE.info()
    minutes = _keepalive_minutes()
    return {
        "configured": True,
        "cookie_source": source,
        "expires_utc": exp.isoformat() if exp else None,
        "expires_local": exp.astimezone().isoformat() if exp else None,
        "hours_remaining": round(left / 3600, 2) if left is not None else None,
        "expired": bool(left is not None and left <= 0),
        "auto_renewed_at": stored.get("updated"),
        "keepalive": (
            f"on, every {minutes:g} min" if minutes > 0 else "off (BB_KEEPALIVE_MINUTES=0)"
        ),
        "note": (
            "The session renews itself whenever a tool runs or the keepalive fires. "
            "You only need a fresh cookie if this server has been stopped for longer "
            "than the idle timeout, or your institution forces a re-login."
        ),
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
