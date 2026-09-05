"""Headless SSO login that refreshes the Blackboard session cookie.

The cookie in `.env` is only ever a seed, and refreshing it by hand means
logging in again and digging through DevTools. This drives a real Chromium
through the same login instead — headless, so no window ever appears — fills the
identity provider's form from `.env`, waits while the Duo push is approved on
the phone, and writes the resulting BbRouter value back into `.env` and
`.bb_session.json`.

The push still has to be accepted by a human: this automates the tedious half of
the login, not the second factor.

The browser profile is kept in `.bb_browser/` so that whatever the identity
provider remembers about the device — "trust this browser" in particular —
survives to the next run, and a later refresh can often skip the push entirely.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from dotenv import load_dotenv
from playwright.sync_api import BrowserContext, Locator, Page, sync_playwright

from . import paths
from .client import normalize_host
from .session import SessionStore, seconds_remaining

ROOT = paths.state_dir()
ENV_PATH = ROOT / ".env"
PROFILE_DIR = ROOT / ".bb_browser"

# Identity providers serve a degraded page to anything they cannot identify as a
# real desktop browser, and Duo's prompt refuses outright, so headless Chromium
# has to introduce itself as the browser it actually is.
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

ME = "/learn/api/public/v1/users/me"
POLL_SECONDS = 1.0
DEFAULT_TIMEOUT = 300.0  # the clock is mostly spent waiting on a human's phone

# How long to let a submitted page settle before reading anything into what is
# on screen. Long enough for a form post to navigate, short enough that a
# rejected password is reported in seconds rather than after a retry cycle.
SETTLE_SECONDS = 4.0
# Three submit methods, tried twice round, before giving up on the form itself.
MAX_SUBMIT_ATTEMPTS = 6

PASSWORD_SEL = "input[type=password]"
USER_SEL = ("input[name='Ecom_User_ID'], input[name='loginfmt'], input#username, "
            "input[type=email], input[type=text]:not([type=hidden])")
SUBMIT_SEL = ("input[type=submit], button[type=submit], #loginButton2, "
              "button:has-text('Log In'), button:has-text('Sign in'), "
              "span[id*='loginButton'], [id*='loginButton'], "
              "[name*='loginButton'], [role=button]")
TRUST_SEL = ("#trust-browser-button, button:has-text('Yes, this is my device'), "
             "button:has-text('Trust browser'), #idSIButton9")
PUSH_SEL = ("button:has-text('Send Me a Push'), button:has-text('Send a Push'), "
            "[data-testid='test-id-push-button']")


class LoginError(RuntimeError):
    """A login that did not finish, and why.

    The reason is what lets a caller tell the two failures apart that need
    different things from the user: credentials the identity provider refused
    (ask again) and everything else (try again). The message stays human.
    """

    def __init__(self, message: str, reason: str = "failed") -> None:
        super().__init__(message)
        self.reason = reason


# What the login is doing right now, for a caller that wants to say so. The Duo
# wait is the whole point: it is the one stage that takes minutes and needs the
# person to go and do something.
STAGES = ("opening", "credentials", "duo", "trusting", "signed_in")


# --- page helpers -----------------------------------------------------------

def _visible(page: Page, selector: str) -> Locator | None:
    """The first genuinely visible match, or None.

    Login pages keep every step's markup in the DOM and hide all but the current
    one, so matching a selector says nothing about what the user is being shown.
    """
    loc = page.locator(selector)
    try:
        count = min(loc.count(), 8)
    except Exception:
        return None
    for i in range(count):
        item = loc.nth(i)
        try:
            if item.is_visible():
                return item
        except Exception:
            continue
    return None


def _click(target: Locator) -> bool:
    try:
        target.click(timeout=5000)
        return True
    except Exception:
        return False


def _page_text(page: Page) -> str:
    try:
        return page.inner_text("body")[:4000].lower()
    except Exception:
        return ""


def entry_url(page: Page, origin: str) -> str:
    """The institution's SAML entry point, read off Blackboard's own front page.

    The `apId` in that link identifies the institution, so following the link the
    page itself offers keeps this working across Blackboard sites — and across
    the day the id changes — instead of pinning one campus's magic number.
    """
    fallback = f"{origin}/auth-saml/saml/login"
    try:
        page.goto(f"{origin}/", wait_until="domcontentloaded", timeout=60_000)
        link = page.locator("a[href*='/auth-saml/saml/login']").first
        href = link.get_attribute("href", timeout=10_000)
    except Exception:
        return fallback
    return urljoin(origin, href) if href else fallback


def authenticated(ctx: BrowserContext, origin: str) -> dict[str, Any] | None:
    """Who the context's cookies belong to, or None if they belong to nobody.

    Blackboard issues a BbRouter to anonymous visitors too, so the cookie being
    present proves nothing at all. Asking the API who we are is the only test
    that distinguishes a logged-in session from a browser that merely loaded the
    login page.
    """
    try:
        response = ctx.request.get(origin + ME, timeout=20_000)
        if response.status != 200:
            return None
        return response.json()
    except Exception:
        return None


def bbrouter(ctx: BrowserContext, origin: str) -> str | None:
    host = urlsplit(origin).hostname or ""
    for cookie in ctx.cookies():
        domain = cookie.get("domain", "").lstrip(".")
        if cookie.get("name") == "BbRouter" and domain and host.endswith(domain):
            return cookie.get("value")
    return None


# --- the login itself -------------------------------------------------------

def _submit_credentials(page: Page, username: str, password: str,
                        attempt: int = 0) -> bool:
    """Fill whatever credential form is on screen and submit it."""
    pw = _visible(page, PASSWORD_SEL)
    if pw is None:
        return False
    user = _visible(page, USER_SEL)
    if user is not None:
        try:
            user.fill(username, timeout=5000)
        except Exception:
            pass
    try:
        pw.fill(password, timeout=5000)
    except Exception:
        return False
    methods = attempt % 3
    if methods == 0:
        submit = _visible(page, SUBMIT_SEL)
        if submit is not None and _click(submit):
            return True
    if methods == 1:
        try:
            pw.press("Enter")
            return True
        except Exception:
            pass

    # Some SSO pages draw their submit control as a span, and others bind
    # validation to requestSubmit rather than a raw form.submit call.
    try:
        page.eval_on_selector(
            "form:has(input[type=password])",
            """f => {
                if (f.requestSubmit) {
                    f.requestSubmit();
                } else {
                    f.submit();
                }
            }""",
        )
        return True
    except Exception:
        pass

    submit = _visible(page, SUBMIT_SEL)
    return bool(submit is not None and _click(submit))


# What a provider says when it refuses. Every one of these still has to appear
# next to a word about credentials — a login page's own help text ("Trouble
# logging in?") is full of them before anything has gone wrong.
REJECTED_WORDS = (
    "incorrect", "invalid", "not recognized", "not recognised",
    "authentication failed", "unable to verify", "did not match",
    "does not match", "unsuccessful", "failed to authenticate",
)
CREDENTIAL_WORDS = ("password", "credential", "user id", "username", "user name")


def _rejected_text(text: str) -> bool:
    return (any(w in text for w in REJECTED_WORDS)
            and any(w in text for w in CREDENTIAL_WORDS))


def _still_typed(field: Locator, password: str) -> bool:
    """Is our password still sitting in the box?

    This is what tells a form that was never submitted apart from one the
    provider handed straight back. A click that missed leaves the field exactly
    as we filled it; a page that came back from the server — which is what a
    refusal looks like when nothing on it says so — comes back empty.
    """
    try:
        return field.input_value(timeout=2000) == password
    except Exception:
        return False


def _looks_like_duo(page: Page, text: str) -> bool:
    url = page.url.lower()
    if "duosecurity.com" in url or "/duo" in url:
        return True
    return "duo" in text and ("push" in text or "verify" in text or "device" in text)


def run_login(*, headless: bool = True, timeout: float = DEFAULT_TIMEOUT,
              force: bool = False, debug: bool = False,
              on_stage: Any = None) -> dict[str, Any]:
    """Sign in and return the fresh cookie plus who it belongs to.

    `on_stage` is called with one of `STAGES` as the login moves through them,
    so a caller driving this from a UI can say what it is waiting for instead of
    showing an unexplained spinner for two minutes.
    """
    def stage(name: str) -> None:
        if on_stage:
            try:
                on_stage(name)
            except Exception:
                pass

    load_dotenv(ENV_PATH)
    origin = normalize_host(os.environ.get("BB_HOST", ""))
    username = (os.environ.get("BB_USERNAME") or "").strip()
    password = os.environ.get("BB_PASSWORD") or ""
    if not username or not password:
        raise LoginError(
            "Set BB_USERNAME and BB_PASSWORD in .env — this needs your NDUS "
            "credentials to fill the login form.",
            reason="credentials",
        )

    debug_dir = PROFILE_DIR / "debug"
    if debug:
        debug_dir.mkdir(parents=True, exist_ok=True)

    def shot(name: str, page: Page) -> None:
        if debug:
            try:
                page.screenshot(path=str(debug_dir / f"{name}.png"), full_page=True)
                print(f"    [debug] {page.url}  ->  {debug_dir / (name + '.png')}")
            except Exception:
                pass

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=headless,
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(20_000)

            who = None if force else authenticated(ctx, origin)
            if who is None:
                stage("opening")
                target = entry_url(page, origin)
                print("→ opening the sign-in page (headless, nothing will appear)")
                page.goto(target, wait_until="domcontentloaded", timeout=60_000)
                who = _drive(ctx, page, origin, username, password,
                             timeout=timeout, shot=shot, stage=stage)
            else:
                print("→ the saved browser profile is still signed in")
            stage("signed_in")

            cookie = bbrouter(ctx, origin)
            if not cookie:
                raise LoginError(
                    "Signed in, but no BbRouter cookie came back — the site may "
                    "have changed how it issues sessions.",
                    reason="no_cookie",
                )
            shot("done", page)
            return {"cookie": f"BbRouter={cookie}", "user": who, "origin": origin}
        finally:
            ctx.close()


def _drive(ctx: BrowserContext, page: Page, origin: str, username: str,
           password: str, *, timeout: float, shot: Any,
           stage: Any = lambda _name: None) -> dict[str, Any]:
    """Poll the page and react to whichever login step it is showing.

    Written as a state machine rather than a fixed script because an SSO chain
    reorders itself: the password page may be skipped for a remembered session,
    Duo may or may not offer the trust prompt, and the provider bounces through
    auto-submitting forms in between. Reacting to what is on screen survives all
    of that, where a step-by-step script breaks on the first variation.
    """
    host = urlsplit(origin).hostname or ""
    deadline = time.monotonic() + timeout
    submitted = False
    submitted_at = 0.0
    credential_attempts = 0
    cleared_at: float | None = None
    announced = False
    last_url = ""
    step = 0

    while time.monotonic() < deadline:
        try:
            url = page.url
        except Exception:
            url = ""
        if url != last_url:
            step += 1
            shot(f"stage-{step}", page)
            last_url = url

        # Only ask the API once we are back on Blackboard: while the provider
        # holds the session there is nothing to ask about, and polling it every
        # second through a two-minute Duo wait is pure noise.
        if host and host in url:
            who = authenticated(ctx, origin)
            if who is not None:
                return who

        text = _page_text(page)

        password_field = _visible(page, PASSWORD_SEL)

        def send_credentials(first: bool) -> None:
            nonlocal submitted, submitted_at, credential_attempts
            print("→ filling in your credentials" if first
                  else "→ the form did not submit; trying another method")
            if first:
                stage("credentials")
            submitted = _submit_credentials(
                page, username, password, attempt=credential_attempts)
            credential_attempts += 1
            if submitted:
                submitted_at = time.monotonic()
                page.wait_for_timeout(1500)

        if password_field is not None and not submitted:
            send_credentials(first=True)
            continue

        # A password form on screen after a submission is one of two things,
        # and the box itself says which.
        if (password_field is not None and submitted
                and time.monotonic() - submitted_at > SETTLE_SECONDS):
            if _still_typed(password_field, password):
                if credential_attempts >= MAX_SUBMIT_ATTEMPTS:
                    raise LoginError(
                        "The sign-in form would not submit — the provider's "
                        "login page may have changed.",
                        reason="failed",
                    )
                send_credentials(first=False)
                continue
            # Empty: this form came back from the server. Nothing else hands a
            # cleared password box back, so it is a refusal whether or not the
            # page says so — and saying it now beats re-posting a password that
            # some providers count towards a lockout.
            #
            # Confirmed a second time a beat later, because the one thing that
            # would also look like this is a page that empties the box while it
            # works and then signs in. Being slower to accuse is worth a second:
            # telling someone their password is wrong when it was right is the
            # one wrong answer here that costs them anything.
            if cleared_at is None:
                cleared_at = time.monotonic()
            elif time.monotonic() - cleared_at >= 1.0:
                raise LoginError(
                    "That username or password was not accepted.",
                    reason="credentials",
                )
            page.wait_for_timeout(int(POLL_SECONDS * 1000))
            continue

        cleared_at = None

        if _looks_like_duo(page, text):
            if not announced:
                print("→ Duo sent a push — approve it on your phone (waiting…)")
                stage("duo")
                announced = True
            push = _visible(page, PUSH_SEL)
            if push is not None:
                _click(push)
            trust = _visible(page, TRUST_SEL)
            if trust is not None and _click(trust):
                # Accepting the trust prompt is what lets the saved profile skip
                # the push next time.
                print("→ trusting this browser for next time")
                stage("trusting")
                page.wait_for_timeout(2000)
                continue

        # The provider saying so outright, which can land before the form comes
        # back — an error page with no password box on it at all.
        if submitted and _rejected_text(text):
            raise LoginError(
                "That username or password was not accepted.",
                reason="credentials",
            )

        page.wait_for_timeout(int(POLL_SECONDS * 1000))

    raise LoginError(
        f"Gave up after {int(timeout)}s without reaching a signed-in session. "
        "Re-run with --debug (and --headed) to see where it stalled.",
        reason="timeout",
    )


# --- writing the result back ------------------------------------------------

def write_env_cookie(cookie: str, path: Path = ENV_PATH) -> None:
    """Replace BB_COOKIE in .env, leaving every other line exactly as it was.

    Written through a temp file so an interrupted write cannot leave a truncated
    cookie — or a truncated .env — behind.
    """
    try:
        existing = path.read_text().splitlines()
    except OSError:
        existing = []
    line = f"BB_COOKIE={cookie}"
    for i, current in enumerate(existing):
        if current.strip().startswith("BB_COOKIE="):
            existing[i] = line
            break
    else:
        existing.append(line)
    body = "\n".join(existing) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.")
    try:
        os.write(fd, body.encode())
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _masked(cookie: str) -> str:
    """Enough of the cookie to recognise it, not enough to reuse it."""
    tail = cookie.split("id:", 1)
    if len(tail) == 2:
        return f"BbRouter=…id:{tail[1][:6]}…"
    return cookie[:18] + "…"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="blackboard-login",
        description="Refresh BB_COOKIE by signing in through a headless browser.")
    parser.add_argument("--headed", action="store_true",
                        help="show the browser window (for debugging only)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="seconds to wait for the whole login, Duo included")
    parser.add_argument("--force", action="store_true",
                        help="log in again even if the saved profile still works")
    parser.add_argument("--debug", action="store_true",
                        help="save a screenshot of each step under .bb_browser/debug")
    args = parser.parse_args()

    try:
        result = run_login(headless=not args.headed, timeout=args.timeout,
                           force=args.force, debug=args.debug)
    except LoginError as e:
        print(f"✗ {e}", file=sys.stderr)
        raise SystemExit(1) from e

    cookie = result["cookie"]
    write_env_cookie(cookie)
    SessionStore().save(cookie, result["origin"])

    who = result.get("user") or {}
    name = who.get("userName") or who.get("id") or "your account"
    left = seconds_remaining(cookie)
    hours = f"{left / 3600:.1f}h" if left else "unknown"
    print(f"✓ signed in as {name}")
    print(f"  {_masked(cookie)}  (idle timeout in {hours})")
    print("  written to .env and .bb_session.json")


if __name__ == "__main__":
    main()
