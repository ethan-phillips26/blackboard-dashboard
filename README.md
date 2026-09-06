# Blackboard MCP

An MCP server that gives an agent read access to your Blackboard Learn courses:
what's due, what the assignment actually says, and the handout files — so you can
say *"what's due this week, put it on my calendar, and pull down the sheet for the
one due soonest"* and have it just happen.

## Why it authenticates with a cookie

Blackboard has an official REST API, but the OAuth2 route requires an Application ID
that **your university's Blackboard administrator has to whitelist on their instance**
([docs](https://blackboard.github.io/rest-apis/learn/getting-started/first-steps)).
Students essentially never get that approval.

The same `/learn/api/public/*` endpoints are served to a logged-in browser session, so
this server reuses your session cookie instead. It only ever issues `GET` requests —
it reads your courses, it never submits, posts, or changes anything.

### About expiry — you don't re-paste every 3 hours

The `BbRouter` cookie carries a ~3 hour idle timeout, but Blackboard **reissues it on
every request**, exactly the way it keeps a browser tab logged in. This server does what
the browser does: it keeps the reissued value and writes it to `.bb_session.json`, so
the seed you paste into `.env` is only ever a starting point.

On top of that, a background keepalive pings Blackboard every 45 minutes so the idle
timer never runs out while the server is running.

The practical result: **paste a cookie once and it keeps working.** You only need a new
one if the server was stopped for longer than the idle timeout, or your university
forces a re-login (SSO sessions often have a hard daily cap regardless of activity).

Run `session_status` any time to see exactly how much life is left.

## Setup

```bash
cd /home/ethan/blackboard
uv sync
cp .env.example .env
```

Then edit `.env`:

```ini
BB_HOST=blackboard.your-university.edu
BB_COOKIE=BbRouter=expires:...,id:...,signature:...
BB_DOWNLOAD_DIR=./blackboard-files
```

### Getting the cookie automatically

```bash
uv run blackboard-login
```

This drives a headless Chromium through the ordinary sign-in: it opens your
institution's SSO page, fills in `BB_USERNAME` / `BB_PASSWORD` from `.env`, waits
while you approve the **Duo push on your phone**, then writes the fresh `BbRouter`
value into both `.env` and `.bb_session.json`.

No window ever appears — the browser is headless, so the only sign it ran is the push
you were going to approve anyway. The first time on a new machine, fetch the browser
it drives with `uv run playwright install chromium`.

```ini
BB_USERNAME=your.ndus.id
BB_PASSWORD=your-password
```

Those two are read by this command and nothing else; the MCP server itself only ever
sees the cookie. `.env` is gitignored, but it is a plaintext file — if you would rather
not keep a password on disk, the manual route below still works.

The browser profile lives in `.bb_browser/` (also gitignored), so when Duo offers *"Yes,
this is my device"* it is accepted and remembered, and later refreshes usually complete
without a push at all.

| Flag | |
|---|---|
| `--headed` | show the window, for when a login step needs watching |
| `--debug` | screenshot each step into `.bb_browser/debug/` |
| `--force` | log in again even if the saved profile still works |
| `--timeout` | seconds to wait for the whole login, Duo included (default 300) |

The dashboard's own sign-in screen runs this same script, headless, and says what
it is waiting for while it does: opening the sign-in page, submitting the
credentials, **waiting for Duo** — the one step that takes minutes and needs you to
pick up your phone — and trusting the browser afterwards. If the identity provider
refuses the username or password, it says so in those words rather than timing out,
drops the password it was given, and asks for it again.

### Getting your cookie by hand

1. Log into Blackboard in your browser.
2. Open DevTools (`F12`) → **Application** tab → **Cookies** → your Blackboard domain.
3. Find the `BbRouter` row and copy its **Value**.
4. Paste it as `BB_COOKIE` in `.env`.

The Application panel is the easiest source: it shows the current live value with no
header hunting and no attributes to trim.

Any of these paste forms work identically — the server keeps what Blackboard needs
(`BbRouter`, `JSESSIONID`, and friends) and discards analytics noise and cookie
attributes (`Path`, `Secure`, `HttpOnly`, `SameSite`, `Expires`):

| Source | Example |
|---|---|
| Application → Cookies **Value** | `expires:178...,id:3E11...` |
| Network → **Request** `Cookie:` header | `_ga=GA1.2.9; BbRouter=expires:178...; AWSALB=xyz` |
| Network → **Response** `Set-Cookie:` | `BbRouter=expires:178...; Path=/; Secure; HttpOnly` |

If you're picking between the Network-tab options: the response `Set-Cookie` is the
fresher of the two (it *is* the reissue), but it only appears on some responses. The
request `Cookie` header is on every request and easier to find. It makes little
difference — whatever you paste is only a seed, and the first tool call captures the
reissue anyway.

Verify it:

```bash
uv run python -c "
import asyncio, blackboard_mcp.server as s
print(asyncio.run(s.check_connection()))"
```

### Connecting an agent

Any MCP client, via stdio. For Claude Code:

```bash
claude mcp add blackboard -- uv --directory /home/ethan/blackboard run blackboard-mcp
```

For openclaw or anything else that takes the standard `mcpServers` JSON:

```json
{
  "mcpServers": {
    "blackboard": {
      "command": "uv",
      "args": ["--directory", "/home/ethan/blackboard", "run", "blackboard-mcp"]
    }
  }
}
```

The server reads `.env` from its own directory, so no secrets go in the agent config.

## Tools

| Tool | What it does |
|---|---|
| `check_connection` | Confirms the cookie works and says who it belongs to. Run this first when something breaks. |
| `list_courses` | Your courses, filtered to the current term by term dates. `all_terms=true` for everything. |
| `list_due_dates` | **The main one.** Upcoming assignments across all courses, with a ready-made `calendar_event` per item. |
| `get_assignment` | Full instructions as plain text, plus the list of attached files. |
| `download_assignment_files` | Saves the handouts/starter code to disk, one folder per assignment. |
| `browse_course_content` | Walks the course content tree — slides, syllabus, readings that have no gradebook entry. |
| `list_announcements` | Recent announcements, which is often where deadline changes get posted. |
| `session_status` | How long the cookie has left, and whether auto-renewal is keeping up. |

### How they chain

`list_due_dates` returns a `content_id` for each assignment, which is exactly what
`get_assignment` and `download_assignment_files` take. That's the whole pipeline:

```
list_due_dates → content_id → get_assignment  (what am I supposed to do?)
                            → download_assignment_files  (give me the sheet)
```

Each item also carries a `calendar_event` block:

```json
{
  "summary": "CS-340-001: Homework 3",
  "start": "2026-09-04T23:29:00-04:00",
  "end": "2026-09-04T23:59:00-04:00",
  "description": "Algorithms\nDue Fri Sep 04, 11:59 PM\n100 points"
}
```

Deadlines are instants, not spans, so each becomes a 30-minute block ending at the
due time — that reads correctly in every calendar app. Hand this straight to whatever
calendar MCP you have connected (Google Calendar, CalDAV, etc.).

### Things worth knowing

- **Submitted work is hidden by default.** `list_due_dates` checks your grade row for
  each assignment and drops anything already handed in or graded. Pass
  `include_submitted=true` to see everything.
- **Calculated columns are filtered.** "Total" and "Weighted Total" are not homework.
- **`get_assignment` and `browse_course_content` return a `web_url`** — Blackboard's own
  link to the item — but only for items Blackboard launches (assignments, tests, LTI
  tools). A file, a document or an external link returns `null`, because opening those
  in Blackboard achieves nothing the tools here have not already done.
- **Manually-created gradebook columns have no `content_id`.** There's no content item
  behind them, so there are no files to download. Use `browse_course_content` to hunt
  for the material by hand.
- **Current-term filtering uses real term dates.** Institutions leave old courses flagged
  "available" for years — one real account had 18 "available" courses spanning four
  terms. The server resolves each course's `termId` to its date range and keeps only
  terms containing today. Courses whose term has no dates are kept rather than hidden,
  since silently dropping coursework is worse than showing one extra course.
- **Ultra and Original store files differently, and all three shapes are handled.**
  Original puts instructions in `body` with files on `/attachments`. Ultra *assignments*
  put instructions in `contentHandler.instructions` with files as `data-bbtype="attachment"`
  anchors. Ultra *documents* (syllabi, lecture slides) put the file in a `data-bbfile`
  anchor with **no href and no `data-bbtype`** — the URL is the `resourceUrl` inside that
  JSON. `/attachments` returns HTTP 400 for both Ultra shapes.
- **Ultra document titles are meaningless.** Every Ultra document body is titled
  `ultraDocumentBody`, so downloads are filed under the parent folder's name
  ("Course Syllabus") instead. The real value is still returned as `raw_title`.
- **A 403 on one course is not an auth failure.** Instructors can hide a gradebook from
  students. Those courses are reported separately under
  `courses_without_gradebook_access` instead of being mistaken for an expired cookie.
- **`course_id` accepts anything reasonable** — the internal id, the code (`ENG-210`),
  a prefix (`ENG`), or a chunk of the title (`writing`).
- **The session renews itself.** `.bb_session.json` holds the current cookie and is
  gitignored and chmod `600`. Delete it to force the `.env` seed to be used again.

## Example prompts

> What's due in the next two weeks?

> Add everything due this month to my calendar.

> What's due soonest? Download the assignment sheet and read me the requirements.

> Did any of my professors post an announcement about a deadline change?

> How long until my Blackboard session expires?

## Whiteboard, the dashboard

The same data, as a web page:

```bash
uv run blackboard-web        # http://127.0.0.1:8765
```

It opens on a countdown to your next deadline, a month calendar, the outstanding
deadline list, and a grade card per course with a "what do I need on this to finish
at 90%" calculator.

That opening screen is one screenful by design: on a window with room for it — wider
than 1080px and taller than 780px — the dashboard is pinned to the viewport and the
month grid takes whatever height is left, so the countdown, the month and the
deadline list are taken in together with nothing to scroll. On a smaller window,
where squeezing the month would cost more than it saved, it scrolls like every other
screen.

The figures across the top are stretched to a common height, so each spreads its
three parts over that height rather than stacking them at the top: the labels line
up along the top, the figures below them, the captions along the floor. Below
1000px the deadline card takes a row on its own — laid on its side, the figure
beside the detail — with the three figures sharing the row underneath, and two by
two on a phone.

**Open in Blackboard** appears on the things Blackboard *runs* rather than the things
it stores: assignments and tests, and LTI items — Vevox class participation, a lab
tool, Zoom, the course-evaluation surveys. Those launch inside Blackboard with a signed
handoff, so there is nothing this dashboard can put in their place; the link is the
only way in, and submitting happens over there too.

Files, documents and folders do not get one: the text is already on the page and the
file is one click away here, so a link there is a longer road to the same thing. Nor do
external links — those leave Blackboard the moment they resolve, and the row already
names where it goes. Nor announcements, which are read in full here. A course still
carries one, since a course is a place rather than an item.

An LTI item's own URL is deliberately not offered as a link. Blackboard reports it —
`https://vevox-us-httpapi.vevox.com/lti/v1p3/launch/…` — but it is the tool's launch
endpoint, which only answers the signed handoff Blackboard performs; following it
directly lands on an error. "Open in Blackboard" is what works.

The address is never assembled here. Learn hands one out on every content item — an
`/ultra/redirect?...` it resolves server-side — and reports its own `externalAccessUrl`
for a course, so these links are right whether the course is Ultra or Original and stay
right if Blackboard changes the shape of its URLs.

A day in the month is one target: clicking anywhere in the cell — the date, a chip,
the empty space beside them — opens that day's deadlines in a modal over the page,
so the month underneath keeps its size. The chips are a preview of what is in the
day, not controls of their own; picking one assignment out of a 60px cell was a
small target sitting inside a large one that did something else. Click a deadline —
a row in that modal, or a row in the deadline list — and a panel slides in with the
assignment's actual instructions and its attached files. **Download files**
pulls the handouts through the same code path the MCP server uses, saves them under
`blackboard-files/`, and then offers each one back through the browser.

### A course page, not a grade page

Every course opens on its own page with four tabs, each with its own URL, so the
back button and a bookmark both land where you left off:

| | |
|---|---|
| **Overview** | What is outstanding in this course, its recent announcements, and where its grade stands. |
| **Materials** | The course's whole content tree — every module, document, link and handout. |
| **Grades** | The weighting, the arithmetic, every gradebook row, and the calculator. |
| **Announcements** | This course's posts in full. |

**Materials** is the half of a course the gradebook says nothing about. Modules
start shut, each one labelled with how much is inside it, so the tab opens as a
readable list of weeks rather than the whole term unrolled. The search runs over
item bodies and file names as well as titles, and opens every folder it finds a hit
in; every row that has a gradebook column behind it carries its deadline or its
score in place.

A row with handouts names its files straight away, and each name is a button:
clicking one pulls that item's files through the same code path the MCP server
uses, saves them under `blackboard-files/`, and hands the file to your browser. The
chip then turns green and reports the size the file actually turned out to be rather
than the one Blackboard claimed. **Open** still gives you the full panel a deadline
does — instructions, files, and the Docs button.

Writing a page in Ultra produces a folder carrying the name the instructor typed —
"Slides Week 1" — holding a single child that Blackboard calls `ultraDocumentBody`,
which is where the text and the files actually are. Drawn faithfully that is a folder
you have to open to reach a row named after Blackboard's internals, so the two are
put back together as one row: the instructor's name, the document's text, and its
handouts, with nothing to click through. A stray wrapper that cannot be folded is
named from its own first line instead, which is the heading it opens with anyway.

The tree is fetched once and cached for six hours like everything else, so opening a
course is instant; **Refresh** on the tab is what goes back to Blackboard. The fold,
the flattening and the counts are all derived on the way out, so a cache written
before any of those rules existed still comes back with them applied.

A course whose gradebook the instructor has hidden from students is dropped entirely:
not in the sidebar, the course count, the grade list, the settings screen, or the
announcement feed. The filter runs once in the sync, so no screen has to know the rule
— the course is simply not in the list they all read.

That is specifically a 403 on the gradebook. Any other failure — a timeout, a dropped
connection — leaves the course listed with no grade to show, because a course should
not disappear over one bad request. `sync.hidden_gradebooks` in `/api/state` names the
ones that were dropped, if you ever wonder where a course went.

| | |
|---|---|
| `GET /api/courses/{course_id}/content` | The content tree, cached for six hours. `?refresh=true` re-walks it. |

The deadline list is never cut short: beside the pinned month it scrolls inside its
own panel, and on a window too small for that layout the page scrolls and the list
runs its full length. There is nothing to press to see the rest.

Clicking a course in the calendar legend filters the month to that course; clicking
it again clears the filter. Colours are assigned per course from a fixed order, so
filtering never repaints the courses that survive it.

Returning to a tab you left open re-checks for new work if more than five minutes
have passed, so a page left up all afternoon is not showing this morning's list.

An announcement you have never been shown pops up once when the dashboard loads,
whole enough to read without going anywhere — several are stacked in the one modal.
Which posts have had their turn is recorded on the server, in
`.blackboard-cache/announced.json`, not in the browser: "have I been told this" has
to hold across a second machine and a cleared profile, or the popup starts nagging
about posts you dealt with weeks ago. The sidebar badge stays a browser preference,
because "have I opened that screen" genuinely is one.

The first run writes every current announcement into that record silently rather
than opening a modal with forty posts in it — so the popup means *posted since you
set this up*, which is the only thing it can honestly mean. It also waits behind an
open assignment drawer instead of stacking on top of it.

The browser tab names what is on screen, narrowest part first, because that is the
end that survives a squeezed tab: `Materials · CSCI 413 · Whiteboard`, and
`Tools Assignment 1 · CSCI 413 · Whiteboard` while that assignment is open. An
overlay outranks the screen it covers — the assignment drawer over the dashboard,
a handout over the drawer — and hands the title back when it closes. The dashboard
itself is the app, so it is plain `Whiteboard`.

Everything is served from the JSON cache in `.blackboard-cache/`, so the page is
instant and re-asks Blackboard only when something has actually gone stale.

### The calendar is the .ics file

The month grid does not read the JSON API. It fetches `/api/calendar.ics`, parses
it in the browser, and renders that — so the grid on screen and the file you import
into Google Calendar are the same document by construction, and neither can quietly
drift from the other.

| | |
|---|---|
| `GET /api/calendar.ics` | The feed. RFC 5545, UTC timestamps, one VEVENT per deadline. |
| `?download=true` | Same document, served as an attachment. **Download .ics** on the page. |
| `?refresh=true` | Force a Blackboard sync before rendering the feed. |

Three more routes back the assignment panel:

| | |
|---|---|
| `GET /api/assignments/{course_id}/{content_id}` | Instructions and attachment list, cached for six hours. |
| `POST /api/assignments/{course_id}/{content_id}/files` | Download the handouts; returns a URL for each saved file. |
| `GET /api/files/{path}` | Serve a file already downloaded. Refuses anything outside `BB_DOWNLOAD_DIR`. |

Ultra assignments often have no written instructions at all — the body is just a
link to the handout. Those anchors are stripped before the text reaches the browser
(they are credentialed URLs, and the file is already in the attachment list), so the
panel says so plainly rather than showing you a signed URL.

A file the page already shows a download button for is not also a link in the text.
Ultra puts an item's handouts into its body as anchors, so the prose would otherwise
repeat the same file — often as a bare signed URL, since Ultra leaves the anchor's
text empty. Those URLs expire, while the download button re-fetches through the
session, so they are taken out: a line that was nothing but an attachment goes
entirely, and a sentence with one in it keeps its words and loses only the link.

Links the instructor actually wrote into the prose are kept as links. Course bodies
are HTML, and flattening one has to decide what to do with an anchor: the MCP tools
put the URL where the anchor was — `[https://…] Bison Advise` — because a URL is the
part an agent can act on, while the dashboard keeps the two together so it can draw
the anchor the author wrote. Nothing renders the course's HTML; only the link's text
and its target ever become markup, and every link opens in a new tab without handing
over the referrer.

The cache holds the markup Blackboard sent, not the text the page drew from it, and
flattens on the way out. Course text is cached for six hours, so storing the rendered
form would leave a change to how it is presented sitting invisible behind stale
entries until they lapsed; rendering on read means the next page load has it. The
markup itself never leaves the server.

Each deadline becomes a 30-minute block ending at the due time, with a stable
`UID` (so re-importing updates the event instead of duplicating it), a day-ahead
`VALARM`, and `X-BB-*` properties carrying the course, points and content id that
the dashboard's own grid reads back out.

Point Google Calendar or Apple Calendar at the URL — **Copy subscription URL** on
the page — and deadlines keep themselves current while the server runs. A copy is
also written to `.blackboard-cache/coursework.ics` on every request, so the file is
importable even with the server stopped.

The feed serves whatever is cached if Blackboard is unreachable, so a subscribed
client polling during an expired session gets the deadlines you already had rather
than an error.

### Running it on a local model

Two things use a model: reading a syllabus for its grade weighting, and sorting the
gradebook's rows into that weighting. Every percentage on screen is arithmetic done
locally, so the model affects how the weighting is inferred — never the numbers.

By default that model is the `claude` CLI. To keep everything on your machine
instead, install [Ollama](https://ollama.com), pull a model, and set two values
in `.env`:

```bash
ollama pull qwen3:8b
```

```
BB_LLM=ollama
BB_OLLAMA_MODEL=qwen3:8b
```

`/api/health` reports which backend is live and whether it can be reached.

An 8B model handles sorting gradebook rows well — "Quiz 3" is a quiz whatever it
was filed under — and is adequate at reading a syllabus, though it matches
category names less reliably, so expect to fix more mappings by hand in the
course panel. Anything it cannot place shows up as unmapped rather than being
silently folded into the wrong bucket.

Two knobs matter if extraction comes back empty: `BB_OLLAMA_CTX` (Ollama
truncates silently past the context window) and `BB_LLM_DOC_CHARS` (how much of
the syllabus is sent). Both are documented in `.env.example`.

### Where state is kept

`.env`, the session cookie, the browser profile, the caches and downloaded files
all live in one directory. Running from a checkout that already has a `.env` —
what the setup above gives you — that directory is the project root, so
everything stays where this README says it is.

Anywhere else, including a packaged build or a run under Electron where neither
the source tree nor the working directory survives, it falls back to the per-user
data directory (`~/.local/share/blackboard`, `~/Library/Application Support/blackboard`,
or `%APPDATA%\blackboard`). Set `BB_STATE_DIR` to place it explicitly.

**Settings → Stored data → Clear stored data** empties that directory of everything
the app fetched, derived or downloaded: the cached courses, deadlines, announcements
and content trees, the syllabus weightings, your local corrections, the record of
which announcements have popped up, and every handout under `blackboard-files/`. The
next load re-syncs and the dashboard fills back in.

Two things survive, because neither is data about your courses and both are tedious
to re-establish: the **Blackboard login** (`.env`, `.bb_session.json`, and the
`.bb_browser/` profile that keeps Duo from asking again) and the **Google Docs
connection** (the client credentials and `.bb_google.json`). The button arms before
it fires, and there is no undo — 400MB of handouts is worth a second press.

If `BB_DOWNLOAD_DIR` has been pointed at a home directory or at the state directory
itself, the reset deletes nothing there rather than taking the surrounding files
with it.

### Opening a handout in Google Docs

Next to each file in the assignment panel is **Open in Docs**: it downloads the
handout if it isn't already local, uploads it to your Drive converted to a Google
Doc, and opens it in LibreWolf.

It appears only on files Drive can actually turn into a document — `.docx`, `.doc`,
`.odt`, `.rtf`, `.txt`, `.html`, `.pdf`. A slide deck or a zip of starter code still
uploads if you ask the API directly, but it lands in the Drive viewer rather than the
Docs editor, so the button does not offer it. The server publishes that list on
`/api/google/status` and the page reads it from there, so the button and the upload
can never disagree about what converts.

It needs a one-time Google setup, because Drive will not accept an upload without
OAuth and there is no way around that. **Settings → Google Docs** walks you
through it: five numbered steps, each linking straight to the console page it
happens on, ending in a drop box for the `client_secret_….json` that Google hands
out. Nothing to retype and no `.env` editing — the app writes
`BB_GOOGLE_CLIENT_ID` and `BB_GOOGLE_CLIENT_SECRET` itself.

Google has no API for minting an OAuth client, so those console steps are
irreducibly manual; the wizard only removes the guesswork. One of them matters
more than it looks: **Audience → Publishing status → Publish app**. While it says
*Testing*, Google expires the connection after seven days and you would be
reconnecting every week. `drive.file` is not a sensitive scope, so publishing
needs no verification review.

If that page shows no publishing status, one of two things is true. Either the
consent screen is not saved yet — there is no app to publish until it is — or the
app is **Internal**, which is offered only on a work or school account. Internal
apps have no Testing mode, so there is nothing to publish and the seven-day limit
does not apply to them.

Then press **Connect Google**. The consent screen opens in a new tab, the
redirect is caught on a loopback port, and the refresh token is stored in
`.bb_google.json` (gitignored, chmod `600`) so you never see it again. The panel
shows *Connected* when it lands, and offers **Disconnect** (which also revokes
the token at Google) and **Forget client**.

The scope requested is **`drive.file`** — the narrowest one that works. It grants
access only to files this app itself creates, so it cannot read anything already
in your Drive. `.docx`, `.doc`, `.odt`, `.rtf`, `.txt` and `.pdf` convert to a
Google Doc; anything else uploads unconverted and opens in the Drive viewer.

The browser the finished document opens in is whichever of `librewolf`, `firefox`
or `xdg-open` is found first, overridable with `BB_BROWSER`. Until Google is
connected the button is hidden and the panel links to the settings screen.

### Rebuilding the UI

```bash
cd src/blackboard_web/frontend
npm install && npm run build      # or: npm run dev, which proxies /api to :8765
```

## Tests

```bash
uv run python tests/test_end_to_end.py    # the Blackboard client
uv run python tests/test_grades.py        # grade arithmetic
uv run python tests/test_web.py           # cache, syllabus weighting, .ics writer
node tests/test_ics_parser.mjs            # .ics writer against the browser reader
node tests/test_course_page.mjs           # the course page, rendered
```

`test_end_to_end.py` runs 158 checks against a stub Blackboard instance that
reproduces the real API's quirks — result paging, calculated gradebook columns,
`302`-to-storage downloads (including the check that your session cookie is never
forwarded off-origin), hostile attachment filenames, and the
HTML-login-page-with-a-200 response that an expired session actually returns.

`test_ics_parser.mjs` builds a feed with the real Python generator and parses it
with the real browser parser, so the two halves of the calendar cannot drift apart
— folding, escaped punctuation, UTF-8 and all.

`test_course_page.mjs` bundles the real components with esbuild and server-renders
every tab of the course page against a realistic snapshot, so a crash, a bad prop or
a search that loses its matches is caught here rather than on screen. It needs the
frontend's `node_modules`, which `npm install` in `src/blackboard_web/frontend`
provides.

None of them need credentials.

## Limitations

- Read-only by design. It will not submit work for you.
- Sessions renew themselves while the server runs, but an SSO re-login prompt or a
  long shutdown still means pasting a fresh cookie. If your institution ever approves
  a developer application, `client.py` is the only file that needs an OAuth2 backend.
- Verified end to end against a live Blackboard SaaS instance (Learn 4000.21, Ultra):
  course listing, term filtering, due dates, grade status, and downloading a real
  assignment handout. Instances still vary between institutions — if an endpoint fails
  on yours, `browse_course_content` is the most portable fallback.
- Assignments in courses whose gradebook is hidden won't appear in `list_due_dates`,
  because the due date lives on the gradebook column. Nothing can be done about that
  short of scraping the content tree.
- Use this on your own account. It's your data, but automated access may still sit
  outside your institution's acceptable-use policy — worth a look before you lean
  on it.
