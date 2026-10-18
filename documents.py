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


def load_book(path, cache_dir):
    """Return a book.Book for a .txt/.md/.epub/.pdf file, or raise."""
    import book

    ext = os.path.splitext(path)[1].lower()
    if ext == ".epub":
        return _cached_epub(path, cache_dir)
    if ext == ".txt":
        return book.from_text(read_plain(path))
    if ext == ".md":
        return book.from_text(read_markdown(path))
    if ext == ".pdf":
        return book.from_text(read_pdf_text(path, cache_dir), kind="pdf")
    raise ValueError(f"Unsupported file type: {ext}")


BOOK_CACHE_VERSION = 1


def _cached_epub(path, cache_dir):
    """Parsing a big EPUB takes seconds on a Pi Zero; keep the parsed Book."""
    import pickle

    import book

    cache_file = os.path.join(cache_dir, f"{file_key(path)}.book")
    try:
        with open(cache_file, "rb") as f:
            version, cached = pickle.load(f)
        if version == BOOK_CACHE_VERSION:
            return cached
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"Ignoring bad book cache {cache_file}: {e}")
    parsed = book.from_epub(path)
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_file + ".tmp", "wb") as f:
            pickle.dump((BOOK_CACHE_VERSION, parsed), f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(cache_file + ".tmp", cache_file)
    except OSError as e:
        logger.warning(f"Couldn't cache parsed book: {e}")
    return parsed


def load_text(path, cache_dir):
    """Plain text of a document (paragraphs separated by newlines)."""
    b = load_book(path, cache_dir)
    return "\n\n".join("\n".join(c.paragraphs) for c in b.chapters if c.paragraphs)
