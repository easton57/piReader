"""End-to-end flow through Application with the mock display."""

import os
import shutil

import pytest

import config
import main


@pytest.fixture
def app(monkeypatch):
    shutil.rmtree(config.BASE_DIR, ignore_errors=True)
    os.makedirs(config.LIBRARY_PATH)
    with open(os.path.join(config.LIBRARY_PATH, "book.txt"), "w") as f:
        f.write("\n\n".join(f"Paragraph number {i} with some words." for i in range(200)))
    os.makedirs(os.path.join(config.LIBRARY_PATH, "Folder"))
    monkeypatch.setattr(main, "SHUTDOWN_COMMAND", "")
    a = main.Application(mock=True)
    a._render()
    return a


def act(app, *actions):
    app._handle_actions(list(actions))


def test_open_read_back_and_resume(app):
    assert app.browser.get_selected_path().endswith("Folder")
    act(app, "next", "select")
    assert app.mode == main.AppMode.READER
    act(app, "next", "next", "next")
    assert app.reader.get_current_page() == 3

    # Simulate reboot: last book reopens on the same page.
    app2 = main.Application(mock=True)
    last = app2.browser.get_last_open()
    assert last and last.endswith("book.txt")
    app2._open(last)
    assert app2.reader.get_current_page() == 3

    act(app, "back")
    assert app.mode == main.AppMode.BROWSER
    assert app.browser.get_selected_path().endswith("book.txt")
    assert main.Application(mock=True).browser.get_last_open() is None


def test_enter_folder_and_back(app):
    act(app, "select")
    assert app.browser.get_current_dir().endswith("Folder")
    act(app, "back")
    assert app.browser.is_root()


def test_unchanged_state_skips_refresh(app):
    count = app.display.show_count
    act(app, "back")  # at root: nothing to do
    assert app.display.show_count == count
    act(app, "next")
    assert app.display.show_count == count + 1


def test_bad_file_shows_notice(app):
    with open(os.path.join(config.LIBRARY_PATH, "broken.epub"), "w") as f:
        f.write("not an epub")
    app.browser.refresh()
    while not app.browser.get_selected_path().endswith("broken.epub"):
        app.browser.move_down()
    act(app, "select")
    assert app.mode == main.AppMode.BROWSER


def test_shutdown_stops(app):
    app.running = True
    act(app, "shutdown")
    assert not app.running


def test_webui_upload_and_delete(app):
    import io

    import webui

    client = webui.app.test_client()
    assert client.get("/").status_code == 200
    r = client.post("/upload", data={"file": (io.BytesIO(b"hi"), "new book.txt")},
                    content_type="multipart/form-data")
    assert r.status_code == 302
    assert os.path.exists(os.path.join(config.LIBRARY_PATH, "new_book.txt"))
    client.post("/delete/new_book.txt")
    assert not os.path.exists(os.path.join(config.LIBRARY_PATH, "new_book.txt"))
    assert client.post("/delete/..%2Fconfig.py").status_code == 302
