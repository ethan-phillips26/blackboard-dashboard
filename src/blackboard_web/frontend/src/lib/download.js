/**
 * Hand a saved file to the browser.
 *
 * `/api/files/...` already answers with `Content-Disposition: attachment`, so a
 * plain same-origin navigation is the whole download; the anchor exists only to
 * start one without leaving the page.
 */
export function save(url, filename) {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename ?? "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** The badge that stands in for a file icon: "PDF", "DOCX", "CSV". */
export function extension(filename) {
  const dot = (filename ?? "").lastIndexOf(".");
  const ext = dot > 0 ? filename.slice(dot + 1) : "";
  return /^[a-z0-9]{1,4}$/i.test(ext) ? ext.toUpperCase() : "FILE";
}
