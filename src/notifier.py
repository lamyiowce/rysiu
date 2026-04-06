"""Telegram notification sender."""

from __future__ import annotations

import logging
import os

import requests

from .models import AnalysisResult, Listing, SearchConfig

logger = logging.getLogger(__name__)

SCORE_EMOJI = {
    range(1, 5): "🔴",
    range(5, 7): "🟡",
    range(7, 9): "🟢",
    range(9, 11): "⭐",
}

PRICE_LABEL = {
    "overpriced": "Overpriced",
    "fair": "Fair price",
    "good_deal": "Good deal",
    "great_deal": "Great deal",
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
            logger.warning("Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")

    def send_deal_alert(self, listing: Listing, analysis: AnalysisResult, search: SearchConfig) -> bool:
        if not self.enabled:
            logger.info("[DRY RUN] Telegram alert for: %s", listing.title)
            return False
        return self._send(self._format_message(listing, analysis, search))

    def _format_message(self, listing: Listing, analysis: AnalysisResult, search: SearchConfig) -> str:
        e = _score_emoji(analysis.deal_score)
        price_label = PRICE_LABEL.get(analysis.price_assessment, analysis.price_assessment)
        lines = [
            f"{e} *Deal score: {analysis.deal_score}/10* — {search.name}",
            "",
            f"📦 *{self._esc(listing.title)}*",
            f"💰 {self._esc(listing.format_price())} — _{self._esc(price_label)}_",
        ]
        if analysis.estimated_market_price:
            lines.append(f"📊 Market est\\.: {self._esc(analysis.estimated_market_price)}")
        if listing.condition:
            lines.append(f"🏷️ {self._esc(listing.condition)}")
        lines += ["", f"🔧 {self._esc(analysis.technical_quality)}"]
        if analysis.key_positives:
            lines += ["", "✅ *Positives:*"] + [f"  • {self._esc(p)}" for p in analysis.key_positives[:3]]
        if analysis.concerns:
            lines += ["", "⚠️ *Concerns:*"] + [f"  • {self._esc(c)}" for c in analysis.concerns[:3]]
        lines += ["", f"💡 _{self._esc(analysis.recommendation)}_"]
        if listing.url:
            lines += ["", f"[View listing]({listing.url})"]
        return "\n".join(lines)

    def _send(self, text: str) -> bool:
        try:
            resp = requests.post(
                f"{self.API_BASE}/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "MarkdownV2"},
                timeout=15,
            )
            resp.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error("Telegram send failed: %s", e)
            try:
                plain = text.replace("*", "").replace("_", "").replace("\\", "")
                resp = requests.post(
                    f"{self.API_BASE}/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": plain},
                    timeout=15,
                )
                resp.raise_for_status()
                return True
            except requests.RequestException:
                return False

    @staticmethod
    def _esc(text: str) -> str:
        for ch in r"\_*[]()~`>#+-=|{}.!":
            text = text.replace(ch, f"\\{ch}")
        return text
