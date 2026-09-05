/**
 * Reading a handout in the page instead of in whatever the OS opens.
 *
 * Everything here runs in the browser. Nothing is converted on the machine
 * serving the app, because that machine is not always this machine and is not
 * always going to have an office suite on it: a PDF, a picture, a video and a
 * text file are things the browser already draws, and Word and PowerPoint are
 * zip archives of XML that can be read where they are shown.
 */

const KINDS = {
  pdf: ["pdf"],
  image: ["png", "jpg", "jpeg", "gif", "webp", "bmp", "avif", "heic"],
  video: ["mp4", "webm", "m4v", "ogv"],
  audio: ["mp3", "wav", "m4a", "ogg", "oga", "flac"],
  docx: ["docx"],
  pptx: ["pptx"],
  text: ["txt", "md", "markdown", "csv", "tsv", "json", "log", "yml", "yaml",
         "py", "js", "jsx", "ts", "tsx", "java", "c", "h", "cpp", "cs", "go",
         "rs", "rb", "php", "sh", "sql", "r", "m", "css", "html", "htm", "xml"],
};

const BY_EXTENSION = Object.fromEntries(
  Object.entries(KINDS).flatMap(([kind, exts]) => exts.map((e) => [e, kind]))
);

/** The extension, lowercased, or "" for a name that has none. */
export function suffix(filename) {
  const dot = (filename ?? "").lastIndexOf(".");
  return dot > 0 ? filename.slice(dot + 1).toLowerCase() : "";
}

/** How this file can be shown, or "none" if it can only be downloaded. */
export function kindOf(filename) {
  return BY_EXTENSION[suffix(filename)] ?? "none";
}

export const canView = (filename) => kindOf(filename) !== "none";

/** What to say when a file cannot be drawn — the format, not a shrug. */
export function whyNot(filename) {
  const ext = suffix(filename);
  const legacy = { doc: "Word", ppt: "PowerPoint", xls: "Excel" }[ext];
  if (legacy) {
    return `.${ext} is ${legacy}'s pre-2007 binary format, which browsers cannot ` +
      `read. Download it, or open it as a Google Doc.`;
  }
  return ext
    ? `.${ext} files cannot be shown in a browser.`
    : "This file has no extension, so there is no telling how to show it.";
}

/* ------------------------------------------------------------------ pptx */

const NS = {
  a: "http://schemas.openxmlformats.org/drawingml/2006/main",
  p: "http://schemas.openxmlformats.org/presentationml/2006/main",
  r: "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
  pr: "http://schemas.openxmlformats.org/package/2006/relationships",
};

const parse = (xml) => new DOMParser().parseFromString(xml, "application/xml");

/** One paragraph of drawing-ML text, with its line breaks kept. */
function paragraphText(node) {
  let out = "";
  for (const child of node.childNodes) {
    if (child.namespaceURI !== NS.a) continue;
    if (child.localName === "r") out += child.textContent;
    else if (child.localName === "br") out += "\n";
    else if (child.localName === "fld") out += child.textContent;
  }
  return out.trim();
}

/** `rId7` → `ppt/media/image3.png`, resolved against the part that named it. */
function relationships(doc, base) {
  const map = new Map();
  if (!doc) return map;
  for (const rel of doc.getElementsByTagNameNS(NS.pr, "Relationship")) {
    const target = rel.getAttribute("Target") ?? "";
    if (/^https?:/i.test(target)) continue;
    const parts = `${base}/${target}`.split("/");
    const resolved = [];
    for (const part of parts) {
      if (part === "." || part === "") continue;
      if (part === "..") resolved.pop();
      else resolved.push(part);
    }
    map.set(rel.getAttribute("Id"), resolved.join("/"));
  }
  return map;
}

/** The slides in the order the deck plays them, not the order the zip lists. */
function slideOrder(presentation, rels) {
  const ids = presentation
    ? [...presentation.getElementsByTagNameNS(NS.p, "sldId")]
    : [];
  const ordered = ids
    .map((n) => rels.get(n.getAttributeNS(NS.r, "id")))
    .filter(Boolean);
  return ordered;
}

/**
 * Read a .pptx into something a page can show: each slide's title, its text in
 * reading order, its pictures, and whatever the speaker notes say.
 *
 * This is the deck's content rather than a photograph of its layout — the point
 * is to be able to read the lecture without leaving the tab, and the original is
 * one button away for anything that needs its real design.
 *
 * @returns {Promise<{slides: Array, urls: string[]}>} `urls` are object URLs the
 *   caller must revoke when the viewer closes.
 */
export async function readDeck(buffer) {
  const JSZip = (await import("jszip")).default;
  const zip = await JSZip.loadAsync(buffer);
  const urls = [];

  const read = async (path) => {
    const entry = zip.file(path);
    return entry ? parse(await entry.async("string")) : null;
  };

  const presentation = await read("ppt/presentation.xml");
  const deckRels = relationships(await read("ppt/_rels/presentation.xml.rels"), "ppt");
  let paths = slideOrder(presentation, deckRels);
  if (!paths.length) {
    paths = Object.keys(zip.files)
      .filter((p) => /^ppt\/slides\/slide\d+\.xml$/.test(p))
      .sort((a, b) => (+a.match(/\d+/)[0]) - (+b.match(/\d+/)[0]));
  }

  const slides = [];
  for (const [i, path] of paths.entries()) {
    const doc = await read(path);
    if (!doc) continue;
    const dir = path.slice(0, path.lastIndexOf("/"));
    const name = path.slice(path.lastIndexOf("/") + 1);
    const rels = relationships(await read(`${dir}/_rels/${name}.rels`), dir);

    // The title placeholder, so the slide has a heading rather than a first line.
    let title = "";
    const lines = [];
    for (const shape of doc.getElementsByTagNameNS(NS.p, "sp")) {
      const placeholder = shape.getElementsByTagNameNS(NS.p, "ph")[0];
      const type = placeholder?.getAttribute("type") ?? "";
      const text = [...shape.getElementsByTagNameNS(NS.a, "p")]
        .map(paragraphText).filter(Boolean);
      if (!text.length) continue;
      if (!title && (type === "title" || type === "ctrTitle")) title = text.shift();
      lines.push(...text);
    }

    const images = [];
    for (const blip of doc.getElementsByTagNameNS(NS.a, "blip")) {
      const file = zip.file(rels.get(blip.getAttributeNS(NS.r, "embed")) ?? "");
      if (!file) continue;
      const url = URL.createObjectURL(await file.async("blob"));
      urls.push(url);
      images.push(url);
    }

    // The notes carry the half of a lecture the slide only gestures at.
    const notesDoc = await read(`ppt/notesSlides/notesSlide${i + 1}.xml`);
    const notes = notesDoc
      ? [...notesDoc.getElementsByTagNameNS(NS.a, "p")]
          .map(paragraphText).filter(Boolean)
          // The notes part repeats the slide number as its own paragraph.
          .filter((line) => line !== String(i + 1))
      : [];

    slides.push({ number: i + 1, title, lines, images, notes });
  }
  return { slides, urls };
}
