import os
import json
from typing import Optional, List, Tuple


class FileBrowser:
    """File browser for navigating and selecting files on an e-reader device."""

    LOCATION_FILE = ".ereader_location"
    SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}

    def __init__(self, root_path: str):
        """Initialize the file browser with a root directory path."""
        self.root_path = os.path.abspath(root_path)
        self.current_path = self.root_path
        self.items: List[Tuple[str, bool]] = []  # List of (name, is_dir) tuples
        self.cursor_position = 0
        self.selected_index: Optional[int] = None
        self.saved_location: Optional[dict] = None

        # Ensure root directory exists
        if not os.path.exists(self.root_path):
            os.makedirs(self.root_path)

        # Load saved location
        self._load_location()

        # Initial refresh
        self._refresh()

    def _load_location(self) -> None:
        """Load saved location from JSON file."""
        self.saved_location = None
        location_file_path = os.path.join(self.root_path, self.LOCATION_FILE)
        
        if os.path.exists(location_file_path):
            try:
                with open(location_file_path, "r", encoding="utf-8") as f:
                    self.saved_location = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.saved_location = None

    def save_location(self, file_path: str, page: int) -> None:
        """Save the current location to a JSON file.
        
        Args:
            file_path: The selected file path
            page: The page number when reading
        """
        location_file_path = os.path.join(self.root_path, self.LOCATION_FILE)
        
        # Extract selected_id (the file/folder name) from path
        selected_id = os.path.basename(file_path) if file_path else None
        
        location_data = {
            "path": file_path,
            "page": page,
            "selected_id": selected_id
        }
        
        try:
            with open(location_file_path, "w", encoding="utf-8") as f:
                json.dump(location_data, f, indent=2)
        except IOError:
            pass  # Silently fail if we can't write

    def get_saved_location(self) -> Optional[dict]:
        """Return the saved location."""
        return self.saved_location

    def go_to_saved_location(self) -> bool:
        """Restore position - if a file was selected when app closed, select it again.
        
        Returns:
            True if successfully restored to saved location, False otherwise
        """
        if not self.saved_location:
            return False
        
        saved_path = self.saved_location.get("path")
        selected_id = self.saved_location.get("selected_id")
        
        if not saved_path or not selected_id:
            return False
        
        # Navigate to the directory containing the saved file
        saved_dir = os.path.dirname(saved_path)
        saved_filename = os.path.basename(saved_path)
        
        # If saved path is the current directory, refresh and find item
        if saved_dir == self.current_path:
            self._refresh()
            # Find and select the item
            for i, (name, is_dir) in enumerate(self.items):
                if name == selected_id:
                    self.cursor_position = i
                    self.selected_index = i
                    return True
        elif os.path.exists(saved_dir):
            # Navigate to the saved directory
            self.current_path = saved_dir
            self._refresh()
            # Find and select the item
            for i, (name, is_dir) in enumerate(self.items):
                if name == selected_id:
                    self.cursor_position = i
                    self.selected_index = i
                    return True
        
        return False

    def _refresh(self) -> None:
        """Refresh the current directory contents."""
        self.items = []
        self.cursor_position = 0
        self.selected_index = None

        try:
            entries = os.listdir(self.current_path)
        except OSError:
            entries = []

        for entry in sorted(entries):
            # Skip hidden files
            if entry.startswith("."):
                continue

            full_path = os.path.join(self.current_path, entry)
            is_dir = os.path.isdir(full_path)

            if is_dir or self._is_supported_file(entry):
                self.items.append((entry, is_dir))

    def _is_supported_file(self, filename: str) -> bool:
        """Check if a file is a supported document type."""
        _, ext = os.path.splitext(filename.lower())
        return ext in self.SUPPORTED_EXTENSIONS

    def move_up(self) -> None:
        """Move the cursor up."""
        if self.items:
            self.cursor_position = (self.cursor_position - 1) % len(self.items)

    def move_down(self) -> None:
        """Move the cursor down."""
        if self.items:
            self.cursor_position = (self.cursor_position + 1) % len(self.items)

    def get_selected(self) -> Optional[str]:
        """Get the currently selected item name."""
        if self.selected_index is not None and self.selected_index < len(self.items):
            return self.items[self.selected_index][0]
        return None

    def get_selected_path(self) -> Optional[str]:
        """Get the full path of the currently selected item."""
        selected = self.get_selected()
        if selected:
            return os.path.join(self.current_path, selected)
        return None

    def select(self) -> None:
        """Select the item at the current cursor position."""
        if self.items:
            self.selected_index = self.cursor_position

    def go_up(self) -> None:
        """Navigate to the parent directory."""
        if self.current_path != self.root_path:
            parent = os.path.dirname(self.current_path)
            if os.path.exists(parent):
                self.current_path = parent
                self._refresh()

    def get_current_dir(self) -> str:
        """Get the current directory path."""
        return self.current_path

    def get_items_for_display(self) -> List[Tuple[str, bool]]:
        """Get the list of items for display."""
        return self.items

    def get_cursor_position(self) -> int:
        """Get the current cursor position."""
        return self.cursor_position
