import time

from PIL import Image, ImageDraw, ImageFont

from display import EPaperDisplay


def test_display_init():
    print("Test: Display Initialization")
    display = EPaperDisplay()
    display.clear()

    canvas = display.create_canvas()
    draw = ImageDraw.Draw(canvas)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    except:
        font = ImageFont.load_default()

    draw.text((60, 50), "Display OK", font=font, fill=0)
    display.show(canvas)

    display.sleep()
    print("  PASSED: Display initialized and shows 'Display OK'")


def test_display_pages():
    print("Test: Display Pages")
    display = EPaperDisplay()
    display.clear()

    pages = [
        "Page 1 of 3",
        "This is the",
        "second page.",
        "",
        "Page 2 of 3",
        "Testing e-ink",
        "display updates.",
        "",
        "Page 3 of 3",
        "Final page for",
        "testing complete.",
    ]

    for i, text in enumerate(pages):
        canvas = display.create_canvas()
        draw = ImageDraw.Draw(canvas)

        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
        except:
            font = ImageFont.load_default()

        draw.text((10, 10), text, font=font, fill=0)
        page_num = "Page " + str((i // 4) + 1) + "/3"
        draw.text((180, 110), page_num, font=font, fill=128)

        display.show(canvas)
        print("  Showing: " + text)

        if i < len(pages) - 1:
            time.sleep(2)

    display.sleep()
    print("  PASSED: All pages displayed successfully")


def run_display_tests():
    print("=" + 50 * "=")
    print("Running Display Tests on Raspberry Pi")
    print("=" + 50 * "=")

    try:
        test_display_init()
        time.sleep(3)

        test_display_pages()
        time.sleep(3)

        print("=" + 50 * "=")
        print("All display tests completed!")
        print("=" + 50 * "=")
    except Exception as e:
        print("ERROR: " + str(e))


if __name__ == "__main__":
    run_display_tests()
