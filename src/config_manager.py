"""Manage searches in config.yaml programmatically."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

from .models import SearchConfig

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def _load_raw() -> dict:
    with CONFIG_PATH.open() as f:
        return yaml.safe_load(f) or {}


def _save_raw(config: dict) -> None:
    with CONFIG_PATH.open("w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def list_searches() -> list[SearchConfig]:
    """Return all configured searches."""
    config = _load_raw()
    searches = []
    for entry in config.get("searches", []):
        searches.append(
            SearchConfig(
                name=entry["name"],
                url=entry["url"],
                context=entry["context"],
                max_price=entry.get("max_price"),
                min_deal_score=entry.get("min_deal_score", 7),
            )
        )
    return searches


def add_search(
    name: str,
    url: str,
    context: str,
    max_price: Optional[float] = None,
    min_deal_score: int = 7,
) -> SearchConfig:
    """Add a new search to config.yaml and return the created SearchConfig."""
    config = _load_raw()
    if "searches" not in config:
        config["searches"] = []

    entry: dict = {
        "name": name,
        "url": url,
        "context": context,
    }
    if max_price is not None:
        entry["max_price"] = max_price
    entry["min_deal_score"] = min_deal_score

    config["searches"].append(entry)
    _save_raw(config)
    logger.info("Added search: %s", name)

    return SearchConfig(
        name=name, url=url, context=context,
        max_price=max_price, min_deal_score=min_deal_score,
    )


def remove_search(name: str) -> bool:
    """Remove a search by name. Returns True if found and removed."""
    config = _load_raw()
    searches = config.get("searches", [])
    original_len = len(searches)
    config["searches"] = [s for s in searches if s["name"].lower() != name.lower()]
    if len(config["searches"]) < original_len:
        _save_raw(config)
        logger.info("Removed search: %s", name)
        return True
    return False
