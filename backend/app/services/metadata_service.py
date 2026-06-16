"""Lightweight sidecar store for per-data-source metadata.

We keep human-editable bits like a custom display name in a JSON file under
DATA_DIR, keyed by the data source id. The file is read on every list call,
so a rename is immediately visible to the next /datasources request.

Why a sidecar instead of renaming the file:
- The original filename is what we use as the upload id stem and what links
  to the per-source SQLite file in data/sqlite/{id}.db. Renaming the file
  would require a cascade update across multiple paths.
- A separate name avoids breaking the on-disk layout when names contain
  spaces, non-ASCII, or weird characters.
- Users don't expect "my renamed file" to also rename the SQLite db.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ..config import settings
from ..utils.logger import get_logger

logger = get_logger(__name__)

# Field name on each entry. We keep the schema flat so the JSON file is
# easy to inspect / edit by hand for debugging.
DISPLAY_NAME_FIELD = "display_name"

_lock = threading.Lock()


def _meta_path() -> Path:
    p = Path(settings.DATA_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p / "datasources.json"


def _load() -> dict[str, dict[str, Any]]:
    path = _meta_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning("datasources.json is not a dict; ignoring")
            return {}
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("failed to read %s: %s", path, e)
        return {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    path = _meta_path()
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def get_display_name(data_source_id: str) -> str | None:
    """Return the custom display name, or None if no override is set."""
    with _lock:
        data = _load()
    entry = data.get(data_source_id)
    if not isinstance(entry, dict):
        return None
    name = entry.get(DISPLAY_NAME_FIELD)
    if not isinstance(name, str) or not name.strip():
        return None
    return name.strip()


def set_display_name(data_source_id: str, name: str) -> str:
    """Set the display name. Returns the normalized value (trimmed, non-empty)."""
    normalized = name.strip()
    if not normalized:
        raise ValueError("display_name must be a non-empty string")
    with _lock:
        data = _load()
        entry = data.get(data_source_id)
        if not isinstance(entry, dict):
            entry = {}
        entry[DISPLAY_NAME_FIELD] = normalized
        data[data_source_id] = entry
        _save(data)
    return normalized


def delete_entry(data_source_id: str) -> None:
    """Forget any metadata for a data source. Idempotent."""
    with _lock:
        data = _load()
        if data_source_id in data:
            del data[data_source_id]
            _save(data)
