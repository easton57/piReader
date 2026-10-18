"""Document model shared by the reader and progress sync.

A Book is a list of chapters; a chapter is a list of whitespace-normalized
paragraphs. A reading position is (chapter index, character offset into the
chapter), where the chapter text is its paragraphs joined by one separator
character. Unlike page numbers, positions survive font/layout changes and can
be translated to other readers.

For EPUBs, each chapter is one spine item (DocFragment) and keeps "anchors"
mapping text back to the element it came from, so positions convert to and
from KOReader/Koodo XPath pointers like
    /body/DocFragment[12]/body/div/p[3]/text().42
(index omitted when an element has no same-tag siblings, as both apps do).
"""

import bisect
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

_WS = re.compile(r"\s+")

# Elements that start a new paragraph on our screen.
BLOCK_TAGS = {
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre",
    "tr", "td", "th", "dd", "dt", "section", "article", "header", "footer",
    "figcaption", "aside", "ul", "ol", "table", "hr", "br", "body",
}
SKIP_TAGS = {"script", "style", "head", "title"}


@dataclass
class Anchor:
    start: int          # chapter offset where this text node's text begins
    path: str           # element path below <body>, e.g. "div/p[3]" ("" = body)
    text_index: int     # 1-based index of the text node among its parent's text nodes


@dataclass
class Chapter:
    paragraphs: List[str] = field(default_factory=list)
    starts: List[int] = field(default_factory=list)  # chapter offset of each paragraph
    anchors: List[Anchor] = field(default_factory=list)
    title: str = ""

    @property
    def length(self):
        if not self.paragraphs:
            return 0
        return self.starts[-1] + len(self.paragraphs[-1])

    def add_paragraph(self, text):
        start = self.length + 1 if self.paragraphs else 0
        self.paragraphs.append(text)
        self.starts.append(start)
        return start


@dataclass
class Book:
    chapters: List[Chapter]
    kind: str = "text"  # "epub" | "text"

    def __post_init__(self):
        self._offsets = []
        total = 0
        for ch in self.chapters:
            self._offsets.append(total)
            total += ch.length + 1
        self.total_chars = max(total - 1, 1)

    # --- percentage ---------------------------------------------------------

    def percentage(self, pos):
        ch, off = self.clamp(pos)
        return min(1.0, (self._offsets[ch] + off) / self.total_chars) if self.chapters else 0.0

    def position_from_percentage(self, pct):
        target = max(0.0, min(1.0, float(pct))) * self.total_chars
        ch = max(0, bisect.bisect_right(self._offsets, target) - 1)
        return self.clamp((ch, int(target - self._offsets[ch])))

    def clamp(self, pos):
        if not self.chapters:
            return (0, 0)
        ch = max(0, min(int(pos[0]), len(self.chapters) - 1))
        off = max(0, min(int(pos[1]), self.chapters[ch].length))
        return (ch, off)

    # --- XPath (EPUB only) --------------------------------------------------

    def to_xpath(self, pos) -> Optional[str]:
        if self.kind != "epub" or not self.chapters:
            return None
        ch, off = self.clamp(pos)
        anchors = self.chapters[ch].anchors
        head = f"/body/DocFragment[{ch + 1}]/body"
        if not anchors:
            return head
        i = max(0, bisect.bisect_right([a.start for a in anchors], off) - 1)
        a = anchors[i]
        text = "text()" if a.text_index == 1 else f"text()[{a.text_index}]"
        path = f"{head}/{a.path}" if a.path else head
        return f"{path}/{text}.{max(0, off - a.start)}"

    def from_xpath(self, xpath) -> Optional[Tuple[int, int]]:
        """Best-effort position for an XPath pointer; None if it can't be placed."""
        if self.kind != "epub" or not xpath:
            return None
        m = re.match(r"^/body/DocFragment\[(\d+)\](.*)$", xpath.strip())
        if not m:
            return None
        ch = int(m.group(1)) - 1
        if not 0 <= ch < len(self.chapters):
            return None
        rest = m.group(2)
        char_off = 0
        text_index = 1
        tm = re.search(r"/text\(\)(?:\[(\d+)\])?(?:\.(\d+))?$", rest)
        if tm:
            text_index = int(tm.group(1) or 1)
            char_off = int(tm.group(2) or 0)
            rest = rest[:tm.start()]
        rest = re.sub(r"^/body", "", rest).strip("/")
        anchors = self.chapters[ch].anchors

        def norm(path, drop_index):
            segs = [s for s in path.split("/") if s]
            if drop_index:
                segs = [re.sub(r"\[\d+\]$", "", s) for s in segs]
            else:  # "p[1]" and "p" mean the same element when written by different apps
                segs = [re.sub(r"\[1\]$", "", s) for s in segs]
            return "/".join(segs)

        for drop in (False, True):
            target = norm(rest, drop)
            # 1. Exact element (+ text node index).
            hits = [a for a in anchors if norm(a.path, drop) == target]
            if hits:
                a = next((h for h in hits if h.text_index == text_index), hits[0])
                return self.clamp((ch, a.start + char_off))
            # 2. Text lives in a descendant (Koodo points at the block element).
            prefix = target + "/" if target else ""
            hits = [a for a in anchors if norm(a.path, drop).startswith(prefix)]
            if hits:
                return self.clamp((ch, hits[0].start))
        # 3. Nearest existing ancestor.
        segs = [s for s in rest.split("/") if s]
        while segs:
            segs.pop()
            prefix = norm("/".join(segs), False)
            hits = [a for a in anchors if not prefix or norm(a.path, False).startswith(prefix)]
            if hits:
                return self.clamp((ch, hits[0].start))
        return (ch, 0)


# --- builders ----------------------------------------------------------------

def normalize(text):
    return _WS.sub(" ", text).strip()


def from_text(text, kind="text"):
    """Plain text: one chapter, one paragraph per line, blank lines kept (collapsed)."""
    ch = Chapter()
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for line in text.split("\n"):
        para = normalize(line.expandtabs(4))
        if para:
            ch.add_paragraph(para)
        elif ch.paragraphs and ch.paragraphs[-1] != "":
            ch.add_paragraph("")
    while ch.paragraphs and ch.paragraphs[-1] == "":
        ch.paragraphs.pop()
        ch.starts.pop()
    return Book([ch], kind=kind)


def element_path(el, body):
    """Path from <body> to el in KOReader/Koodo style ("div/p[3]")."""
    parts = []
    while el is not None and el is not body:
        parent = el.parent
        if parent is None:
            break
        same = parent.find_all(el.name, recursive=False)
        index = next(i for i, c in enumerate(same) if c is el) + 1
        parts.append(f"{el.name}[{index}]" if len(same) > 1 else el.name)
        el = parent
    return "/".join(reversed(parts))


def chapter_from_html(html):
    from bs4 import BeautifulSoup, Comment, NavigableString, Tag

    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup
    ch = Chapter()
    buf = []          # pieces of the current paragraph
    pending = []      # (offset within paragraph, path, text_index) for buf
    path_cache = {}

    def flush():
        para = "".join(buf).strip()
        if para:
            # Anchors were recorded relative to the unstripped buffer.
            lead = len("".join(buf)) - len("".join(buf).lstrip())
            start = ch.add_paragraph(para)
            for rel, path, idx in pending:
                ch.anchors.append(Anchor(start + max(0, rel - lead), path, idx))
        buf.clear()
        pending.clear()

    def walk(node):
        for child in list(node.children):
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                text = _WS.sub(" ", str(child))
                current = "".join(buf)
                if not current or current.endswith(" "):
                    text = text.lstrip()
                if not text:
                    continue
                parent = child.parent
                if id(parent) not in path_cache:
                    path_cache[id(parent)] = element_path(parent, body)
                siblings = [c for c in parent.children
                            if isinstance(c, NavigableString) and not isinstance(c, Comment)]
                # Identity, not ==: equal strings (e.g. two " " nodes) compare equal.
                index = next(i for i, c in enumerate(siblings) if c is child) + 1
                pending.append((len(current), path_cache[id(parent)], index))
                buf.append(text)
            elif isinstance(child, Tag):
                name = (child.name or "").lower()
                if name in SKIP_TAGS:
                    continue
                if name in BLOCK_TAGS:
                    flush()
                    walk(child)
                    flush()
                else:
                    walk(child)

    walk(body)
    flush()
    return ch


def from_epub(path):
    import ebooklib
    from ebooklib import epub

    book = epub.read_epub(path, options={"ignore_ncx": True})
    chapters = []
    # One chapter per spine item, *including* non-linear and non-document ones,
    # so chapter index == DocFragment index - 1 as in KOReader and Koodo.
    for idref, _linear in book.spine:
        item = book.get_item_with_id(idref)
        if item is None:
            continue
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            chapters.append(chapter_from_html(item.get_content()))
        else:
            chapters.append(Chapter())
    if not any(c.paragraphs for c in chapters):
        raise ValueError("EPUB has no readable text")
    return Book(chapters, kind="epub")
