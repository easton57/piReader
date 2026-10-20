"""KOReader progress sync ("kosync") client — the protocol Koodo Reader and
KOReader speak. Settings live in kosync.json (gitignored), set from the web UI.

Books are identified by KOReader's partial MD5 of the file, so the same EPUB
matches on every device regardless of filename.
"""

import hashlib
import json
import logging
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from config import CACHE_DIR, KOSYNC_PUSH_DELAY, KOSYNC_SETTINGS_FILE, KOSYNC_TIMEOUT

logger = logging.getLogger(__name__)

ACCEPT = "application/vnd.koreader.v1+json"
DEVICE_NAME = "piReader"


class KoSyncError(Exception):
    pass


def partial_md5(path):
    """KOReader's document id: MD5 over 1 KiB samples at 0, 1K, 4K, 16K, ... 1G.

    KOReader computes the first offset as lshift(1024, -2), which wraps to 0
    in 32-bit arithmetic (Koodo's JS `1024 << -2` does the same).
    """
    md5 = hashlib.md5()
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        for i in range(-1, 11):
            offset = 0 if i < 0 else 1024 << (2 * i)
            if offset >= size:
                break
            f.seek(offset)
            chunk = f.read(1024)
            if not chunk:
                break
            md5.update(chunk)
    return md5.hexdigest()


def device_id():
    path = os.path.join(CACHE_DIR, "device_id")
    try:
        with open(path) as f:
            value = f.read().strip()
            if value:
                return value
    except FileNotFoundError:
        pass
    value = uuid.uuid4().hex.upper()
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w") as f:
        f.write(value)
    return value


def password_key(password):
    return hashlib.md5(password.encode()).hexdigest()


def load_settings(path=None):
    path = path or KOSYNC_SETTINGS_FILE
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Ignoring unreadable {path}: {e}")
        return None
    if not isinstance(data, dict) or not all(data.get(k) for k in ("url", "user", "key")):
        return None
    return data


def save_settings(url, user, key, path=None):
    path = path or KOSYNC_SETTINGS_FILE
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"url": url.strip().rstrip("/"), "user": user.strip(), "key": key}, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def client_from_settings(path=None):
    s = load_settings(path)
    return KoSyncClient(s["url"], s["user"], s["key"]) if s else None


class KoSyncClient:
    def __init__(self, url, user, key, timeout=KOSYNC_TIMEOUT):
        self.url = url.rstrip("/")
        self.user = user
        self.key = key
        self.timeout = timeout
        self._ssl = ssl.create_default_context()

    def _call(self, method, path, body=None, auth=True):
        headers = {"Accept": ACCEPT, "Content-Type": "application/json",
                   "User-Agent": DEVICE_NAME}
        if auth:
            headers.update({"x-auth-user": self.user, "x-auth-key": self.key})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise KoSyncError("Login failed (wrong user or password)") from e
            if e.code == 402:
                raise KoSyncError("Username already taken") from e
            raise KoSyncError(f"Server returned HTTP {e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            raise KoSyncError(f"Can't reach sync server: {getattr(e, 'reason', e)}") from e
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError as e:
            raise KoSyncError("Sync server sent invalid JSON") from e

    def auth(self):
        self._call("GET", "/users/auth")
        return True

    def register(self):
        self._call("POST", "/users/create", {"username": self.user, "password": self.key},
                   auth=False)
        return True

    def get_progress(self, document):
        """Remote record or None: {progress, percentage, device, device_id, timestamp}."""
        data = self._call("GET", "/syncs/progress/" + urllib.parse.quote(document))
        if not data or "percentage" not in data:
            return None
        try:
            data["percentage"] = float(data["percentage"])
            data["timestamp"] = int(data.get("timestamp") or 0)
        except (TypeError, ValueError):
            return None
        return data

    def put_progress(self, document, progress, percentage, device_id):
        return self._call("PUT", "/syncs/progress", {
            "document": document,
            "progress": progress,
            "percentage": round(float(percentage), 4),
            "device": DEVICE_NAME,
            "device_id": device_id,
        })


class SyncWorker:
    """Pushes progress in the background so page turns never wait on the network.

    Pushes are coalesced per book and sent KOSYNC_PUSH_DELAY seconds after the
    last change (or immediately on flush(), e.g. when leaving a book).
    """

    def __init__(self, client_factory=client_from_settings, delay=KOSYNC_PUSH_DELAY,
                 pending_file=None):
        self._client_factory = client_factory
        self.delay = delay
        self.device_id = device_id()
        # document -> (due, progress, percentage, read_at). Persisted so progress
        # made offline is pushed after a reboot once the network is back.
        self._pending_file = pending_file or os.path.join(CACHE_DIR, "kosync_pending.json")
        self._pending = self._load_pending()
        self._cond = threading.Condition()
        self._busy = False
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="KoSync")
        self._thread.start()

    def enabled(self):
        return self._client_factory() is not None

    def pull(self, document):
        """Fetch the remote position (blocking, short timeout). None if unavailable."""
        client = self._client_factory()
        if client is None:
            return None
        try:
            remote = client.get_progress(document)
        except KoSyncError as e:
            logger.warning(f"Progress pull failed: {e}")
            return None
        if remote and remote.get("device_id") == self.device_id:
            remote["own"] = True  # our own last push
        return remote

    def _load_pending(self):
        try:
            with open(self._pending_file, encoding="utf-8") as f:
                data = json.load(f)
            # Retry soon after startup (give Wi-Fi a moment).
            return {d: (time.monotonic() + 15, p, pct, at) for d, (p, pct, at) in data.items()}
        except (OSError, ValueError, TypeError):
            return {}

    def _save_pending(self):
        data = {d: [p, pct, at] for d, (_, p, pct, at) in self._pending.items()}
        try:
            os.makedirs(os.path.dirname(self._pending_file), exist_ok=True)
            tmp = self._pending_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self._pending_file)
        except OSError as e:
            logger.warning(f"Couldn't save pending sync: {e}")

    def push(self, document, progress, percentage, read_at=None):
        if not progress:
            return
        with self._cond:
            self._pending[document] = (time.monotonic() + self.delay, progress, percentage,
                                       int(read_at or time.time()))
            self._save_pending()
            self._cond.notify()

    def flush(self, timeout=None):
        """Send everything pending now; wait up to timeout for it to finish."""
        with self._cond:
            self._pending = {d: (0, p, pct, at) for d, (_, p, pct, at) in self._pending.items()}
            self._cond.notify()
            end = time.monotonic() + (timeout if timeout is not None else 0)
            while timeout and (self._pending or self._busy):
                left = end - time.monotonic()
                if left <= 0:
                    break
                self._cond.wait(left)

    def stop(self, timeout=5):
        self.flush(timeout)
        with self._cond:
            self._stop = True
            self._cond.notify()

    def _run(self):
        while True:
            with self._cond:
                while not self._stop:
                    now = time.monotonic()
                    due = [d for d, (t, *_) in self._pending.items() if t <= now]
                    if due:
                        break
                    wait = min((v[0] for v in self._pending.values()), default=now + 60) - now
                    self._cond.wait(max(0.05, wait))
                if self._stop:
                    return
                batch = {d: self._pending.pop(d) for d in due}
                self._busy = True
            client = self._client_factory()
            for doc, (_, progress, pct, read_at) in batch.items():
                if client is None:
                    break
                try:
                    # Don't clobber newer progress another device synced while we
                    # were offline (our push would get a newer server timestamp).
                    remote = client.get_progress(doc)
                    if (remote and remote.get("device_id") != self.device_id
                            and remote["timestamp"] > read_at):
                        logger.info(f"Skipping stale push for {doc[:8]}: "
                                    f"{remote.get('device', 'another device')} is newer")
                    else:
                        client.put_progress(doc, progress, pct, self.device_id)
                        logger.info(f"Pushed progress {pct:.1%} for {doc[:8]}")
                except KoSyncError as e:
                    logger.warning(f"Progress push failed, will retry later: {e}")
                    with self._cond:  # keep newer local changes if any arrived meanwhile
                        self._pending.setdefault(doc, (time.monotonic() + 60, progress, pct, read_at))
            with self._cond:
                self._save_pending()
            with self._cond:
                self._busy = False
                self._cond.notify_all()
