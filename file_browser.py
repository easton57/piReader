import json
import logging
import os
from typing import List, Optional, Tuple

from config import SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)

PARENT = ".."  # pseudo-entry at the top of every subfolder


class FileBrowser:
    """Directory navigation plus per-book reading progress."""

    def __init__(self, root_path: str, state_file: str):
        self.root_path = os.path.abspath(root_path)
        self.state_file = state_file
        self.current_path = self.root_path
        self.items: List[Tuple[str, bool]] = []
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

    def save_progress(self, path: str, page: int, mode: Optional[str] = None) -> None:
        self.state["progress"][path] = {"page": page, "mode": mode}
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

    def _scan(self) -> List[Tuple[str, bool]]:
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
        key = lambda item: item[0].lower()
        parent = [(PARENT, True)] if self.current_path != self.root_path else []
        return parent + sorted(dirs, key=key) + sorted(files, key=key)

    def _refresh(self, keep: Optional[str] = None) -> None:
        self.items = self._scan()
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
        return os.path.join(self.current_path, self.items[self.cursor_position][0])

    def selected_is_parent(self) -> bool:
        return bool(self.items) and self.items[self.cursor_position][0] == PARENT

    def enter(self, path: str) -> None:
        if os.path.isdir(path):
            self.current_path = os.path.abspath(path)
            self._refresh()

    def go_up(self) -> bool:
        if self.current_path == self.root_path:
            return False
        child = os.path.basename(self.current_path)
        self.current_path = os.path.dirname(self.current_path)
        self._refresh(keep=child)
        return True

    def get_current_dir(self) -> str:
        return self.current_path

    def is_root(self) -> bool:
        return self.current_path == self.root_path

    def get_items_for_display(self) -> List[Tuple[str, bool]]:
        return self.items

    def get_cursor_position(self) -> int:
        return self.cursor_position
