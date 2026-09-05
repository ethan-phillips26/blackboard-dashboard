/**
 * A way back to the thing on Blackboard itself.
 *
 * This dashboard reads Blackboard; it does not replace it. Submitting, posting,
 * taking a quiz and everything else that writes still happens over there, so
 * anything shown here that exists as a page in Blackboard carries a link to it.
 *
 * The address is never assembled here. Blackboard hands one out on every content
 * item — an `/ultra/redirect?...` it resolves server-side — and reports its own
 * URL for a course, so the shape of a Blackboard page stays Blackboard's
 * business and these links keep working across Ultra and Original alike.
 */
export default function BbLink({ href, label = "Open in Blackboard",
                                 className = "bblink" }) {
  if (!href) return null;
  return (
    <a
      className={className}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={`${label} (opens in a new tab)`}
      // The row this sits in is usually a control of its own.
      onClick={(e) => e.stopPropagation()}
    >
      {label}
      <span className="ext" aria-hidden="true">↗</span>
    </a>
  );
}
