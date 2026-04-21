"""
Flask web application for the e-reader.
"""

import logging, os, time, urllib.request
from flask import Flask, flash, jsonify, redirect, render_template_string, request, url_for
from werkzeug.utils import secure_filename

try:
    import RPi.GPIO as GPIO
    IS_PI = True
except ImportError:
    IS_PI = False

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-key")

try:
    from config import LIBRARY_PATH, CACHE_DIR
except ImportError:
    LIBRARY_PATH = "library"
    CACHE_DIR = "cache"

UPLOAD_FOLDER = LIBRARY_PATH
SCREENSAVER_FOLDER = CACHE_DIR
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["SCREENSAVER_FOLDER"] = SCREENSAVER_FOLDER

SUPPORTED_EXTENSIONS = {".txt", ".epub", ".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def get_battery_percentage():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8421/battery", timeout=2) as r:
            return int(r.read().decode("utf-8"))
    except:
        return 100

def get_page_count():
    return 0

def ensure_directory(path):
    os.makedirs(path, exist_ok=True)

def is_running_on_pi():
    return IS_PI

def get_library_files():
    files = []
    if os.path.exists(UPLOAD_FOLDER):
        for fn in os.listdir(UPLOAD_FOLDER):
            fp = os.path.join(UPLOAD_FOLDER, fn)
            if os.path.isfile(fp):
                _, ext = os.path.splitext(fn.lower())
                if ext in SUPPORTED_EXTENSIONS:
                    files.append({"name": fn, "path": fp, "size": os.path.getsize(fp), "extension": ext})
    return sorted(files, key=lambda x: x["name"].lower())

def get_current_screensaver():
    for ext in IMAGE_EXTENSIONS:
        ip = os.path.join(SCREENSAVER_FOLDER, "screensaver" + ext)
        if os.path.exists(ip):
            return ip
    return None

def format_file_size(sb):
    for u in ["B", "KB", "MB", "GB"]:
        if sb < 1024.0:
            return f"{sb:.0f} {u}"
        sb /= 1024.0
    return f"{sb:.1f} GB"

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>e-Reader Web UI</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f5f5f5;color:#333;line-height:1.6}
.container{max-width:800px;margin:0 auto;padding:20px}
header{background:#fff;padding:20px;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,0.1);margin-bottom:20px;display:flex;justify-content:space-between;align-items:center}
h1{font-size:1.5rem;color:#222}
.battery{display:flex;align-items:center;gap:8px;font-weight:600}
.battery-icon{width:24px;height:12px;border:2px solid #333;border-radius:2px;position:relative}
.battery-icon::after{content:"";position:absolute;right:-4px;top:50%;transform:translateY(-50%);width:3px;height:6px;background:#333;border-radius:0 1px 1px 0}
.battery-level{height:100%;background:#4CAF50;transition:width 0.3s}
.card{background:#fff;padding:20px;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,0.1);margin-bottom:20px}
.card h2{font-size:1.1rem;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid #eee}
.screensaver{display:flex;gap:20px;align-items:flex-start}
.screensaver-thumb{width:120px;height:75px;background:#eee;border-radius:4px;overflow:hidden;display:flex;align-items:center;justify-content:center;font-size:0.8rem;color:#666}
.screensaver-thumb img{max-width:100%;max-height:100%;object-fit:contain}
.file-list{list-style:none}
.file-item{display:flex;justify-content:space-between;align-items:center;padding:12px 0;border-bottom:1px solid #eee}
.file-item:last-child{border-bottom:none}
.file-info{display:flex;align-items:center;gap:12px}
.file-ext{background:#e0e0e0;padding:4px 8px;border-radius:4px;font-size:0.75rem;font-weight:600;text-transform:uppercase}
.file-ext.txt{background:#e3f2fd;color:#1565c0}
.file-ext.pdf{background:#ffebee;color:#c62828}
.file-ext.epub{background:#f3e5f5;color:#7b1fa2}
.file-name{font-weight:500}
.file-size{color:#666;font-size:0.85rem}
.delete-btn{background:#ffebee;color:#c62828;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:0.85rem}
.delete-btn:hover{background:#ffcdd2}
.upload-form{display:flex;gap:12px;flex-wrap:wrap}
.upload-form input[type="file"]{flex:1;min-width:200px;padding:8px;border:1px solid #ddd;border-radius:4px}
.btn{background:#1976d2;color:#fff;border:none;padding:10px 20px;border-radius:4px;cursor:pointer;font-size:0.9rem}
.btn:hover{background:#1565c0}
.empty-state{text-align:center;padding:40px 20px;color:#666}
.flash-messages{margin-bottom:20px}
.flash{padding:12px 16px;border-radius:4px;margin-bottom:8px}
.flash.success{background:#e8f5e9;color:#2e7d32}
.flash.error{background:#ffebee;color:#c62828}
.status-info{display:flex;gap:24px;flex-wrap:wrap}
.status-item{display:flex;flex-direction:column}
.status-label{font-size:0.75rem;color:#666;text-transform:uppercase;letter-spacing:0.5px}
.status-value{font-size:1.25rem;font-weight:600}
</style>
</head>
<body>
<div class="container">
<header>
<h1>e-Reader</h1>
<div class="battery">
<div class="battery-icon"><div class="battery-level" style="width:{{battery}}%;"></div></div>
<span>{{battery}}%</span>
</div>
</header>
{%with messages=get_flashed_messages(with_categories=true)%}{%if messages%}<div class="flash-messages">{%for category,message in messages%}<div class="flash {{category}}">{{message}}</div>{%endfor%}</div>{%endif%}{%endwith%}
<div class="card"><h2>Status</h2><div class="status-info"><div class="status-item"><span class="status-label">Battery</span><span class="status-value">{{battery}}%</span></div><div class="status-item"><span class="status-label">Books</span><span class="status-value">{{books|length}}</span></div><div class="status-item"><span class="status-label">Running on Pi</span><span class="status-value">{{"Yes" if is_pi else "No"}}</span></div></div></div>
<div class="card"><h2>Screensaver</h2><div class="screensaver">{%if screensaver%}<div class="screensaver-thumb"><img src="{{url_for("screensaver_file",filename=screensaver_filename)}}" alt="Current screensaver"></div><div><p style="margin-bottom:12px">Current screensaver set</p><form action="{{url_for("screensaver")}}" method="post" enctype="multipart/form-data"><input type="file" name="image" accept=".png,.jpg,.jpeg,.gif,.bmp" style="margin-bottom:12px"><button type="submit" class="btn">Change Screensaver</button></form></div>{%else%}<div class="empty-state" style="padding:20px;text-align:left"><p style="margin-bottom:12px">No screensaver set</p><form action="{{url_for("screensaver")}}" method="post" enctype="multipart/form-data"><input type="file" name="image" accept=".png,.jpg,.jpeg,.gif,.bmp" style="margin-bottom:12px"><button type="submit" class="btn">Set Screensaver</button></form></div>{%endif%}</div></div>
<div class="card"><h2>Library ({{books|length}} books)</h2>{%if books%}<ul class="file-list">{%for book in books%}<li class="file-item"><div class="file-info"><span class="file-ext {{book.extension[1:]}}">{{book.extension[1:]}}</span><span class="file-name">{{book.name}}</span><span class="file-size">{{book.size_formatted}}</span></div><form action="{{url_for("delete_file",filename=book.name)}}" method="post"><button type="submit" class="delete-btn" onclick="return confirm("Delete {{book.name}}?")">Delete</button></form></li>{%endfor%}</ul>{%else%}<div class="empty-state"><p>No books in library</p><p style="font-size:0.85rem;margin-top:8px">Upload .txt, .epub, or .pdf files below</p></div>{%endif%}</div>
<div class="card"><h2>Upload Books</h2><form action="{{url_for("upload")}}" method="post" enctype="multipart/form-data" class="upload-form"><input type="file" name="file" accept=".txt,.epub,.pdf" required><button type="submit" class="btn">Upload</button></form><p style="margin-top:12px;font-size:0.85rem;color:#666">Supported formats: .txt, .epub, .pdf (max 50MB)</p></div>
</div>
</body>
</html>
"""

# Routes
@app.route("/")
def index():
    ensure_directory(UPLOAD_FOLDER)
    ensure_directory(SCREENSAVER_FOLDER)
    battery = get_battery_percentage()
    books = get_library_files()
    for book in books:
        book["size_formatted"] = format_file_size(book["size"])
    screensaver = get_current_screensaver()
    screensaver_filename = os.path.basename(screensaver) if screensaver else None
    return render_template_string(HTML_TEMPLATE, battery=battery, books=books, screensaver=screensaver, screensaver_filename=screensaver_filename, is_pi=IS_PI)


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        flash("No file selected", "error")
        return redirect(url_for("index"))
    file = request.files["file"]
    if file.filename == "":
        flash("No file selected", "error")
        return redirect(url_for("index"))
    _, ext = os.path.splitext(file.filename.lower())
    if ext not in SUPPORTED_EXTENSIONS:
        flash(f"Unsupported file type: {ext}", "error")
        return redirect(url_for("index"))
    filename = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.exists(filepath):
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(filepath):
            filename = f"{base}_{counter}{ext}"
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            counter += 1
    file.save(filepath)
    flash(f"Uploaded {filename} successfully", "success")
    return redirect(url_for("index"))


@app.route("/screensaver", methods=["POST"])
def screensaver():
    if "image" not in request.files:
        flash("No image selected", "error")
        return redirect(url_for("index"))
    file = request.files["image"]
    if file.filename == "":
        flash("No image selected", "error")
        return redirect(url_for("index"))
    _, ext = os.path.splitext(file.filename.lower())
    if ext not in IMAGE_EXTENSIONS:
        flash(f"Unsupported image type: {ext}", "error")
        return redirect(url_for("index"))
    ensure_directory(SCREENSAVER_FOLDER)
    filename = f"screensaver{ext}"
    filepath = os.path.join(SCREENSAVER_FOLDER, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    file.save(filepath)

    try:
        from display import EPaperDisplay, WIDTH, HEIGHT
        from PIL import Image
        display = EPaperDisplay(debug_mode=False)
        if display.initialized:
            img = Image.open(filepath)
            img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
            display.show(img, partial=False)
            time.sleep(2)
            display.clear()
            display.sleep()
            flash("Screensaver set and shown on display", "success")
        else:
            flash("Screensaver saved (display not available)", "success")
    except Exception as e:
        logger.error(f"Display update failed: {e}")
        flash("Screensaver saved", "success")

    return redirect(url_for("index"))


@app.route("/screensaver/<path:filename>")
def screensaver_file(filename):
    from flask import send_from_directory
    return send_from_directory(SCREENSAVER_FOLDER, filename)


@app.route("/delete/<path:filename>", methods=["POST"])
def delete_file(filename):
    safe_name = secure_filename(filename)
    filepath = os.path.join(UPLOAD_FOLDER, safe_name)
    if not os.path.exists(filepath):
        flash("File not found", "error")
        return redirect(url_for("index"))
    if not os.path.isfile(filepath):
        flash("Cannot delete directories", "error")
        return redirect(url_for("index"))
    try:
        os.remove(filepath)
        flash(f"Deleted {safe_name} successfully", "success")
    except Exception as e:
        flash(f"Error deleting file: {str(e)}", "error")
    return redirect(url_for("index"))


@app.route("/status")
def status():
    return jsonify({"battery": get_battery_percentage(), "page_count": get_page_count(), "is_pi": IS_PI, "library_path": UPLOAD_FOLDER, "cache_dir": SCREENSAVER_FOLDER})


if __name__ == "__main__":
    ensure_directory(UPLOAD_FOLDER)
    ensure_directory(SCREENSAVER_FOLDER)
    logger.info("Starting e-Reader Web UI...")
    logger.info(f"Library path: {UPLOAD_FOLDER}")
    logger.info(f"Cache dir: {SCREENSAVER_FOLDER}")
    logger.info(f"Running on Pi: {IS_PI}")
    app.run(host="0.0.0.0", port=5000, debug=False)
