"""Turn supported files into plain text for the reflowing reader."""

import hashlib
import logging
import os
import re

logger = logging.getLogger(__name__)


def file_key(path):
    """Cache key that changes when the file is replaced or edited."""
    st = os.stat(path)
    raw = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def read_plain(path):
    with open(path, "rb") as f:
        data = f.read()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_markdown(path):
    """Strip the markdown syntax that just looks like noise on a tiny screen."""
    text = read_plain(path)
    text = re.sub(r"^```.*$", "", text, flags=re.M)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return text


def read_epub(path):
    from bs4 import BeautifulSoup
    import ebooklib
    from ebooklib import epub

    book = epub.read_epub(path, options={"ignore_ncx": True})
    parts = []
    # Spine order is reading order; fall back to all documents if it's empty.
    items = [book.get_item_with_id(idref) for idref, _ in book.spine]
    items = [i for i in items if i is not None and i.get_type() == ebooklib.ITEM_DOCUMENT]
    if not items:
        items = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
    for item in items:
        soup = BeautifulSoup(item.get_content(), "html.parser")
        for tag in soup(["script", "style", "head"]):
            tag.decompose()
        blocks = soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote"])
        if blocks:
            text = "\n".join(
                b.get_text(" ", strip=True) for b in blocks if not b.find(["p", "li"])
            )
        else:
            text = soup.get_text("\n", strip=True)
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts)


def read_pdf_text(path, cache_dir):
    """Extract PDF text with pdfminer, cached because it's slow on a Pi Zero."""
    cache_file = os.path.join(cache_dir, f"{file_key(path)}.txt")
    if os.path.exists(cache_file):
        return read_plain(cache_file)
    from pdfminer.high_level import extract_text

    text = extract_text(path) or ""
    # pdfminer hard-wraps at the PDF's line breaks; rejoin lines within a paragraph.
    text = text.replace("\x0c", "\n\n")
    text = re.sub(r"-\n(?=[a-z])", "", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    os.makedirs(cache_dir, exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        f.write(text)
    return text


def load_text(path, cache_dir):
    """Return the text of a .txt/.md/.epub/.pdf file, or raise."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".txt":
        return read_plain(path)
    if ext == ".md":
        return read_markdown(path)
    if ext == ".epub":
        return read_epub(path)
    if ext == ".pdf":
        return read_pdf_text(path, cache_dir)
    raise ValueError(f"Unsupported file type: {ext}")
