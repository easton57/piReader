"""Exercise display.py against the real Waveshare driver with fake SPI/GPIO."""

import sys
import types

import pytest
from PIL import ImageDraw


@pytest.fixture
def fake_panel(monkeypatch):
    writes = []
    cfg = types.ModuleType("epd2in13b_v4.epdconfig")
    cfg.RST_PIN, cfg.DC_PIN, cfg.CS_PIN, cfg.BUSY_PIN = 17, 25, 8, 24
    cfg.module_init = lambda: 0
    cfg.module_exit = lambda: None
    cfg.digital_write = lambda pin, v: None
    cfg.digital_read = lambda pin: 0
    cfg.delay_ms = lambda ms: None
    cfg.spi_writebyte = lambda data: writes.append(("cmd_or_byte", list(data)))
    cfg.spi_writebyte2 = lambda data: writes.append(("bulk", bytes(data)))
    monkeypatch.setitem(sys.modules, "epd2in13b_v4.epdconfig", cfg)
    monkeypatch.delitem(sys.modules, "epd2in13b_v4.epd2in13b_V4", raising=False)
    import epd2in13b_v4

    monkeypatch.setattr(epd2in13b_v4, "epdconfig", cfg, raising=False)
    return writes


def test_frame_buffers(fake_panel):
    from display import EPaperDisplay

    d = EPaperDisplay()
    assert d.initialized
    img = d.create_canvas()
    ImageDraw.Draw(img).rectangle([(0, 0), (9, 9)], fill=0)
    fake_panel.clear()
    d.show(img)

    black, red = [data for kind, data in fake_panel if kind == "bulk"]
    assert len(black) == len(red) == 16 * 250  # 122px rows padded to 16 bytes
    assert set(red) == {0xFF}  # no red pixels
    # The last byte of each row has 6 padding bits past the 122px edge; force them white.
    visible = bytes(b | 0x3F if i % 16 == 15 else b for i, b in enumerate(black))
    dark = sum(8 - bin(b).count("1") for b in visible)
    assert dark == 100  # exactly the 10x10 black square


def test_sleep_then_show_wakes(fake_panel):
    from display import EPaperDisplay

    d = EPaperDisplay()
    d.sleep()
    assert d.asleep
    d.show(d.create_canvas())
    assert not d.asleep
