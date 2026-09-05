/**
 * The separator between two facts on one line.
 *
 * Metadata lines all over the app read "Fri, Sep 4 · 20 pt · in 3 days". Some
 * were built by string concatenation and some by a flex gap, so the same kind
 * of line came out three different ways. This is the one of them, and it is
 * hidden from screen readers because a middot is not a word.
 */
export function Sep() {
  // The spacing is a margin rather than literal spaces: half of these lines are
  // flex rows, and a flex container strips the whitespace either side of a text
  // node, which left the dot jammed against both words.
  return <span className="sep" aria-hidden="true">·</span>;
}

export default Sep;
