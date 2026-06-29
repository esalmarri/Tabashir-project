"""Artifact storage backends.

Phase 1 ships only `LocalFSStorage`. Phase 5 adds S3 behind the same
interface. Agent code should depend on the `Storage` protocol only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> str:
        """Store bytes under `key`. Returns a URI/path the caller can reference later."""
        ...

    def put_text(self, key: str, text: str) -> str:
        ...


@dataclass
class LocalFSStorage:
    """Writes everything under `root`. `key` is a relative path-like string.

    Returns absolute filesystem paths as the URI (prefixed with `file://`).
    """

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        # Normalize the key so it can't escape `self.root`.
        rel = Path(key)
        if rel.is_absolute():
            raise ValueError(f"Artifact key must be relative, got: {key}")
        full = (self.root / rel).resolve()
        if not str(full).startswith(str(self.root)):
            raise ValueError(f"Artifact key escapes storage root: {key}")
        full.parent.mkdir(parents=True, exist_ok=True)
        return full

    def put(self, key: str, data: bytes) -> str:
        p = self._resolve(key)
        p.write_bytes(data)
        return f"file://{p}"

    def put_text(self, key: str, text: str) -> str:
        p = self._resolve(key)
        p.write_text(text, encoding="utf-8")
        return f"file://{p}"
