# rysiu

Monitors your saved searches on [Ricardo.ch](https://www.ricardo.ch) and uses Claude AI to analyze each new listing — alerting you via Telegram **only when a listing is genuinely a good deal**.

## How it works

```
┌─────────────────┐     every N min      ┌──────────────────┐
│  config.yaml    │ ──── search URLs ──▶ │  Ricardo Scraper │
│  (your searches)│                       └────────┬─────────┘
└─────────────────┘                                │ new listings
                                                   ▼
                                         ┌──────────────────┐
                                         │  SQLite DB       │ ◀── deduplication
                                         └────────┬─────────┘
                                                  │ unseen listings
                                                  ▼
                                         ┌──────────────────┐
                                         │  Claude Opus 4.6 │ ◀── your search context
                                         │  (deal analyzer) │
                                         └────────┬─────────┘
                                                  │ score ≥ threshold
                                                  ▼
                                         ┌──────────────────┐
                                         │  Telegram alert  │
                                         └──────────────────┘
```

Claude receives the full listing (title, price, condition, description) plus your natural-language description of what you're looking for and what a fair price looks like. It returns a structured assessment:

- **Deal score** (1–10)
- **Price assessment** (overpriced / fair / good deal / great deal)
- **Technical quality** summary
- **Key positives** and **concerns**
- **Estimated market price**
- **Recommendation**

Only listings that score at or above your threshold trigger a Telegram notification.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
# Edit .env and fill in your keys
```

You need:
- `ANTHROPIC_API_KEY` — from [console.anthropic.com](https://console.anthropic.com)
- `TELEGRAM_BOT_TOKEN` — create a bot via [@BotFather](https://t.me/botfather) on Telegram
- `TELEGRAM_CHAT_ID` — find your chat ID by messaging [@userinfobot](https://t.me/userinfobot)

### 3. Configure your searches

Edit `config.yaml`:

```yaml
searches:
  - name: "MacBook Air M2"
    url: "https://www.ricardo.ch/de/s/macbook-air-m2/"
    context: |
      I'm looking for a MacBook Air M2 (2022) in good or excellent condition.
      8GB RAM minimum, 256GB or 512GB SSD. Budget CHF 700–1050.
      Not interested in cracked screens or missing charger.
    max_price: 1050   # CHF — pre-filter before Claude analysis
    min_deal_score: 7 # 1–10, only alert at this score or above
```

**Tips for the `context` field:**
- Be specific about which model/generation you want
- Mention acceptable condition levels
- State your price range and what a "great deal" looks like
- List any dealbreakers

### 4. Test the scraper

Verify the scraper can find listings before spending API credits:

```bash
python main.py --test
```

### 5. Run

```bash
# Run once and exit
python main.py --once

# Run continuously (checks every N minutes as configured)
python main.py
```

## Project structure

```
rysiu/
├── main.py              # Entry point & CLI
├── config.yaml          # Your searches and settings
├── requirements.txt
├── .env                 # API keys (not committed)
├── data/
│   └── rysiu.db        # SQLite — seen listings & analyses
└── src/
    ├── models.py        # Listing, SearchConfig, AnalysisResult
    ├── database.py      # SQLite persistence
    ├── scraper.py       # Ricardo.ch scraper (Next.js __NEXT_DATA__ parsing)
    ├── analyzer.py      # Claude Opus deal analysis
    ├── notifier.py      # Telegram notifications
    └── scheduler.py     # Monitoring pipeline
```

## Notes on the scraper

Ricardo uses Next.js and serves listing data in a `__NEXT_DATA__` JSON blob on the page. The scraper extracts this without needing a browser or Selenium. If the site structure changes and the scraper stops working, run `--test` to diagnose — it will show you what (if anything) was found.

Ricardo does not have a public API, so scraping is the only option. Be respectful: the default delay between requests is 2 seconds, and the scheduler checks every 30 minutes.
