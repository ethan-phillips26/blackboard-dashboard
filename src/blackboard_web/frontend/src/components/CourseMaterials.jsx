import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { extension, save } from "../lib/download.js";
import { ago, dateTime, filesize, points, relative } from "../lib/format.js";
import { canView } from "../lib/viewer.js";
import BbLink from "./BbLink.jsx";
import DocumentViewer from "./DocumentViewer.jsx";
import RichText, { clamp, visibleLength } from "./RichText.jsx";
import { Sep } from "./Sep.jsx";

/** Blackboard's contentHandler ids, in words a student would use. */
const KIND = {
  assignment: "Assignment",
  "asmt-test-link": "Test",
  document: "Document",
  file: "File",
  externallink: "Link",
  courselink: "Course",
  blankpage: "Page",
  toollink: "Tool",
  forumlink: "Forum",
  discussionfolder: "Forums",
  syllabus: "Syllabus",
  folder: "Folder",
  lesson: "Module",
};

const CLAMP = 260;

function kindLabel(node) {
  return KIND[node.type] ?? "Item";
}

/** Keep every node that matches, plus every folder on the way down to one. */
export function filterTree(nodes, needle) {
  const out = [];
  for (const node of nodes) {
    const children = filterTree(node.children ?? [], needle);
    const haystack = [
      node.title,
      node.body ?? "",
      ...(node.files ?? []).map((f) => f.filename),
    ].join(" ").toLowerCase();
    const hit = haystack.includes(needle);
    if (hit || children.length) out.push({ ...node, children, matched: hit });
  }
  return out;
}

export function countLeaves(nodes) {
  return nodes.reduce(
    (n, node) => n + (node.is_folder ? countLeaves(node.children ?? []) : 1),
    0
  );
}

function collectFolderIds(nodes, into = []) {
  for (const node of nodes) {
    if (node.is_folder) {
      into.push(node.content_id);
      collectFolderIds(node.children ?? [], into);
    }
  }
  return into;
}

/**
 * One content item: what it is, what it says, and — the part Blackboard leaves
 * out — where it stands. A row that is also a gradebook column carries its due
 * date and its score right there, so reading the week's material and knowing
 * what it is worth is one glance rather than two screens.
 */
function Item({ node, courseId, assignment, column, onOpen, forceOpen }) {
  const [open, setOpen] = useState(false);
  // What the download route saved for this item, once anything has asked for
  // it. One call fetches every file the item has, so the second chip you click
  // is served from this rather than from Blackboard again.
  const [saved, setSaved] = useState(null);
  const [busy, setBusy] = useState(null);
  const [viewing, setViewing] = useState(null);
  const [error, setError] = useState(null);
  const body = (node.body ?? "").trim();
  // Measured as it reads, not as it is stored: a paragraph with three links in
  // it is not long just because their URLs are.
  const long = visibleLength(body) > CLAMP;
  const showAll = open || forceOpen;

  const files = node.files ?? [];
  const due = assignment?.due_local
    ? new Date(assignment.due_local)
    : column?.due
      ? new Date(column.due)
      : null;
  // A document has nothing behind it worth a round trip; an assignment's full
  // instructions and its handouts do.
  const openable = node.content_id && (node.is_assignment || files.length > 0);

  /** Blackboard names a file; this is what turns that into the file itself. */
  function downloaded(result, filename, index) {
    const rows = result?.downloaded ?? [];
    return rows.find((f) => f.filename === filename) ?? rows[index];
  }

  /**
   * A file has to be here before it can be read, so pressing it fetches it and
   * then does whatever that kind of file allows: shown in the page if the
   * browser can draw it, saved if it cannot.
   */
  async function grab(filename, index) {
    setBusy(filename);
    setError(null);
    try {
      const result = saved ?? await api.fetchFiles(courseId, node.content_id);
      if (!saved) setSaved(result);
      const file = downloaded(result, filename, index);
      if (!file?.url) setError(`Blackboard would not give up ${filename}.`);
      else if (canView(filename)) setViewing(file);
      else save(file.url, file.filename);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className={"mat-item" + (node.matched ? " hit" : "")}>
      <div className="mat-row">
        <span className={"mat-kind k-" + node.type} title={kindLabel(node)}>
          {kindLabel(node)}
        </span>
        <div className="mat-id">
          <div className="mat-title">
            {node.url ? (
              <a className="link" href={node.url} target="_blank" rel="noreferrer">
                {node.title} ↗
              </a>
            ) : (
              <b>{node.title}</b>
            )}
            {column?.graded && (
              <span className="mat-score">
                {column.score} / {column.possible}
              </span>
            )}
            {!column?.graded && due && (
              <span className={"mat-due" + (due < new Date() ? " bad" : "")}>
                {relative(due)}
              </span>
            )}
          </div>
          <div className="mat-meta note dim">
            {[
              due && <span key="due">Due {dateTime(due)}</span>,
              column && points(column.possible) &&
                <span key="pts">{points(column.possible)}</span>,
              !due && node.modified &&
                <span key="mod">Updated {ago(new Date(node.modified))}</span>,
              files.length > 0 &&
                <span key="files">
                  {files.length} {files.length === 1 ? "file" : "files"}
                </span>,
            ].filter(Boolean).map((part, i) => (
              <span className="mat-meta-part" key={part.key}>
                {i > 0 && <Sep />}{part}
              </span>
            ))}
          </div>

          {body && (
            <p className="mat-body">
              <RichText text={showAll || !long ? body : `${clamp(body, CLAMP)}…`} />
            </p>
          )}
          {body && long && !forceOpen && (
            <button className="linkish mat-more" onClick={() => setOpen((v) => !v)}>
              {open ? "Show less" : "Read more"}
            </button>
          )}

          {files.length > 0 && (
            <div className="mat-files">
              {files.map((f, i) => {
                // Blackboard's claimed size until the file is here; the
                // measured one after, which is the only one that was ever true.
                const here = downloaded(saved, f.filename, i);
                const size = filesize(here?.bytes ?? f.bytes);
                return (
                  <button
                    className={"mat-file" + (here ? " have" : "")}
                    key={`${f.filename}-${i}`}
                    title={canView(f.filename)
                      ? `Read ${f.filename} here`
                      : `Download ${f.filename}`}
                    disabled={busy === f.filename}
                    onClick={() => grab(f.filename, i)}
                  >
                    {busy === f.filename
                      ? <span className="spin" />
                      : <span className="fileext" aria-hidden="true">
                          {extension(f.filename)}
                        </span>}
                    <span className="fn">{f.filename}</span>
                    {size && <em>{size}</em>}
                    {!canView(f.filename) && (
                      <span className="dl" aria-hidden="true">↓</span>
                    )}
                  </button>
                );
              })}
            </div>
          )}

          {error && <p className="err">{error}</p>}
          {saved?.failed?.length > 0 && (
            <p className="err">
              {saved.failed.map((f) => f.filename).join(", ")} could not be fetched.
            </p>
          )}
          {viewing && (
            <DocumentViewer file={viewing} onClose={() => setViewing(null)} />
          )}
        </div>
        {(node.web_url || openable) && (
          // Stacked, with Blackboard on top: reading it here is the ordinary
          // thing and gets the lower, larger control; going over there is the
          // exception and sits above it, out of the way of a run of Opens.
          <div className="mat-actions">
            <BbLink href={node.web_url} className="bblink mat-bblink" />
            {openable && (
              <button
                className="mat-open"
                onClick={() =>
                  onOpen({
                    contentId: node.content_id,
                    title: node.title,
                    points: column?.possible ?? assignment?.points_possible ?? null,
                    due,
                  })
                }
              >
                Open
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Folder({ node, depth, expanded, onToggle, ...rest }) {
  // Folders start shut — a course is a list of weeks, and the whole term
  // unrolled at once is not a page you can scan. A search opens every folder it
  // reaches, though: a hit hidden inside a shut one reads as no hit at all.
  const isOpen = rest.forceOpen || expanded.has(node.content_id);
  const children = node.children ?? [];
  const leaves = countLeaves(children);
  const body = (node.body ?? "").trim();

  return (
    <section className={"mat-folder d" + Math.min(depth, 2)}>
      <button
        className="mat-folder-head"
        onClick={() => onToggle(node.content_id)}
        aria-expanded={isOpen}
      >
        <span className={"caret" + (isOpen ? " open" : "")} aria-hidden="true">›</span>
        <b>{node.title}</b>
        <span className="note dim">
          {leaves === 0 ? "empty" : `${leaves} ${leaves === 1 ? "item" : "items"}`}
        </span>
      </button>
      {isOpen && (
        <div className="mat-folder-body">
          {body && <p className="mat-body dim"><RichText text={body} /></p>}
          {children.length === 0 ? (
            <p className="empty">Nothing posted in here yet.</p>
          ) : (
            <MaterialTree nodes={children} depth={depth + 1} expanded={expanded}
                          onToggle={onToggle} {...rest} />
          )}
        </div>
      )}
    </section>
  );
}

/** The tree itself: folders that open, items that carry their own standing. */
export function MaterialTree({ nodes, depth, ...rest }) {
  return nodes.map((node) =>
    node.is_folder ? (
      <Folder key={node.content_id} node={node} depth={depth} {...rest} />
    ) : (
      <Item
        key={node.content_id ?? node.title}
        node={node}
        courseId={rest.courseId}
        assignment={rest.byContent.get(node.content_id)}
        column={rest.byColumn.get(node.grade_column_id)}
        onOpen={rest.onOpen}
        forceOpen={rest.forceOpen}
      />
    )
  );
}

/**
 * The course as its instructor actually laid it out — every module, document,
 * link and handout on one searchable page.
 *
 * Blackboard makes you click into each folder in turn and tells you nothing
 * about what any of it is worth. Here the whole tree is open at once, the
 * search runs over item bodies as well as their titles, and anything with a
 * gradebook column behind it shows its deadline and its score in place.
 */
export default function CourseMaterials({ courseId, courseLabel, assignments,
                                          standing, onOpenAssignment }) {
  const [tree, setTree] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [query, setQuery] = useState("");
  // Which folders the reader has opened. Holding it this way round means a
  // folder posted between two syncs arrives shut like every other one, with
  // nothing to keep in step with the tree.
  const [expanded, setExpanded] = useState(() => new Set());

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    api
      .courseContent(courseId)
      .then((d) => live && setTree(d))
      .catch((e) => live && setError(e.message))
      .finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [courseId]);

  async function refresh() {
    setRefreshing(true);
    setError(null);
    try {
      setTree(await api.courseContent(courseId, true));
    } catch (e) {
      setError(e.message);
    } finally {
      setRefreshing(false);
    }
  }

  // What each row can be told about itself: its deadline, and its score.
  const byContent = useMemo(
    () => new Map((assignments ?? []).filter((a) => a.content_id)
      .map((a) => [a.content_id, a])),
    [assignments]
  );
  const byColumn = useMemo(() => {
    const map = new Map();
    for (const cat of standing?.categories ?? []) {
      for (const col of cat.columns ?? []) map.set(col.column_id, col);
    }
    return map;
  }, [standing]);

  const needle = query.trim().toLowerCase();
  const nodes = useMemo(() => {
    const all = tree?.nodes ?? [];
    return needle ? filterTree(all, needle) : all;
  }, [tree, needle]);

  function toggle(id) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  const allFolders = useMemo(
    () => collectFolderIds(tree?.nodes ?? []), [tree]
  );
  const anyOpen = expanded.size > 0;

  if (loading) {
    return (
      <section className="panel">
        <p className="empty"><span className="spin" /> Reading the course…</p>
      </section>
    );
  }

  if (error && !tree) {
    return (
      <section className="panel">
        <p className="err">{error}</p>
        <button onClick={refresh} disabled={refreshing}>
          {refreshing ? <><span className="spin" /> Reading</> : "Try again"}
        </button>
      </section>
    );
  }

  if (tree && !tree.accessible) {
    return (
      <section className="panel">
        <p className="note">{tree.reason ?? "This course's content is not visible."}</p>
      </section>
    );
  }

  const counts = tree?.counts ?? {};
  const found = needle ? countLeaves(nodes) : null;

  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <h2>Materials</h2>
          <span className="note dim">
            {counts.items ?? 0} items · {counts.folders ?? 0} folders
            {counts.files ? ` · ${counts.files} files` : ""}
          </span>
          <div className="spacer" />
          {allFolders.length > 0 && (
            <button
              onClick={() => setExpanded(anyOpen ? new Set() : new Set(allFolders))}
            >
              {anyOpen ? "Collapse all" : "Expand all"}
            </button>
          )}
          <button onClick={refresh} disabled={refreshing}>
            {refreshing ? <><span className="spin" /> Reading</> : "Refresh"}
          </button>
        </div>

        <input
          className="set-search"
          type="text"
          value={query}
          placeholder="Search titles, text and file names…"
          onChange={(e) => setQuery(e.target.value)}
        />
        {needle && (
          <p className="note dim">
            {found} {found === 1 ? "item" : "items"} match “{query.trim()}”.
          </p>
        )}
        {error && <p className="err">{error}</p>}
        {tree?.truncated && (
          <p className="note dim">
            This course is deeper than the tree shown; the last folders were left
            unopened.
          </p>
        )}
        {tree?.fetched_at && (
          <p className="note dim">Read {ago(new Date(tree.fetched_at))}.</p>
        )}
      </section>

      {nodes.length === 0 ? (
        <section className="panel">
          <p className="empty">
            {needle
              ? "Nothing in this course matches that."
              : "This course has no content posted."}
          </p>
        </section>
      ) : (
        <div className="mat-tree">
          <MaterialTree
            nodes={nodes}
            depth={0}
            courseId={courseId}
            expanded={expanded}
            onToggle={toggle}
            byContent={byContent}
            byColumn={byColumn}
            forceOpen={!!needle}
            onOpen={(target) =>
              onOpenAssignment({ ...target, courseId, course: courseLabel })
            }
          />
        </div>
      )}
    </>
  );
}
