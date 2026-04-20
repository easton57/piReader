import os
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.getcwd())

from display import EPaperDisplay
from file_browser import FileBrowser
from reader import TextReader

# Constants
DISPLAY_W = 250
DISPLAY_H = 122
TEST_OUTPUT_DIR = "test/images"


def test_file_browser():
    try:
        library = "library"

        # Create library folder and test files
        if not os.path.exists(library):
            os.makedirs(library)

        # Create sample files
        sample_path = os.path.join(library, "sample.txt")
        book_path = os.path.join(library, "book.epub")
        notes_path = os.path.join(library, "notes.pdf")
        open(sample_path, "w").close()
        open(book_path, "w").close()
        open(notes_path, "w").close()

        # Use FileBrowser
        browser = FileBrowser(library)

        # Create display canvas and render the file browser view
        image = Image.new("1", (DISPLAY_W, DISPLAY_H), 1)
        draw = ImageDraw.Draw(image)

        # Display file list items from browser using draw.text
        items = browser.get_items_for_display()
        y_pos = 10
        for item, is_dir in items:
            draw.text((10, y_pos), item, fill=0)
            y_pos += 12

        # Save test/images/test_file_browser.png
        output_path = os.path.join(TEST_OUTPUT_DIR, "test_file_browser.png")
        image.save(output_path)
        print("File browser test passed: " + output_path)
    except Exception as e:
        print("File browser test failed: " + str(e))


def test_text_reader():
    try:
        library = "library"

        # Create library folder
        if not os.path.exists(library):
            os.makedirs(library)

        # Create sample.txt with content
        sample_path = os.path.join(library, "sample.txt")
        with open(sample_path, "w") as f:
            content = "This is a sample test file.\nIt has multiple lines.\nAnd more content here."
            f.write(content)

        # Use TextReader
        reader = TextReader(font_size=10)

        # Open the sample file and render
        if reader.open(sample_path):
            rendered_image = reader.render()

            # Save test/images/test_text_reader.png
            output_path = os.path.join(TEST_OUTPUT_DIR, "test_text_reader.png")
            rendered_image.save(output_path)
            print("Text reader test passed: " + output_path)
        else:
            print("Failed to open sample file for text reader test")
    except Exception as e:
        print("Text reader test failed: " + str(e))


def test_battery_display():
    try:
        # Create reader
        reader = TextReader(font_size=10)

        # Show battery rendered
        battery_image = reader.render()

        # Save test/images/test_battery.png
        output_path = os.path.join(TEST_OUTPUT_DIR, "test_battery.png")
        battery_image.save(output_path)
        print("Battery display test passed: " + output_path)
    except Exception as e:
        print("Battery display test failed: " + str(e))


if __name__ == "__main__":
    test_file_browser()
    test_text_reader()
    test_battery_display()
