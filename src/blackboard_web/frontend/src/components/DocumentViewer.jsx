import { useCallback, useEffect, useRef, useState } from "react";
import { extension } from "../lib/download.js";
import { filesize } from "../lib/format.js";
import { READER, useTitle } from "../lib/title.js";
import { kindOf, readDeck, whyNot } from "../lib/viewer.js";

/**
 * A handout, read in the page.
 *
 * The browser already draws a PDF, a picture, a video and a text file, so those
 * are pointed straight at the file. Word and PowerPoint it cannot, so they are
 * read here rather than converted on the server: the machine serving this app is
 * not necessarily the machine you are reading on, and nothing about the feature
 * should depend on an office suite being installed on either of them.
 *
 * Anything else says what it is and offers the download, which is the honest
 * answer and better than an empty frame.
 */
export default function DocumentViewer({ file, onClose, onDocs, docsBusy, docsUrl }) {
  const kind = kindOf(file.filename);
  const [status, setStatus] = useState(kind === "docx" || kind === "pptx" ? "loading" : "ready");
  const [error, setError] = useState(null);
  const [text, setText] = useState(null);
  const [deck, setDeck] = useState(null);
  const docxRef = useRef(null);
  const closeRef = useRef(null);

  // Whatever opened this reader — a deadline's handout or a file in the course
  // materials — the document on screen is now the thing being read, so it takes
  // the tab and gives it back on close.
  useTitle([file.filename], READER);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const source = file.view ?? file.url;

  // Word and PowerPoint are read here; everything else is pointed at the file.
  useEffect(() => {
    if (kind !== "docx" && kind !== "pptx" && kind !== "text") return;
    let live = true;
    let urls = [];
    setStatus("loading");
    setError(null);

    (async () => {
      const res = await fetch(source);
      if (!res.ok) throw new Error(`The file could not be read (${res.status}).`);
      if (kind === "text") {
        const body = await res.text();
        if (live) setText(body);
        return;
      }
      const buffer = await res.arrayBuffer();
      if (!live) return;
      if (kind === "pptx") {
        const read = await readDeck(buffer);
        urls = read.urls;
        if (live) setDeck(read.slides);
        return;
      }
      // docx-preview writes the document's own styles into the container, so it
      // is loaded only for the documents that need it.
      const { renderAsync } = await import("docx-preview");
      if (!live || !docxRef.current) return;
      await renderAsync(buffer, docxRef.current, null, {
        className: "docx", inWrapper: true, breakPages: true,
        ignoreLastRenderedPageBreak: true, useBase64URL: true,
      });
    })()
      .catch((e) => live && setError(e.message))
      .finally(() => live && setStatus("ready"));

    return () => {
      live = false;
      urls.forEach(URL.revokeObjectURL);
    };
  }, [kind, source]);

  const size = filesize(file.bytes);
  const busy = status === "loading";

  const body = useCallback(() => {
    if (error) {
      return (
        <div className="viewer-fallback">
          <p className="err">{error}</p>
          <a className="btn primary" href={file.url} download={file.filename}>
            Download it instead
          </a>
        </div>
      );
    }
    switch (kind) {
      case "pdf":
        return <iframe className="viewer-frame" src={source} title={file.filename} />;
      case "image":
        return <img className="viewer-image" src={source} alt={file.filename} />;
      case "video":
        return <video className="viewer-media" src={source} controls autoPlay={false} />;
      case "audio":
        return <audio className="viewer-media" src={source} controls />;
      case "text":
        return text == null ? null : <pre className="viewer-text">{text}</pre>;
      case "docx":
        return <div className="viewer-docx" ref={docxRef} />;
      case "pptx":
        return deck ? <Deck slides={deck} /> : null;
      default:
        return (
          <div className="viewer-fallback">
            <p className="note">{whyNot(file.filename)}</p>
            <div className="row">
              <a className="btn primary" href={file.url} download={file.filename}>
                Download
              </a>
              {onDocs && (
                <button onClick={onDocs} disabled={!!docsBusy}>
                  {docsBusy ? <><span className="spin" /> Opening</> : "Open in Docs"}
                </button>
              )}
            </div>
          </div>
        );
    }
  }, [kind, error, source, text, deck, file, onDocs, docsBusy]);

  return (
    <>
      <div className="scrim over" onClick={onClose} />
      <div className="viewer" role="dialog" aria-modal="true" aria-label={file.filename}>
        <div className="viewer-head">
          <span className="fileext" aria-hidden="true">{extension(file.filename)}</span>
          <div className="viewer-id">
            <b title={file.filename}>{file.filename}</b>
            <span className="note dim">
              {size}
              {kind === "docx" || kind === "pptx" ? " · read in the browser" : ""}
            </span>
          </div>
          <div className="spacer" />
          {docsUrl ? (
            <a className="btn" href={docsUrl} target="_blank" rel="noreferrer">
              Reopen in Docs
            </a>
          ) : onDocs && kind !== "none" ? (
            <button onClick={onDocs} disabled={!!docsBusy}>
              {docsBusy ? <><span className="spin" /> Opening</> : "Open in Docs"}
            </button>
          ) : null}
          <a className="btn" href={file.url} download={file.filename}>Download</a>
          <button ref={closeRef} onClick={onClose}>Close</button>
        </div>

        <div className={`viewer-body kind-${error ? "none" : kind}`}>
          {body()}
          {busy && (
            <p className="viewer-busy">
              <span className="spin" /> Reading {extension(file.filename)}…
            </p>
          )}
        </div>
      </div>
    </>
  );
}

/** A deck as something you can read:each slide's heading, its text, its pictures. */
function Deck({ slides }) {
  if (!slides?.length) {
    return <p className="empty viewer-busy">This deck has no slides in it.</p>;
  }
  return (
    <div className="deck">
      {slides.map((slide) => (
        <section className="slide" key={slide.number}>
          <div className="slide-no">Slide {slide.number}</div>
          {slide.title && <h3>{slide.title}</h3>}
          {slide.images.length > 0 && (
            <div className="slide-images">
              {slide.images.map((src) => (
                <img key={src} src={src} alt="" loading="lazy" />
              ))}
            </div>
          )}
          {slide.lines.length > 0 && (
            <ul className="slide-lines">
              {slide.lines.map((line, i) => <li key={i}>{line}</li>)}
            </ul>
          )}
          {slide.notes.length > 0 && (
            <details className="slide-notes">
              <summary>Speaker notes</summary>
              {slide.notes.map((line, i) => <p key={i}>{line}</p>)}
            </details>
          )}
          {!slide.title && !slide.lines.length && !slide.images.length && (
            <p className="empty">Nothing on this slide but its layout.</p>
          )}
        </section>
      ))}
    </div>
  );
}
