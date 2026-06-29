"""Per-run artifact bundle: screenshots, trace, final result JSON.

Each apply_one invocation gets its own directory under `artifacts/{job_id}/`.
`job_id` is a short stable hash of the job URL + timestamp so reruns don't
collide.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoapply.agent.storage import LocalFSStorage, Storage


def make_job_id(job_url: str, now: datetime | None = None) -> str:
    """Short, collision-resistant id for a single apply attempt.

    Combines a URL hash (stable across runs of the same URL) with a wall-clock
    timestamp (so reruns produce separate directories).
    """
    now = now or datetime.now(timezone.utc)
    url_hash = hashlib.sha1(job_url.encode("utf-8")).hexdigest()[:8]
    stamp = now.strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{url_hash}"


@dataclass
class ArtifactBundle:
    """Binds a `Storage` backend to a single job_id namespace."""

    job_id: str
    storage: Storage
    manifest: list[dict[str, Any]] = field(default_factory=list)

    def _key(self, name: str) -> str:
        return f"{self.job_id}/{name}"

    def save_screenshot(self, name: str, png_bytes: bytes) -> str:
        uri = self.storage.put(self._key(name), png_bytes)
        self.manifest.append({"kind": "screenshot", "name": name, "uri": uri})
        return uri

    def save_json(self, name: str, obj: Any) -> str:
        text = json.dumps(obj, indent=2, default=str)
        uri = self.storage.put_text(self._key(name), text)
        self.manifest.append({"kind": "json", "name": name, "uri": uri})
        return uri

    def save_text(self, name: str, text: str) -> str:
        uri = self.storage.put_text(self._key(name), text)
        self.manifest.append({"kind": "text", "name": name, "uri": uri})
        return uri

    def save_manifest(self) -> str:
        return self.storage.put_text(
            self._key("manifest.json"),
            json.dumps(self.manifest, indent=2, default=str),
        )


def new_bundle(artifacts_dir: Path, job_url: str) -> ArtifactBundle:
    """Creates a fresh ArtifactBundle backed by local filesystem."""
    storage = LocalFSStorage(root=artifacts_dir)
    return ArtifactBundle(job_id=make_job_id(job_url), storage=storage)
