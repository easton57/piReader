"""Position model, XPath mapping and KOReader-sync (kosync) tests."""

import hashlib
import json
import os
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import book as bookmod
import config
import kosync
import main
from reader import TextReader

# --- fixtures ----------------------------------------------------------------

CH1 = "<html><body><h1>Chapter One</h1><p>It was a dark night.</p></body></html>"
CH2 = """<html><head><title>x</title><style>p{}</style></head><body>
<div class="chapter">
  <h2>Two</h2>
  <p>First <em>emphasised</em> paragraph of two.</p>
  <p>Second   paragraph,
     with odd   whitespace.</p>
  <p><span>Third</span> paragraph <b>bold</b> tail.</p>
</div>
<p>Outside the div.</p>
</body></html>"""
LONG = "<html><body>" + "".join(
    f"<p>Paragraph {i} " + "lorem ipsum dolor sit amet " * 6 + "</p>" for i in range(120)
) + "</body></html>"


def make_epub(path, chapters, nonlinear_cover=True):
    from ebooklib import epub

    b = epub.EpubBook()
    b.set_identifier("id")
    b.set_title("T")
    b.set_language("en")
    spine = []
    if nonlinear_cover:
        cover = epub.EpubHtml(title="cover", file_name="cover.xhtml")
        cover.content = "<html><body><img src='c.jpg'/></body></html>"
        b.add_item(cover)
        spine.append((cover, "no"))
    for i, html in enumerate(chapters):
        c = epub.EpubHtml(title=f"c{i}", file_name=f"c{i}.xhtml")
        c.content = html
        b.add_item(c)
        spine.append(c)
    b.add_item(epub.EpubNcx())
    b.add_item(epub.EpubNav())
    b.spine = spine
    epub.write_epub(str(path), b)
    return str(path)


@pytest.fixture
def epub_book(tmp_path):
    return bookmod.from_epub(make_epub(tmp_path / "b.epub", [CH1, CH2, LONG]))


# --- partial MD5 -------------------------------------------------------------

def js_partial_md5(data):
    """Straight port of Koodo's JS (32-bit `<<`) as an independent reference."""
    def js_shl(a, b):
        r = (a << (b & 31)) & 0xFFFFFFFF
        return r - (1 << 32) if r & 0x80000000 else r

    md5 = hashlib.md5()
    for i in range(-1, 11):
        pos = js_shl(1024, 2 * i)
        if pos >= len(data):
            break
        chunk = data[pos:min(pos + 1024, len(data))]
        if not chunk:
            break
        md5.update(chunk)
    return md5.hexdigest()


@pytest.mark.parametrize("size", [10, 1500, 5000, 300_000, 3_000_000])
def test_partial_md5_matches_koodo(tmp_path, size):
    data = os.urandom(size)
    p = tmp_path / "f.bin"
    p.write_bytes(data)
    assert kosync.partial_md5(str(p)) == js_partial_md5(data)


# --- book model / xpath ------------------------------------------------------

def test_docfragment_indexing_counts_nonlinear_spine(epub_book):
    assert len(epub_book.chapters) == 4  # cover (non-linear) + 3
    assert epub_book.chapters[0].paragraphs == []
    assert epub_book.chapters[2].paragraphs == [
        "Two", "First emphasised paragraph of two.",
        "Second paragraph, with odd whitespace.",
        "Third paragraph bold tail.", "Outside the div.",
    ]


def test_to_xpath_format(epub_book):
    ch = epub_book.chapters[2]
    second = ch.starts[2]
    assert epub_book.to_xpath((2, second + 7)) == "/body/DocFragment[3]/body/div/p[2]/text().7"
    assert epub_book.to_xpath((2, ch.starts[4])) == "/body/DocFragment[3]/body/p/text().0"
    # Inside <em>: text node belongs to the em element.
    em = ch.starts[1] + len("First ")
    assert epub_book.to_xpath((2, em + 2)) == "/body/DocFragment[3]/body/div/p[1]/em/text().2"
    # Text after <em> is the 2nd text node of the <p>.
    tail = ch.starts[1] + len("First emphasised ")
    assert epub_book.to_xpath((2, tail)) == "/body/DocFragment[3]/body/div/p[1]/text()[2].1"


def test_xpath_roundtrip(epub_book):
    for ci, ch in enumerate(epub_book.chapters):
        for off in range(0, ch.length, 7):
            x = epub_book.to_xpath((ci, off))
            assert epub_book.from_xpath(x) == (ci, off), x


@pytest.mark.parametrize("xpath,expected_para", [
    ("/body/DocFragment[3]/body/div/p[3]", 3),             # Koodo: element only
    ("/body/DocFragment[3]/body/div[1]/p[3]/text().0", 3),  # explicit [1]
    ("/body/DocFragment[3]/body/div/p[2]/text().0", 2),
    ("/body/DocFragment[3]/body/div", 0),                   # container -> first text
    ("/body/DocFragment[3]/body/div/p[9]/text().0", 1),     # no p[9]: index-insensitive match
    ("/body/DocFragment[3]/body/section/p/text().3", 0),    # unknown path -> chapter
    ("/body/DocFragment[3]", 0),
])
def test_from_xpath_fallbacks(epub_book, xpath, expected_para):
    ch, off = epub_book.from_xpath(xpath)
    assert ch == 2
    para = max(i for i, s in enumerate(epub_book.chapters[2].starts) if s <= off)
    assert para == expected_para


def test_from_xpath_rejects_garbage(epub_book):
    assert epub_book.from_xpath("/body/DocFragment[99]/body/p") is None
    assert epub_book.from_xpath("12") is None
    assert bookmod.from_text("x").from_xpath("/body/DocFragment[1]") is None


def test_percentage_roundtrip(epub_book):
    for pct in (0, 0.1, 0.5, 0.93, 1):
        pos = epub_book.position_from_percentage(pct)
        assert abs(epub_book.percentage(pos) - pct) < 0.01


def test_position_survives_font_change(epub_book):
    small, big = TextReader(font_size=10), TextReader(font_size=14)
    small.load_book(epub_book)
    big.load_book(epub_book)
    assert big.get_total_pages() > small.get_total_pages()
    small.set_page(small.get_total_pages() // 2)
    big.set_position(small.get_position())
    # The big-font page shows the same text the small-font page started with.
    ps, pb = small.get_position(), big.get_position()
    assert pb[0] == ps[0] and pb <= ps
    big.next_page()
    assert big.get_position() > ps


def test_chapters_start_new_pages(epub_book):
    r = TextReader()
    r.load_book(epub_book)
    starts = r.page_starts
    assert (1, 0) in starts and (2, 0) in starts and (3, 0) in starts


# --- fake kosync server -----------------------------------------------------

class KoSyncServer:
    def __init__(self):
        self.users = {}
        self.progress = {}
        self.puts = []
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _authed(self):
                u, k = self.headers.get("x-auth-user"), self.headers.get("x-auth-key")
                return u if u in srv.users and srv.users[u] == k else None

            def _body(self):
                return json.loads(self.rfile.read(int(self.headers["Content-Length"])))

            def do_POST(self):
                if self.path == "/users/create":
                    b = self._body()
                    if b["username"] in srv.users:
                        return self._send(402, {"message": "taken"})
                    srv.users[b["username"]] = b["password"]
                    return self._send(201, {"username": b["username"]})
                self._send(404, {})

            def do_GET(self):
                user = self._authed()
                if not user:
                    return self._send(401, {"message": "Unauthorized"})
                if self.path == "/users/auth":
                    return self._send(200, {"authorized": "OK"})
                if self.path.startswith("/syncs/progress/"):
                    doc = self.path.rsplit("/", 1)[1]
                    return self._send(200, srv.progress.get((user, doc), {}))
                self._send(404, {})

            def do_PUT(self):
                user = self._authed()
                if not user:
                    return self._send(401, {})
                b = self._body()
                b["timestamp"] = int(time.time())
                srv.progress[(user, b["document"])] = b
                srv.puts.append(b)
                self._send(200, {"document": b["document"], "timestamp": b["timestamp"]})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = KoSyncServer()
    yield s
    s.close()


def test_client_register_auth_progress(server):
    c = kosync.KoSyncClient(server.url, "me", kosync.password_key("pw"))
    c.register()
    with pytest.raises(kosync.KoSyncError, match="taken"):
        c.register()
    assert c.auth()
    with pytest.raises(kosync.KoSyncError, match="Login failed"):
        kosync.KoSyncClient(server.url, "me", kosync.password_key("nope")).auth()
    assert c.get_progress("abc") is None
    c.put_progress("abc", "/body/DocFragment[2]/body/p/text().0", 0.25, "DEV")
    got = c.get_progress("abc")
    assert got["percentage"] == 0.25 and got["device"] == "piReader" and got["device_id"] == "DEV"


def test_worker_coalesces_and_flushes(server):
    key = kosync.password_key("pw")
    server.users["me"] = key
    w = kosync.SyncWorker(lambda: kosync.KoSyncClient(server.url, "me", key), delay=30)
    for i in range(5):
        w.push("doc", f"/body/DocFragment[1]/body/p[{i + 1}]", i / 10)
    time.sleep(0.3)
    assert server.puts == []  # still waiting for the delay
    w.flush(timeout=3)
    assert len(server.puts) == 1 and server.puts[0]["progress"].endswith("p[5]")


# --- end to end through the app ---------------------------------------------

@pytest.fixture
def app_env(server, tmp_path):
    shutil.rmtree(config.BASE_DIR, ignore_errors=True)
    os.makedirs(config.LIBRARY_PATH)
    path = make_epub(os.path.join(config.LIBRARY_PATH, "novel.epub"), [CH1, CH2, LONG])
    key = kosync.password_key("pw")
    server.users["me"] = key
    kosync.save_settings(server.url, "me", key)
    yield path, kosync.partial_md5(path)
    shutil.rmtree(config.BASE_DIR, ignore_errors=True)


def new_app():
    a = main.Application(mock=True)
    a.sync.delay = 0.1
    return a


def test_pull_newer_remote_on_open(server, app_env):
    path, doc = app_env
    # Koodo on the phone read into chapter 4 (DocFragment[4]).
    server.progress[("me", doc)] = {
        "document": doc, "progress": "/body/DocFragment[4]/body/p[60]",
        "percentage": 0.7, "device": "Koodo Reader", "device_id": "PHONE",
        "timestamp": int(time.time()),
    }
    a = new_app()
    assert a._open(path)
    target = (3, a.reader.book.chapters[3].starts[59])  # <p> #60
    page = a.reader.get_current_page()
    assert a.reader.page_starts[page] <= target < a.reader.page_starts[page + 1]
    assert server.puts == []  # opening never pushes


def test_page_turns_push_xpath(server, app_env):
    path, doc = app_env
    a = new_app()
    a._open(path)
    a._handle_actions(["next", "next", "next"])
    a.sync.flush(timeout=3)
    last = server.puts[-1]
    assert last["document"] == doc
    assert last["progress"].startswith("/body/DocFragment[")
    assert "/text()" in last["progress"]
    assert a.reader.book.from_xpath(last["progress"]) == a.reader.get_position()


def test_older_remote_is_ignored(server, app_env):
    path, doc = app_env
    a = new_app()
    a._open(path)
    a._handle_actions(["next"] * 4)
    mine = a.reader.get_position()
    a._handle_actions(["long"])  # back to list, flushes
    a.sync.flush(timeout=3)
    server.progress[("me", doc)].update(
        progress="/body/DocFragment[2]/body/h1", device_id="PHONE", timestamp=1)
    a._open(path)
    assert a.reader.get_position() == mine


def test_offline_open_does_not_overwrite(server, app_env):
    path, doc = app_env
    url = server.url
    server.close()  # server unreachable
    a = new_app()
    a._open(path)
    a.sync.flush(timeout=1)
    assert server.puts == []
    kosync.save_settings(url, "me", "x")  # still configured, still down
    a._open(path)
    assert a.mode == main.AppMode.READER


def test_webui_kosync_card(server):
    import webui

    shutil.rmtree(config.BASE_DIR, ignore_errors=True)
    c = webui.app.test_client()
    r = c.post("/kosync", data={"url": server.url, "user": "new", "password": "pw",
                                "register": "on"}, follow_redirects=True)
    assert b"Registered" in r.data
    assert server.users["new"] == kosync.password_key("pw")
    r = c.post("/kosync", data={"url": server.url, "user": "new", "password": ""},
               follow_redirects=True)
    assert b"Connected" in r.data
    r = c.post("/kosync", data={"url": server.url, "user": "new", "password": "bad"},
               follow_redirects=True)
    assert b"Login failed" in r.data
    c.post("/kosync", data={"url": ""})
    assert not os.path.exists(config.KOSYNC_SETTINGS_FILE)
