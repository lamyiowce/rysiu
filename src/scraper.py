"""Ricardo.ch scraper.

Ricardo uses Next.js, so the initial page data lives in a
<script id="__NEXT_DATA__"> JSON blob. We extract listings from that blob
first and fall back to BeautifulSoup HTML parsing if needed.
"""

from __future__ import annotations

import json
import re
import time
import random
import logging
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlencode, parse_qs

import requests
from bs4 import BeautifulSoup

from .models import Listing

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ricardo.ch"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "de-CH,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Cache-Control": "max-age=0",
}


class RicardoScraper:
    def __init__(self, request_delay: float = 2.0):
        self.delay = request_delay
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._warmed_up = False

    # ------------------------------------------------------------------ #
    #  Public interface                                                    #
    # ------------------------------------------------------------------ #

    def fetch_listings(self, search_url: str, max_listings: int = 30) -> list[Listing]:
        """Fetch up to *max_listings* listings from a Ricardo search URL."""
        listings: list[Listing] = []
        page = 1

        while len(listings) < max_listings:
            url = self._page_url(search_url, page)
            logger.debug("Fetching %s", url)

            html = self._get_html(url)
            if html is None:
                break

            page_listings = self._parse_page(html, search_url)
            if not page_listings:
                break

            listings.extend(page_listings)

            if len(page_listings) < 10:
                # Fewer results than expected — probably the last page
                break

            page += 1
            time.sleep(self.delay + random.uniform(0, 1))

        return listings[:max_listings]

    def fetch_listing_detail(self, listing: Listing) -> Listing:
        """Enrich a listing with the full description from its detail page."""
        if not listing.url:
            return listing

        html = self._get_html(listing.url)
        if html is None:
            return listing

        description = self._extract_description(html)
        if description:
            listing.description = description

        return listing

    # ------------------------------------------------------------------ #
    #  Parsing                                                             #
    # ------------------------------------------------------------------ #

    def _parse_page(self, html: str, base_url: str) -> list[Listing]:
        # Strategy 1: __NEXT_DATA__ JSON blob
        listings = self._parse_next_data(html)
        if listings:
            return listings

        # Strategy 2: BeautifulSoup HTML
        listings = self._parse_html_fallback(html, base_url)
        return listings

    def _parse_next_data(self, html: str) -> list[Listing]:
        soup = BeautifulSoup(html, "lxml")
        tag = soup.find("script", {"id": "__NEXT_DATA__"})
        if not tag or not tag.string:
            return []

        try:
            data = json.loads(tag.string)
        except json.JSONDecodeError:
            return []

        raw_listings = self._dig_for_listings(data)
        return [self._normalise(r) for r in raw_listings if r]

    def _dig_for_listings(self, node: Any, depth: int = 0) -> list[dict]:
        """Recursively find arrays of listing-like objects in Next.js data."""
        if depth > 12:
            return []

        results: list[dict] = []

        if isinstance(node, dict):
            if self._looks_like_listing(node):
                return [node]
            for v in node.values():
                results.extend(self._dig_for_listings(v, depth + 1))

        elif isinstance(node, list) and node:
            # Check if this list itself is a list of listings
            candidates = [x for x in node if isinstance(x, dict) and self._looks_like_listing(x)]
            if len(candidates) >= 2:
                # Looks like a proper results list
                return candidates
            # Otherwise recurse into each element
            for item in node:
                results.extend(self._dig_for_listings(item, depth + 1))

        return results

    def _looks_like_listing(self, d: dict) -> bool:
        has_id = any(k in d for k in ("id", "articleId", "itemId", "article_id"))
        has_title = any(k in d for k in ("title", "name", "articleTitle"))
        has_price = any(
            k in d
            for k in (
                "price", "buyNowPrice", "startingBid", "currentBid",
                "auctionPrice", "fixedPrice",
            )
        )
        return has_id and has_title

    def _normalise(self, raw: dict) -> Optional[Listing]:
        """Convert a raw dict (any shape) into a Listing."""
        try:
            listing_id = str(
                raw.get("id")
                or raw.get("articleId")
                or raw.get("itemId")
                or raw.get("article_id")
                or ""
            )
            if not listing_id:
                return None

            title = str(
                raw.get("title")
                or raw.get("name")
                or raw.get("articleTitle")
                or ""
            )

            price = self._extract_price(raw)
            condition = self._extract_condition(raw)
            url = self._extract_url(raw, listing_id, title)
            image_url = self._extract_image(raw)
            location = str(raw.get("location") or raw.get("city") or "")
            listing_type = "auction" if raw.get("auctionPrice") or raw.get("startingBid") else "buy_now"

            return Listing(
                id=listing_id,
                title=title,
                url=url,
                price=price,
                condition=condition,
                location=location or None,
                listing_type=listing_type,
                image_url=image_url,
            )
        except Exception as e:
            logger.debug("Failed to normalise listing: %s — %s", raw, e)
            return None

    def _extract_price(self, raw: dict) -> Optional[float]:
        for key in ("price", "buyNowPrice", "fixedPrice", "currentBid", "startingBid"):
            val = raw.get(key)
            if val is None:
                continue
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, dict):
                for sub in ("amount", "value", "price"):
                    if sub in val:
                        try:
                            return float(val[sub])
                        except (TypeError, ValueError):
                            pass
            try:
                return float(str(val).replace("'", "").replace(",", ".").replace("CHF", "").strip())
            except ValueError:
                pass
        return None

    def _extract_condition(self, raw: dict) -> Optional[str]:
        for key in ("condition", "itemCondition", "conditionLabel", "conditionText"):
            val = raw.get(key)
            if val:
                return str(val)
        return None

    def _extract_url(self, raw: dict, listing_id: str, title: str) -> str:
        for key in ("url", "href", "link", "articleUrl", "itemUrl"):
            val = raw.get(key)
            if val and isinstance(val, str):
                return val if val.startswith("http") else urljoin(BASE_URL, val)

        # Construct URL from title + id (Ricardo's standard format)
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        return f"{BASE_URL}/de/a/{slug}-{listing_id}/"

    def _extract_image(self, raw: dict) -> Optional[str]:
        for key in ("image", "thumbnail", "imageUrl", "thumbnailUrl", "pictureUrl"):
            val = raw.get(key)
            if isinstance(val, str) and val:
                return val
            if isinstance(val, dict):
                for sub in ("url", "src", "href"):
                    if sub in val:
                        return str(val[sub])
        return None

    def _extract_description(self, html: str) -> Optional[str]:
        soup = BeautifulSoup(html, "lxml")

        # Try __NEXT_DATA__ first
        tag = soup.find("script", {"id": "__NEXT_DATA__"})
        if tag and tag.string:
            try:
                data = json.loads(tag.string)
                desc = self._dig_for_field(data, ("description", "body", "articleBody", "text"))
                if desc and len(desc) > 30:
                    return str(desc)
            except json.JSONDecodeError:
                pass

        # Fallback: look for a description element in the HTML
        for selector in (
            "[data-testid='article-description']",
            ".description",
            "#description",
            "article .body",
            "[class*='description']",
        ):
            el = soup.select_one(selector)
            if el:
                return el.get_text(separator="\n", strip=True)

        return None

    def _dig_for_field(self, node: Any, keys: tuple, depth: int = 0) -> Optional[str]:
        if depth > 10:
            return None
        if isinstance(node, dict):
            for k in keys:
                if k in node and isinstance(node[k], str) and len(node[k]) > 50:
                    return node[k]
            for v in node.values():
                result = self._dig_for_field(v, keys, depth + 1)
                if result:
                    return result
        elif isinstance(node, list):
            for item in node:
                result = self._dig_for_field(item, keys, depth + 1)
                if result:
                    return result
        return None

    def _parse_html_fallback(self, html: str, base_url: str) -> list[Listing]:
        """Last-resort HTML parsing when __NEXT_DATA__ fails."""
        soup = BeautifulSoup(html, "lxml")
        listings: list[Listing] = []

        # Try to find article cards by common patterns
        cards = (
            soup.select("article[data-article-id]")
            or soup.select("[data-testid*='article']")
            or soup.select("li[class*='article']")
            or soup.select("div[class*='article-card']")
            or soup.select("a[href*='/de/a/']")
        )

        for card in cards[:50]:
            listing = self._card_to_listing(card, base_url)
            if listing:
                listings.append(listing)

        return listings

    def _card_to_listing(self, el: Any, base_url: str) -> Optional[Listing]:
        try:
            # Try to extract id from the element or its link
            listing_id = (
                el.get("data-article-id")
                or el.get("data-id")
                or el.get("data-item-id")
            )

            link = el if el.name == "a" else el.find("a", href=True)
            href = ""
            if link:
                href = link.get("href", "")
                if href and not href.startswith("http"):
                    href = urljoin(base_url, href)
                if not listing_id:
                    # Extract id from URL like /de/a/some-title-12345678/
                    m = re.search(r"-(\d{6,})/?$", href)
                    if m:
                        listing_id = m.group(1)

            if not listing_id:
                return None

            title_el = el.find(["h2", "h3", "h4"]) or el.find(class_=re.compile(r"title"))
            title = title_el.get_text(strip=True) if title_el else ""

            if not title and link:
                title = link.get_text(strip=True)

            price_el = el.find(class_=re.compile(r"price|amount|cost", re.I))
            price = None
            if price_el:
                price_text = price_el.get_text(strip=True)
                m = re.search(r"[\d',.]+", price_text)
                if m:
                    try:
                        price = float(m.group().replace("'", "").replace(",", "."))
                    except ValueError:
                        pass

            return Listing(
                id=listing_id,
                title=title,
                url=href,
                price=price,
            )
        except Exception as e:
            logger.debug("card_to_listing failed: %s", e)
            return None

    # ------------------------------------------------------------------ #
    #  HTTP helpers                                                        #
    # ------------------------------------------------------------------ #

    def _warm_up_session(self) -> None:
        """Visit the homepage to collect cookies before making search requests."""
        if self._warmed_up:
            return
        try:
            logger.debug("Warming up session (visiting homepage)…")
            resp = self.session.get(BASE_URL + "/de/", timeout=30)
            logger.debug("Homepage status: %s, cookies: %s", resp.status_code, list(self.session.cookies.keys()))
            # After visiting the homepage, subsequent navigations are same-origin
            self.session.headers.update({
                "Sec-Fetch-Site": "same-origin",
                "Referer": BASE_URL + "/de/",
            })
            time.sleep(1 + random.uniform(0, 1))
        except requests.RequestException as e:
            logger.debug("Homepage warm-up failed: %s", e)
        self._warmed_up = True

    def _get_html(self, url: str) -> Optional[str]:
        self._warm_up_session()
        for attempt in range(3):
            try:
                resp = self.session.get(url, timeout=30)
                resp.raise_for_status()
                return resp.text
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code in (403, 429):
                    wait = 10 * (attempt + 1)
                    body = e.response.text[:500] if e.response.text else ""
                    if "cloudflare" in body.lower() or "challenge" in body.lower():
                        logger.warning(
                            "Blocked by bot protection (Cloudflare challenge). "
                            "Consider running with a residential IP or adding a delay."
                        )
                    logger.warning("Rate-limited (%s). Waiting %ds…", e.response.status_code, wait)
                    time.sleep(wait)
                else:
                    logger.warning("HTTP error fetching %s: %s", url, e)
                    break
            except requests.RequestException as e:
                logger.warning("Request failed for %s (attempt %d): %s", url, attempt + 1, e)
                if attempt < 2:
                    time.sleep(2 ** attempt)
        return None

    @staticmethod
    def _page_url(base: str, page: int) -> str:
        if page == 1:
            return base
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}page={page}"
