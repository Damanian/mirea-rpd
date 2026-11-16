"""Session storage for MIREA RPD."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_SESSION_DIR = Path.home() / ".mirea"
DEFAULT_SESSION_FILE = DEFAULT_SESSION_DIR / "rpd_session.json"


class SessionStore:
    """Manages saving and loading session cookies."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or os.environ.get("MIREA_RPD_SESSION_FILE") or DEFAULT_SESSION_FILE)

    def save(self, cookies: dict[str, str], user_info: dict[str, Any] | None = None) -> None:
        """Save cookies and user info to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "cookies": cookies,
            "user_info": user_info or {},
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self) -> dict[str, Any] | None:
        """Load session data if available."""
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def clear(self) -> None:
        """Clear stored session."""
        if self.path.exists():
            self.path.unlink()
