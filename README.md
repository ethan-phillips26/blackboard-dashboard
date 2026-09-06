# Whiteboard

A read-only dashboard over Blackboard Learn — what's due, where you stand in each
course, and a calendar feed you can subscribe to. Ships as a desktop app for
Windows, macOS and Linux, and as an MCP server so an agent can answer "what's due
this week" against the same data.

Unofficial, and not affiliated with Anthology Inc.

**Everything is read-only.** The Blackboard client only ever issues `GET`. Nothing
here submits, posts, or changes anything on the university's side.

## How it works

Two front ends sit on one Blackboard client:

```
blackboard_mcp/        the Blackboard layer
  client.py            async REST client; GET only; cookie auth
  server.py            8 MCP tools over that client
  session.py           cookie persistence, expiry maths, .env writes
  paths.py             the one place that decides where state lives

blackboard_web/        the dashboard
  app.py               HTTP routes
  sync.py              fetch → cache → serve
  cache.py             JSON store on disk, per-kind TTLs
  grades.py            weighted grade maths (pure)
  frontend/            React + Vite

src-tauri/             the desktop shell (Rust, ~450 lines)
```

**Data flow.** Every read goes through a JSON cache on disk with per-kind TTLs, so
opening the app costs nothing and only an explicit refresh goes back to
Blackboard. The whole cache is a few hundred KB. Grade maths is pure and runs
locally — Blackboard supplies the structure and your scores, the syllabus supplies
the weighting, and the percentages are computed here.

**Authentication is a cookie.** Blackboard's OAuth2 route needs an Application ID
that your university's administrator has to whitelist, so this uses a session
cookie instead — the same one your browser holds.

**Signing in uses a real browser window.** The desktop shell opens your
institution's own login page in a webview and reads the session cookie out of it.
There is no form to recognise and no second factor to anticipate, so it works at
any university rather than only the ones somebody could test. Whatever your school
asks for — a push, a code, a passkey — happens in an ordinary browser window.

**The desktop app is a thin shell.** Tauri starts the Python server as a child
process, waits for it to answer, and points a window at it. The server is frozen
with PyInstaller and bundled as a resource. The shell holds an open stdin pipe the
server watches, so the server dies with the window however the window goes.

## Quick start

Requires Python 3.12, [uv](https://docs.astral.sh/uv/), and Node 22+.

```bash
uv sync
cd src/blackboard_web/frontend && npm ci && npm run build && cd -
uv run blackboard-web --open
```

That serves the dashboard on `http://127.0.0.1:8765`.

In a plain browser tab there is no window for the shell to open, so set a cookie
by hand the first time: log into Blackboard, open DevTools → Network, copy the
`Cookie` request header from any request to your Blackboard domain, and put it in
`.env` as `BB_COOKIE`, along with `BB_HOST`. Once the desktop app has signed in
once, both front ends share the cookie and this stops being necessary.

## Desktop app

```bash
npm ci
npm run app       # freeze the sidecar, then build the shell
npm run bundle    # installers under src-tauri/target/release/bundle/
```

**Order matters.** `tauri-build` copies the frozen sidecar into `target/` at Rust
build time, so freezing after cargo leaves the app running the previous server.
`npm run app` does both in the right order; don't run them separately.

Run it with an explicit state directory — a frozen binary has no `.env` beside it,
so without one you get an empty first-run:

```bash
BB_STATE_DIR=~/blackboard-state ./src-tauri/target/release/whiteboard
```

On Arch, AppImage bundling needs `NO_STRIP=true`: the bundled `strip` can't read
the `.relr.dyn` sections a current toolchain emits. CI builds on `ubuntu-22.04`,
where it doesn't arise.

## MCP server

```bash
claude mcp add blackboard -- uv --directory /path/to/blackboard run blackboard-mcp
```

Eight tools: `check_connection`, `list_courses`, `list_due_dates`,
`get_assignment`, `download_assignment_files`, `browse_course_content`,
`list_announcements`, `session_status`. Start with `list_due_dates` — it returns a
`calendar_event` block per item, plus the `content_id` that
`download_assignment_files` needs.

The MCP server has no window, so it can't sign in on its own. It reads whatever
cookie the desktop app last wrote, or `BB_COOKIE`.

## Configuration

`.env` lives in the state directory (see below). `BB_HOST` and `BB_COOKIE` are the
only ones that matter; everything else has a working default.

| key | what it does |
| --- | --- |
| `BB_HOST` | your school's Blackboard hostname |
| `BB_COOKIE` | session cookie; the desktop app fills this in |
| `BB_STATE_DIR` | override where all state lives |
| `BB_KEEPALIVE_MINUTES` | how often to ping so the session doesn't idle out (default 45) |
| `BB_LLM` | `claude` or `ollama` — reads a syllabus for its grade weighting |
| `BB_GOOGLE_CLIENT_ID` / `_SECRET` | optional, for "Open in Docs" |

See `.env.example` for the rest.

## Where state lives

One directory, decided by `paths.py` — `.env`, the session cookie, the cache and
downloads all resolve through it, never against the working directory, because a
packaged build doesn't control that.

| | |
| --- | --- |
| Linux | `~/.local/share/blackboard` |
| macOS | `~/Library/Application Support/blackboard` |
| Windows | `%APPDATA%\blackboard` |

A source checkout with an `.env` already beside it keeps using itself.
`BB_STATE_DIR` overrides either.

## Tests

Standalone scripts, not pytest; each exits non-zero on failure.

```bash
uv run python tests/test_web.py         # cache, auth, syllabus weights
uv run python tests/test_grades.py      # weighted grade maths
uv run python tests/test_end_to_end.py  # every MCP tool against a stub server
```

`tests/stub_blackboard.py` is a fake Blackboard instance — tests never touch the
real one.

## Limitations

- Grade weightings come from the syllabus, which a model reads. Check them; a
  syllabus that states weights in prose is easy to get wrong.
- Blackboard Original and Ultra differ in what they expose. Links come from
  Blackboard's own `links[rel=alternate]`, never built by hand.
- Use this on your own account. It's your data, but automated access may still sit
  outside your institution's acceptable-use policy — worth a look before you lean
  on it.
