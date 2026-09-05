/**
 * Course text with its links intact.
 *
 * Blackboard bodies are HTML; the server flattens them to text but keeps every
 * anchor as `[what it said](where it goes)`, because a URL dumped into the
 * middle of a sentence is not something you can click. This turns those back
 * into real anchors and leaves everything else exactly as written.
 *
 * Nothing here renders HTML — the text is course-authored, so it goes into the
 * page as text, and only the two captured pieces of a link ever become markup.
 */

// A URL never contains a space or a closing paren, and a label escapes any "]"
// of its own, so this cannot run past the end of the link it started on.
const LINK = /\[((?:[^\]\\]|\\.)*)\]\((https?:\/\/[^)\s]+)\)/g;

const unescape = (label) => label.replace(/\\(.)/g, "$1");

/** Where the visible text of a link starts and ends, in source offsets. */
function links(text) {
  return [...(text ?? "").matchAll(LINK)];
}

/**
 * Cut text to roughly `n` characters without slicing a link in half.
 *
 * A link cut down the middle loses the shape the renderer matches on and the
 * reader is left staring at half a URL, so the cut moves back to where the link
 * began instead.
 */
export function clamp(text, n) {
  if (!text || text.length <= n) return text ?? "";
  const straddled = links(text).find(
    (m) => m.index < n && m.index + m[0].length > n
  );
  return text.slice(0, straddled ? straddled.index : n).trimEnd();
}

/** How long the text reads as, ignoring the URLs the reader never sees. */
export function visibleLength(text) {
  return links(text).reduce(
    (n, m) => n - m[0].length + unescape(m[1]).length,
    (text ?? "").length
  );
}

export default function RichText({ text }) {
  const source = text ?? "";
  const out = [];
  let at = 0;
  for (const match of links(source)) {
    if (match.index > at) out.push(source.slice(at, match.index));
    out.push(
      <a
        className="link"
        key={match.index}
        href={match[2]}
        target="_blank"
        rel="noreferrer noopener"
      >
        {unescape(match[1])}
      </a>
    );
    at = match.index + match[0].length;
  }
  if (at < source.length) out.push(source.slice(at));
  return out;
}
