"""FastAPI backend for the Blackboard dashboard.

Data routes call the Blackboard layer directly and serve from the JSON cache, so
the UI is instant and costs nothing. Only one route involves a model: reading a
syllabus for its grade weighting. It is cached on its inputs and only reruns
when those inputs change.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any
from mimetypes import guess_type
from urllib.parse import quote

from dotenv import dotenv_values, set_key
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from blackboard_mcp import paths
from blackboard_mcp.client import BlackboardError, html_to_text, normalize_host
from blackboard_mcp.login import (
    ENV_PATH, PROFILE_DIR, LoginError, run_login, write_env_cookie,
)
from blackboard_mcp.session import SessionStore, best_cookie, seconds_remaining

from . import __version__
from . import grades as G
from . import edits as E
from . import gdocs, ics, llm, notices, sync
from .cache import Cache

app = FastAPI(title="Whiteboard")
cache = Cache()

FRONTEND = Path(__file__).resolve().parent / "frontend" / "dist"

# Where downloaded handouts land. Pinned to the state directory rather than left
# to the process's working directory, so the path we save to and the path we
# serve back agree however the server was started.
DOWNLOADS = paths.download_dir()

# The shape of a cached assignment detail. Bumped when a field is added or its
# meaning narrows, so entries written under the old shape are re-fetched rather
# than served for the rest of their six hours.
DETAIL_SCHEMA = 2


class MappingBody(BaseModel):
    syllabus_label: str
    category_id: str | None = None


class NeededBody(BaseModel):
    column_id: str
    target: float = 90.0
    rate: float = 1.0


class CredentialsBody(BaseModel):
    host: str
    username: str
    password: str
    timeout: float = 300.0


def _set_env_values(values: dict[str, str]) -> None:
    """Persist Blackboard settings and mirror them into this running process."""
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not ENV_PATH.exists():
        ENV_PATH.touch(mode=0o600)
    for key, value in values.items():
        set_key(str(ENV_PATH), key, value or "", quote_mode="always")
        os.environ[key] = value or ""
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass


def _drop_dashboard_cache() -> None:
    for key in cache.keys():
        cache.drop(key)


# What the login browser is doing right now. A sign-in is mostly spent waiting
# on a phone, and a spinner that cannot say so is indistinguishable from a hang.
_LOGIN: dict[str, Any] = {"stage": None, "active": False, "since": None}


def _stage(name: str | None, *, active: bool | None = None) -> None:
    _LOGIN["stage"] = name
    if active is not None:
        _LOGIN["active"] = active
    _LOGIN["since"] = time.time()


def _credentials_failed(e: LoginError) -> bool:
    return getattr(e, "reason", "failed") == "credentials"


def _login_failure(e: LoginError, fallback: str) -> HTTPException:
    """Turn a failed login into a response the sign-in screen can act on.

    A refused username or password is the one failure where retrying with what
    is stored is pointless, so the stored password goes: `configured` drops to
    false, which is what puts the form back in front of the reader instead of an
    automatic sign-in that will fail the same way.
    """
    reason = getattr(e, "reason", "failed")
    if reason == "credentials":
        _set_env_values({"BB_PASSWORD": ""})
    return HTTPException(
        401 if reason == "credentials" else 502,
        {"message": str(e) or fallback, "reason": reason},
    )


def _measure(paths_: list[Path]) -> int:
    total = 0
    for path in paths_:
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def _wipe_downloads() -> tuple[int, int]:
    """Empty the downloads folder, keeping the folder itself.

    `BB_DOWNLOAD_DIR` is a path somebody typed, so it is checked before anything
    is removed: pointed at a home directory or at the state directory itself, the
    honest answer is to delete nothing rather than to take the surrounding files
    with it.
    """
    target = DOWNLOADS
    forbidden = {Path.home().resolve(), paths.state_dir().resolve(), Path("/")}
    if not target.is_dir() or target.resolve() in forbidden:
        return 0, 0
    files = [p for p in target.rglob("*") if p.is_file()]
    freed = _measure(files)
    removed = 0
    for path in files:
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    # Then the folders they were in, deepest first.
    for path in sorted((p for p in target.rglob("*") if p.is_dir()),
                       key=lambda p: len(p.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass
    return removed, freed


def _auth_status() -> dict[str, Any]:
    env = dotenv_values(ENV_PATH)
    host = (os.environ.get("BB_HOST") or env.get("BB_HOST") or "").strip()
    username = (os.environ.get("BB_USERNAME") or env.get("BB_USERNAME") or "").strip()
    password = os.environ.get("BB_PASSWORD")
    if password is None:
        password = env.get("BB_PASSWORD") or ""

    env_cookie = os.environ.get("BB_COOKIE")
    if env_cookie is None:
        env_cookie = env.get("BB_COOKIE") or ""
    store = SessionStore()
    cookie, source = best_cookie(env_cookie, store, host)
    remaining = seconds_remaining(cookie) if cookie else None
    logged_in = bool(cookie) and (remaining is None or remaining > 0)

    try:
        origin = normalize_host(host) if host else None
    except BlackboardError:
        origin = None

    session = store.info()
    return {
        "configured": bool(host and username and password),
        "logged_in": logged_in,
        "host": origin or host,
        "username": username,
        "has_password": bool(password),
        "cookie_source": source if cookie else None,
        "seconds_remaining": remaining,
        "expires": session.get("expires"),
    }


@app.get("/api/auth/status")
async def auth_status() -> dict[str, Any]:
    return _auth_status()


@app.post("/api/auth/login")
async def auth_login(body: CredentialsBody) -> dict[str, Any]:
    host = body.host.strip()
    username = body.username.strip()
    password = body.password
    if not host or not username or not password:
        raise HTTPException(400, "Blackboard host, username, and password are required.")
    try:
        origin = normalize_host(host)
    except BlackboardError as e:
        raise HTTPException(400, str(e)) from e

    _drop_dashboard_cache()
    _set_env_values({
        "BB_HOST": origin,
        "BB_USERNAME": username,
        "BB_PASSWORD": password,
    })

    _stage("opening", active=True)
    try:
        result = await asyncio.to_thread(
            run_login,
            headless=True,
            timeout=max(30.0, min(body.timeout, 900.0)),
            force=True,
            on_stage=_stage,
        )
    except LoginError as e:
        raise _login_failure(e, "Login failed.") from e
    except Exception as e:
        raise HTTPException(502, {"message": f"Login failed: {e}",
                                  "reason": "failed"}) from e
    finally:
        _stage(None, active=False)

    cookie = result["cookie"]
    write_env_cookie(cookie)
    os.environ["BB_COOKIE"] = cookie
    SessionStore().save(cookie, result["origin"])

    sync_status: dict[str, Any] | None = None
    sync_error: str | None = None
    try:
        sync_status = await sync.refresh(cache, force=True)
    except Exception as e:
        sync_error = str(e)

    return {
        "auth": _auth_status(),
        "user": result.get("user") or {},
        "origin": result["origin"],
        "sync": sync_status,
        "sync_error": sync_error,
    }


class ReloginBody(BaseModel):
    timeout: float = 300.0


@app.post("/api/auth/relogin")
async def auth_relogin(body: ReloginBody | None = None) -> dict[str, Any]:
    """Sign back in with the credentials already stored, and nothing typed.

    A Blackboard session dies on its own — an idle timeout, or the daily cap an
    SSO puts on it regardless of activity — and the only thing the old sign-in
    form asked for at that point was a password the server already had. This
    runs the same login script against the stored credentials.

    Unlike a deliberate sign-in this does **not** force a fresh authentication:
    the login browser keeps its own profile, so when that profile's session is
    still good the whole thing completes without a Duo push. Only when it is not
    does the user see the prompt on their phone.
    """
    env = dotenv_values(ENV_PATH)
    host = (os.environ.get("BB_HOST") or env.get("BB_HOST") or "").strip()
    username = (os.environ.get("BB_USERNAME") or env.get("BB_USERNAME") or "").strip()
    password = os.environ.get("BB_PASSWORD")
    if password is None:
        password = env.get("BB_PASSWORD") or ""
    if not (host and username and password):
        raise HTTPException(
            400, "No saved credentials to sign in with — sign in with your "
                 "Blackboard username and password first.")

    # `_set_env_values` mirrors .env into this process, but a server started
    # fresh has only the file; put them where run_login reads them.
    os.environ.setdefault("BB_HOST", host)
    os.environ["BB_USERNAME"] = username
    os.environ["BB_PASSWORD"] = password

    _stage("opening", active=True)
    try:
        result = await asyncio.to_thread(
            run_login,
            headless=True,
            timeout=max(30.0, min((body.timeout if body else 300.0), 900.0)),
            force=False,
            on_stage=_stage,
        )
    except LoginError as e:
        raise _login_failure(e, "Automatic sign-in failed.") from e
    except Exception as e:
        raise HTTPException(502, {"message": f"Automatic sign-in failed: {e}",
                                  "reason": "failed"}) from e
    finally:
        _stage(None, active=False)

    cookie = result["cookie"]
    write_env_cookie(cookie)
    os.environ["BB_COOKIE"] = cookie
    SessionStore().save(cookie, result["origin"])

    sync_error: str | None = None
    try:
        await sync.refresh(cache, force=False)
    except Exception as e:
        sync_error = str(e)

    return {"auth": _auth_status(), "user": result.get("user") or {},
            "sync_error": sync_error}


@app.get("/api/auth/progress")
async def auth_progress() -> dict[str, Any]:
    """Which step the sign-in is on, for the screen that is waiting on it."""
    since = _LOGIN.get("since")
    return {
        "stage": _LOGIN.get("stage"),
        "active": bool(_LOGIN.get("active")),
        "seconds": round(time.time() - since, 1) if since else None,
    }


@app.post("/api/auth/logout")
async def auth_logout() -> dict[str, Any]:
    # The saved password goes with the cookie. It is what `relogin` signs back in
    # with, so leaving it behind would mean the next screen silently undid the
    # logout that was just asked for.
    _drop_dashboard_cache()
    _set_env_values({"BB_COOKIE": "", "BB_PASSWORD": ""})
    SessionStore().path.unlink(missing_ok=True)
    try:
        shutil.rmtree(PROFILE_DIR)
    except OSError:
        pass
    return _auth_status()


@app.get("/api/state")
async def state(refresh: bool = False) -> dict[str, Any]:
    """Everything the dashboard renders, served from cache when it is fresh."""
    status = await sync.refresh(cache, force=refresh)
    courses = cache.get_data("courses", []) or []
    due = cache.get_data("assignments", {}) or {}
    standings = {}
    for c in courses:
        try:
            standings[c["course_id"]] = await sync.course_standing(cache, c["course_id"])
        except Exception as e:  # one bad course must not blank the dashboard
            standings[c["course_id"]] = {"course_id": c["course_id"],
                                         "accessible": False, "reason": str(e)[:200]}
    seen = cache.get_data("seen", {}) or {}
    announcements = (cache.get_data("announcements", []) or [])[:40]
    stored_edits = E.load(cache)
    items = due.get("items", [])
    # Hidden assignments are dropped from the list the dashboard draws, but the
    # settings screen still has to name them to offer them back, and only the
    # fetched item knows which course one belongs to.
    with_hidden = E.apply_assignments(items, stored_edits, keep_hidden=True)
    return {
        "me": cache.get_data("me", {}),
        "courses": courses,
        "assignments": [a for a in with_hidden if not a.get("hidden")],
        "hidden_assignments": [a for a in with_hidden if a.get("hidden")],
        "assignment_meta": {k: v for k, v in due.items() if k != "items"},
        "announcements": announcements,
        "standings": standings,
        "first_seen": seen.get("assignments", {}),
        # The announcements screen marks itself read against these, so a
        # badge means "posted since you last looked" rather than "exists".
        "first_seen_announcements": seen.get("announcements", {}),
        # Posts that have not been put in front of the reader yet. The dashboard
        # pops these up on load and then says so, which is what stops them
        # popping up again on the next one.
        "unannounced": notices.pending(cache, announcements),
        "edits": stored_edits,
        "sync": status,
        "cached_at": (cache.read("assignments") or {}).get("fetched_at"),
    }


class AnnouncedBody(BaseModel):
    ids: list[str]


@app.post("/api/announcements/announced")
async def mark_announced(body: AnnouncedBody) -> dict[str, Any]:
    """Record that these announcements have been shown, so they are not again."""
    shown = notices.mark(cache, body.ids)
    return {"announced": len(shown)}


@app.post("/api/refresh")
async def force_refresh() -> dict[str, Any]:
    return await sync.refresh(cache, force=True)


@app.get("/api/courses/{course_id}/weights")
async def weights(course_id: str, extract: bool = False) -> dict[str, Any]:
    """Stored grade weighting; pass extract=true to read the syllabus with a model."""
    stored = cache.get_data(f"weights_{course_id}")
    if stored is None and not extract:
        return {"course_id": course_id, "status": "not_extracted"}
    try:
        return await sync.get_weights(cache, course_id, force=extract)
    except llm.LLMError as e:
        raise HTTPException(503, f"Syllabus reading failed: {e}") from e


@app.get("/api/courses/{course_id}/content")
async def course_content(course_id: str, refresh: bool = False) -> dict[str, Any]:
    """The course's content tree — the material the gradebook says nothing about.

    Served from cache like everything else, so opening a course is instant and
    only the explicit refresh goes back to Blackboard.
    """
    try:
        return await sync.course_content(cache, course_id, force=refresh)
    except BlackboardError as e:
        raise HTTPException(502, f"Blackboard would not list this course: {e}") from e


@app.post("/api/courses/{course_id}/mapping")
async def set_mapping(course_id: str, body: MappingBody) -> dict[str, Any]:
    """Attach a syllabus component the model could not place to a real category."""
    key = f"weights_{course_id}"
    stored = cache.get_data(key)
    if not stored:
        raise HTTPException(404, "No syllabus weighting stored for this course yet.")
    overrides = dict(stored.get("overrides") or {})
    if body.category_id:
        overrides[body.syllabus_label] = body.category_id
    else:
        overrides.pop(body.syllabus_label, None)
    stored["overrides"] = overrides
    cache.write(key, stored)
    return await sync.course_standing(cache, course_id)


@app.post("/api/courses/{course_id}/needed")
async def needed(course_id: str, body: NeededBody) -> dict[str, Any]:
    """What score is required on one assignment to finish at a target grade."""
    standing = await sync.course_standing(cache, course_id)
    if not standing.get("accessible"):
        raise HTTPException(403, standing.get("reason", "Gradebook not accessible."))
    breakdown = [{
        "category_id": c["category_id"], "title": c["title"], "weight": c["weight"],
        "earned": c["earned"], "graded_possible": c["graded_possible"],
        "total_possible": c["total_possible"],
        "remaining_possible": c["total_possible"] - c["graded_possible"],
        "pct_graded": c["pct"], "columns": c["columns"],
    } for c in standing["categories"]]
    result = G.needed_on(breakdown, body.column_id, body.target, body.rate)
    if result is None:
        raise HTTPException(404, "That assignment is not in this course's gradebook.")
    return result


@app.get("/api/calendar.ics")
async def calendar_feed(refresh: bool = False, download: bool = False) -> Response:
    """The due dates as an iCalendar feed.

    This is the dashboard's calendar source — the grid on screen parses this
    exact document — and it is equally a real subscription URL, so pointing
    Google Calendar or Apple Calendar at it gives the same deadlines.
    """
    try:
        await sync.refresh(cache, force=refresh)
    except Exception:
        # A subscribed calendar client polling while Blackboard is unreachable
        # should still get the deadlines we already know about.
        pass
    due = cache.get_data("assignments", {}) or {}
    me = cache.get_data("me", {}) or {}
    given = ((me.get("name") or {}).get("given") or "").strip()
    calname = f"{given}'s coursework" if given else "Coursework"

    text = ics.build(
        E.apply_assignments(due.get("items", []), E.load(cache)),
        calname=calname,
        description="Assignment due dates from Blackboard. Read-only.",
    )
    ics.write(cache.root / "coursework.ics", text)
    disposition = "attachment" if download else "inline"
    return Response(
        text,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'{disposition}; filename="coursework.ics"',
            "Cache-Control": "no-cache",
        },
    )


# The two shapes an anchor can arrive in: the bare URL an agent is given, and
# the markdown link the dashboard renders. A body can hold either — instructions
# are re-read from the HTML when it is there, and fall back to the agent's text
# when it is not.
def _present(detail: dict[str, Any]) -> dict[str, Any]:
    """The assignment as the page should see it, rendered from stored markup.

    The raw HTML is course-authored and the dashboard renders the text version,
    so the markup never leaves the server. It is kept in the cache rather than
    thrown away at fetch time, though, so a change to how course text is
    presented reaches the page on its next load instead of waiting six hours for
    the entry to go stale. An entry cached before that fell back on the text the
    agent tool produced, which puts bare URLs where the anchors were.
    """
    out = {k: v for k, v in detail.items() if k != "instructions_html"}
    markup = detail.get("instructions_html")
    out["instructions"] = sync.strip_attachment_links(
        html_to_text(markup, links="markdown") if markup
        else detail.get("instructions"),
        [a.get("filename") for a in detail.get("attachments") or []],
    )
    return out


@app.get("/api/assignments/{course_id}/{content_id}")
async def assignment(course_id: str, content_id: str,
                     refresh: bool = False) -> dict[str, Any]:
    """One assignment's instructions and its list of attached files."""
    key = f"assignment_{course_id}_{content_id}"
    stored = cache.get_data(key) or {}
    # An entry written under an older shape is fresh but wrong — missing a field
    # it should have, or carrying one whose meaning has since narrowed. Waiting
    # out its TTL would serve that for hours, so the shape is versioned and a
    # mismatch re-fetches. Bump DETAIL_SCHEMA whenever the shape changes.
    if not refresh and cache.fresh(key, "content") and stored.get("schema") == DETAIL_SCHEMA:
        return {**_present(stored), "cached": True}

    from blackboard_mcp.server import get_assignment
    try:
        detail = await get_assignment(course_id, content_id)
    except Exception as e:
        raise HTTPException(502, f"Blackboard would not return this assignment: {e}") from e

    cache.write(key, {**detail, "schema": DETAIL_SCHEMA})
    return {**_present(detail), "cached": False}


@app.post("/api/assignments/{course_id}/{content_id}/files")
async def download_assignment_files_route(course_id: str, content_id: str) -> dict[str, Any]:
    """Download the assignment's handouts, and say where to fetch each one."""
    from blackboard_mcp.server import download_assignment_files
    try:
        result = await download_assignment_files(course_id, content_id,
                                                 dest_dir=str(DOWNLOADS))
    except Exception as e:
        raise HTTPException(502, f"Download failed: {e}") from e

    for saved in result.get("downloaded", []):
        try:
            rel = Path(saved["path"]).resolve().relative_to(DOWNLOADS)
        except ValueError:  # saved outside the root we serve; offer the path only
            continue
        path = "/".join(quote(part) for part in rel.parts)
        saved["url"] = "/api/files/" + path
        # The same bytes, but servable to an <iframe> or a fetch() rather than
        # straight to the downloads folder.
        saved["view"] = "/api/view/" + path
    return result


def _downloaded(relpath: str) -> Path:
    """Resolve a path inside the downloads directory, and nowhere else."""
    target = (DOWNLOADS / relpath).resolve()
    if not target.is_relative_to(DOWNLOADS) or not target.is_file():
        raise HTTPException(404, "No such downloaded file.")
    return target


@app.get("/api/files/{relpath:path}")
async def saved_file(relpath: str) -> FileResponse:
    """Serve a file we already downloaded, and nothing outside that directory."""
    target = _downloaded(relpath)
    return FileResponse(target, filename=target.name)


# Markup a course wrote is text to look at, not a page to run: served as its own
# type it would execute in this app's origin, one click from the session cookie.
AS_TEXT = {".html", ".htm", ".xhtml", ".svg", ".xml", ".xsl"}


@app.get("/api/view/{relpath:path}")
async def view_file(relpath: str) -> FileResponse:
    """The same file, inline — what the in-page viewer draws from.

    `saved_file` answers with `Content-Disposition: attachment`, which is right
    when you asked for the file and wrong when you asked to read it: a browser
    hands an attachment to the downloads folder instead of rendering it. This is
    the same bytes with the disposition and the media type a viewer needs.
    """
    target = _downloaded(relpath)
    suffix = target.suffix.lower()
    media = "text/plain; charset=utf-8" if suffix in AS_TEXT else (
        guess_type(target.name)[0] or "application/octet-stream")
    return FileResponse(
        target,
        media_type=media,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(target.name)}",
            # The media type above is deliberate; do not let a sniffer override it.
            "X-Content-Type-Options": "nosniff",
        },
    )


def _find_downloaded(filename: str) -> Path | None:
    """Locate a handout we already saved, without trusting the name as a glob."""
    wanted = Path(filename or "").name
    if not wanted:
        return None
    for path in DOWNLOADS.rglob("*"):
        if path.is_file() and path.name == wanted:
            return path
    return None


@app.get("/api/google/status")
async def google_status() -> dict[str, Any]:
    """Whether the Docs button can work yet, and what is missing if not."""
    browser = gdocs.browser_command()
    client_id = (os.environ.get("BB_GOOGLE_CLIENT_ID") or "").strip()
    return {
        "configured": gdocs.configured(),
        "authorised": gdocs.authorised(),
        "browser": Path(browser[0]).name if browser else None,
        # The id is public — it travels in the consent URL — so showing it back
        # is how you tell which client the app is holding.
        "client_id": client_id or None,
        # Which files the Docs button can honestly offer. Drive converts these
        # into an editable document; anything else would upload and open in the
        # Drive viewer, which is not what the button says it does.
        "convertible": sorted(gdocs.CONVERTIBLE),
        **gdocs.flow_state(),
    }


class GoogleCredentialsBody(BaseModel):
    # Either the whole client_secret_*.json, or the two fields typed in by hand.
    client_json: str | None = None
    client_id: str | None = None
    client_secret: str | None = None


@app.post("/api/google/credentials")
async def google_credentials(body: GoogleCredentialsBody) -> dict[str, Any]:
    """Store the OAuth client the consent flow will identify itself with."""
    if body.client_json:
        try:
            client_id, secret = gdocs.parse_client_json(body.client_json)
        except gdocs.GoogleError as e:
            raise HTTPException(400, str(e)) from e
    else:
        client_id = (body.client_id or "").strip()
        secret = (body.client_secret or "").strip()
        if not client_id or not secret:
            raise HTTPException(400, "Both the client ID and the secret are needed.")
        if not client_id.endswith(".apps.googleusercontent.com"):
            raise HTTPException(
                400,
                "That does not look like a Google client ID — it should end in "
                ".apps.googleusercontent.com",
            )

    # New credentials describe a different app, so any consent granted to the
    # old one is meaningless.
    await asyncio.to_thread(gdocs.disconnect)
    _set_env_values({
        "BB_GOOGLE_CLIENT_ID": client_id,
        "BB_GOOGLE_CLIENT_SECRET": secret,
    })
    return await google_status()


@app.delete("/api/google/credentials")
async def google_forget() -> dict[str, Any]:
    """Drop the OAuth client and the authorisation that went with it."""
    await asyncio.to_thread(gdocs.disconnect)
    _set_env_values({"BB_GOOGLE_CLIENT_ID": "", "BB_GOOGLE_CLIENT_SECRET": ""})
    return await google_status()


@app.post("/api/google/authorise")
async def google_authorise() -> dict[str, Any]:
    """Begin consent and hand back the URL for the browser to open itself."""
    try:
        url = await asyncio.to_thread(gdocs.begin)
    except gdocs.GoogleError as e:
        raise HTTPException(503, str(e)) from e
    return {"url": url, **await google_status()}


@app.post("/api/google/disconnect")
async def google_disconnect() -> dict[str, Any]:
    """Forget the authorisation but keep the client, so reconnecting is one click."""
    await asyncio.to_thread(gdocs.disconnect)
    return await google_status()


class DocsBody(BaseModel):
    filename: str


@app.post("/api/assignments/{course_id}/{content_id}/gdocs")
async def open_in_docs(course_id: str, content_id: str,
                       body: DocsBody) -> dict[str, Any]:
    """Download the handout if needed, upload it as a Google Doc, and open it."""
    if not gdocs.configured():
        raise HTTPException(503, gdocs._MISSING_CREDENTIALS)
    if not gdocs.authorised():
        raise HTTPException(428, "Not connected to Google yet.")

    path = _find_downloaded(body.filename)
    if path is None:
        # Not fetched yet — get it, then look again.
        try:
            await download_assignment_files_route(course_id, content_id)
        except HTTPException:
            raise
        path = _find_downloaded(body.filename)
    if path is None:
        raise HTTPException(404, f"{body.filename} is not among this assignment's files.")

    try:
        uploaded = await asyncio.to_thread(gdocs.upload_as_doc, path)
        opened = await asyncio.to_thread(gdocs.open_in_browser, uploaded["url"])
    except gdocs.GoogleError as e:
        raise HTTPException(502, str(e)) from e
    return {**uploaded, "opened_in": opened}


class AssignmentEditBody(BaseModel):
    title: str | None = None
    due_utc: str | None = None
    points_possible: float | None = None
    hidden: bool | None = None
    note: str | None = None


class WeightsEditBody(BaseModel):
    weights: dict[str, float]


@app.get("/api/edits")
async def get_edits() -> dict[str, Any]:
    """Every local correction on file, and what it applies to."""
    return E.load(cache)


@app.put("/api/edits/assignments/{key}")
async def put_assignment_edit(key: str, body: AssignmentEditBody) -> dict[str, Any]:
    """Correct one assignment. Fields left out keep whatever they already were."""
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(400, "Nothing to change.")
    if "due_utc" in patch and patch["due_utc"]:
        if E._reparse(patch["due_utc"]) is None:
            raise HTTPException(400, f"{patch['due_utc']!r} is not a date I can read.")
    return E.set_assignment(cache, key, patch)


@app.delete("/api/edits/assignments/{key}")
async def delete_assignment_edit(key: str) -> dict[str, Any]:
    """Hand this assignment back to whatever Blackboard says about it."""
    return E.clear_assignment(cache, key)


@app.put("/api/edits/weights/{course_id}")
async def put_weights_edit(course_id: str, body: WeightsEditBody) -> dict[str, Any]:
    """Override the weighting on a course's syllabus components."""
    for label, pct in body.weights.items():
        if not 0 <= pct <= 100:
            raise HTTPException(400, f"{label!r}: {pct} is not a percentage.")
    E.set_weights(cache, course_id, body.weights)
    return await sync.course_standing(cache, course_id)


@app.delete("/api/edits/weights/{course_id}")
async def delete_weights_edit(course_id: str) -> dict[str, Any]:
    """Go back to the weighting read off the syllabus."""
    E.clear_weights(cache, course_id)
    return await sync.course_standing(cache, course_id)


@app.post("/api/reset")
async def reset_data() -> dict[str, Any]:
    """Throw away everything fetched or derived, and every downloaded file.

    What survives is what it would be tedious and unpleasant to re-establish: the
    Blackboard login (`.env`, the session cookie, the remembered browser profile)
    and the Google Docs connection (client credentials and the token). Everything
    else — the cached courses, deadlines, announcements, content trees, grade
    weightings, the local corrections, which announcements have been popped up,
    and the handouts on disk — is re-fetchable, so it goes.
    """
    cache_files = [p for p in cache.root.iterdir() if p.is_file()]
    cache_freed = _measure(cache_files)
    removed_cache = 0
    for path in cache_files:
        try:
            path.unlink()
            removed_cache += 1
        except OSError:
            pass
    removed_files, files_freed = _wipe_downloads()
    return {
        "cache_entries": removed_cache,
        "files": removed_files,
        "bytes": cache_freed + files_freed,
        "kept": ["Blackboard login", "Google Docs connection"],
    }


@app.get("/api/health")
async def health() -> dict[str, Any]:
    from blackboard_mcp.server import check_connection, session_status
    return {"blackboard": await check_connection(),
            "session": await session_status(),
            "llm_available": llm.available(),
            "llm_backend": llm.backend(),
            "llm_model": llm.model(),
            "version": __version__,
            "state_dir": str(paths.state_dir()),
            "cache_keys": cache.keys()}


if FRONTEND.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str) -> FileResponse:
        candidate = FRONTEND / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND / "index.html")

else:
    # A packaging mistake looks exactly like this: every API route answers and
    # every page is blank. Cheaper to say it here than to find it in a browser.
    import sys as _sys
    print(f"warning: no built frontend at {FRONTEND} — serving the API only. "
          "Run `npm run build` in src/blackboard_web/frontend.",
          file=_sys.stderr, flush=True)


def main() -> None:
    """Serve the dashboard.

    The chosen port is announced on stdout before anything is served. The desktop
    shell spawns this as a child process and has no other way to learn which port
    `--port 0` landed on, and picking one for it would only move the clash.
    """
    import argparse
    import socket
    import sys
    import uvicorn

    parser = argparse.ArgumentParser(
        prog="blackboard-web", description="Serve the Whiteboard dashboard.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="interface to bind (default: loopback only)")
    parser.add_argument("--port", type=int, default=8765,
                        help="port to serve on; 0 asks the OS for a free one")
    parser.add_argument("--open", action="store_true",
                        help="open the dashboard in the default browser once it is up")
    parser.add_argument("--exit-on-stdin-close", action="store_true",
                        help="shut down when stdin closes (how the desktop shell "
                             "makes sure the server dies with it)")
    args = parser.parse_args()

    # Bound here rather than inside uvicorn so the port is known before the first
    # request is served: the shell is already waiting to read it.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((args.host, args.port))
    except OSError as e:
        raise SystemExit(f"cannot bind {args.host}:{args.port}: {e}") from e
    sock.listen(128)
    port = sock.getsockname()[1]
    url = f"http://{args.host}:{port}"

    # The shell parses this line to find the port. Keep the prefix stable.
    print(f"BLACKBOARD_PORT={port}", flush=True)
    print(f"Whiteboard serving on {url}", file=sys.stderr, flush=True)

    if args.open:
        import threading
        import webbrowser
        # After a beat, so the browser does not race the first bind.
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=port))

    if args.exit_on_stdin_close:
        import threading

        def watch_parent() -> None:
            # The shell holds the write end of this pipe and never writes to it.
            # Reaching EOF means the shell is gone however it went — closed,
            # killed, or crashed — and a server still holding a signed-in
            # session with no window attached is exactly what not to leave
            # behind. Killing the child from the parent only covers the polite
            # exit; this covers the rest.
            try:
                sys.stdin.buffer.read()
            except Exception:
                pass
            server.should_exit = True

        threading.Thread(target=watch_parent, daemon=True).start()

    server.run(sockets=[sock])
