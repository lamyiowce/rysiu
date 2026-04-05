"""SQLite persistence layer — tracks which listings have been seen and analyzed."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import AnalysisResult, Listing

DB_PATH = Path(__file__).parent.parent / "data" / "rysiu.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen_listings (
                id          TEXT PRIMARY KEY,
                search_name TEXT NOT NULL,
                title       TEXT,
                url         TEXT,
                price       REAL,
                scraped_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS analyses (
                listing_id          TEXT PRIMARY KEY,
                search_name         TEXT NOT NULL,
                is_good_deal        INTEGER NOT NULL,
                deal_score          INTEGER NOT NULL,
                price_assessment    TEXT,
                technical_quality   TEXT,
                concerns            TEXT,
                key_positives       TEXT,
                recommendation      TEXT,
                estimated_market    TEXT,
                analyzed_at         TEXT NOT NULL,
                alerted             INTEGER NOT NULL DEFAULT 0
            );
            """
        )


def is_seen(listing_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM seen_listings WHERE id = ?", (listing_id,)
        ).fetchone()
        return row is not None


def mark_seen(listing: Listing, search_name: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO seen_listings (id, search_name, title, url, price, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                listing.id,
                search_name,
                listing.title,
                listing.url,
                listing.price,
                listing.scraped_at.isoformat(),
            ),
        )


def save_analysis(
    listing: Listing,
    search_name: str,
    result: AnalysisResult,
    alerted: bool = False,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO analyses
                (listing_id, search_name, is_good_deal, deal_score, price_assessment,
                 technical_quality, concerns, key_positives, recommendation,
                 estimated_market, analyzed_at, alerted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                listing.id,
                search_name,
                int(result.is_good_deal),
                result.deal_score,
                result.price_assessment,
                result.technical_quality,
                json.dumps(result.concerns),
                json.dumps(result.key_positives),
                result.recommendation,
                result.estimated_market_price,
                datetime.utcnow().isoformat(),
                int(alerted),
            ),
        )


def recent_alert_count(search_name: str, hours: int = 24) -> int:
    """How many alerts were sent for this search in the last N hours."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM analyses
            WHERE search_name = ?
              AND alerted = 1
              AND analyzed_at >= datetime('now', ?)
            """,
            (search_name, f"-{hours} hours"),
        ).fetchone()
        return row[0] if row else 0
