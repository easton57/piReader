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
import sqlite3
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

    def download(self, local_path):
        """Fetch the remote counterpart of local_path into the mirror."""
        client = self._client_factory()
        if client is None:
            raise WebDAVError("Cloud not configured")
        logger.info(f"Downloading {self.rel(local_path)}")
        return client.download(self.rel(local_path), local_path)

    # --- Koodo Reader titles ------------------------------------------------

    def title_for(self, local_dir, name):
        """Readable title for a Koodo-named file ("1779244646577.epub"), else None."""
        m = KOODO_FILE.match(name)
        if not m:
            return None
        return self._koodo_titles(local_dir).get(m.group(1))

    def _koodo_titles(self, local_dir):
        rel = self.rel(local_dir)
        cached = self._titles.get(rel)
        if cached and time.monotonic() - cached[0] < CLOUD_LIST_TTL:
            return cached[1]
        db_path = os.path.join(
            self.cache_dir, f"koodo-books-{hashlib.md5(rel.encode()).hexdigest()[:8]}.db")
        client = self._client_factory()
        if client is not None:
            try:
                client.download_sibling(posixpath.join(rel, "..", "config", "books.db"), db_path)
            except WebDAVError as e:
                # Offline or not a Koodo folder: fall back to the last copy, if any.
                logger.info(f"No Koodo books.db for {rel or 'cloud root'}: {e}")
        titles = read_koodo_titles(db_path)
        self._titles[rel] = (time.monotonic(), titles)
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
