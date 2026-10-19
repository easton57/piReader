"""Minimal WebDAV client (PROPFIND + GET) using only the standard library.

Settings live in webdav.json (gitignored), editable from the web UI:
    {"url": "https://cloud.example.com/remote.php/dav/files/USER/Books",
     "user": "USER", "password": "app-password"}
For Nextcloud, use an app password (Settings > Security > Devices & sessions).
"""

import base64
import json
import logging
import os
import posixpath
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from config import WEBDAV_SETTINGS_FILE, WEBDAV_TIMEOUT

logger = logging.getLogger(__name__)

DAV = "{DAV:}"
PROPFIND_BODY = b"""<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:"><d:prop>
<d:resourcetype/><d:getcontentlength/><d:getlastmodified/><d:getetag/>
</d:prop></d:propfind>"""


class WebDAVError(Exception):
    pass


def load_settings(path=None):
    path = path or WEBDAV_SETTINGS_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Ignoring unreadable {path}: {e}")
        return None
    if not isinstance(data, dict) or not data.get("url"):
        return None
    return data


def save_settings(url, user, password, path=None):
    path = path or WEBDAV_SETTINGS_FILE
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"url": url.strip(), "user": user.strip(), "password": password}, f, indent=2)
    os.chmod(tmp, 0o600)  # holds a password
    os.replace(tmp, path)


def client_from_settings(path=None):
    s = load_settings(path)
    if not s:
        return None
    return WebDAVClient(s["url"], s.get("user", ""), s.get("password", ""))


class WebDAVClient:
    def __init__(self, url, user="", password="", timeout=WEBDAV_TIMEOUT):
        self.base_url = url.rstrip("/") + "/"
        self.base_path = urllib.parse.urlsplit(self.base_url).path
        self.timeout = timeout
        self._headers = {"User-Agent": "piReader"}
        if user:
            token = base64.b64encode(f"{user}:{password}".encode()).decode()
            self._headers["Authorization"] = f"Basic {token}"
        self._ssl = ssl.create_default_context()

    def _url(self, rel, is_dir=False):
        rel = rel.strip("/")
        quoted = "/".join(urllib.parse.quote(p) for p in rel.split("/")) if rel else ""
        url = self.base_url + quoted
        if is_dir and rel:
            url += "/"
        return url

    def _request(self, method, url, body=None, headers=None):
        req = urllib.request.Request(url, data=body, method=method,
                                     headers={**self._headers, **(headers or {})})
        try:
            return urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise WebDAVError("Login failed (check user / app password)") from e
            if e.code == 404:
                raise WebDAVError("Folder not found on server") from e
            raise WebDAVError(f"Server returned HTTP {e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise WebDAVError(f"Can't reach server: {reason}") from e

    def list(self, rel=""):
        """List one directory: [{"name", "is_dir", "size", "etag", "modified"}]."""
        resp = self._request("PROPFIND", self._url(rel, is_dir=True), PROPFIND_BODY,
                             {"Depth": "1", "Content-Type": "application/xml"})
        with resp:
            data = resp.read()
        try:
            root = ET.fromstring(data)
        except ET.ParseError as e:
            raise WebDAVError("Server sent an invalid PROPFIND response") from e

        this_dir = posixpath.normpath(posixpath.join(self.base_path, rel.strip("/")))
        entries = []
        for response in root.iter(DAV + "response"):
            href = response.findtext(DAV + "href") or ""
            path = urllib.parse.unquote(urllib.parse.urlsplit(href).path)
            if posixpath.normpath(path) == this_dir:
                continue  # the directory itself
            name = posixpath.basename(path.rstrip("/"))
            if not name:
                continue
            props = {}
            for propstat in response.iter(DAV + "propstat"):
                if "200" not in (propstat.findtext(DAV + "status") or "200"):
                    continue
                prop = propstat.find(DAV + "prop")
                if prop is not None:
                    props.update({child.tag: child for child in prop})
            rtype = props.get(DAV + "resourcetype")
            is_dir = rtype is not None and rtype.find(DAV + "collection") is not None
            size = props.get(DAV + "getcontentlength")
            entries.append({
                "name": name,
                "is_dir": is_dir,
                "size": int(size.text) if size is not None and (size.text or "").isdigit() else None,
                "etag": (props[DAV + "getetag"].text or "").strip('"') if DAV + "getetag" in props else None,
                "modified": props[DAV + "getlastmodified"].text if DAV + "getlastmodified" in props else None,
            })
        return entries

    def download(self, rel, dest, progress=None):
        """Stream a remote file to dest (atomically). progress(done, total) optional."""
        return self._download_url(self._url(rel), dest, progress)

    def download_sibling(self, rel, dest):
        """Download a path relative to the base folder that may climb above it
        (e.g. "../config/books.db"), resolved against the server path."""
        parts = urllib.parse.urlsplit(self.base_url)
        path = posixpath.normpath(posixpath.join(parts.path, rel))
        quoted = "/".join(urllib.parse.quote(p) for p in path.split("/"))
        return self._download_url(f"{parts.scheme}://{parts.netloc}{quoted}", dest)

    def _download_url(self, url, dest, progress=None):
        resp = self._request("GET", url)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        part = dest + ".part"
        total = int(resp.headers.get("Content-Length") or 0) or None
        done = 0
        try:
            with resp, open(part, "wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
            if total is not None and done != total:
                raise WebDAVError(f"Download incomplete ({done}/{total} bytes)")
            os.replace(part, dest)
        except (OSError, WebDAVError) as e:
            try:
                os.remove(part)
            except OSError:
                pass
            if isinstance(e, WebDAVError):
                raise
            raise WebDAVError(f"Download failed: {e}") from e
        return dest
