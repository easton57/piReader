"""Exercise display.py against the real Waveshare drivers with fake SPI/GPIO."""

import sys
import types

import pytest

import display as display_mod


@pytest.fixture
def fake_panel(monkeypatch):
    """Returns a log of ("cmd", byte) / ("data", byte) / ("bulk", bytes)."""
    log = []
    state = {"dc": 0}
    cfg = types.ModuleType("waveshare.epdconfig")
    cfg.RST_PIN, cfg.DC_PIN, cfg.CS_PIN, cfg.BUSY_PIN = 17, 25, 8, 24
    cfg.module_init = lambda *a, **k: 0
    cfg.module_exit = lambda *a, **k: None

    def digital_write(pin, v):
        if pin == cfg.DC_PIN:
            state["dc"] = v

    cfg.digital_write = digital_write
    cfg.digital_read = lambda pin: 0
    cfg.delay_ms = lambda ms: None
    cfg.spi_writebyte = lambda data: log.append(("data" if state["dc"] else "cmd", data[0]))
    cfg.spi_writebyte2 = lambda data: log.append(("bulk", bytes(data)))
    monkeypatch.setitem(sys.modules, "waveshare.epdconfig", cfg)
    for mod in ("waveshare.epd2in13_V4", "waveshare.epd2in13b_V4"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    import waveshare

    monkeypatch.setattr(waveshare, "epdconfig", cfg, raising=False)
    return log


def update_modes(log):
    """Values written to Display Update Control 2 (0x22) before each refresh."""
    return [log[i + 1][1] for i, e in enumerate(log) if e == ("cmd", 0x22)]


def square_canvas(d):
    from display import new_draw

    img = d.create_canvas()
    new_draw(img).rectangle([(0, 0), (9, 9)], fill=0)
    return img


def test_bw_v4_partial_and_full(fake_panel, monkeypatch):
    monkeypatch.setattr(display_mod, "DISPLAY_DRIVER", "epd2in13_V4")
    d = display_mod.EPaperDisplay()
    assert d.initialized and d.supports_partial

    fake_panel.clear()
    d.show(square_canvas(d), partial=True)  # no base frame yet -> full
    assert d.last_refresh == "full"
    assert update_modes(fake_panel) == [0xF7]
    bulks = [b for k, b in fake_panel if k == "bulk"]
    assert len(bulks) == 2 and bulks[0] == bulks[1]  # base written to both RAMs
    dark = sum(8 - bin(b | 0x3F if i % 16 == 15 else b).count("1") for i, b in enumerate(bulks[0]))
    assert dark == 100  # exactly the 10x10 square

    for _ in range(3):
        fake_panel.clear()
        d.show(d.create_canvas(), partial=True, limit=3)
        assert d.last_refresh == "partial" and update_modes(fake_panel) == [0xFF]
    d.show(d.create_canvas(), partial=True, limit=3)  # limit reached -> full to clear ghosting
    assert d.last_refresh == "full"
    d.show(d.create_canvas(), partial=False)
    assert d.last_refresh == "full"


def test_sleep_forces_full_after_wake(fake_panel, monkeypatch):
    monkeypatch.setattr(display_mod, "DISPLAY_DRIVER", "epd2in13_V4")
    d = display_mod.EPaperDisplay()
    d.show(d.create_canvas())
    d.sleep()
    assert d.asleep
    d.show(d.create_canvas(), partial=True)
    assert not d.asleep and d.last_refresh == "full"


def test_tricolour_driver_no_red(fake_panel, monkeypatch):
    monkeypatch.setattr(display_mod, "DISPLAY_DRIVER", "epd2in13b_V4")
    d = display_mod.EPaperDisplay()
    assert not d.supports_partial
    fake_panel.clear()
    d.show(square_canvas(d), partial=True)
    assert d.last_refresh == "full"
    black, red = [b for k, b in fake_panel if k == "bulk"]
    assert len(black) == len(red) == 16 * 250
    assert set(red) == {0xFF}
