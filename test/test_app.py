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
    act(app, "next", "long")
    assert app.mode == main.AppMode.READER
    act(app, "next", "next", "next")
    assert app.reader.get_current_page() == 3

    # Simulate reboot: last book reopens on the same page.
    app2 = main.Application(mock=True)
    last = app2.browser.get_last_open()
    assert last and last.endswith("book.txt")
    app2._open(last)
    assert app2.reader.get_current_page() == 3

    act(app, "long")
    assert app.mode == main.AppMode.BROWSER
    assert app.browser.get_selected_path().endswith("book.txt")
    assert main.Application(mock=True).browser.get_last_open() is None


def test_enter_folder_and_back(app):
    act(app, "triple")  # 3 taps also opens
    assert app.browser.get_current_dir().endswith("Folder")
    assert app.browser.selected_is_parent()
    act(app, "long")  # ".." goes up
    assert app.browser.is_root()
    assert app.browser.get_selected_path().endswith("Folder")


def test_unchanged_state_skips_refresh(app):
    act(app, "triple")  # opens Folder
    act(app, "long")   # back up
    count = app.display.show_count
    act(app, "prev", "next")  # net no movement
    assert app.display.show_count == count
    act(app, "next")
    assert app.display.show_count == count + 1


def test_bad_file_shows_notice(app):
    with open(os.path.join(config.LIBRARY_PATH, "broken.epub"), "w") as f:
        f.write("not an epub")
    app.browser.refresh()
    while not app.browser.get_selected_path().endswith("broken.epub"):
        app.browser.move_down()
    act(app, "long")
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


def test_webui_shutdown_queues_action(app):
    import webui

    webui.ACTION_QUEUE = app.actions
    try:
        r = webui.app.test_client().post("/shutdown")
        assert r.status_code == 200
        assert app.actions.get_nowait() == "shutdown"
    finally:
        webui.ACTION_QUEUE = None


def test_inverted_row_text_survives_threshold(app):
    """Regression: anti-aliased white-on-black text was thresholded away."""
    from display import EPaperDisplay
    from PIL import Image

    img = EPaperDisplay.to_1bit(app._render_browser())
    row = img.crop((0, 14, 250, 14 + config.FONT_SIZE + 2))  # highlighted first row
    white = sum(1 for p in row.getdata() if p)
    clean = Image.new("L", row.size, 0)
    from display import load_font, new_draw
    new_draw(clean).text((config.MARGIN_LEFT, 1), "Folder/", fill=255, font=load_font(config.FONT_SIZE))
    assert white >= 0.9 * sum(1 for p in clean.getdata() if p)


def test_partial_refresh_only_in_file_list(app):
    act(app, "next")
    assert app.display.last_refresh == "partial"   # moving in the list
    act(app, "long")
    assert app.mode == main.AppMode.READER
    assert app.display.last_refresh == "full"      # opening a book
    act(app, "next")
    assert app.display.last_refresh == "full"      # page turns
    act(app, "long")
    assert app.display.last_refresh == "full"      # back to the list
    act(app, "prev")
    assert app.display.last_refresh == "partial"


def test_book_partial_refresh_setting(app, monkeypatch):
    monkeypatch.setattr(main, "BOOK_FULL_REFRESH_EVERY", 3)
    act(app, "next", "long")
    assert app.display.last_refresh == "full"  # opening is always full
    seen = []
    for _ in range(6):
        act(app, "next")
        seen.append(app.display.last_refresh)
    assert seen == ["partial", "partial", "full", "partial", "partial", "full"]
