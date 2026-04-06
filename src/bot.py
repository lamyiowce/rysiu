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
from typing import Optional
from urllib.parse import quote_plus

import requests
from openai import OpenAI
from pydantic import BaseModel, Field

from . import config_manager as cm

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


# ------------------------------------------------------------------ #
#  Structured output for parsing user messages                        #
# ------------------------------------------------------------------ #

class ParsedSearch(BaseModel):
    """What the user wants to monitor, extracted from their message."""

    name: str = Field(
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
    context: str = Field(
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

The user will send a casual message describing what they want to buy. Extract:
- A short name for the search
- A search query: keep it SHORT (1-3 keywords max). Ricardo.ch treats multiple \
  words as AND — too many keywords returns zero results. Pick only the most \
  essential keyword(s) that capture what the user wants. For example: \
  "plattenspieler" not "plattenspieler thorens technics dual pioneer". \
  Put brand/model preferences in the context instead.
- If the user provides a direct Ricardo.ch URL, set search_query to null — \
  the URL will be used as-is.
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

        # Use URL from message if present, otherwise build from search_query
        explicit_url = self._extract_ricardo_url(text)
        if explicit_url:
            url = explicit_url
        elif parsed.search_query:
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
        lines = [
            f"✅ Now monitoring: *{self._esc(search.name)}*\n",
            f"🔗 {self._esc(url)}",
        ]
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
            response = self.openai.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": _PARSE_SYSTEM_PROMPT},
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
