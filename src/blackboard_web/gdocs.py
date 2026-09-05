"""Upload a handout to Google Drive as a Google Doc, and open it in a browser.

Google will not accept a file without OAuth, and there is no way around that, so
this does the loopback flow for installed apps: `begin()` hands back a consent
URL for the dashboard to open in the tab the user is already looking at, the
redirect is caught on 127.0.0.1, and the refresh token is kept in
`.bb_google.json` so the consent screen is a one-time thing.

Google has no API for minting an OAuth client, so the client id and secret still
have to come from a human in the Cloud console. `parse_client_json` takes the
file that console hands out, which is the closest this gets to painless.

The scope is `drive.file`, the narrowest one that works — it grants access only
to files this app itself creates. It cannot read anything already in your Drive.
"""

from __future__ import annotations

import http.server
import json
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

from blackboard_mcp import paths

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
SCOPE = "https://www.googleapis.com/auth/drive.file"
DOC_MIME = "application/vnd.google-apps.document"

# What Drive will convert into a Google Doc. Anything else uploads as-is.
CONVERTIBLE = {".docx", ".doc", ".odt", ".rtf", ".txt", ".html", ".htm", ".pdf"}


class GoogleError(RuntimeError):
    pass


def _store() -> Path:
    return paths.state_file(".bb_google.json", "BB_GOOGLE_TOKEN_FILE")


_MISSING_CREDENTIALS = (
    "Google is not set up yet. Open Settings and follow the Google Docs steps "
    "to create an OAuth client and connect your account."
)


def credentials() -> tuple[str, str]:
    cid = (os.environ.get("BB_GOOGLE_CLIENT_ID") or "").strip()
    secret = (os.environ.get("BB_GOOGLE_CLIENT_SECRET") or "").strip()
    if not cid or not secret:
        raise GoogleError(_MISSING_CREDENTIALS)
    return cid, secret


def configured() -> bool:
    try:
        credentials()
    except GoogleError:
        return False
    return True


def authorised() -> bool:
    return bool(_read_tokens().get("refresh_token"))


def _read_tokens() -> dict[str, Any]:
    try:
        return json.loads(_store().read_text())
    except (OSError, ValueError):
        return {}


def _save_tokens(data: dict[str, Any]) -> None:
    path = _store()
    path.write_text(json.dumps(data, indent=2))
    try:
        path.chmod(0o600)  # it is a credential, not a config file
    except OSError:
        pass


def browser_command() -> list[str] | None:
    """The browser to open results in — LibreWolf when it is installed."""
    override = os.environ.get("BB_BROWSER")
    if override:
        return override.split()
    for name in ("librewolf", "firefox", "xdg-open"):
        found = shutil.which(name)
        if found:
            return [found]
    return None


def open_in_browser(url: str) -> str | None:
    """Launch the browser on `url`. Returns the command used, or None."""
    cmd = browser_command()
    if not cmd:
        return None
    try:
        subprocess.Popen([*cmd, url], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        raise GoogleError(f"Could not launch {cmd[0]}: {e}") from e
    return cmd[0]


# ------------------------------------------------------- client credentials


def parse_client_json(text: str) -> tuple[str, str]:
    """Pull the id and secret out of the client_secret_*.json Google hands you.

    Retyping two 70-character strings is where this setup usually goes wrong, so
    the file Google downloads at the end of the console flow is accepted whole.
    """
    try:
        payload = json.loads(text)
    except ValueError as e:
        raise GoogleError(
            "That is not a JSON file — download the client secret from the "
            "credentials page and upload that."
        ) from e
    if not isinstance(payload, dict):
        raise GoogleError("That JSON is not a Google client secret file.")

    # A web client only accepts redirect URIs registered up front, and this
    # flow redirects to a loopback port picked at runtime. Saying so now beats
    # a redirect_uri_mismatch on the consent screen.
    if "web" in payload and "installed" not in payload:
        raise GoogleError(
            "That is a Web application client. Create a Desktop app client "
            "instead — this signs in over a loopback redirect."
        )

    block = payload.get("installed") or payload
    client_id = str(block.get("client_id") or "").strip()
    secret = str(block.get("client_secret") or "").strip()
    if not client_id or not secret:
        raise GoogleError(
            "No client_id and client_secret in that file — it may be the wrong "
            "download."
        )
    return client_id, secret


# ------------------------------------------------------------------- oauth


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# The consent flow spans two requests — one to start it, one from Google's
# redirect — so its progress lives here rather than on a call stack. Only one
# attempt is ever in flight; starting a second abandons the first.
_flow_lock = threading.Lock()
_flow: dict[str, Any] = {"pending": False, "error": None, "started": 0.0}
_server: http.server.HTTPServer | None = None

_PAGE_OK = (b"<!doctype html><meta charset=utf-8><title>Connected</title>"
            b"<h2>Connected.</h2><p>You can close this tab and go back to the "
            b"dashboard.</p>")
_PAGE_BAD = (b"<!doctype html><meta charset=utf-8><title>Not connected</title>"
             b"<h2>Authorisation failed.</h2><p>Close this tab: the dashboard "
             b"says what went wrong.</p>")


def flow_state() -> dict[str, Any]:
    """Whether a consent attempt is in flight, and why the last one failed."""
    with _flow_lock:
        return {"pending": _flow["pending"], "error": _flow["error"]}


def cancel() -> None:
    """Abandon an attempt in flight, so a second try is not refused a port."""
    global _server
    with _flow_lock:
        server, _server = _server, None
        _flow["pending"] = False
    if server:
        threading.Thread(target=server.shutdown, daemon=True).start()


def _exchange(code: str, redirect: str, client_id: str, secret: str) -> None:
    with httpx.Client(timeout=30) as client:
        res = client.post(TOKEN_URL, data={
            "code": code, "client_id": client_id, "client_secret": secret,
            "redirect_uri": redirect, "grant_type": "authorization_code",
        })
    if res.status_code != 200:
        raise GoogleError(f"Token exchange failed: {res.text[:200]}")
    payload = res.json()
    if not payload.get("refresh_token"):
        raise GoogleError("Google returned no refresh token; retry the consent step.")
    _save_tokens({"refresh_token": payload["refresh_token"]})


def begin(timeout: int = 600) -> str:
    """Start the consent flow and return the URL the browser should open.

    The redirect is caught by a one-shot server on a loopback port, which is the
    supported flow for a desktop client and keeps the secret off the network.
    The browser doing the opening is the user's own — this only hands back a URL.
    """
    global _server
    client_id, client_secret = credentials()
    cancel()

    port = _free_port()
    redirect = f"http://127.0.0.1:{port}"
    nonce = secrets.token_urlsafe(16)
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = {k: v[0] for k, v in urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query).items()}
            ok = query.get("state") == nonce and "code" in query
            error: str | None = None
            if ok:
                try:
                    _exchange(query["code"], redirect, client_id, client_secret)
                except GoogleError as e:
                    ok, error = False, str(e)
            elif query.get("state") not in (None, nonce):
                error = "The authorisation response did not match the request."
            else:
                error = ("Google refused authorisation: "
                         f"{query.get('error', 'no code returned')}")
            with _flow_lock:
                _flow["error"] = error
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_PAGE_OK if ok else _PAGE_BAD)
            done.set()

        def log_message(self, *_args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def close() -> None:
        global _server
        if not done.wait(timeout):
            with _flow_lock:
                if _flow["pending"]:
                    _flow["error"] = "Timed out waiting for Google authorisation."
        with _flow_lock:
            _flow["pending"] = False
            if _server is server:
                _server = None
        server.shutdown()

    threading.Thread(target=close, daemon=True).start()
    with _flow_lock:
        _server = server
        _flow.update({"pending": True, "error": None, "started": time.time()})

    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        # Without this an already-consented account returns no refresh token.
        "prompt": "consent",
        "state": nonce,
    })
    return f"{AUTH_URL}?{params}"


def disconnect() -> None:
    """Forget the stored authorisation, and tell Google to drop it too."""
    cancel()
    refresh = _read_tokens().get("refresh_token")
    _store().unlink(missing_ok=True)
    if refresh:
        try:  # best effort — the local token is gone either way
            with httpx.Client(timeout=15) as client:
                client.post(REVOKE_URL, data={"token": refresh})
        except httpx.HTTPError:
            pass


def _access_token() -> str:
    client_id, client_secret = credentials()
    refresh = _read_tokens().get("refresh_token")
    if not refresh:
        raise GoogleError("Not connected to Google yet.")
    with httpx.Client(timeout=30) as client:
        res = client.post(TOKEN_URL, data={
            "refresh_token": refresh, "client_id": client_id,
            "client_secret": client_secret, "grant_type": "refresh_token",
        })
    if res.status_code != 200:
        raise GoogleError(
            "Google rejected the stored authorisation — reconnect. "
            f"({res.status_code})"
        )
    token = res.json().get("access_token")
    if not token:
        raise GoogleError("Google returned no access token.")
    return str(token)


# ------------------------------------------------------------------ upload


def doc_url(file_id: str, mime: str | None = None) -> str:
    """Where to open an uploaded file — the Docs editor when it converted."""
    if mime == DOC_MIME or mime is None:
        return f"https://docs.google.com/document/d/{file_id}/edit"
    return f"https://drive.google.com/file/d/{file_id}/view"


def upload_as_doc(path: Path, name: str | None = None) -> dict[str, Any]:
    """Upload one file, converting it to a Google Doc when Drive can.

    A format Drive cannot convert (a .zip of starter code, say) still uploads —
    it just opens in the Drive viewer instead of the Docs editor.
    """
    if not path.is_file():
        raise GoogleError(f"{path.name} is not on disk to upload.")
    convert = path.suffix.lower() in CONVERTIBLE
    metadata: dict[str, Any] = {"name": name or path.stem}
    if convert:
        metadata["mimeType"] = DOC_MIME

    token = _access_token()
    with httpx.Client(timeout=120) as client:
        res = client.post(
            UPLOAD_URL,
            params={"uploadType": "multipart", "fields": "id,mimeType,name,webViewLink"},
            headers={"Authorization": f"Bearer {token}"},
            files={
                "metadata": (None, json.dumps(metadata), "application/json"),
                "file": (path.name, path.read_bytes(),
                         "application/octet-stream"),
            },
        )
    if res.status_code not in (200, 201):
        raise GoogleError(f"Drive upload failed ({res.status_code}): {res.text[:200]}")
    payload = res.json()
    return {
        "id": payload.get("id"),
        "name": payload.get("name"),
        "mime_type": payload.get("mimeType"),
        "converted": payload.get("mimeType") == DOC_MIME,
        "url": doc_url(payload.get("id", ""), payload.get("mimeType")),
    }
