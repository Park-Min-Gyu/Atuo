"""Turn a document into a list of pages (plain text)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

CHUNK_CHARS = 3000  # page size for formats that have no real pages

SUPPORTED = {".pdf", ".docx", ".txt", ".md"}


@dataclass
class Page:
    number: int  # 1-based
    text: str


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def chunk_paragraphs(paragraphs: list[str], limit: int = CHUNK_CHARS) -> list[str]:
    """Group paragraphs into pseudo-pages of about `limit` characters.

    A paragraph is never dropped; one longer than `limit` is split.
    """
    pieces: list[str] = []
    for para in paragraphs:
        para = para.strip()
        while len(para) > limit:
            pieces.append(para[:limit])
            para = para[limit:]
        if para:
            pieces.append(para)

    pages: list[str] = []
    current: list[str] = []
    size = 0
    for piece in pieces:
        if current and size + len(piece) > limit:
            pages.append("\n\n".join(current))
            current, size = [], 0
        current.append(piece)
        size += len(piece)
    if current:
        pages.append("\n\n".join(current))
    return pages


def load_pages(path: str | Path) -> list[Page]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        texts = [(page.extract_text() or "") for page in reader.pages]
    elif suffix == ".docx":
        import docx

        document = docx.Document(str(path))
        paragraphs = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                paragraphs.append(" | ".join(cell.text.strip() for cell in row.cells))
        texts = chunk_paragraphs(paragraphs)
    elif suffix in {".txt", ".md"}:
        raw = path.read_text(encoding="utf-8", errors="replace")
        if "\f" in raw:  # form feed = explicit page break
            texts = raw.split("\f")
        else:
            texts = chunk_paragraphs(raw.split("\n\n"))
    else:
        raise ValueError(f"지원하지 않는 형식입니다: {suffix} (지원: {sorted(SUPPORTED)})")
    return [Page(number=i, text=text.strip()) for i, text in enumerate(texts, start=1)]
