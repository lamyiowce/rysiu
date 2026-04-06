"""State persistence — tracks seen listing IDs in a plain JSON file.

Using JSON instead of SQLite so the state file is small, human-readable,
and diffs cleanly when committed back to the repository by GitHub Actions.

File: data/seen.json  (a JSON object mapping listing_id → ISO timestamp)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import AnalysisResult, Listing

STATE_PATH = Path(__file__).parent.parent / "data" / "seen.json"


def _load() -> dict[str, str]:
    """Return the seen-IDs mapping, or {} if the file doesn't exist yet."""
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save(state: dict[str, str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))


def init_db() -> None:
    """No-op — state file is created on first write."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)


def is_seen(listing_id: str) -> bool:
    return listing_id in _load()


def mark_seen(listing: Listing, search_name: str) -> None:
    state = _load()
    if listing.id not in state:
        state[listing.id] = datetime.utcnow().isoformat()
        _save(state)


def save_analysis(
    listing: Listing,
    search_name: str,
    result: AnalysisResult,
    alerted: bool = False,
) -> None:
    # Analyses are ephemeral in this setup — the seen-IDs file is all we
    # need to persist. Callers that previously relied on this are still safe.
    pass
