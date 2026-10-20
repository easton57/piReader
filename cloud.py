"""The [Cloud] folder: a WebDAV directory merged with its local download mirror.

Remote files are listed but not fetched until you open one; it is then saved
under library/.cloud/<same path as on the server> and behaves like any local
book (offline reading, its own saved progress).
"""

import hashlib
import logging
import os
import posixpath
import re
import shutil
import sqlite3
import threading
import time

from config import CACHE_DIR, CLOUD_LIST_TTL, SUPPORTED_EXTENSIONS
from webdav import WebDAVError, client_from_settings

logger = logging.getLogger(__name__)

MIRROR_DIRNAME = ".cloud"

# Koodo Reader keeps books in KoodoReader/book/<key>.<format> (key = a long
# number) and their titles in KoodoReader/config/books.db (SQLite).
KOODO_FILE = re.compile(r"^(\d{10,})\.[A-Za-z0-9]+$")


class CloudSource:
    def __init__(self, library_root, client_factory=client_from_settings, cache_dir=CACHE_DIR):
        self.mirror_root = os.path.join(os.path.abspath(library_root), MIRROR_DIRNAME)
        self.cache_dir = cache_dir
        self._titles = {}  # rel -> (time, {key: title})
        self._client_factory = client_factory
        self._cache = {}  # rel -> (time, entries)
        self._failed = {}  # rel -> time of last failed listing
        self.last_error = None

    def enabled(self):
        return self._client_factory() is not None

    def contains(self, path):
        path = os.path.abspath(path)
        return path == self.mirror_root or path.startswith(self.mirror_root + os.sep)

    def rel(self, path):
        r = os.path.relpath(os.path.abspath(path), self.mirror_root)
        return "" if r == "." else r.replace(os.sep, "/")

    def remote_entries(self, local_dir, force=False):
        """Remote (name, is_dir) for the folder mirrored at local_dir, or None if offline."""
        rel = self.rel(local_dir)
        cached = self._cache.get(rel)
        now = time.monotonic()
        if cached and not force and now - cached[0] < CLOUD_LIST_TTL:
            return cached[1]
        # Don't stall every render on a dead network: retry at most every 30s
        # unless the user explicitly (re)enters the folder.
        if not force and now - self._failed.get(rel, -1e9) < 30:
            return cached[1] if cached else None
        client = self._client_factory()
        if client is None:
            self.last_error = "Cloud not configured"
            return None
        try:
            raw = client.list(rel)
        except WebDAVError as e:
            logger.warning(f"Cloud listing failed: {e}")
            self.last_error = str(e)
            self._failed[rel] = time.monotonic()
            # Keep showing a stale listing rather than nothing.
            return cached[1] if cached else None
        entries = [
            (e["name"], e["is_dir"]) for e in raw
            if not e["name"].startswith(".")
            and (e["is_dir"] or os.path.splitext(e["name"])[1].lower() in SUPPORTED_EXTENSIONS)
        ]
        self._cache[rel] = (time.monotonic(), entries)
        self._failed.pop(rel, None)
        self.last_error = None
        return entries

    def download(self, local_path, progress=None):
        """Fetch the remote counterpart of local_path into the mirror.
        progress(done, total_or_None) is called per chunk; raising aborts."""
        client = self._client_factory()
        if client is None:
            raise WebDAVError("Cloud not configured")
        logger.info(f"Downloading {self.rel(local_path)}")
        return client.download(self.rel(local_path), local_path, progress=progress)

    def downloaded_books(self, library_root):
        """Downloaded cloud books as paths relative to library_root
        (".cloud/KoodoReader/book/123.epub"), for listing on the home page."""
        found = []
        for dirpath, dirnames, filenames in os.walk(self.mirror_root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for f in filenames:
                if not f.startswith(".") and os.path.splitext(f)[1].lower() in SUPPORTED_EXTENSIONS:
                    found.append(os.path.relpath(os.path.join(dirpath, f), library_root))
        return found

    def delete_download(self, local_path):
        """Remove a downloaded copy and its caches. Reading progress is kept,
        so downloading it again resumes where you were."""
        from documents import file_key

        local_path = os.path.abspath(local_path)
        if not self.contains(local_path) or not os.path.isfile(local_path):
            raise ValueError("Not a downloaded cloud book")
        key = file_key(local_path)
        os.remove(local_path)
        for name in (f"{key}.book", f"{key}.txt"):
            try:
                os.remove(os.path.join(self.cache_dir, name))
            except FileNotFoundError:
                pass
        shutil.rmtree(os.path.join(self.cache_dir, key), ignore_errors=True)  # PDF page images
        # Drop now-empty mirror folders.
        folder = os.path.dirname(local_path)
        while folder != self.mirror_root and self.contains(folder) and not os.listdir(folder):
            os.rmdir(folder)
            folder = os.path.dirname(folder)

    # --- Koodo Reader titles ------------------------------------------------

    def title_for(self, local_dir, name, fetch=True):
        """Readable title for a Koodo-named file ("1779244646577.epub"), else None.
        fetch=False only uses the cached copy of Koodo's books.db (no network)."""
        m = KOODO_FILE.match(name)
        if not m:
            return None
        return self._koodo_titles(local_dir, fetch).get(m.group(1))

    def _db_path(self, rel):
        return os.path.join(
            self.cache_dir, f"koodo-books-{hashlib.md5(rel.encode()).hexdigest()[:8]}.db")

    def _fetch_titles(self, rel):
        client = self._client_factory()
        if client is not None:
            try:
                client.download_sibling(posixpath.join(rel, "..", "config", "books.db"),
                                        self._db_path(rel))
            except WebDAVError as e:
                # Offline or not a Koodo folder: keep using the last copy, if any.
                logger.info(f"No Koodo books.db for {rel or 'cloud root'}: {e}")
        self._titles[rel] = (time.monotonic(), read_koodo_titles(self._db_path(rel)))

    def refresh_titles(self, local_dir):
        """Fetch Koodo's books.db now (blocking). Used when entering a folder,
        behind the loading screen; elsewhere titles refresh in the background."""
        self._fetch_titles(self.rel(local_dir))

    def _koodo_titles(self, local_dir, fetch=True):
        rel = self.rel(local_dir)
        cached = self._titles.get(rel)
        if cached and time.monotonic() - cached[0] < CLOUD_LIST_TTL:
            return cached[1]
        titles = cached[1] if cached else read_koodo_titles(self._db_path(rel))
        if fetch:
            # Never block a screen redraw on the network: refresh in the background
            # and use what we have (the cached copy works offline).
            self._titles[rel] = (time.monotonic(), titles)
            threading.Thread(target=self._fetch_titles, args=(rel,), daemon=True).start()
        return titles


def read_koodo_titles(db_path):
    """{key: "Title - Author"} from a Koodo books.db; {} if missing/unreadable."""
    if not os.path.exists(db_path):
        return {}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT key, name, author FROM books").fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        logger.warning(f"Can't read Koodo library {db_path}: {e}")
        return {}
    titles = {}
    for key, name, author in rows:
        name = (name or "").strip()
        if not name:
            continue
        author = (author or "").strip()
        titles[str(key)] = f"{name} - {author}" if author else name
    return titles
