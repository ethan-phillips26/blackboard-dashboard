"""Pull plain text out of the file formats syllabi actually arrive in."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path


def _xml_text(xml: bytes, para_tags: tuple[str, ...]) -> str:
    text = xml.decode("utf-8", "ignore")
    for tag in para_tags:
        text = text.replace(f"</{tag}>", "\n")
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def from_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        return _xml_text(z.read("word/document.xml"), ("w:p",))


def from_pptx(path: Path) -> str:
    out: list[str] = []
    with zipfile.ZipFile(path) as z:
        slides = sorted(n for n in z.namelist()
                        if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
        for i, name in enumerate(slides, 1):
            body = _xml_text(z.read(name), ("a:p",))
            if body:
                out.append(f"--- Slide {i} ---\n{body}")
    return "\n\n".join(out)


def from_pdf(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages).strip()


class UnreadableFile(RuntimeError):
    """The file exists but its text could not be recovered."""


def extract(path: str | Path) -> str:
    """Plain text from a file, or "" for a format we do not handle.

    A missing file raises rather than returning "" — silently treating an absent
    syllabus as an empty one leads to a confidently wrong grade weighting.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No such file: {path}")
    suffix = path.suffix.lower()
    try:
        if suffix == ".docx":
            return from_docx(path)
        if suffix == ".pptx":
            return from_pptx(path)
        if suffix == ".pdf":
            return from_pdf(path)
        if suffix in (".txt", ".md", ".csv"):
            return path.read_text("utf-8", "ignore").strip()
        if suffix in (".html", ".htm"):
            from blackboard_mcp.client import html_to_text
            return html_to_text(path.read_text("utf-8", "ignore"))
    except Exception as e:
        raise UnreadableFile(f"Could not read text from {path.name}: {e}") from e
    return ""
