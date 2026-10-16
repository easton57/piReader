import pytest
from PIL import Image, ImageDraw

import config
from button import ButtonHandler
from documents import load_text
from file_browser import FileBrowser
from reader import TextReader


# --- button gestures ---------------------------------------------------------

def make_handler():
    h = ButtonHandler(gesture_window=0.5)
    fired = []
    for key in (1, 2, 3, 4, 5, "long"):
        h.on_click(key)(lambda k=key: fired.append(k))
    return h, fired


@pytest.mark.parametrize("events,expected", [
    (["single"], 1),
    (["double"], 2),
    (["double", "single"], 3),
    (["double", "double"], 4),
    (["double", "double", "single"], 5),
])
def test_tap_gestures(events, expected):
    h, fired = make_handler()
    t = 0.0
    for e in events:
        h.feed(e, now=t)
        h.poll(now=t + 0.1)  # still inside the window: nothing fires yet
        t += 0.3
    assert fired == []
    h.poll(now=t + 1.0)
    assert fired == [expected]


def test_long_press_fires_immediately_and_cancels_taps():
    h, fired = make_handler()
    h.feed("single", now=0)
    h.feed("long", now=0.2)
    h.poll(now=5)
    assert fired == ["long"]


def test_separate_gestures():
    h, fired = make_handler()
    h.feed("single", now=0)
    h.poll(now=1)
    h.feed("double", now=2)
    h.poll(now=3)
    assert fired == [1, 2]


# --- text reader -------------------------------------------------------------

def test_wraps_long_lines_within_width():
    r = TextReader()
    r.load_text(("word " * 400).strip())
    img = Image.new("L", (r.WIDTH, r.HEIGHT))
    draw = ImageDraw.Draw(img)
    assert r.get_total_pages() > 1
    for page in r.pages:
        assert len(page) <= r.lines_per_page
        for line in page:
            assert draw.textlength(line, font=r.font) <= r.max_width


def test_breaks_overlong_words():
    r = TextReader()
    r.load_text("x" * 500)
    assert "".join(sum(r.pages, [])) == "x" * 500


def test_navigation_and_bounds():
    r = TextReader()
    r.load_text("\n\n".join(f"Paragraph {i}" for i in range(100)))
    assert not r.prev_page()
    r.set_page(10_000)
    assert r.get_current_page() == r.get_total_pages() - 1
    assert not r.next_page()
    assert r.render().size == (config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT)


def test_empty_text():
    r = TextReader()
    r.load_text("")
    assert r.get_total_pages() == 1
    r.render()


# --- documents ---------------------------------------------------------------

def test_txt_non_utf8(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes("caf\xe9".encode("cp1252"))
    assert load_text(str(p), str(tmp_path)) == "café"


def test_markdown_stripped(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("# Title\n\nSome **bold** and [a link](http://x).")
    text = load_text(str(p), str(tmp_path))
    assert "#" not in text and "**" not in text and "a link" in text and "http" not in text


def test_epub(tmp_path):
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("id1")
    book.set_title("T")
    ch = epub.EpubHtml(title="c1", file_name="c1.xhtml")
    ch.content = "<html><body><h1>Chapter One</h1><p>Hello there.</p><p>Second para.</p></body></html>"
    book.add_item(ch)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = [ch]
    path = str(tmp_path / "b.epub")
    epub.write_epub(path, book)

    text = load_text(path, str(tmp_path))
    assert "Chapter One" in text and "Hello there." in text and "Second para." in text


def make_pdf(path, pages=2):
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(path)
    for i in range(pages):
        c.drawString(72, 720, f"This is page {i + 1} of the test document.")
        c.showPage()
    c.save()


def test_pdf_text_and_images(tmp_path):
    pytest.importorskip("reportlab")
    path = str(tmp_path / "d.pdf")
    make_pdf(path)
    text = load_text(path, str(tmp_path / "cache"))
    assert "page 2 of the test document" in text

    from pdf_reader import PDFReader

    pr = PDFReader(cache_dir=str(tmp_path / "cache"))
    assert pr.open(path)
    assert pr.get_total_pages() == 2
    assert pr.render().size == (config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT)
    assert pr.next_page() and not pr.next_page()


def test_bad_pdf_does_not_open(tmp_path):
    from pdf_reader import PDFReader

    p = tmp_path / "bad.pdf"
    p.write_text("not a pdf")
    assert not PDFReader(cache_dir=str(tmp_path)).open(str(p))


# --- file browser ------------------------------------------------------------

def test_browser_dirs_first_and_navigation(tmp_path):
    lib = tmp_path / "lib"
    (lib / "Sub").mkdir(parents=True)
    (lib / "Sub" / "inner.txt").write_text("x")
    (lib / "b.txt").write_text("x")
    (lib / "a.epub").write_text("x")
    (lib / "ignore.exe").write_text("x")
    (lib / ".hidden.txt").write_text("x")
    fb = FileBrowser(str(lib), str(tmp_path / "state.json"))
    assert fb.get_items_for_display() == [("Sub", True), ("a.epub", False), ("b.txt", False)]

    fb.enter(fb.get_selected_path())
    assert fb.get_items_for_display() == [("inner.txt", False)]
    assert fb.go_up() and fb.get_selected_path().endswith("Sub")
    assert not fb.go_up()  # already at root


def test_progress_persists(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "a.txt").write_text("x")
    state = str(tmp_path / "state.json")
    fb = FileBrowser(str(lib), state)
    fb.save_progress(str(lib / "a.txt"), 7, "reader")
    fb2 = FileBrowser(str(lib), state)
    assert fb2.get_progress(str(lib / "a.txt")) == {"page": 7, "mode": "reader"}
    assert fb2.get_last_open() == str(lib / "a.txt")


def test_refresh_keeps_cursor(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    for n in ("b.txt", "c.txt"):
        (lib / n).write_text("x")
    fb = FileBrowser(str(lib), str(tmp_path / "s.json"))
    fb.move_down()
    assert fb.get_selected_path().endswith("c.txt")
    (lib / "a.txt").write_text("x")
    fb.refresh_if_changed()
    assert fb.get_selected_path().endswith("c.txt")
