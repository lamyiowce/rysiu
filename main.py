#!/usr/bin/env python3
"""
Rysiu — Ricardo Deal Analyzer
==============================
Monitors saved searches on Ricardo.ch and uses Claude to alert you
only when a listing is genuinely worth buying.

Usage:
    python main.py              # Run once immediately, then on the configured schedule
    python main.py --once       # Run a single cycle and exit
    python main.py --test       # Dry-run scraper only (no Claude calls, no Telegram)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import schedule
import yaml
from dotenv import load_dotenv

from src import database as db
from src.models import SearchConfig
from src.scheduler import MonitoringPipeline

# ------------------------------------------------------------------ #
#  Logging                                                            #
# ------------------------------------------------------------------ #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rysiu")


# ------------------------------------------------------------------ #
#  Config loading                                                     #
# ------------------------------------------------------------------ #

def load_config(path: str = "config.yaml") -> dict:
    config_path = Path(path)
    if not config_path.exists():
        logger.error("Config file not found: %s", config_path.resolve())
        sys.exit(1)
    with config_path.open() as f:
        return yaml.safe_load(f)


def build_searches(config: dict) -> list[SearchConfig]:
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
    if not searches:
        logger.error("No searches defined in config.yaml")
        sys.exit(1)
    return searches


def check_env() -> None:
    missing = [k for k in ("OPENAI_API_KEY",) if not os.environ.get(k)]
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)

    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
        logger.warning(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — "
            "alerts will only be logged, not sent."
        )


# ------------------------------------------------------------------ #
#  Test mode                                                          #
# ------------------------------------------------------------------ #

def run_test(searches: list[SearchConfig], config: dict) -> None:
    """Dry-run: scrape only, no Claude, no Telegram."""
    from src.scraper import RicardoScraper

    monitoring = config.get("monitoring", {})
    scraper = RicardoScraper(request_delay=monitoring.get("request_delay_seconds", 2))
    max_l = monitoring.get("max_listings_per_search", 10)

    for search in searches:
        print(f"\n{'='*60}")
        print(f"Search: {search.name}")
        print(f"URL:    {search.url}")
        print(f"{'='*60}")

        listings = scraper.fetch_listings(search.url, max_listings=min(max_l, 5))
        if not listings:
            print("  ⚠️  No listings found — the scraper may need adjustment.")
            print("     Check if the URL works in a browser and if __NEXT_DATA__ is present.")
        else:
            for i, l in enumerate(listings, 1):
                print(f"\n  [{i}] {l.title}")
                print(f"       Price:     {l.format_price()}")
                print(f"       Condition: {l.condition or 'unknown'}")
                print(f"       URL:       {l.url}")


# ------------------------------------------------------------------ #
#  Main                                                               #
# ------------------------------------------------------------------ #

def main() -> None:
    parser = argparse.ArgumentParser(description="Ricardo Deal Analyzer")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--test", action="store_true", help="Scraper dry-run, no AI calls")
    parser.add_argument("--bot", action="store_true", help="Run Telegram bot for managing searches via chat")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    args = parser.parse_args()

    load_dotenv()

    config = load_config(args.config)
    searches = build_searches(config)

    if args.test:
        run_test(searches, config)
        return

    check_env()

    if args.bot:
        model = config.get("openai", {}).get("model", "gpt-4o")
        from src.bot import TelegramBot
        bot = TelegramBot(model=model)
        bot.run()
        return
    db.init_db()

    monitoring = config.get("monitoring", {})
    interval = monitoring.get("interval_minutes", 30)
    max_listings = monitoring.get("max_listings_per_search", 30)
    delay = monitoring.get("request_delay_seconds", 2.0)

    model = config.get("openai", {}).get("model", "gpt-4o")

    pipeline = MonitoringPipeline(
        searches=searches,
        model=model,
        max_listings_per_search=max_listings,
        request_delay=delay,
    )

    if args.once:
        pipeline.run_once()
        return

    # Scheduled mode
    logger.info(
        "Starting Rysiu — monitoring %d search(es) every %d minutes.",
        len(searches),
        interval,
    )

    # Run immediately on startup
    pipeline.run_once()

    schedule.every(interval).minutes.do(pipeline.run_once)
    logger.info("Next run in %d minutes. Press Ctrl+C to stop.", interval)

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
