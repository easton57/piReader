import json
import logging
import os
from typing import List, Optional, Tuple

from config import CLOUD_LABEL, SHOW_CLOUD_ON_HOME, SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)

PARENT = ".."  # pseudo-entry at the top of every subfolder


class FileBrowser:
    """Directory navigation plus per-book reading progress."""

    def __init__(self, root_path: str, state_file: str, cloud=None):
        self.root_path = os.path.abspath(root_path)
        self.state_file = state_file
        self.cloud = cloud  # optional CloudSource
        self.current_path = self.root_path
        self.items: List[Tuple[str, bool]] = []
        self.remote_only = set()  # names in the current cloud folder not downloaded yet
        self.cloud_offline = False
        self.cursor_position = 0
        self._listing_signature = None
        os.makedirs(self.root_path, exist_ok=True)
        self.state = self._load_state()
        self._refresh()

    # --- persisted state ----------------------------------------------------
    # {"last_open": path | null, "progress": {path: {"page": n, "mode": m}}}

    def _load_state(self) -> dict:
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
            if isinstance(state, dict):
                state.setdefault("progress", {})
                state.setdefault("last_open", None)
                return state
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Ignoring unreadable state file: {e}")
        return {"last_open": None, "progress": {}}

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        tmp = self.state_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, self.state_file)  # atomic: power loss can't corrupt it
        except OSError as e:
            logger.error(f"Couldn't save state: {e}")

    def save_progress(self, path: str, page, mode: Optional[str] = None) -> None:
        """page may be a full progress dict ({"page", "mode", "pos", ...})."""
        entry = dict(page) if isinstance(page, dict) else {"page": page, "mode": mode}
        self.state["progress"][path] = entry
        self.state["last_open"] = path
        self._save_state()

    def get_progress(self, path: str) -> dict:
        return self.state["progress"].get(path, {})

    def set_last_open(self, path: Optional[str]) -> None:
        self.state["last_open"] = path
        self._save_state()

    def get_last_open(self) -> Optional[str]:
        path = self.state.get("last_open")
        return path if path and os.path.isfile(path) else None

    def reveal(self, path: str) -> None:
        """Navigate to the folder containing path and put the cursor on it."""
        folder = os.path.dirname(os.path.abspath(path))
        if not folder.startswith(self.root_path) or not os.path.isdir(folder):
            return
        self.current_path = folder
        self._refresh(keep=os.path.basename(path))

    # --- listing ------------------------------------------------------------

    def in_cloud(self) -> bool:
        return self.cloud is not None and self.cloud.contains(self.current_path)

    def _scan(self, force_remote: bool = False) -> List[Tuple[str, bool]]:
        try:
            entries = os.listdir(self.current_path)
        except OSError as e:
            logger.error(f"Error listing {self.current_path}: {e}")
            entries = []
        dirs, files = [], []
        for entry in entries:
            if entry.startswith("."):
                continue
            full = os.path.join(self.current_path, entry)
            if os.path.isdir(full):
                dirs.append((entry, True))
            elif os.path.splitext(entry)[1].lower() in SUPPORTED_EXTENSIONS:
                files.append((entry, False))
        remote_only = set()
        if self.in_cloud():
            remote = self.cloud.remote_entries(self.current_path, force=force_remote)
            self.cloud_offline = remote is None
            local = {name for name, _ in dirs + files}
            for name, is_dir in remote or []:
                if name not in local:
                    (dirs if is_dir else files).append((name, is_dir))
                    if not is_dir:
                        remote_only.add(name)
        self.remote_only = remote_only

        if self.is_root() and self.cloud is not None and SHOW_CLOUD_ON_HOME:
            files.extend((name, False) for name in self.cloud.downloaded_books(self.root_path))

        key = lambda item: self.label(item[0]).lower()
        top = []
        if self.current_path != self.root_path:
            top.append((PARENT, True))
        elif self.cloud is not None and self.cloud.enabled():
            top.append((CLOUD_LABEL, True))
        return top + sorted(dirs, key=key) + sorted(files, key=key)

    def _refresh(self, keep: Optional[str] = None, force_remote: bool = False) -> None:
        self.items = self._scan(force_remote)
        self._listing_signature = tuple(self.items)
        self.cursor_position = 0
        if keep:
            for i, (name, _) in enumerate(self.items):
                if name == keep:
                    self.cursor_position = i
                    break

    def refresh_if_changed(self) -> None:
        """Pick up files added/removed (e.g. via the web UI) without losing the cursor."""
        if tuple(self._scan()) != self._listing_signature:
            current = self.items[self.cursor_position][0] if self.items else None
            self._refresh(keep=current)

    def refresh(self) -> None:
        self._refresh()

    # --- navigation ---------------------------------------------------------

    def move_up(self) -> None:
        if self.items:
            self.cursor_position = (self.cursor_position - 1) % len(self.items)

    def move_down(self) -> None:
        if self.items:
            self.cursor_position = (self.cursor_position + 1) % len(self.items)

    def get_selected_path(self) -> Optional[str]:
        if not self.items:
            return None
        name = self.items[self.cursor_position][0]
        if name == CLOUD_LABEL and self.current_path == self.root_path and self.cloud:
            return self.cloud.mirror_root
        return os.path.join(self.current_path, name)

    def label(self, name: str) -> str:
        """Display name: Koodo's book title for its numbered files, else the filename."""
        if self.cloud is not None:
            full = os.path.join(self.current_path, name)
            if self.cloud.contains(full) and full != self.cloud.mirror_root:
                title = self.cloud.title_for(os.path.dirname(full), os.path.basename(full))
                if title:
                    return title
                if os.sep in name:  # downloaded cloud book listed on the home page
                    return os.path.basename(name)
        return name

    def select_path(self, path: str) -> bool:
        """Put the cursor on path if it's listed in the current folder."""
        for i, (name, _) in enumerate(self.items):
            if os.path.join(self.current_path, name) == path:
                self.cursor_position = i
                return True
        return False

    def selected_is_dir(self) -> bool:
        return bool(self.items) and self.items[self.cursor_position][1]

    def selected_is_remote(self) -> bool:
        """Selected item is a cloud file that hasn't been downloaded yet."""
        return bool(self.items) and self.items[self.cursor_position][0] in self.remote_only

    def selected_is_parent(self) -> bool:
        return bool(self.items) and self.items[self.cursor_position][0] == PARENT

    def enter(self, path: str) -> None:
        if self.cloud is not None and self.cloud.contains(path):
            os.makedirs(path, exist_ok=True)  # remote folder not mirrored yet
            self.current_path = os.path.abspath(path)
            self._refresh(force_remote=True)
        elif os.path.isdir(path):
            self.current_path = os.path.abspath(path)
            self._refresh()

    def go_up(self) -> bool:
        if self.current_path == self.root_path:
            return False
        child = os.path.basename(self.current_path)
        if self.cloud is not None and self.current_path == self.cloud.mirror_root:
            child = CLOUD_LABEL
        self.current_path = os.path.dirname(self.current_path)
        self._refresh(keep=child)
        return True

    def get_current_dir(self) -> str:
        return self.current_path

    def get_title(self) -> str:
        if self.is_root():
            return "Library"
        if self.cloud is not None and self.current_path == self.cloud.mirror_root:
            return "Cloud"
        return os.path.basename(self.current_path)

    def is_root(self) -> bool:
        return self.current_path == self.root_path

    def get_items_for_display(self) -> List[Tuple[str, bool]]:
        return self.items

    def get_cursor_position(self) -> int:
        return self.cursor_position
