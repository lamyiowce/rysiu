"""Main monitoring pipeline — ties scraper, analyzer, and notifier together."""

from __future__ import annotations

import logging
import os
from typing import Optional

from . import database as db
from .analyzer import DealAnalyzer
from .models import Listing, SearchConfig
from .notifier import TelegramNotifier
from .scraper import RicardoScraper

logger = logging.getLogger(__name__)


class MonitoringPipeline:
    def __init__(
        self,
        searches: list[SearchConfig],
        model: str = "claude-opus-4-6",
        max_listings_per_search: int = 30,
        request_delay: float = 2.0,
    ):
        self.searches = searches
        self.max_listings = max_listings_per_search
        self.scraper = RicardoScraper(request_delay=request_delay)
        self.analyzer = DealAnalyzer(model=model)
        self.notifier = TelegramNotifier()

    def run_once(self) -> None:
        """Run one full monitoring cycle across all configured searches."""
        logger.info("Starting monitoring cycle (%d searches)", len(self.searches))

        for search in self.searches:
            try:
                self._process_search(search)
            except Exception as e:
                logger.error("Error processing search '%s': %s", search.name, e, exc_info=True)

        logger.info("Monitoring cycle complete.")

    # ------------------------------------------------------------------ #

    def _process_search(self, search: SearchConfig) -> None:
        logger.info("[%s] Fetching listings from %s", search.name, search.url)

        listings = self.scraper.fetch_listings(
            search.url, max_listings=self.max_listings
        )
        logger.info("[%s] Found %d listings", search.name, len(listings))

        new_listings = [l for l in listings if not db.is_seen(l.id)]
        logger.info("[%s] %d new (unseen) listings", search.name, len(new_listings))

        for listing in new_listings:
            db.mark_seen(listing, search.name)

            if not self._passes_prefilter(listing, search):
                logger.debug(
                    "[%s] Skipping %s (pre-filter: price %.0f > %.0f)",
                    search.name,
                    listing.id,
                    listing.price or 0,
                    search.max_price or 0,
                )
                continue

            # Enrich with full description from the detail page
            listing = self.scraper.fetch_listing_detail(listing)

            logger.info(
                "[%s] Analyzing: %s — %s",
                search.name,
                listing.title[:60],
                listing.format_price(),
            )

            result = self.analyzer.analyze(listing, search)
            if result is None:
                continue

            logger.info(
                "[%s] Score %d/10 (%s) — %s",
                search.name,
                result.deal_score,
                result.price_assessment,
                listing.title[:50],
            )

            should_alert = (
                result.is_good_deal
                and result.deal_score >= search.min_deal_score
            )

            db.save_analysis(listing, search.name, result, alerted=should_alert)

            if should_alert:
                logger.info(
                    "[%s] 🔔 ALERTING — score %d: %s",
                    search.name,
                    result.deal_score,
                    listing.title,
                )
                self.notifier.send_deal_alert(listing, result, search)
            else:
                logger.debug(
                    "[%s] No alert (score %d < threshold %d)",
                    search.name,
                    result.deal_score,
                    search.min_deal_score,
                )

    @staticmethod
    def _passes_prefilter(listing: Listing, search: SearchConfig) -> bool:
        """Quick checks before spending an API call on analysis."""
        if search.max_price is not None and listing.price is not None:
            if listing.price > search.max_price:
                return False
        return True
