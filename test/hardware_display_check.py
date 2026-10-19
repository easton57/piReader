"""Run on the Pi to check the panel: python3 test/hardware_display_check.py

Not a pytest test (needs the real display). You should see black text on white
with no red anywhere.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from display import EPaperDisplay, load_font, new_draw  # noqa: E402


def main():
    display = EPaperDisplay()
    if not display.initialized:
        print("FAILED: display didn't initialize (SPI enabled? wiring?)")
        return 1
    canvas = display.create_canvas()
    draw = new_draw(canvas)
    draw.text((10, 10), "Display OK", font=load_font(20), fill=0)
    draw.text((10, 45), "Top-left should be up.", font=load_font(12), fill=0)
    draw.rectangle([(0, 0), (249, 121)], outline=0)
    display.show(canvas)
    display.sleep()
    print("Done. If the image is upside down, change DISPLAY_ROTATION in config.py (90/270).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
