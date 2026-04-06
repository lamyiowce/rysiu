"""Telegram and email notification senders."""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

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


# ------------------------------------------------------------------ #
#  Telegram                                                           #
# ------------------------------------------------------------------ #

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
        lines = [
            f"{e} *Deal score: {analysis.deal_score}/10* — {search.name}",
            "",
            f"📦 *{self._esc(listing.title)}*",
            f"💰 {self._esc(listing.format_price())} — _{self._esc(PRICE_LABEL.get(analysis.price_assessment, analysis.price_assessment))}_",
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
            # Fallback: strip markdown and retry
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


# ------------------------------------------------------------------ #
#  Email                                                              #
# ------------------------------------------------------------------ #

class EmailNotifier:
    def __init__(self, config: dict):
        cfg = config.get("notifications", {}).get("email", {})
        self.enabled = cfg.get("enabled", False)
        self.smtp_host = os.environ.get("EMAIL_SMTP_HOST", cfg.get("smtp_host", "smtp.gmail.com"))
        self.smtp_port = int(os.environ.get("EMAIL_SMTP_PORT", cfg.get("smtp_port", 587)))
        self.username = os.environ.get("EMAIL_USERNAME", "")
        self.password = os.environ.get("EMAIL_PASSWORD", "")
        self.to = os.environ.get("EMAIL_TO", cfg.get("to", self.username))
        self.min_score = cfg.get("min_score", 9)

        if self.enabled and not (self.username and self.password):
            logger.warning("Email enabled but EMAIL_USERNAME / EMAIL_PASSWORD not set")
            self.enabled = False

    def should_send(self, analysis: AnalysisResult) -> bool:
        return self.enabled and analysis.deal_score >= self.min_score

    def send_deal_alert(self, listing: Listing, analysis: AnalysisResult, search: SearchConfig) -> bool:
        if not self.enabled:
            logger.info("[DRY RUN] Email alert for: %s", listing.title)
            return False

        subject = (
            f"⭐ Ricardo deal {analysis.deal_score}/10 — {listing.title[:60]}"
        )
        html = self._build_html(listing, analysis, search)
        return self._send(subject, html)

    def _build_html(self, listing: Listing, analysis: AnalysisResult, search: SearchConfig) -> str:
        score_emoji = _score_emoji(analysis.deal_score)
        price_label = PRICE_LABEL.get(analysis.price_assessment, analysis.price_assessment)

        positives_html = "".join(f"<li>{p}</li>" for p in analysis.key_positives)
        concerns_html = "".join(f"<li>{c}</li>" for c in analysis.concerns)

        link_html = (
            f'<p><a href="{listing.url}" style="font-size:16px;font-weight:bold;">→ View listing on Ricardo</a></p>'
            if listing.url else ""
        )

        market_html = (
            f"<p><strong>Market estimate:</strong> {analysis.estimated_market_price}</p>"
            if analysis.estimated_market_price else ""
        )

        condition_html = (
            f"<p><strong>Condition:</strong> {listing.condition}</p>"
            if listing.condition else ""
        )

        return f"""
        <html><body style="font-family:sans-serif;max-width:600px;margin:auto;padding:20px">
          <h2>{score_emoji} Deal score: {analysis.deal_score}/10 — {search.name}</h2>
          <h3 style="margin-bottom:4px">{listing.title}</h3>
          <p style="font-size:20px;margin-top:4px">
            <strong>{listing.format_price()}</strong>
            <span style="color:#666;font-size:14px"> — {price_label}</span>
          </p>
          {market_html}
          {condition_html}
          <hr>
          <p><strong>Technical assessment:</strong><br>{analysis.technical_quality}</p>
          {"<p><strong>Positives:</strong></p><ul>" + positives_html + "</ul>" if positives_html else ""}
          {"<p><strong>Concerns:</strong></p><ul style='color:#c0392b'>" + concerns_html + "</ul>" if concerns_html else ""}
          <hr>
          <p style="font-style:italic">{analysis.recommendation}</p>
          {link_html}
          <p style="color:#aaa;font-size:12px">Sent by rysiu — Ricardo deal monitor</p>
        </body></html>
        """

    def _send(self, subject: str, html: str) -> bool:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.username
        msg["To"] = self.to
        msg.attach(MIMEText(html, "html"))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(self.username, self.password)
                smtp.sendmail(self.username, self.to, msg.as_string())
            logger.info("Email sent to %s", self.to)
            return True
        except Exception as e:
            logger.error("Email send failed: %s", e)
            return False
