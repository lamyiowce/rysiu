"""Deal analyzer — uses Claude Opus to evaluate each listing."""

from __future__ import annotations

import logging
import os
from typing import Optional

import anthropic

from .models import AnalysisResult, Listing, SearchConfig

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """\
You are an expert consumer electronics and second-hand marketplace analyst \
specializing in Swiss second-hand platforms (Ricardo.ch). \
You evaluate listings to determine if they represent good value for money.

When given a listing and the buyer's search context, you produce a structured \
assessment covering price fairness, technical quality, red flags, and positives. \
Be concise and specific. Base price estimates on Swiss CHF market prices for \
used goods. Err on the side of caution with red flags.
"""


class DealAnalyzer:
    def __init__(self, model: str = "claude-opus-4-6"):
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.model = model

    def analyze(self, listing: Listing, search: SearchConfig) -> Optional[AnalysisResult]:
        """Analyze a listing and return a structured result, or None on failure."""
        prompt = self._build_prompt(listing, search)

        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=1024,
                thinking={"type": "adaptive"},
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                output_format=AnalysisResult,
            )
            return response.parsed_output
        except Exception as e:
            logger.error("Analysis failed for listing %s: %s", listing.id, e)
            return None

    @staticmethod
    def _build_prompt(listing: Listing, search: SearchConfig) -> str:
        parts = [
            f"## Buyer context\n{search.context.strip()}",
            "",
            "## Listing details",
            f"**Title:** {listing.title}",
            f"**Price:** {listing.format_price()}",
        ]

        if listing.condition:
            parts.append(f"**Condition:** {listing.condition}")
        if listing.location:
            parts.append(f"**Location:** {listing.location}")
        if listing.listing_type == "auction":
            parts.append("**Type:** Auction")
        if listing.description:
            parts.append(f"\n**Description:**\n{listing.description[:2000]}")
        else:
            parts.append("\n*(No description available — base assessment on title and price only)*")

        parts += [
            "",
            "## Task",
            (
                "Evaluate whether this listing is a good deal for the buyer described above. "
                "Fill in all fields of the structured output. "
                "For deal_score: 1–4 = bad/skip, 5–6 = average, 7–8 = good, 9–10 = exceptional."
            ),
        ]

        return "\n".join(parts)
