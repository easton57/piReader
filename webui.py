"""
Flask web application for the e-reader.
"""

import logging
import os

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template_string,
    request,
    url_for,
)
from werkzeug.utils import secure_filename

from config import CACHE_DIR, LIBRARY_PATH, MAX_UPLOAD_MB, SUPPORTED_EXTENSIONS
from pisugar import get_battery_percentage as _battery

IS_PI = os.path.exists("/proc/device-tree/model")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(16)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

UPLOAD_FOLDER = LIBRARY_PATH
SCREENSAVER_FOLDER = CACHE_DIR
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp"}

logger = logging.getLogger(__name__)

# Set by main.py so the web UI can ask the reader to do things (e.g. shut down
# cleanly: save progress, push sync, show the screensaver, then power off).
ACTION_QUEUE = None


def get_battery_percentage():
    value = _battery()
    return value if value is not None else 0


def ensure_directory(path):
    os.makedirs(path, exist_ok=True)


def get_library_files():
    files = []
    if os.path.exists(UPLOAD_FOLDER):
        for fn in os.listdir(UPLOAD_FOLDER):
            fp = os.path.join(UPLOAD_FOLDER, fn)
            _, ext = os.path.splitext(fn.lower())
            if os.path.isfile(fp) and not fn.startswith(".") and ext in SUPPORTED_EXTENSIONS:
                files.append(
                    {"name": fn, "path": fp, "size": os.path.getsize(fp), "extension": ext}
                )
    return sorted(files, key=lambda x: x["name"].lower())


def get_current_screensaver():
    for ext in sorted(IMAGE_EXTENSIONS):
        ip = os.path.join(SCREENSAVER_FOLDER, "screensaver" + ext)
        if os.path.exists(ip):
            return ip
    return None


def format_file_size(sb):
    for u in ["B", "KB", "MB"]:
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
.cloud-form{display:flex;flex-direction:column;gap:10px}
.cloud-form label{display:flex;flex-direction:column;font-size:0.85rem;color:#555;gap:4px}
.cloud-form input{padding:8px;border:1px solid #ddd;border-radius:4px;font-size:0.95rem}
.cloud-form .btn{align-self:flex-start}
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
<div class="card"><h2>Cloud (WebDAV)</h2><form action="{{url_for("cloud_settings")}}" method="post" class="cloud-form"><label>Folder URL<input type="url" name="url" value="{{cloud.url}}" placeholder="https://cloud.example.com/remote.php/dav/files/USER/Books"></label><label>User<input type="text" name="user" value="{{cloud.user}}" autocomplete="username"></label><label>Password / app password<input type="password" name="password" placeholder="{{"(unchanged)" if cloud.has_password else ""}}" autocomplete="new-password"></label><button type="submit" class="btn">Save &amp; test</button></form><p style="margin-top:12px;font-size:0.85rem;color:#666">Shows up as [Cloud] on the reader. Books download when you open them. Leave the URL empty to turn it off. For Nextcloud, use an app password.</p></div>
<div class="card"><h2>Reading progress sync (KOReader / Koodo)</h2><form action="{{url_for("kosync_settings")}}" method="post" class="cloud-form"><label>Sync server URL<input type="url" name="url" value="{{kosync.url}}" placeholder="https://sync.koreader.rocks"></label><label>User<input type="text" name="user" value="{{kosync.user}}" autocomplete="username"></label><label>Password<input type="password" name="password" placeholder="{{"(unchanged)" if kosync.configured else ""}}" autocomplete="new-password"></label><label style="flex-direction:row;align-items:center;gap:8px"><input type="checkbox" name="register"> Create this account on the server</label><button type="submit" class="btn">Save &amp; test</button></form><p style="margin-top:12px;font-size:0.85rem;color:#666">Use the same server and login in Koodo Reader under Settings &gt; "Auto sync reading progress with KOReader". EPUBs only. Leave the URL empty to turn it off.</p></div>
<div class="card"><h2>Screensaver</h2><div class="screensaver">{%if screensaver%}<div class="screensaver-thumb"><img src="{{url_for("screensaver_file",filename=screensaver_filename)}}" alt="Current screensaver"></div><div><p style="margin-bottom:12px">Current screensaver set</p><form action="{{url_for("screensaver")}}" method="post" enctype="multipart/form-data"><input type="file" name="image" accept=".png,.jpg,.jpeg,.gif,.bmp" style="margin-bottom:12px"><button type="submit" class="btn">Change Screensaver</button></form></div>{%else%}<div class="empty-state" style="padding:20px;text-align:left"><p style="margin-bottom:12px">No screensaver set</p><form action="{{url_for("screensaver")}}" method="post" enctype="multipart/form-data"><input type="file" name="image" accept=".png,.jpg,.jpeg,.gif,.bmp" style="margin-bottom:12px"><button type="submit" class="btn">Set Screensaver</button></form></div>{%endif%}</div></div>
<div class="card"><h2>Library ({{books|length}} books)</h2>{%if books%}<ul class="file-list">{%for book in books%}<li class="file-item"><div class="file-info"><span class="file-ext {{book.extension[1:]}}">{{book.extension[1:]}}</span><span class="file-name">{{book.name}}</span><span class="file-size">{{book.size_formatted}}</span></div><form action="{{url_for("delete_file",filename=book.name)}}" method="post"><button type="submit" class="delete-btn" onclick="return confirm('Delete ' + this.dataset.name + '?')" data-name="{{book.name}}">Delete</button></form></li>{%endfor%}</ul>{%else%}<div class="empty-state"><p>No books in library</p><p style="font-size:0.85rem;margin-top:8px">Upload .txt, .md, .epub, or .pdf files below</p></div>{%endif%}</div>
<div class="card"><h2>Power</h2><form action="{{url_for("shutdown")}}" method="post"><button type="submit" class="btn" style="background:#c62828" onclick="return confirm('Shut down the reader?')">Shut down</button></form><p style="margin-top:12px;font-size:0.85rem;color:#666">Saves your place and powers off. Same as holding the GPIO button for 3 s.</p></div>
<div class="card"><h2>Downloaded from Cloud ({{downloads|length}})</h2>{%if downloads%}<ul class="file-list">{%for d in downloads%}<li class="file-item"><div class="file-info"><span class="file-ext {{d.extension[1:]}}">{{d.extension[1:]}}</span><span class="file-name">{{d.title}}</span><span class="file-size">{{d.size_formatted}}</span></div><form action="{{url_for("delete_download")}}" method="post"><input type="hidden" name="path" value="{{d.rel}}"><button type="submit" class="delete-btn" onclick="return confirm('Remove the downloaded copy of ' + this.dataset.name + '? It stays in the cloud; reading progress is kept.')" data-name="{{d.title}}">Remove</button></form></li>{%endfor%}</ul><p style="margin-top:12px;font-size:0.85rem;color:#666">Total {{downloads_total}}. Removing only frees space on the reader; the book stays in the cloud and progress is kept.</p>{%else%}<div class="empty-state" style="padding:20px"><p>Nothing downloaded</p></div>{%endif%}</div>
<div class="card"><h2>Upload Books</h2><form action="{{url_for("upload")}}" method="post" enctype="multipart/form-data" class="upload-form"><input type="file" name="file" multiple accept=".txt,.md,.epub,.pdf" required><button type="submit" class="btn">Upload</button></form><p style="margin-top:12px;font-size:0.85rem;color:#666">Supported formats: .txt, .md, .epub, .pdf (max {{max_mb}}MB)</p></div>
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
    downloads = get_downloads()
    screensaver = get_current_screensaver()
    screensaver_filename = os.path.basename(screensaver) if screensaver else None
    return render_template_string(
        HTML_TEMPLATE,
        battery=battery,
        books=books,
        screensaver=screensaver,
        screensaver_filename=screensaver_filename,
        is_pi=IS_PI,
        max_mb=MAX_UPLOAD_MB,
        cloud=_cloud_view(),
        downloads=downloads,
        downloads_total=format_file_size(sum(d["size"] for d in downloads)),
        kosync=_kosync_view(),
    )


def _kosync_view():
    import kosync

    s = kosync.load_settings() or {}
    return {"url": s.get("url", ""), "user": s.get("user", ""), "configured": bool(s)}


@app.route("/kosync", methods=["POST"])
def kosync_settings():
    import kosync
    from config import KOSYNC_SETTINGS_FILE

    url = request.form.get("url", "").strip().rstrip("/")
    user = request.form.get("user", "").strip()
    password = request.form.get("password", "")
    if not url:
        try:
            os.remove(KOSYNC_SETTINGS_FILE)
        except FileNotFoundError:
            pass
        flash("Progress sync turned off", "success")
        return redirect(url_for("index"))
    if not url.startswith(("http://", "https://")) or not user:
        flash("Need a server URL (https://...) and a user", "error")
        return redirect(url_for("index"))
    old = kosync.load_settings() or {}
    if password:
        key = kosync.password_key(password)
    elif old.get("user") == user and old.get("key"):
        key = old["key"]  # blank keeps the saved password
    else:
        flash("Enter the password", "error")
        return redirect(url_for("index"))
    client = kosync.KoSyncClient(url, user, key)
    try:
        if request.form.get("register"):
            client.register()
            flash(f"Registered {user} on the sync server", "success")
        client.auth()
    except kosync.KoSyncError as e:
        flash(f"Not saved: {e}", "error")
        return redirect(url_for("index"))
    kosync.save_settings(url, user, key)
    flash("Saved. Connected to the sync server.", "success")
    return redirect(url_for("index"))


def _cloud_view():
    from webdav import load_settings

    s = load_settings() or {}
    return {"url": s.get("url", ""), "user": s.get("user", ""),
            "has_password": bool(s.get("password"))}


@app.route("/cloud", methods=["POST"])
def cloud_settings():
    from config import WEBDAV_SETTINGS_FILE
    from webdav import WebDAVClient, WebDAVError, load_settings, save_settings

    url = request.form.get("url", "").strip()
    user = request.form.get("user", "").strip()
    password = request.form.get("password", "")
    if not url:
        try:
            os.remove(WEBDAV_SETTINGS_FILE)
        except FileNotFoundError:
            pass
        flash("Cloud turned off", "success")
        return redirect(url_for("index"))
    if not url.startswith(("http://", "https://")):
        flash("URL must start with https:// (or http://)", "error")
        return redirect(url_for("index"))
    if not password:  # blank keeps the saved one
        password = (load_settings() or {}).get("password", "")
    save_settings(url, user, password)
    try:
        entries = WebDAVClient(url, user, password).list("")
        flash(f"Saved. Connected: {len(entries)} item(s) in that folder.", "success")
    except WebDAVError as e:
        flash(f"Saved, but the test failed: {e}", "error")
    return redirect(url_for("index"))


@app.route("/upload", methods=["POST"])
def upload():
    files = [f for f in request.files.getlist("file") if f and f.filename]
    if not files:
        flash("No file selected", "error")
        return redirect(url_for("index"))
    ensure_directory(UPLOAD_FOLDER)
    for file in files:
        _, ext = os.path.splitext(file.filename.lower())
        if ext not in SUPPORTED_EXTENSIONS:
            flash(f"Skipped {file.filename}: unsupported type {ext}", "error")
            continue
        filename = secure_filename(file.filename) or f"upload{ext}"
        base, ext = os.path.splitext(filename)
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        counter = 1
        while os.path.exists(filepath):
            filename = f"{base}_{counter}{ext}"
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            counter += 1
        file.save(filepath)
        flash(f"Uploaded {filename}", "success")
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
    file.save(filepath)

    for old_ext in IMAGE_EXTENSIONS:  # only keep one screensaver
        old = os.path.join(SCREENSAVER_FOLDER, "screensaver" + old_ext)
        if old != filepath and os.path.exists(old):
            os.remove(old)
    # The reader shows it on idle/shutdown. Don't touch the panel from here:
    # the main loop owns it, and a second driver instance fights over SPI/GPIO.
    flash("Screensaver saved", "success")
    return redirect(url_for("index"))


@app.route("/screensaver/<path:filename>")
def screensaver_file(filename):
    from flask import send_from_directory

    return send_from_directory(SCREENSAVER_FOLDER, filename)


@app.route("/delete/<path:filename>", methods=["POST"])
def delete_file(filename):
    names = {f["name"] for f in get_library_files()}
    if filename not in names:
        flash("File not found", "error")
        return redirect(url_for("index"))
    try:
        os.remove(os.path.join(UPLOAD_FOLDER, filename))
        flash(f"Deleted {filename}", "success")
    except OSError as e:
        flash(f"Error deleting file: {e}", "error")
    return redirect(url_for("index"))


def _cloud():
    from cloud import CloudSource

    return CloudSource(UPLOAD_FOLDER)


def get_downloads():
    """Downloaded cloud books with titles (cached Koodo names, no network)."""
    cloud = _cloud()
    items = []
    for rel in cloud.downloaded_books(UPLOAD_FOLDER):
        full = os.path.join(UPLOAD_FOLDER, rel)
        name = os.path.basename(rel)
        title = cloud.title_for(os.path.dirname(full), name, fetch=False) or name
        size = os.path.getsize(full)
        items.append({"rel": rel, "title": title, "size": size,
                      "size_formatted": format_file_size(size),
                      "extension": os.path.splitext(name)[1].lower()})
    return sorted(items, key=lambda d: d["title"].lower())


@app.route("/downloads/delete", methods=["POST"])
def delete_download():
    rel = request.form.get("path", "")
    match = next((d for d in get_downloads() if d["rel"] == rel), None)
    if match is None:  # only paths we listed: no traversal
        flash("Download not found", "error")
        return redirect(url_for("index"))
    try:
        _cloud().delete_download(os.path.join(UPLOAD_FOLDER, rel))
        flash(f"Removed {match['title']} from the reader", "success")
    except (OSError, ValueError) as e:
        flash(f"Couldn't remove it: {e}", "error")
    return redirect(url_for("index"))


@app.errorhandler(413)
def too_large(_):
    flash(f"File too large (max {MAX_UPLOAD_MB}MB)", "error")
    return redirect(url_for("index"))


@app.route("/shutdown", methods=["POST"])
def shutdown():
    if ACTION_QUEUE is None:
        flash("Reader app isn't running", "error")
        return redirect(url_for("index"))
    ACTION_QUEUE.put("shutdown")
    return "<p style='font-family:sans-serif'>Shutting down. You can close this page.</p>"


@app.route("/status")
def status():
    return jsonify(
        {
            "battery": get_battery_percentage(),
            "is_pi": IS_PI,
            "library_path": UPLOAD_FOLDER,
            "cache_dir": SCREENSAVER_FOLDER,
        }
    )


if __name__ == "__main__":
    ensure_directory(UPLOAD_FOLDER)
    ensure_directory(SCREENSAVER_FOLDER)
    logger.info("Starting e-Reader Web UI...")
    logger.info(f"Library path: {UPLOAD_FOLDER}")
    logger.info(f"Cache dir: {SCREENSAVER_FOLDER}")
    logger.info(f"Running on Pi: {IS_PI}")
    logging.basicConfig(level=logging.INFO)
    app.run(host="0.0.0.0", port=5000, debug=False)
