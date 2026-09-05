# Whiteboard — a read-only dashboard over Blackboard Learn

Two front ends over one Blackboard client: an **MCP server** (`blackboard_mcp`) so an
agent can answer "what's due this week", and a **web dashboard** (`blackboard_web`,
FastAPI + React) for a person. ~5k lines of Python, ~5.9k of frontend.

**Everything is read-only.** The client only ever issues `GET` to Blackboard. Nothing
here submits, posts, or changes anything on the university's side. Keep it that way.

## Commands

```bash
# from the repo root
uv sync                                  # install
.venv/bin/blackboard-web                 # the dashboard on http://127.0.0.1:8765
.venv/bin/blackboard-mcp                 # the MCP server (stdio)
.venv/bin/blackboard-login               # refresh the session cookie by hand

cd src/blackboard_web/frontend
npm run dev                              # Vite on :5173, proxies /api to :8765
npm run build                            # -> dist/, which FastAPI serves

# tests are standalone scripts, not pytest; each exits non-zero on failure
.venv/bin/python tests/test_web.py       # cache + syllabus weight resolution
.venv/bin/python tests/test_grades.py    # weighted grade maths
.venv/bin/python tests/test_end_to_end.py  # every MCP tool against a stub server

# the desktop app (from the repo root; needs rust + the tauri cli via `npm install`)
.venv/bin/python scripts/build_sidecar.py   # freeze the server -> src-tauri/sidecar/
npm run tauri build                         # -> src-tauri/target/release/bundle/
npm run tauri dev                           # the shell against an already-frozen sidecar

# a release: bump the version in all three files, then tag. CI builds every
# platform, signs it, and opens a draft release with the updater's latest.json.
git tag v0.2.0 && git push --tags
```

`tests/stub_blackboard.py` is a fake Blackboard instance — tests never touch the real
one. **Never call `/api/auth/login` or `/api/auth/relogin` while testing**: they drive a
real headless browser against the university's SSO and can fire a Duo push at the user's
phone. Stub those routes in Playwright instead.

After changing anything under `frontend/src`, run `npm run build` **and** restart
`blackboard-web` if you touched Python — the server loads routes at import time.

## Layout

```
src/blackboard_mcp/       the Blackboard layer + the MCP server
  client.py               async REST client; GET only; cookie auth
  server.py               8 MCP tools: list_due_dates, get_assignment, …
  login.py                headless Playwright SSO; run_login(force=…)
  session.py              cookie persistence + expiry maths
  paths.py                the one place that decides where state lives

src/blackboard_web/       the dashboard
  app.py                  every HTTP route
  sync.py                 fetch → cache → serve; the read path
  cache.py                JSON knowledge store on disk, with per-kind TTLs
  grades.py               weighted grade maths
  edits.py                local corrections layered over Blackboard's answers
  ics.py                  the .ics feed the calendar grid renders from
  llm.py                  the only model calls: syllabus weights, cached on inputs
  notices.py              which announcements have been popped up already
  gdocs.py                OAuth upload of a handout to Google Docs
  textextract.py          plain text out of pdf/docx/etc.
  frontend/src/
    App.jsx               shell, routing, auth states, the shared course order
    styles.css            the whole design system (tokens → primitives → screens)
    api.js                every endpoint, named once
    lib/                  format, ics parser, viewer (docx/pptx), theme, route, read, title
    components/           one file per screen or widget

src-tauri/                the desktop shell (Windows and macOS; Linux installs the wheel)
  src/main.rs             starts the sidecar, waits for it, points a window at it
  ui/index.html           the splash, shown while that happens
  tauri.conf.json         window, bundle targets, and the sidecar as a resource
scripts/
  build_sidecar.py        PyInstaller -> a self-contained server binary
  sidecar_entry.py        the script PyInstaller freezes
```

## Things that will bite you

**State lives in one place, decided by `paths.py`.** `.env`, the session cookie, the
cache and downloads all resolve through it — never against `__file__` or the working
directory, because a packaged build has neither. `BB_STATE_DIR` overrides.

**The calendar grid renders the generated `.ics`, not the JSON.** That is deliberate:
what is on screen and what lands in Google Calendar cannot disagree. An edited due date
has to invalidate both (`load()` *and* `loadCalendar()` in `App.jsx`).

**Course colours come from one order, computed in `App.jsx`** from the full course list
and passed down. Screens must not derive their own from whatever subset they hold — that
was a real bug: the same course came out three different colours.

**A `.panel-head` is itself a `div`,** so `:first-of-type` on a row inside a panel never
matches the row you mean. Use `.panel-head + .row-class` to kill the first divider.

**`button { white-space: nowrap }`** is right for a label and wrong for a row that
happens to be a button. Row-shaped buttons (`.due`, `.detail`, `.set-item-head`) reset it
or long titles run under the date pinned to their right.

**The tab title is claimed, not assigned.** `lib/title.js` keeps the claims and the
highest level wins (`SCREEN` < `OVERLAY` < `READER`), because React runs a child's
effect before its parent's — a plain "last mount wins" would let a screen overwrite the
drawer it contains.

**The announcement popup is once-only, and the record is server-side** (`notices.py`,
`announced.json`). A first run with no record baselines everything silently instead of
showing it; posts are marked when the modal *opens*, not when it is closed.

**`/api/reset` empties the state directory but never the credentials.** It refuses to
touch `BB_DOWNLOAD_DIR` if it resolves to `$HOME`, the state directory or `/` — that
path is typed by a person, and a wrong one would take the surrounding files with it.

**Never build a Blackboard URL.** `web_link()` returns the `links[rel=alternate]` href
Learn puts on every content item, and a course carries `externalAccessUrl`; both are
Blackboard's own answers and work for Ultra and Original alike.

**`launch_url()`, not `web_link()`, is what callers want.** It gates the link on
`launches_in_blackboard()`: LTI placements (prefix-matched — every install has its own
suffix), assignments, tests, forums, tool links. Files, documents, folders and external
links get nothing, because this dashboard already shows them and an external link leaves
Blackboard anyway. An LTI item's `contentHandler.url` is its tool's launch endpoint and
is **not** a usable link — only Blackboard's signed handoff works — so `_node` drops it
for everything but `externallink`.

**A cached entry that predates a field is stale, not fresh.** `content_*` carries
`schema: TREE_SCHEMA` and an assignment detail carries `schema: DETAIL_SCHEMA`; bump
either one whenever the shape changes *or a field's meaning narrows* — the link rule
narrowing `web_url` needed a bump exactly as much as adding it did. Without that, a new field is invisible for the
six hours the entry stays fresh.

**A hidden gradebook drops the whole course, once, in `sync.refresh`.** Screens do not
filter — they render whatever is in `courses`. The flag is `bundle["hidden"]`, which is
specifically a `ForbiddenError`; `accessible` is still false for any failure, so do not
filter on that or a timeout will make a course vanish.

**A calendar cell is the button, and the chips inside it are inert.** That is what
lets the whole day be one target; adding an interactive chip back would nest a button
in a button and break both.

**A wrong password is detected by the password box, not the wording.** A form that
comes back with the box *empty* was re-rendered by the provider — that is a refusal,
whatever language it is in; a box still holding what we typed means the submit never
took, so that is the retry path. Text matching is only the fast path. Do not "simplify"
these into one check: the old wording-only version took 32s to notice a silent refusal
and re-posted the password five times doing it, which some providers count towards a
lockout.

**A refused password is a `reason: "credentials"` failure** (`login.py` raises it,
`_login_failure` maps it to a 401 and clears `BB_PASSWORD`). Anything else is a 502 and
keeps the password, because retrying it is the right move. The sign-in screen polls
`/api/auth/progress` for the stage — `duo` is the one worth showing.

**Escape belongs to the topmost overlay.** The drawer and the document viewer both listen
on `window`; the drawer registered first, so it checks `viewing` and stands down itself.

**Logging out clears the saved password**, not just the cookie — that password is what
auto-login signs back in with, so leaving it would silently undo the logout.

**The port is a contract between two processes.** `blackboard-web --port 0` lets the OS
pick a free port and prints `BLACKBOARD_PORT=<n>` on stdout as its first line; `main.rs`
parses exactly that prefix. A fixed port collides with whatever else is on the machine —
and with a second copy of this app — so do not "simplify" it back to 8765, and do not
reword the line.

**`child.stdin` is deliberately never taken in `spawn_sidecar`.** The shell writes
nothing to it; the open pipe is a dead-man's switch. Killing the child on exit only
covers the polite exits, and a server that outlives its window is a signed-in session
nobody can see. Taking the handle would drop it at the end of the function and stop the
server instantly instead.

**The sidecar is a console program on purpose.** Its stdout is the only way the shell
learns the port, and a windowed build leaves `sys.stdout` as `None` on Windows. What
stops a console appearing is `CREATE_NO_WINDOW` on the parent's spawn, not the subsystem.

**PyInstaller runs in onedir mode, not onefile.** A onefile bundle unpacks its whole
~280MB payload to a temp directory on every launch — slow, and the shape antivirus
heuristics flag hardest. Playwright's driver (a bundled node, ~124MB of the total) has to
be collected explicitly or the sign-in screen has no browser to drive.

**Build the Linux bundles on the oldest distro you can stand.** AppImage's bundled
`strip` cannot read the `.relr.dyn` sections a current toolchain emits, and it fails the
whole bundle; `NO_STRIP=true` works around it locally. The deeper reason is the same
either way: the glibc an AppImage is built against is the oldest one it will run on. CI
pins `ubuntu-22.04`.

**Two Macs, not a universal binary.** A universal build would need a universal CPython
for the sidecar, which PyInstaller does not produce. Apple Silicon and Intel each get
their own installer.

**The updater's signing key is the one secret with no recovery.** It lives at
`~/.tauri/whiteboard-updater.key`, never in the repo, and in CI as
`TAURI_SIGNING_PRIVATE_KEY`. Every installed copy trusts the matching public key that is
compiled into it, so losing the private key does not mean generating another — it means
no installed copy can ever be updated again, and every user has to reinstall by hand.
Back it up somewhere that is not this machine.

**The update endpoint is compiled in, and is currently a placeholder.** It points at a
`.invalid` host, which fails DNS instantly rather than costing a timeout on every launch.
Replace it with the real releases URL before the first release; installed copies check
whatever it said when they were built, forever.

**Every updater failure is soft, deliberately.** A missing endpoint, a dead network, a
malformed feed and a refused signature all end the same way: a line on stderr and the app
opening normally. The dashboard's data is already on disk, and an updater that can stop
someone reading their own deadlines is worse than one that quietly misses a version.

**The check runs before the sidecar starts.** That is what keeps an install from having
to tear down a running server mid-request, and why `app.restart()` is safe where it sits.

**Three files carry the version** — `pyproject.toml` (which backs `__version__` and so
what `/api/health` reports), `src-tauri/tauri.conf.json` (which the updater compares
against the feed) and `package.json`. They answer different questions, so drift is
invisible until an installed copy reports one version while updating from another.
`scripts/build_sidecar.py` refuses to build when they disagree.

## Conventions

- Comments say **why**, not what. Match the density already there; it is deliberate.
- No server-side document conversion: the machine serving the app is not necessarily the
  machine reading it, and neither is guaranteed an office suite. Word and PowerPoint are
  read in the browser (`lib/viewer.js`, `docx-preview`, `jszip`).
- `styles.css` is tokens → primitives → screens. A screen composes primitives and never
  restyles them; a change to a button should be a change to every button.
- Anything appearing on more than one screen is a component (`Sep.jsx`, `RichText.jsx`)
  or a primitive class, defined once.
- Frontend deps stay minimal and advisory-free (`npm audit` is at 0). Renderers are lazy
  `import()` so they stay out of the main bundle.

## Verifying frontend work

There is no test runner for the UI. Drive it with Playwright against a running server and
check for horizontal overflow and console errors across routes × viewports × themes —
that combination is where the real bugs were. Use `channel="chromium"` when a PDF is
involved; the default headless shell has no PDF plugin and renders a blank frame.
