"""Telegram bot — lets you message Rysiu to add/remove/list monitored searches.

Usage examples (send these as Telegram messages):
    "I want a Nintendo Switch under 200 CHF"
    "Monitor MacBook Pro M3 budget 1500-2000 good condition"
    "/list"          — show all active searches
    "/remove iPhone 15 Pro"  — remove a search by name
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import requests
from openai import OpenAI
from pydantic import BaseModel, Field

from . import config_manager as cm

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"

# ------------------------------------------------------------------ #
#  Ricardo category tree                                               #
# ------------------------------------------------------------------ #

_CATEGORIES_PATH = Path(__file__).parent.parent / "categories.json"


def _load_categories() -> list[dict]:
    """Load the Ricardo category tree from categories.json.

    Returns a flat list of dicts with keys: id, slug, name, parent_name.
    """
    if not _CATEGORIES_PATH.exists():
        logger.warning("categories.json not found — category matching disabled")
        return []

    with _CATEGORIES_PATH.open() as f:
        raw = json.load(f)

    flat: list[dict] = []
    for cat in raw:
        slug = cat["to"].rstrip("/").split("/")[-1]
        flat.append({"id": cat["id"], "slug": slug, "name": cat["name"], "parent_name": None})
        for sub in cat.get("subCategories", []):
            sub_slug = sub["to"].rstrip("/").split("/")[-1]
            flat.append({"id": sub["id"], "slug": sub_slug, "name": sub["name"], "parent_name": cat["name"]})
    return flat


def _build_top_level_list(categories: list[dict]) -> str:
    """Build a compact text list of top-level categories for the LLM prompt."""
    lines = []
    for c in categories:
        if c["parent_name"] is None:
            lines.append(f"- {c['name']} (id={c['id']})")
    return "\n".join(lines)


def _find_best_category(categories: list[dict], category_id: Optional[int], search_query: Optional[str]) -> Optional[dict]:
    """Find the best matching category.

    If category_id is given, return the matching category.
    If search_query is also given, try to find a subcategory under category_id
    whose name matches the query (simple substring match).
    """
    if not category_id or not categories:
        return None

    # Find the top-level category
    parent = None
    children: list[dict] = []
    for c in categories:
        if c["id"] == category_id:
            parent = c
        elif c["parent_name"] is not None:
            # Check if this subcategory belongs to category_id
            pass

    if parent is None:
        return None

    # Gather subcategories under this parent
    for c in categories:
        if c["parent_name"] == parent["name"]:
            children.append(c)

    # If we have a search query, try to match a subcategory
    if search_query and children:
        query_lower = search_query.lower()
        query_words = query_lower.split()
        best_match = None
        best_score = 0
        for child in children:
            name_lower = child["name"].lower()
            score = sum(1 for w in query_words if w in name_lower)
            if score > best_score:
                best_score = score
                best_match = child
        if best_match:
            return best_match

    return parent


# ------------------------------------------------------------------ #
#  Structured output for parsing user messages                        #
# ------------------------------------------------------------------ #

class ParsedSearch(BaseModel):
    """What the user wants to monitor, extracted from their message."""

    is_valid: bool = Field(
        description=(
            "True if the message is a genuine request to monitor a product or "
            "category on Ricardo.ch. False if the message is gibberish, a greeting, "
            "a question, spam, or anything that doesn't express intent to buy/monitor "
            "a specific product or category of products."
        )
    )
    rejection_reason: Optional[str] = Field(
        default=None,
        description=(
            "If is_valid is false, a brief friendly explanation of why the message "
            "can't be turned into a search (e.g. 'This looks like a greeting, not a "
            "product search'). Null when is_valid is true."
        ),
    )
    name: str = Field(
        default="",
        description="Short, descriptive name for this search (e.g. 'Nintendo Switch OLED')"
    )
    search_query: Optional[str] = Field(
        default=None,
        description=(
            "Short search query (1-3 keywords) to use on Ricardo.ch, "
            "e.g. 'macbook pro m3' or 'nintendo switch oled'. "
            "Keep it short — too many keywords will return no results. "
            "Set to null if the user provides a direct Ricardo.ch URL in their message."
        ),
    )
    category_id: Optional[int] = Field(
        default=None,
        description=(
            "The Ricardo.ch top-level category ID that best matches this search. "
            "Pick the single most relevant category from the list provided. "
            "Set to null only if no category is a reasonable fit."
        ),
    )
    context: str = Field(
        default="",
        description=(
            "Natural language context for the deal analyzer, describing what the "
            "user wants: condition, specs, budget, dealbreakers, etc. "
            "Written in first person as if the buyer is speaking."
        )
    )
    max_price: Optional[float] = Field(
        default=None,
        description="Maximum price in CHF to pre-filter listings, or null if not specified",
    )
    min_deal_score: int = Field(
        default=7,
        description="Minimum deal score (1-10) to trigger an alert. Default 7.",
    )


_PARSE_SYSTEM_PROMPT = """\
You are a helpful assistant that parses user requests into structured search \
configurations for a Swiss second-hand marketplace monitor (Ricardo.ch).

FIRST: decide if the message is a valid product search request. Set is_valid \
to false if it's gibberish, a greeting, a question not about buying, spam, or \
anything that doesn't express intent to buy or monitor a specific product or \
category. When is_valid is false, set rejection_reason and leave other fields \
at their defaults — do NOT invent a search.

If the message IS a valid search request, extract:
- A short name for the search
- A search query: keep it SHORT (1-3 keywords max). Ricardo.ch treats multiple \
  words as AND — too many keywords returns zero results. Pick only the most \
  essential keyword(s) that capture what the user wants. For example: \
  "plattenspieler" not "plattenspieler thorens technics dual pioneer". \
  Put brand/model preferences in the context instead.
- If the user provides a direct Ricardo.ch URL, set search_query to null — \
  the URL will be used as-is.
- A category_id: pick the best matching top-level category from this list:
{categories}
- A detailed buyer context paragraph (expand on what the user said — include \
  reasonable assumptions about condition, specs, and dealbreakers). Include \
  specific brands, models, or features the user mentioned here so the deal \
  analyzer can evaluate them.
- A max_price if the user mentions a budget or price ceiling
- A min_deal_score (default 7, lower if the user seems flexible, higher if picky)

If the user's message is vague, make reasonable assumptions and fill in the \
context with sensible defaults for a Swiss buyer. Prices are always in CHF.
"""


class TelegramBot:
    """Long-polling Telegram bot for managing monitored searches."""

    def __init__(self, model: str = "gpt-4o"):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.openai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.model = model
        self._offset: int = 0
        self._categories = _load_categories()
        self._category_prompt = _build_top_level_list(self._categories)

        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required for bot mode")

    # ------------------------------------------------------------------ #
    #  Main loop                                                          #
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        """Start the long-polling loop. Blocks forever."""
        logger.info("Telegram bot started. Waiting for messages…")
        while True:
            try:
                updates = self._get_updates()
                for update in updates:
                    self._handle_update(update)
            except KeyboardInterrupt:
                logger.info("Bot stopped.")
                break
            except Exception as e:
                logger.error("Bot error: %s", e, exc_info=True)
                time.sleep(5)

    # ------------------------------------------------------------------ #
    #  Update handling                                                    #
    # ------------------------------------------------------------------ #

    def _handle_update(self, update: dict) -> None:
        message = update.get("message")
        if not message:
            return

        chat_id = str(message["chat"]["id"])
        text = (message.get("text") or "").strip()

        # Only respond to the configured chat (security)
        if self.chat_id and chat_id != self.chat_id:
            logger.warning("Ignoring message from unknown chat: %s", chat_id)
            return

        if not text:
            return

        logger.info("Received message: %s", text[:100])

        if text.lower() == "/list":
            self._handle_list(chat_id)
        elif text.lower().startswith("/remove"):
            name = text[len("/remove"):].strip()
            self._handle_remove(chat_id, name)
        elif text.lower() == "/help":
            self._handle_help(chat_id)
        else:
            self._handle_add(chat_id, text)

    def _handle_list(self, chat_id: str) -> None:
        searches = cm.list_searches()
        if not searches:
            self._send(chat_id, "No active searches. Send me a message to add one!")
            return

        lines = ["📋 *Active searches:*\n"]
        for i, s in enumerate(searches, 1):
            price_info = f" (max CHF {s.max_price:,.0f})" if s.max_price else ""
            lines.append(f"{i}\\. *{self._esc(s.name)}*{self._esc(price_info)}")
            # Show first line of context
            first_line = s.context.strip().split("\n")[0][:80]
            lines.append(f"   _{self._esc(first_line)}_")
        lines.append(f"\nTo remove: `/remove SearchName`")
        self._send(chat_id, "\n".join(lines), parse_mode="MarkdownV2")

    def _handle_remove(self, chat_id: str, name: str) -> None:
        if not name:
            self._send(chat_id, "Usage: /remove SearchName")
            return
        if cm.remove_search(name):
            self._send(chat_id, f"✅ Removed \"{name}\" from monitored searches.")
        else:
            self._send(chat_id, f"❌ No search found with name \"{name}\".")

    def _handle_help(self, chat_id: str) -> None:
        self._send(
            chat_id,
            "🤖 *Rysiu — Ricardo Deal Monitor*\n\n"
            "Just tell me what you want to buy and I'll monitor Ricardo\\.ch for deals\\.\n\n"
            "*Examples:*\n"
            "• _I want a MacBook Air M2 under 900 CHF_\n"
            "• _Looking for a PS5 in good condition, budget 350_\n"
            "• _Monitor Sony WH\\-1000XM5 headphones_\n"
            "• _https://www\\.ricardo\\.ch/de/c/hifi\\-audio\\-12345 — turntables under 300_\n\n"
            "*Commands:*\n"
            "/list — show all monitored searches\n"
            "/remove Name — stop monitoring a search\n"
            "/help — show this message",
            parse_mode="MarkdownV2",
        )

    @staticmethod
    def _extract_ricardo_url(text: str) -> Optional[str]:
        """Extract a ricardo.ch URL from the user's message, if present."""
        match = re.search(r'https?://(?:www\.)?ricardo\.ch/\S+', text)
        return match.group(0).rstrip(".,;)") if match else None

    def _handle_add(self, chat_id: str, text: str) -> None:
        self._send(chat_id, "🔍 Parsing your request…")

        parsed = self._parse_message(text)
        if parsed is None:
            self._send(chat_id, "❌ Sorry, I couldn't understand that. Try something like:\n\"I want a MacBook Pro under 1500 CHF\"")
            return

        # Reject invalid/nonsensical messages
        if not parsed.is_valid:
            reason = parsed.rejection_reason or "That doesn't look like a product search."
            self._send(chat_id, f"🤔 {reason}\n\nTry something like:\n\"I want a MacBook Pro under 1500 CHF\"")
            return

        # Use URL from message if present, otherwise build from search_query
        explicit_url = self._extract_ricardo_url(text)
        if explicit_url:
            url = explicit_url
        elif parsed.search_query:
            # Try to find the best category for a scoped search URL
            category = _find_best_category(self._categories, parsed.category_id, parsed.search_query)
            if category:
                url = f"https://www.ricardo.ch/de/c/{category['slug']}/{quote_plus(parsed.search_query)}/"
            else:
                url = f"https://www.ricardo.ch/de/s/{quote_plus(parsed.search_query)}/"
        else:
            self._send(chat_id, "❌ I need either a Ricardo.ch URL or a search query. Try again with one of those.")
            return

        search = cm.add_search(
            name=parsed.name,
            url=url,
            context=parsed.context,
            max_price=parsed.max_price,
            min_deal_score=parsed.min_deal_score,
        )

        # Confirm to the user
        category = _find_best_category(self._categories, parsed.category_id, parsed.search_query)
        cat_info = f"📂 Category: {category['name']}" if category else ""

        lines = [
            f"✅ Now monitoring: *{self._esc(search.name)}*\n",
            f"🔗 {self._esc(url)}",
        ]
        if cat_info:
            lines.append(self._esc(cat_info))
        if search.max_price:
            lines.append(f"💰 Max price: CHF {search.max_price:,.0f}")
        lines.append(f"📊 Alert threshold: {search.min_deal_score}/10")
        lines.append(f"\n📝 _{self._esc(search.context.strip().split(chr(10))[0][:100])}_")
        lines.append(f"\nI'll notify you when good deals appear\\!")

        self._send(chat_id, "\n".join(lines), parse_mode="MarkdownV2")

    # ------------------------------------------------------------------ #
    #  OpenAI message parsing                                             #
    # ------------------------------------------------------------------ #

    def _parse_message(self, text: str) -> Optional[ParsedSearch]:
        try:
            system_prompt = _PARSE_SYSTEM_PROMPT.format(
                categories=self._category_prompt or "(no categories available)"
            )
            response = self.openai.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                response_format=ParsedSearch,
            )
            return response.choices[0].message.parsed
        except Exception as e:
            logger.error("Failed to parse message: %s", e)
            return None

    # ------------------------------------------------------------------ #
    #  Telegram API                                                       #
    # ------------------------------------------------------------------ #

    def _get_updates(self) -> list[dict]:
        try:
            resp = requests.get(
                f"{TELEGRAM_API}/bot{self.token}/getUpdates",
                params={"offset": self._offset, "timeout": 30},
                timeout=35,
            )
            resp.raise_for_status()
            data = resp.json()
            updates = data.get("result", [])
            if updates:
                self._offset = updates[-1]["update_id"] + 1
            return updates
        except requests.RequestException as e:
            logger.error("Failed to get updates: %s", e)
            time.sleep(5)
            return []

    def _send(self, chat_id: str, text: str, parse_mode: Optional[str] = None) -> bool:
        payload: dict = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            resp = requests.post(
                f"{TELEGRAM_API}/bot{self.token}/sendMessage",
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error("Telegram send failed: %s", e)
            # Retry without formatting if MarkdownV2 failed
            if parse_mode:
                return self._send(chat_id, text.replace("\\", "").replace("*", "").replace("_", ""))
            return False

    @staticmethod
    def _esc(text: str) -> str:
        for ch in r"\_*[]()~`>#+-=|{}.!":
            text = text.replace(ch, f"\\{ch}")
        return text
