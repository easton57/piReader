"""The [Cloud] folder: a WebDAV directory merged with its local download mirror.

Remote files are listed but not fetched until you open one; it is then saved
under library/.cloud/<same path as on the server> and behaves like any local
book (offline reading, its own saved progress).
"""

import logging
import os
import time

from config import CLOUD_LIST_TTL, SUPPORTED_EXTENSIONS
from webdav import WebDAVError, client_from_settings

logger = logging.getLogger(__name__)

MIRROR_DIRNAME = ".cloud"


class CloudSource:
    def __init__(self, library_root, client_factory=client_from_settings):
        self.mirror_root = os.path.join(os.path.abspath(library_root), MIRROR_DIRNAME)
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
