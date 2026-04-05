"""Telegram notification sender."""

from __future__ import annotations

import logging
import os
from typing import Optional

import requests

from .models import AnalysisResult, Listing, SearchConfig

logger = logging.getLogger(__name__)

SCORE_EMOJI = {
    range(1, 5): "🔴",
    range(5, 7): "🟡",
    range(7, 9): "🟢",
    range(9, 11): "⭐",
}

PRICE_EMOJI = {
    "overpriced": "💸",
    "fair": "💰",
    "good_deal": "✅",
    "great_deal": "🎉",
}


def _score_emoji(score: int) -> str:
    for r, emoji in SCORE_EMOJI.items():
        if score in r:
            return emoji
    return "⚪"


class TelegramNotifier:
    API_BASE = "https://api.telegram.org"

    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.token and self.chat_id)

        if not self.enabled:
            logger.warning(
                "Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
            )

    def send_deal_alert(
        self,
        listing: Listing,
        analysis: AnalysisResult,
        search: SearchConfig,
    ) -> bool:
        """Send a deal alert. Returns True on success."""
        if not self.enabled:
            logger.info("[DRY RUN] Would send alert for: %s", listing.title)
            return False

        message = self._format_message(listing, analysis, search)
        return self._send(message)

    def send_status(self, text: str) -> bool:
        """Send a plain status message (e.g. startup notification)."""
        if not self.enabled:
            return False
        return self._send(text)

    # ------------------------------------------------------------------ #

    def _format_message(
        self, listing: Listing, analysis: AnalysisResult, search: SearchConfig
    ) -> str:
        score_emoji = _score_emoji(analysis.deal_score)
        price_emoji = PRICE_EMOJI.get(analysis.price_assessment, "💰")

        lines = [
            f"{score_emoji} *Deal score: {analysis.deal_score}/10* — {search.name}",
            "",
            f"📦 *{self._escape(listing.title)}*",
            f"{price_emoji} {self._escape(listing.format_price())} — _{self._escape(analysis.price_assessment.replace('_', ' ').title())}_",
        ]

        if analysis.estimated_market_price:
            lines.append(f"📊 Market est\\.: {self._escape(analysis.estimated_market_price)}")

        if listing.condition:
            lines.append(f"🏷️ Condition: {self._escape(listing.condition)}")

        lines.append("")
        lines.append(f"🔧 *Technical:* {self._escape(analysis.technical_quality)}")

        if analysis.key_positives:
            lines.append("")
            lines.append("✅ *Positives:*")
            for p in analysis.key_positives[:3]:
                lines.append(f"  • {self._escape(p)}")

        if analysis.concerns:
            lines.append("")
            lines.append("⚠️ *Concerns:*")
            for c in analysis.concerns[:3]:
                lines.append(f"  • {self._escape(c)}")

        lines.append("")
        lines.append(f"💡 _{self._escape(analysis.recommendation)}_")

        if listing.url:
            lines.append("")
            lines.append(f"[View listing]({listing.url})")

        return "\n".join(lines)

    def _send(self, text: str) -> bool:
        url = f"{self.API_BASE}/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": False,
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
            resp.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error("Telegram send failed: %s", e)
            # Try without markdown if formatting is the issue
            if "parse_mode" in str(e):
                return self._send_plain(text)
            return False

    def _send_plain(self, text: str) -> bool:
        """Fallback: send without markdown."""
        url = f"{self.API_BASE}/bot{self.token}/sendMessage"
        # Strip markdown characters
        plain = text.replace("*", "").replace("_", "").replace("\\", "")
        payload = {"chat_id": self.chat_id, "text": plain}
        try:
            resp = requests.post(url, json=payload, timeout=15)
            resp.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error("Telegram plain send also failed: %s", e)
            return False

    @staticmethod
    def _escape(text: str) -> str:
        """Escape special characters for Telegram MarkdownV2."""
        special = r"\_*[]()~`>#+-=|{}.!"
        for ch in special:
            text = text.replace(ch, f"\\{ch}")
        return text
