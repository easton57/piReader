"""Cloud folder tests against a real WebDAV server (wsgidav, run in-process).

pip install wsgidav cheroot   (skipped if missing)
"""

import os
import shutil
import socket
import threading
import time

import pytest

wsgidav = pytest.importorskip("wsgidav.wsgidav_app")
cheroot_wsgi = pytest.importorskip("cheroot.wsgi")

import config  # noqa: E402
import main  # noqa: E402
import webdav  # noqa: E402
from cloud import CloudSource  # noqa: E402
from file_browser import FileBrowser  # noqa: E402

USER, PASSWORD = "reader", "s3cret"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "dav"
    (root / "Books" / "Sci Fi").mkdir(parents=True)
    (root / "Books" / "Dune & Co.txt").write_text("Remote book text.\n\nSecond paragraph.")
    (root / "Books" / "Sci Fi" / "Foundation.md").write_text("# Foundation\n\nPsychohistory.")
    (root / "Books" / "cover.jpg").write_bytes(b"\xff\xd8")
    (root / "Books" / ".hidden.txt").write_text("x")
    port = free_port()
    app = wsgidav.WsgiDAVApp({
        "host": "127.0.0.1", "port": port,
        "provider_mapping": {"/remote.php/dav/files/reader": str(root)},
        "simple_dc": {"user_mapping": {"*": {USER: {"password": PASSWORD}}}},
        "http_authenticator": {"accept_basic": True, "accept_digest": False,
                               "default_to_digest": False},
        "verbose": 0, "logging": {"enable": False},
    })
    srv = cheroot_wsgi.Server(("127.0.0.1", port), app)
    t = threading.Thread(target=srv.start, daemon=True)
    t.start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.05)
    yield {"url": f"http://127.0.0.1:{port}/remote.php/dav/files/reader/Books", "root": root}
    srv.stop()


def test_client_list_and_download(server, tmp_path):
    c = webdav.WebDAVClient(server["url"], USER, PASSWORD)
    names = {(e["name"], e["is_dir"]) for e in c.list("")}
    assert ("Dune & Co.txt", False) in names and ("Sci Fi", True) in names
    assert {e["name"] for e in c.list("Sci Fi")} == {"Foundation.md"}

    dest = tmp_path / "out" / "Foundation.md"
    c.download("Sci Fi/Foundation.md", str(dest))
    assert dest.read_text().startswith("# Foundation")
    assert not os.path.exists(str(dest) + ".part")


def test_bad_password_and_unreachable(server):
    with pytest.raises(webdav.WebDAVError, match="Login failed"):
        webdav.WebDAVClient(server["url"], USER, "wrong").list("")
    with pytest.raises(webdav.WebDAVError, match="Can't reach"):
        webdav.WebDAVClient(f"http://127.0.0.1:{free_port()}/x", timeout=1).list("")


def make_browser(tmp_path, url, password=PASSWORD):
    settings = tmp_path / "webdav.json"
    webdav.save_settings(url, USER, password, path=str(settings))
    cloud = CloudSource(str(tmp_path / "lib"),
                        client_factory=lambda: webdav.client_from_settings(str(settings)))
    return FileBrowser(str(tmp_path / "lib"), str(tmp_path / "state.json"), cloud=cloud), cloud


def test_browser_cloud_folder(server, tmp_path):
    fb, cloud = make_browser(tmp_path, server["url"])
    assert fb.get_items_for_display()[0] == (config.CLOUD_LABEL, True)

    fb.enter(fb.get_selected_path())
    assert fb.in_cloud() and fb.get_title() == "Cloud"
    items = fb.get_items_for_display()
    # Unsupported (.jpg) and hidden files are filtered; dirs first.
    assert items == [("..", True), ("Sci Fi", True), ("Dune & Co.txt", False)]
    assert fb.remote_only == {"Dune & Co.txt"}

    fb.move_down(); fb.move_down()
    assert fb.selected_is_remote()
    path = fb.get_selected_path()
    cloud.download(path)
    fb.refresh_if_changed()
    assert not fb.selected_is_remote()  # now local, cursor kept
    assert fb.get_selected_path() == path

    fb.go_up()
    assert fb.is_root() and fb.get_selected_path() == cloud.mirror_root


def test_offline_shows_downloaded_only(server, tmp_path):
    fb, cloud = make_browser(tmp_path, server["url"])
    fb.enter(cloud.mirror_root)
    cloud.download(os.path.join(cloud.mirror_root, "Dune & Co.txt"))
    fb.go_up()

    shutil.rmtree(server["root"])  # server folder gone -> 404, same as offline for us
    cloud._cache.clear()
    fb.enter(cloud.mirror_root)
    assert fb.cloud_offline
    assert fb.get_items_for_display() == [("..", True), ("Dune & Co.txt", False)]


def test_no_cloud_entry_when_unconfigured(tmp_path):
    cloud = CloudSource(str(tmp_path / "lib"), client_factory=lambda: None)
    fb = FileBrowser(str(tmp_path / "lib"), str(tmp_path / "state.json"), cloud=cloud)
    assert fb.get_items_for_display() == []


def test_app_download_and_open(server, monkeypatch):
    shutil.rmtree(config.BASE_DIR, ignore_errors=True)
    os.makedirs(config.LIBRARY_PATH)
    webdav.save_settings(server["url"], USER, PASSWORD)
    app = main.Application(mock=True)
    app._handle_actions(["long"])  # open [Cloud]; cursor lands on ".."
    assert app.browser.get_title() == "Cloud"
    app._handle_actions(["next", "next", "long"])  # skip "Sci Fi", open Dune
    assert app.mode == main.AppMode.READER
    assert app.current_file.endswith(os.path.join(".cloud", "Dune & Co.txt"))
    assert "Remote book text." in app.reader.pages[0][0]

    # Leaving the book goes to the home screen, where the download is now listed.
    app._handle_actions(["long"])
    assert app.browser.is_root()
    assert app.browser.get_selected_path().endswith(os.path.join(".cloud", "Dune & Co.txt"))


def test_webui_cloud_settings(server):
    import webui

    shutil.rmtree(config.BASE_DIR, ignore_errors=True)
    client = webui.app.test_client()
    r = client.post("/cloud", data={"url": server["url"], "user": USER, "password": PASSWORD},
                    follow_redirects=True)
    assert b"Connected: 4 item(s)" in r.data
    assert oct(os.stat(config.WEBDAV_SETTINGS_FILE).st_mode & 0o777) == "0o600"
    # Blank password keeps the saved one; page never echoes it.
    r = client.post("/cloud", data={"url": server["url"], "user": USER, "password": ""},
                    follow_redirects=True)
    assert b"Connected" in r.data and PASSWORD.encode() not in r.data
    client.post("/cloud", data={"url": ""})
    assert not os.path.exists(config.WEBDAV_SETTINGS_FILE)


def make_koodo_tree(root):
    """KoodoReader/{book/<key>.epub, config/books.db} as Koodo writes it to WebDAV."""
    import sqlite3

    (root / "KoodoReader" / "book").mkdir(parents=True)
    (root / "KoodoReader" / "config").mkdir()
    (root / "KoodoReader" / "book" / "1779244646577.epub").write_text("x")
    (root / "KoodoReader" / "book" / "1779244699999.epub").write_text("y")
    con = sqlite3.connect(root / "KoodoReader" / "config" / "books.db")
    con.execute('CREATE TABLE "books" ("key" text PRIMARY KEY, "name" text, "author" text,'
                ' "description" text, "md5" text, "cover" text, "format" text,'
                ' "publisher" text, "size" integer, "page" integer, "path" text, "charset" text)')
    con.executemany("INSERT INTO books (key, name, author, format) VALUES (?, ?, ?, ?)", [
        ("1779244646577", "Moby-Dick", "Herman Melville", "EPUB"),
        ("1779244699999", "Anathem", "", "EPUB"),
    ])
    con.commit()
    con.close()


@pytest.mark.parametrize("subpath,enter", [("KoodoReader/book", []), ("KoodoReader", ["book"])])
def test_koodo_titles(server, tmp_path, subpath, enter):
    make_koodo_tree(server["root"] / "Books")
    fb, cloud = make_browser(tmp_path, f"{server['url']}/{subpath}")
    cloud.cache_dir = str(tmp_path / "cache")
    fb.enter(cloud.mirror_root)
    for d in enter:
        fb.enter(os.path.join(fb.get_current_dir(), d))
    labels = [fb.label(n) for n, _ in fb.get_items_for_display()]
    # Sorted by title, not by key.
    assert labels == ["..", "Anathem", "Moby-Dick - Herman Melville"]

    # Titles still show offline from the cached books.db.
    cloud._titles.clear()
    shutil.rmtree(server["root"] / "Books" / "KoodoReader" / "config")
    assert fb.label("1779244646577.epub") == "Moby-Dick - Herman Melville"


def test_non_koodo_folder_untouched(server, tmp_path):
    fb, cloud = make_browser(tmp_path, server["url"])
    cloud.cache_dir = str(tmp_path / "cache")
    fb.enter(cloud.mirror_root)
    assert fb.label("Dune & Co.txt") == "Dune & Co.txt"
    assert not os.path.exists(cloud.cache_dir)  # never fetched a books.db


def test_downloaded_cloud_books_on_home_page(server, tmp_path):
    make_koodo_tree(server["root"] / "Books")
    fb, cloud = make_browser(tmp_path, f"{server['url']}/KoodoReader/book")
    cloud.cache_dir = str(tmp_path / "cache")
    (tmp_path / "lib" / "local.txt").write_text("x")
    fb.enter(cloud.mirror_root)
    cloud.download(os.path.join(cloud.mirror_root, "1779244646577.epub"))
    fb.go_up()
    fb.refresh()
    labels = [fb.label(n) for n, _ in fb.get_items_for_display()]
    # Only the downloaded one (not Anathem), shown by title, sorted with local files.
    assert labels == [config.CLOUD_LABEL, "local.txt", "Moby-Dick - Herman Melville"]
    fb.move_down(); fb.move_down()
    assert fb.get_selected_path() == os.path.join(cloud.mirror_root, "1779244646577.epub")
    assert fb.select_path(os.path.join(cloud.mirror_root, "1779244646577.epub"))


def test_webui_lists_and_removes_downloads(server):
    import webui

    shutil.rmtree(config.BASE_DIR, ignore_errors=True)
    os.makedirs(config.LIBRARY_PATH)
    make_koodo_tree(server["root"] / "Books")
    webdav.save_settings(f"{server['url']}/KoodoReader/book", USER, PASSWORD)
    cloud = CloudSource(config.LIBRARY_PATH)
    book = os.path.join(cloud.mirror_root, "1779244646577.epub")
    cloud.download(book)
    cloud.title_for(cloud.mirror_root, "1779244646577.epub")  # caches books.db like the reader does

    client = webui.app.test_client()
    page = client.get("/").get_data(as_text=True)
    assert "Downloaded from Cloud (1)" in page and "Moby-Dick - Herman Melville" in page

    assert b"not found" in client.post("/downloads/delete", data={"path": "../config.py"},
                                       follow_redirects=True).data
    r = client.post("/downloads/delete", data={"path": ".cloud/1779244646577.epub"},
                    follow_redirects=True)
    assert b"Removed Moby-Dick" in r.data
    assert not os.path.exists(book)
    assert "Nothing downloaded" in client.get("/").get_data(as_text=True)
    assert not os.path.exists(cloud.mirror_root + "/KoodoReader")


def test_cancelled_download_leaves_no_partial_file(server, tmp_path):
    (server["root"] / "Books" / "big.epub").write_bytes(os.urandom(2 * 1024 * 1024))
    c = webdav.WebDAVClient(server["url"], USER, PASSWORD)
    dest = tmp_path / "out" / "big.epub"
    seen = []

    class Stop(Exception):
        pass

    def progress(done, total):
        seen.append((done, total))
        if done > 256 * 1024:
            raise Stop()

    with pytest.raises(Stop):
        c.download("big.epub", str(dest), progress=progress)
    assert seen[0][1] == 2 * 1024 * 1024  # total known from Content-Length
    assert not dest.exists() and not os.path.exists(str(dest) + ".part")
