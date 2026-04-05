from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


@dataclass
class Listing:
    """A single listing scraped from Ricardo."""

    id: str
    title: str
    url: str
    price: Optional[float] = None
    currency: str = "CHF"
    condition: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    listing_type: str = "buy_now"  # 'buy_now' or 'auction'
    seller_info: Optional[str] = None
    image_url: Optional[str] = None
    scraped_at: datetime = field(default_factory=datetime.utcnow)

    def format_price(self) -> str:
        if self.price is None:
            return "Price unknown"
        return f"{self.currency} {self.price:,.0f}"

    def short_description(self) -> str:
        """First 500 characters of description for logs."""
        if not self.description:
            return "(no description)"
        return self.description[:500] + ("…" if len(self.description) > 500 else "")


@dataclass
class SearchConfig:
    """One entry from the 'searches' list in config.yaml."""

    name: str
    url: str
    context: str
    max_price: Optional[float] = None
    min_deal_score: int = 7


class AnalysisResult(BaseModel):
    """Structured output returned by the Claude analyzer."""

    is_good_deal: bool = Field(
        description="True if this listing is worth alerting the user about."
    )
    deal_score: int = Field(
        ge=1, le=10,
        description="Overall deal quality from 1 (terrible) to 10 (exceptional)."
    )
    price_assessment: str = Field(
        description=(
            "One of: 'overpriced', 'fair', 'good_deal', 'great_deal'. "
            "Reflects price relative to typical market value."
        )
    )
    technical_quality: str = Field(
        description=(
            "Brief assessment of the item's technical condition and specs "
            "based on the listing text."
        )
    )
    concerns: list[str] = Field(
        default_factory=list,
        description="Red flags or concerns found in the listing."
    )
    key_positives: list[str] = Field(
        default_factory=list,
        description="Positive aspects that make this listing attractive."
    )
    recommendation: str = Field(
        description="One-sentence recommendation to the buyer."
    )
    estimated_market_price: Optional[str] = Field(
        default=None,
        description=(
            "Estimated fair market price for this item in CHF, e.g. 'CHF 800–950'. "
            "Omit if genuinely uncertain."
        )
    )
