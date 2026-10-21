"""End-to-end flow through Application with the mock display."""

import os
import shutil
import time

import pytest

import config
import main


@pytest.fixture
def app(monkeypatch):
    shutil.rmtree(config.BASE_DIR, ignore_errors=True)  # also resets settings.json
    os.makedirs(config.LIBRARY_PATH)
    with open(os.path.join(config.LIBRARY_PATH, "book.txt"), "w") as f:
        f.write("\n\n".join(f"Paragraph number {i} with some words." for i in range(200)))
    os.makedirs(os.path.join(config.LIBRARY_PATH, "Folder"))
    monkeypatch.setattr(main, "SHUTDOWN_COMMANDS", [])
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
    app.settings.set("book_full_refresh_every", 3)
    act(app, "next", "long")
    assert app.display.last_refresh == "full"  # opening is always full
    seen = []
    for _ in range(6):
        act(app, "next")
        seen.append(app.display.last_refresh)
    assert seen == ["partial", "partial", "full", "partial", "partial", "full"]


def test_pdf_open_uses_pdftotext_and_can_be_cancelled(app, monkeypatch, tmp_path):
    import threading

    import documents
    from reportlab.pdfgen import canvas

    pdf = os.path.join(config.LIBRARY_PATH, "slow.pdf")
    c = canvas.Canvas(pdf)
    c.drawString(72, 720, "Hello from a PDF page.")
    c.showPage()
    c.save()

    # Normal open goes through pdftotext.
    assert app._open(pdf)
    assert "Hello from a PDF page." in app.reader.book.chapters[0].paragraphs[0]
    act(app, "long")

    # A stuck extractor: long press must cancel it promptly.
    for f in os.listdir(config.CACHE_DIR):
        if f.endswith(".txt"):
            os.remove(os.path.join(config.CACHE_DIR, f))
    monkeypatch.setattr(documents, "PDFTOTEXT", ["sh", "-c", "sleep 30", "x"])
    threading.Timer(0.5, lambda: app.actions.put("long")).start()
    t = time.monotonic()
    assert not app._open(pdf)
    assert time.monotonic() - t < 5
    assert app.mode == main.AppMode.BROWSER and app.notice == "Cancelled"


def fake_download(chunks, delay):
    def download(local_path, progress=None):
        total = chunks * 1024 * 1024
        for i in range(1, chunks + 1):
            time.sleep(delay)
            progress(i * 1024 * 1024, total)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "w") as f:
            f.write("downloaded text")
    return download


def test_download_progress_bar(app, monkeypatch):
    monkeypatch.setattr(main, "DOWNLOAD_REDRAW_SECONDS", 0)
    monkeypatch.setattr(app.cloud, "download", fake_download(chunks=5, delay=0.3))
    refreshes = []
    real_show = app.display.show

    def show(img, **kw):
        real_show(img, **kw)
        refreshes.append(app.display.last_refresh)
    monkeypatch.setattr(app.display, "show", show)

    target = os.path.join(app.cloud.mirror_root, "big.txt")
    assert app._download(target)
    assert os.path.exists(target)
    assert refreshes[0] == "full" and refreshes.count("partial") >= 3


def test_download_cancel(app, monkeypatch):
    import threading

    monkeypatch.setattr(app.cloud, "download", fake_download(chunks=100, delay=0.1))
    threading.Timer(0.5, lambda: app.actions.put("long")).start()
    t = time.monotonic()
    assert not app._download(os.path.join(app.cloud.mirror_root, "big.txt"))
    assert time.monotonic() - t < 3
    assert app.notice == "Download cancelled"


def test_back_from_book_in_subfolder_goes_home(app):
    with open(os.path.join(config.LIBRARY_PATH, "Folder", "inner.txt"), "w") as f:
        f.write("inner book")
    act(app, "long")           # into Folder
    act(app, "next", "long")   # past "..", open inner.txt
    assert app.mode == main.AppMode.READER
    act(app, "long")
    assert app.browser.is_root()


def test_settings_screen(app):
    # [Settings] is the last entry on the home screen.
    assert app.browser.get_items_for_display()[-1][0] == config.SETTINGS_LABEL
    act(app, "prev", "long")
    assert app.mode == main.AppMode.SETTINGS
    assert app.display.last_refresh == "full"

    # Row 0: font. Long press cycles it and applies immediately.
    before = app.settings.get("font")
    act(app, "long")
    assert app.settings.get("font") != before
    assert app.reader.face == app.settings.get("font")

    # Row 1: text size.
    act(app, "next", "long")
    assert app.settings.get("font_size") == 13
    assert app.reader.font_size == 13 and app.font_size == 13
    assert app.display.last_refresh == "partial"

    # Persisted for the next boot.
    assert main.Application(mock=True).settings.get("font_size") == 13

    # Back (last row) returns home with the cursor on [Settings].
    while app.settings_cursor != app.BACK_ROW:
        act(app, "next")
    act(app, "long")
    assert app.mode == main.AppMode.BROWSER and app.browser.is_root()
    assert app.browser.selected_name() == config.SETTINGS_LABEL


def test_font_change_keeps_reading_position(app):
    with open(os.path.join(config.LIBRARY_PATH, "book.txt"), "a") as f:
        f.write("\n\n" + "\n\n".join("Extra paragraph %d with words." % i for i in range(400)))
    act(app, "next", "long")
    for _ in range(20):
        act(app, "next")
    pos = app.reader.get_position()
    # Change size from the web UI while reading.
    import webui
    webui.app.test_client().post("/settings", data={"font_size": "16"})
    app._render()
    assert app.reader.font_size == 16
    start = app.reader.page_starts[app.reader.current_page]
    end = app.reader.page_starts[app.reader.current_page + 1]
    assert start <= tuple(pos) < end


def test_menu_refresh_limit_from_settings(app):
    app.settings.set("menu_full_refresh_every", 5)
    seen = []
    act(app, "next")  # first move after a full frame
    for _ in range(7):
        act(app, "next")
        seen.append(app.display.last_refresh)
    assert "full" in seen and seen.count("partial") >= 4


def test_settings_shutdown_needs_confirmation(app):
    act(app, "prev", "long")  # open Settings
    while app.settings_cursor != app.SHUTDOWN_ROW:
        act(app, "next")
    act(app, "long")
    assert app.confirm_shutdown and app.actions.empty()
    act(app, "next")  # moving away cancels
    assert not app.confirm_shutdown
    act(app, "prev", "long", "long")
    assert app.actions.get_nowait() == "shutdown"


def test_failed_power_off_keeps_reader_running(app, monkeypatch):
    monkeypatch.setattr(main, "SHUTDOWN_COMMANDS",
                        [["sh", "-c", "echo 'sudo: a password is required' >&2; exit 1"]])
    monkeypatch.setattr(app.display, "mock", False)  # take the real power-off path
    monkeypatch.setattr(app.display, "show", lambda *a, **k: None)
    messages = []
    monkeypatch.setattr(app, "_show_message", lambda text, **k: messages.append(text))
    app.running = True
    app._shutdown()
    assert app.running  # app stays up instead of silently exiting
    assert "a password is required" in messages[-1]


def test_power_off_success_stops(app, monkeypatch):
    monkeypatch.setattr(main, "SHUTDOWN_COMMANDS", [["false"], ["true"]])  # falls through to 2nd
    monkeypatch.setattr(app.display, "mock", False)
    monkeypatch.setattr(app.display, "show", lambda *a, **k: None)
    monkeypatch.setattr(app.display, "sleep", lambda: None)
    app.running = True
    app._shutdown()
    assert not app.running


def test_auto_power_off_after_idle(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "_shutdown", lambda **k: calls.append(k))
    monkeypatch.setattr(main.pisugar, "is_plugged_in", lambda: False)
    app.settings.set("auto_power_off", 15)
    app.last_activity = time.monotonic() - 14 * 60
    app._check_idle()
    assert calls == [] and app.idle  # screensaver at 5 min, not off yet
    app.last_activity = time.monotonic() - 15 * 60
    app._check_idle()
    assert calls == [{"status": main.STATUS_POWERED_OFF}]


def test_no_auto_power_off_while_charging(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "_shutdown", lambda **k: calls.append(k))
    monkeypatch.setattr(main.pisugar, "is_plugged_in", lambda: True)
    app.last_activity = time.monotonic() - 60 * 60
    app._check_idle()
    app._check_idle()
    assert calls == []
    assert app._idle_wait() == 60  # rechecks in 5 min; main loop sleeps, no 1 s spin


def test_auto_power_off_never(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "_shutdown", lambda **k: calls.append(k))
    app.settings.set("auto_power_off", 0)
    app.last_activity = time.monotonic() - 10 * 3600
    app._check_idle()
    assert calls == []


def test_open_restarts_idle_clock(app, monkeypatch):
    """Time spent loading a book must not count towards the sleep timer."""
    real_open_doc = app._open_doc

    def slow_open(path):
        app.last_activity = time.monotonic() - 10 * 3600  # as if loading took hours
        return real_open_doc(path)

    monkeypatch.setattr(app, "_open_doc", slow_open)
    act(app, "next", "long")
    assert app.mode == main.AppMode.READER
    assert time.monotonic() - app.last_activity < 5


def test_speed_mode_only_full_refreshes_when_opening_a_book(app):
    app.settings.set("speed_mode", True)
    app._apply_settings()
    assert app.display.speed_mode
    seen = []

    def step(*actions):
        act(app, *actions)
        seen.append(app.display.last_refresh)

    step("next")                       # move in the list
    step("prev")
    step("next", "long")               # open the book
    assert app.mode == main.AppMode.READER
    opened = len(seen) - 1
    for _ in range(12):
        step("next")                   # page turns, past any refresh limit
    step("long")                       # back to the list
    for _ in range(25):
        step("next")                   # past menu_full_refresh_every
    app._show_message("A message")     # messages too
    seen.append(app.display.last_refresh)
    assert seen[opened] == "full"
    assert [r for i, r in enumerate(seen) if i != opened] == ["partial"] * (len(seen) - 1)


def test_speed_mode_still_full_after_display_sleep(app):
    """The panel loses its base frame in deep sleep, so the first frame after
    waking has to be full even in speed mode."""
    app.settings.set("speed_mode", True)
    app._apply_settings()
    app.display._base_valid = False
    act(app, "next")
    assert app.display.last_refresh == "full"
    act(app, "next")
    assert app.display.last_refresh == "partial"


def test_speed_mode_from_settings_screen(app):
    act(app, "prev", "long")  # open Settings
    while settings_key(app) != "speed_mode":
        act(app, "next")
    act(app, "long")
    assert app.settings.get("speed_mode") is True and app.display.speed_mode
    act(app, "long")
    assert app.settings.get("speed_mode") is False and not app.display.speed_mode


def settings_key(app):
    import settings as sm
    return sm.ORDER[app.settings_cursor] if app.settings_cursor < len(sm.ORDER) else None


def test_speed_mode_off_by_default_and_webui_toggle(app):
    assert app.settings.get("speed_mode") is False
    import webui
    webui.app.test_client().post("/settings", data={"speed_mode": "True"})
    app._render()
    assert app.display.speed_mode


def test_footer_shows_percentage(app):
    from reader import page_label
    assert page_label(0, 1) == "1/1 100%"
    assert page_label(0, 5) == "1/5 0%"
    assert page_label(2, 5) == "3/5 50%"
    assert page_label(4, 5) == "5/5 100%"


def _label_pixels(img):
    """Pixels of the bottom-centre strip where the status label goes."""
    img = img.convert("L")
    return img.crop((img.width // 2 - 40, img.height - 16, img.width // 2 + 40, img.height))


def test_lock_screen_labels(app, monkeypatch):
    from PIL import ImageChops

    from display import stamp_status

    shown = []
    monkeypatch.setattr(app.display, "sleep", lambda: None)
    real_show = app.display.show

    def show(img, **k):
        shown.append((img.copy(), k))
        real_show(img, **k)

    monkeypatch.setattr(app.display, "show", show)
    monkeypatch.setattr(main.pisugar, "is_plugged_in", lambda: False)

    # Idle: "Sleeping" stamped on the current screen (no screensaver set).
    base = app.display.last_frame.copy()
    app.last_activity = time.monotonic() - 6 * 60
    app._check_idle()
    img, kw = shown[-1]
    assert kw.get("force_full")
    expect = stamp_status(base, main.STATUS_SLEEPING, app.small_font)
    assert ImageChops.difference(img.convert("L"), expect).getbbox() is None

    # Auto power off: relabelled from the same base, labels don't stack.
    app.settings.set("auto_power_off", 15)
    app.last_activity = time.monotonic() - 16 * 60
    app._check_idle()
    img, _ = shown[-1]
    expect = stamp_status(base, main.STATUS_POWERED_OFF, app.small_font)
    assert ImageChops.difference(img.convert("L"), expect).getbbox() is None


def test_manual_shutdown_label(app, monkeypatch):
    from PIL import ImageChops

    from display import stamp_status

    base = app.display.last_frame.copy()
    shown = []
    monkeypatch.setattr(app.display, "show", lambda img, **k: shown.append(img.copy()))
    app._shutdown()
    expect = stamp_status(base, main.STATUS_SHUT_DOWN, app.small_font)
    assert ImageChops.difference(shown[-1].convert("L"), expect).getbbox() is None


def test_lock_screen_label_on_screensaver(app, monkeypatch):
    from PIL import Image

    Image.new("L", (300, 150), 0).save(os.path.join(config.CACHE_DIR, "screensaver.png"))
    shown = []
    monkeypatch.setattr(app.display, "show", lambda img, **k: shown.append(img.copy()))
    app._shutdown(status=main.STATUS_POWERED_OFF)
    strip = _label_pixels(shown[-1])
    assert 255 in strip.getdata() and 0 in strip.getdata()  # white label on black art
