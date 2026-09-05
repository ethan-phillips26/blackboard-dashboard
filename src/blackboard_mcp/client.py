"""Thin async client for the Blackboard Learn REST API, authenticated with a
browser session cookie.

Blackboard exposes the same /learn/api/public/* endpoints to a logged-in browser
session as it does to an OAuth2 application. The OAuth2 route needs your school's
Blackboard administrator to whitelist an Application ID, which students generally
cannot get, so we ride on the session cookie instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable, Iterable
from urllib.parse import unquote

import httpx

logging.getLogger("httpx").setLevel(logging.WARNING)

# Blackboard rejects requests with an unfamiliar user agent on some instances.
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class BlackboardError(RuntimeError):
    """Any failure talking to Blackboard."""


class AuthError(BlackboardError):
    """The session cookie is missing, malformed, or expired."""


class ForbiddenError(BlackboardError):
    """The session is valid but this course or item is closed to the user."""


def normalize_cookie(raw: str) -> str:
    """Accept anything the user might paste and return a Cookie header value.

    Handles a bare BbRouter value, a `BbRouter=...` pair, or a full
    `document.cookie` dump with a dozen unrelated cookies in it.
    """
    raw = (raw or "").strip().strip('"').strip("'")
    if not raw:
        raise AuthError(
            "No Blackboard cookie configured. Set BB_COOKIE in your .env file "
            "(see README for how to copy it out of your browser)."
        )
    # A full cookie dump, or at least one k=v pair: keep the pairs Blackboard cares
    # about and drop analytics/CDN noise that can push the header over size limits.
    if "=" in raw:
        keep = {"bbrouter", "jsessionid", "bb-sessionid", "awsalb", "awsalbcors",
                "xsrf-token", "session_id", "web_client_cache_guid"}
        pairs = []
        for part in raw.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            name = part.split("=", 1)[0].strip()
            if name.lower() in keep:
                pairs.append(part)
        if pairs:
            if not any(p.split("=", 1)[0].strip().lower() == "bbrouter" for p in pairs):
                raise AuthError(
                    "The cookie you pasted has no BbRouter value. Copy the whole "
                    "Cookie header from a request to your Blackboard site."
                )
            return "; ".join(pairs)
    # A bare cookie value with no name.
    if raw.startswith("expires:") or "," in raw:
        return f"BbRouter={raw}"
    raise AuthError(
        "Could not make sense of BB_COOKIE. Expected something like "
        "'BbRouter=expires:...,id:...' — see the README."
    )


def normalize_host(raw: str) -> str:
    """Turn whatever the user put in BB_HOST into an https origin."""
    raw = (raw or "").strip().rstrip("/")
    if not raw:
        raise BlackboardError(
            "No Blackboard host configured. Set BB_HOST in your .env file, "
            "e.g. BB_HOST=blackboard.university.edu"
        )
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    # Trim a pasted deep link down to the origin.
    m = re.match(r"^(https?://[^/]+)", raw)
    if not m:
        raise BlackboardError(f"Could not parse BB_HOST: {raw!r}")
    return m.group(1)


def _escape_label(label: str) -> str:
    """Make link text safe to sit inside the brackets of a markdown link."""
    return label.replace("\\", "\\\\").replace("]", "\\]")


class _TextExtractor(HTMLParser):
    """Flatten Blackboard's HTML instruction bodies into readable plain text.

    Links come out one of two ways. `bracket` puts the URL where the anchor was
    and leaves its text alone — "[https://x] Read this" — which is what an agent
    reading a tool result wants, since the URL is the part it can act on.
    `markdown` keeps the two together as "[Read this](https://x)", which is what
    a renderer needs to draw the anchor the author actually wrote.
    """

    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
              "section", "article", "blockquote", "pre", "table"}

    def __init__(self, links: str = "bracket") -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._markdown = links == "markdown"
        # The anchor's own text, collected until </a> so the URL can be written
        # after it. None whenever we are not inside one.
        self._label: list[str] | None = None
        self._href = ""

    def _emit(self, text: str) -> None:
        """Text goes to the anchor being collected, or straight to the output.

        A block tag inside an anchor would otherwise break the label across
        lines, so while collecting one its newlines flatten to spaces.
        """
        if self._label is None:
            self.parts.append(text)
        else:
            self._label.append(text.replace("\n", " "))

    def _close_anchor(self) -> None:
        label = re.sub(r"\s+", " ", "".join(self._label or [])).strip()
        href = self._href
        self._label, self._href = None, ""
        # An anchor wrapped around nothing but an image has no text to show, so
        # the URL becomes its own label rather than vanishing.
        #
        # No padding, unlike the bracket form: this link stands exactly where the
        # anchor stood, so the space before it and the full stop after it are
        # already in the surrounding text. Padding it would put a gap in front of
        # that full stop.
        self.parts.append(f"[{_escape_label(label or href)}]({href})")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "a":
            href = dict(attrs).get("href")
            if not href or href.startswith(("#", "javascript:")):
                return
            if not self._markdown:
                self.parts.append(f" [{href}] ")
            elif self._label is None:
                self._label, self._href = [], href
        elif tag == "li":
            self._emit("\n- ")
        elif tag in self._BLOCK:
            self._emit("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag == "a":
            if self._label is not None:
                self._close_anchor()
        elif tag == "li":
            pass  # the next <li> supplies its own newline; avoid double-spacing
        elif tag in self._BLOCK:
            self._emit("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._emit(data)

    def text(self) -> str:
        # Markup in the wild is not always closed; an unterminated anchor still
        # has to give its text back rather than swallow it.
        if self._label is not None:
            self._close_anchor()
        out = "".join(self.parts)
        out = re.sub(r"[ \t\xa0]+", " ", out)
        out = re.sub(r"\n\s*\n\s*\n+", "\n\n", out)
        return out.strip()


def html_to_text(html: str | None, *, links: str = "bracket") -> str:
    """Blackboard's HTML as readable text. See _TextExtractor for `links`."""
    if not html:
        return ""
    if "<" not in html:
        return html.strip()
    p = _TextExtractor(links)
    try:
        p.feed(html)
        p.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html).strip()
    return p.text()


def parse_bb_time(value: str | None) -> datetime | None:
    """Blackboard emits ISO 8601 in UTC, usually '2026-09-05T03:59:00.000Z'."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class EmbeddedFile:
    """A file linked inline in Ultra assignment instructions."""
    filename: str
    url: str
    mime_type: str | None = None
    size: int | None = None


class _AttachmentFinder(HTMLParser):
    """Pull `data-bbtype="attachment"` links out of Ultra instruction HTML.

    Ultra does not expose these through the /attachments endpoint (it returns
    400); the only handle on them is the bbcswebdav link in the markup, with the
    real filename carried in a JSON `data-bbfile` attribute.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.files: list[EmbeddedFile] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        a = {k: (v or "") for k, v in attrs}
        raw = a.get("data-bbfile")
        # Ultra emits two shapes. Assignment instructions use an href plus
        # data-bbtype="attachment"; document bodies omit both and carry the URL
        # inside the data-bbfile JSON as resourceUrl. Accept either.
        if not raw and a.get("data-bbtype") != "attachment":
            return
        meta: dict[str, Any] = {}
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    meta = parsed
            except ValueError:
                meta = {}
        url = (a.get("href") or meta.get("resourceUrl")
               or meta.get("viewerUrl") or "")
        if not url:
            return
        name = (meta.get("fileName") or meta.get("linkName")
                or meta.get("displayName") or meta.get("alternativeText") or "")
        if not name:
            name = unquote(url.rsplit("/", 1)[-1].split("?")[0]) or "attachment"
        size = meta.get("fileSize")
        self.files.append(EmbeddedFile(
            filename=name, url=url,
            mime_type=meta.get("mimeType"),
            size=size if isinstance(size, int) else None,
        ))


def extract_embedded_files(html: str | None) -> list[EmbeddedFile]:
    if not html or ("data-bbfile" not in html and "data-bbtype" not in html):
        return []
    finder = _AttachmentFinder()
    try:
        finder.feed(html)
        finder.close()
    except Exception:
        return []
    seen: set[tuple[str, str]] = set()
    out: list[EmbeddedFile] = []
    for f in finder.files:
        # The signed query string differs per render, so key on the path.
        key = (f.filename, f.url.split("?")[0])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def is_assignment_column(col: dict[str, Any]) -> bool:
    """Is this gradebook column real coursework, rather than a running total?

    Blackboard mixes calculated columns ("Overall Grade", "Weighted Total") in
    with real assignments. They carry a grade and a points-possible like anything
    else, so counting one as coursework badly skews a grade calculation. They are
    identified inconsistently — `grading.type` is the only reliable signal, since
    some carry no scoreProviderHandle at all.
    """
    grading = col.get("grading") or {}
    if grading.get("type") == "Calculated":
        return False
    if col.get("scoreProviderHandle") == "resource/x-bb-calculatedgrade":
        return False
    if col.get("calculatedColumn") is not None:
        return False
    return True


# What Blackboard *runs* for you, as opposed to what it merely stores.
#
# An LTI placement is the case that matters: Vevox class participation, a lab
# tool, Zoom, a course-evaluation tool. Blackboard launches those inside itself
# with a signed handoff, so there is nothing this dashboard can show in their
# place — the link is the only way in. Assignments and tests belong here too:
# reading one here is fine, but submitting happens over there.
#
# The `blti` prefix is matched rather than listed because each placement carries
# its own suffix — `…-bltiplacement-zoomlti_13_ndsu`,
# `…-bltiplacement-SmartEvals_Course_Tool` — one per tool the institution installs.
LTI_PREFIX = "resource/x-bb-blti"
LAUNCHED_TYPES = frozenset({
    "resource/x-bb-assignment",
    "resource/x-bb-asmt-test-link",
    # A pointer to another item in the same course: still Blackboard.
    "resource/x-bb-courselink",
    "resource/x-bb-forumlink",
    "resource/x-bb-toollink",
    "resource/x-bb-journal",
    "resource/x-bb-blog",
    "resource/x-bb-wiki",
})


def launches_in_blackboard(item: dict[str, Any]) -> bool:
    """Is opening this in Blackboard worth doing?

    Files, documents and folders are not: this dashboard already shows the text
    and hands over the file, so a link there is a longer road to the same thing.
    An external link is worse than useless — it leaves Blackboard the moment it
    resolves, and the row here already names where it goes.

    What is left is what Blackboard does rather than stores.
    """
    kind = (item.get("contentHandler") or {}).get("id") or ""
    return kind.startswith(LTI_PREFIX) or kind in LAUNCHED_TYPES


def launch_url(item: dict[str, Any], origin: str) -> str | None:
    """Blackboard's link to this item, for the items where that link earns its place."""
    return web_link(item, origin) if launches_in_blackboard(item) else None


def web_link(item: dict[str, Any], origin: str) -> str | None:
    """Blackboard's own link to this item's page, made absolute.

    Learn hands one out on every content item — an `/ultra/redirect?...` that it
    resolves server-side to wherever the item actually lives. Following it beats
    assembling a URL here: the shape of a course page is Blackboard's business
    and differs between Ultra and Original, and this link is right in both
    without us knowing which we are looking at.
    """
    for link in item.get("links") or []:
        href = (link or {}).get("href")
        if not href:
            continue
        if (link.get("rel") or "alternate") != "alternate":
            continue
        if href.startswith(("http://", "https://")):
            return href
        return origin.rstrip("/") + "/" + href.lstrip("/")
    return None


def content_instructions(item: dict[str, Any]) -> str:
    """Ultra keeps instructions on the contentHandler; Original uses `body`."""
    handler = item.get("contentHandler") or {}
    for candidate in (item.get("body"), handler.get("instructions"),
                      handler.get("description"), handler.get("text")):
        if candidate and str(candidate).strip():
            return str(candidate)
    return ""


@dataclass
class Course:
    id: str
    course_id: str          # the human "CS-340-001" style identifier
    name: str
    term: str | None
    available: bool
    term_id: str | None = None
    term_start: datetime | None = None
    term_end: datetime | None = None
    last_accessed: datetime | None = None
    # Where this course lives in Blackboard's own web UI, as Blackboard reports
    # it — the one URL we never have to construct.
    external_url: str | None = None

    @property
    def is_current(self) -> bool | None:
        """True/False if the term has real dates, None if it cannot be determined.

        Institutions leave old courses flagged "available" for years, so term
        dates are the only reliable way to tell this semester from last.
        """
        if self.term_start is None or self.term_end is None:
            return None
        return self.term_start <= datetime.now(timezone.utc) <= self.term_end

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "Course":
        term = raw.get("term")
        return cls(
            id=raw.get("id", ""),
            course_id=raw.get("courseId") or raw.get("externalId") or "",
            name=raw.get("name") or raw.get("displayName") or "(untitled course)",
            term=term.get("name") if isinstance(term, dict) else None,
            available=(raw.get("availability") or {}).get("available") in ("Yes", "Term"),
            term_id=raw.get("termId"),
            external_url=raw.get("externalAccessUrl"),
        )


class BlackboardClient:
    def __init__(self, host: str, cookie: str, *, timeout: float = 30.0,
                 max_concurrency: int = 6,
                 on_cookie_refresh: Callable[[str], None] | None = None) -> None:
        self.origin = normalize_host(host)
        self.cookie = normalize_cookie(cookie)
        self._on_refresh = on_cookie_refresh
        self._sem = asyncio.Semaphore(max_concurrency)
        self._http = httpx.AsyncClient(
            base_url=self.origin,
            timeout=timeout,
            follow_redirects=False,   # a redirect to /webapps/login means expired
            headers={
                "User-Agent": _UA,
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        # Seed the jar rather than pinning a Cookie header. httpx then applies
        # every Set-Cookie the server sends, which is how the session renews
        # itself, and it scopes cookies to this host so they cannot leak to the
        # signed storage domains that attachment downloads redirect to.
        host_name = httpx.URL(self.origin).host
        for pair in self.cookie.split(";"):
            pair = pair.strip()
            if "=" in pair:
                name, value = pair.split("=", 1)
                self._http.cookies.set(name.strip(), value.strip(),
                                       domain=host_name, path="/")
        self._last_bbrouter = self._http.cookies.get("BbRouter")
        self._me: dict[str, Any] | None = None
        self._terms: dict[str, dict[str, Any] | None] = {}

    def current_cookie(self) -> str:
        """The cookie as it stands now, including any server-side renewal."""
        pairs = [f"{k}={v}" for k, v in self._http.cookies.items()]
        return "; ".join(pairs) if pairs else self.cookie

    def _capture_refresh(self) -> None:
        """Persist the cookie if Blackboard just reissued it."""
        current = self._http.cookies.get("BbRouter")
        if current and current != self._last_bbrouter:
            self._last_bbrouter = current
            if self._on_refresh:
                try:
                    self._on_refresh(self.current_cookie())
                except Exception:
                    pass  # persistence is best-effort; never fail a tool call

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "BlackboardClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # ---------------------------------------------------------------- transport

    async def _request(self, method: str, path: str, *, raw_redirect: bool = False,
                       **kw: Any) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(4):
            async with self._sem:
                try:
                    resp = await self._http.request(method, path, **kw)
                except httpx.RequestError as e:  # DNS, TLS, timeouts
                    last_exc = e
                    await asyncio.sleep(0.6 * 2**attempt)
                    continue

            self._capture_refresh()

            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if (retry_after or "").isdigit() else 0.6 * 2**attempt
                await asyncio.sleep(min(delay, 10.0))
                continue

            # An expired session redirects to the login page instead of 401ing.
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location", "")
                if "login" in location.lower() or "webapps/login" in location.lower():
                    raise AuthError(
                        "Blackboard redirected to the login page — your BB_COOKIE has "
                        "expired. Log into Blackboard in your browser and copy a fresh "
                        "cookie into .env."
                    )
                if raw_redirect:
                    return resp
                raise BlackboardError(f"Unexpected redirect to {location!r} for {path}")

            if resp.status_code == 401:
                raise AuthError(
                    f"Blackboard returned 401 for {path}. Your BB_COOKIE is expired "
                    "or invalid. Copy a fresh cookie from your browser and try again."
                )
            if resp.status_code == 403:
                # The session is fine; this particular course or item is closed to
                # the student role. Callers treat this as "skip", not "re-auth".
                raise ForbiddenError(
                    f"Blackboard returned 403 for {path}. The session is valid but "
                    "this item is not accessible to you — instructors can hide a "
                    "course's gradebook from students."
                )
            if resp.status_code == 404:
                raise BlackboardError(f"Not found: {path}")
            if resp.status_code >= 400:
                raise BlackboardError(
                    f"Blackboard returned {resp.status_code} for {path}: {resp.text[:300]}"
                )
            return resp

        raise BlackboardError(f"Request to {path} failed after retries: {last_exc}")

    async def get_json(self, path: str, **kw: Any) -> dict[str, Any]:
        resp = await self._request("GET", path, **kw)
        ctype = resp.headers.get("Content-Type", "")
        if "json" not in ctype:
            # Blackboard serves the login HTML page with a 200 in some configs.
            if "<html" in resp.text[:400].lower():
                raise AuthError(
                    "Blackboard returned an HTML page instead of JSON, which almost "
                    "always means the session cookie expired. Refresh BB_COOKIE."
                )
            raise BlackboardError(f"Expected JSON from {path}, got {ctype!r}")
        try:
            return resp.json()
        except ValueError as e:
            raise BlackboardError(f"Malformed JSON from {path}: {e}") from e

    async def paged(self, path: str, *, limit: int = 500,
                    params: dict[str, Any] | None = None, **kw: Any) -> list[dict[str, Any]]:
        """Follow Blackboard's paging.nextPage links and concatenate results."""
        out: list[dict[str, Any]] = []
        next_path: str | None = path
        query: dict[str, Any] | None = dict(params or {})
        query.setdefault("limit", 200)
        seen_pages: set[str] = set()
        while next_path and len(out) < limit:
            # nextPage is a full path with its own query string. Passing a params
            # dict alongside it would overwrite that query and refetch page 1
            # forever, so only send params on the first request.
            if query is not None:
                data = await self.get_json(next_path, params=query, **kw)
                query = None
            else:
                data = await self.get_json(next_path, **kw)
            results = data.get("results")
            if results is None:
                # A single-object endpoint; hand it back as a one-item list.
                return [data] if data else []
            out.extend(results)
            seen_pages.add(next_path)
            next_path = (data.get("paging") or {}).get("nextPage")
            if next_path in seen_pages:  # malformed paging; stop rather than spin
                break
        return out[:limit]

    # -------------------------------------------------------------- endpoints

    async def me(self) -> dict[str, Any]:
        if self._me is None:
            self._me = await self.get_json("/learn/api/public/v1/users/me")
        return self._me

    async def user_id(self) -> str:
        me = await self.me()
        uid = me.get("id")
        if not uid:
            raise BlackboardError("Blackboard did not return a user id for the session.")
        return uid

    async def term(self, term_id: str) -> dict[str, Any] | None:
        """One term, cached. Returns None if the instance withholds it."""
        if term_id in self._terms:
            return self._terms[term_id]
        try:
            data = await self.get_json(f"/learn/api/public/v1/terms/{term_id}")
        except BlackboardError:
            data = None
        self._terms[term_id] = data
        return data

    async def _attach_terms(self, courses: list[Course]) -> None:
        ids = {c.term_id for c in courses if c.term_id}
        if not ids:
            return
        ordered = sorted(ids)
        fetched = await asyncio.gather(
            *(self.term(i) for i in ordered), return_exceptions=True
        )
        by_id = {i: (d if isinstance(d, dict) else None)
                 for i, d in zip(ordered, fetched)}
        for c in courses:
            data = by_id.get(c.term_id or "")
            if not data:
                continue
            c.term = data.get("name") or c.term
            duration = (data.get("availability") or {}).get("duration") or {}
            if duration.get("type") == "DateRange":
                c.term_start = parse_bb_time(duration.get("start"))
                c.term_end = parse_bb_time(duration.get("end"))

    async def courses(self, *, include_unavailable: bool = False,
                      current_only: bool = False) -> list[Course]:
        uid = await self.user_id()
        memberships = await self.paged(
            f"/learn/api/public/v1/users/{uid}/courses",
            params={"expand": "course"},
        )
        seen: dict[str, Course] = {}
        for m in memberships:
            raw = m.get("course") or {}
            if not raw.get("id"):
                continue
            # Skip organisations/communities — they carry no assignments.
            if raw.get("organization"):
                continue
            course = Course.from_api(raw)
            if not include_unavailable and not course.available:
                continue
            course.last_accessed = parse_bb_time(m.get("lastAccessed"))
            seen[course.id] = course

        courses = list(seen.values())
        await self._attach_terms(courses)
        if current_only:
            # Keep anything we cannot disprove: a course with no term dates might
            # still be this semester's, and silently hiding coursework is worse
            # than showing one course too many.
            courses = [c for c in courses if c.is_current is not False]
        return sorted(courses, key=lambda c: (c.term or "", c.name))

    async def gradebook_columns(self, course_id: str) -> list[dict[str, Any]]:
        return await self.paged(
            f"/learn/api/public/v2/courses/{course_id}/gradebook/columns"
        )

    async def gradebook_categories(self, course_id: str) -> list[dict[str, Any]]:
        """The course's gradebook categories (Quiz, Exam, Homework, ...).

        Every gradebook column carries a gradebookCategoryId pointing here, which
        is what lets syllabus weights ("quizzes are 25%") attach to real columns.
        """
        try:
            return await self.paged(
                f"/learn/api/public/v1/courses/{course_id}/gradebook/categories"
            )
        except BlackboardError:
            return []

    async def gradebook_grades(self, course_id: str, user_id: str
                               ) -> dict[str, dict[str, Any]]:
        """Every grade the user has in one course, keyed by column id.

        A single call replaces one request per assignment. Columns with no attempt
        are simply absent from the result.
        """
        try:
            rows = await self.paged(
                f"/learn/api/public/v2/courses/{course_id}/gradebook/users/{user_id}"
            )
        except BlackboardError:
            return {}
        return {r["columnId"]: r for r in rows if r.get("columnId")}

    async def contents(self, course_id: str, content_id: str | None = None
                       ) -> list[dict[str, Any]]:
        if content_id:
            path = f"/learn/api/public/v1/courses/{course_id}/contents/{content_id}/children"
        else:
            path = f"/learn/api/public/v1/courses/{course_id}/contents"
        return await self.paged(path, params={"expand": "body"})

    async def content(self, course_id: str, content_id: str) -> dict[str, Any]:
        return await self.get_json(
            f"/learn/api/public/v1/courses/{course_id}/contents/{content_id}"
        )

    async def attachments(self, course_id: str, content_id: str) -> list[dict[str, Any]]:
        """Classic attachment records. Ultra items 400 here and use inline links."""
        try:
            return await self.paged(
                f"/learn/api/public/v1/courses/{course_id}/contents/"
                f"{content_id}/attachments"
            )
        except BlackboardError:
            return []

    async def download_attachment(self, course_id: str, content_id: str,
                                  attachment_id: str) -> tuple[bytes, str | None]:
        resp = await self._request(
            "GET",
            f"/learn/api/public/v1/courses/{course_id}/contents/{content_id}"
            f"/attachments/{attachment_id}/download",
            raw_redirect=True,
        )
        # The download endpoint 302s to a signed storage URL on many instances.
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location", "")
            if not loc:
                raise BlackboardError("Download redirect had no Location header.")
            target = httpx.URL(loc)
            if not target.is_absolute_url:
                target = httpx.URL(self.origin).join(loc)
            # Signed storage URLs usually live on a different host (S3 and the like).
            # They carry their own signature, so never forward the session cookie
            # off Blackboard's own origin.
            headers = None
            if target.host != httpx.URL(self.origin).host:
                # The jar is domain-scoped and would withhold the cookie anyway;
                # this makes the guarantee explicit rather than incidental.
                headers = {"Cookie": "", "User-Agent": _UA}
            async with self._sem:
                resp = await self._http.get(
                    target, follow_redirects=True, headers=headers
                )
            if resp.status_code >= 400:
                raise BlackboardError(f"Download failed with {resp.status_code}")
        return resp.content, resp.headers.get("Content-Disposition")

    async def download_url(self, url: str) -> tuple[bytes, str | None]:
        """Fetch a file by absolute URL (Ultra's inline bbcswebdav links)."""
        target = httpx.URL(url)
        if not target.is_absolute_url:
            target = httpx.URL(self.origin).join(url)
        headers = None
        if target.host != httpx.URL(self.origin).host:
            headers = {"Cookie": "", "User-Agent": _UA}
        async with self._sem:
            resp = await self._http.get(target, follow_redirects=True, headers=headers)
        if resp.status_code >= 400:
            raise BlackboardError(
                f"Download failed with {resp.status_code} for {target.path}"
            )
        return resp.content, resp.headers.get("Content-Disposition")

    async def calendar_items(self, since: datetime, until: datetime
                             ) -> list[dict[str, Any]]:
        return await self.paged(
            "/learn/api/public/v1/calendars/items",
            params={
                "since": since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "until": until.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            },
        )

    async def announcements(self, course_id: str) -> list[dict[str, Any]]:
        return await self.paged(
            f"/learn/api/public/v1/courses/{course_id}/announcements"
        )


async def gather_limited(coros: Iterable[Any]) -> list[Any]:
    """Run coroutines concurrently, returning exceptions instead of raising."""
    return await asyncio.gather(*coros, return_exceptions=True)
