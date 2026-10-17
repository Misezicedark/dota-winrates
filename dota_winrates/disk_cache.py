"""JSON files on disk, each stored as {"fetched_at": <unix time>, "data": ...}."""

import json
import time
from pathlib import Path

MISSING = object()


class DiskCache:
    def __init__(self, directory: Path, clock=time.time):
        self.directory = Path(directory)
        self.clock = clock

    def read(self, name: str, max_age_seconds: float | None):
        """The cached data, or MISSING when absent, unreadable or older than max_age_seconds."""
        try:
            cached = json.loads((self.directory / name).read_text(encoding="utf-8"))
            fetched_at, data = cached["fetched_at"], cached["data"]
        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
            return MISSING
        if max_age_seconds is not None and self.clock() - fetched_at > max_age_seconds:
            return MISSING
        return data

    def write(self, name: str, data) -> None:
        path = self.directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_name(path.name + ".tmp")
        temporary_path.write_text(json.dumps({"fetched_at": self.clock(), "data": data}), encoding="utf-8")
        # Replacing in one step means an interrupted run never leaves a half-written cache file.
        temporary_path.replace(path)
