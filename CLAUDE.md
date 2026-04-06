# CLAUDE.md

## Project Overview

Rysiu is a Ricardo.ch deal analyzer. It monitors saved searches on Ricardo.ch (Swiss online marketplace), uses AI to analyze listings, and sends Telegram alerts for good deals.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env  # Fill in OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
```

## Running

```bash
python main.py            # Continuous monitoring + Telegram bot
python main.py --once     # Single monitoring cycle
python main.py --test     # Dry-run scraper (no AI/Telegram calls)
python main.py --no-bot   # Monitoring only, no Telegram bot
```

## Project Structure

- `main.py` — CLI entry point
- `src/models.py` — Pydantic data models (Listing, SearchConfig, AnalysisResult)
- `src/database.py` — SQLite persistence
- `src/scraper.py` — Ricardo.ch scraper (parses Next.js `__NEXT_DATA__` JSON)
- `src/analyzer.py` — AI-powered deal analysis
- `src/notifier.py` — Telegram notifications
- `src/scheduler.py` — Monitoring pipeline orchestrator
- `src/config_manager.py` — YAML config management
- `src/bot.py` — Telegram bot for interactive search management
- `config.yaml` — Search definitions

## Key Details

- Python 3 project, dependencies in `requirements.txt`
- No test suite or linter configured
- SQLite database for deduplication of seen listings
- GitHub Actions runs monitoring on schedule (`.github/workflows/monitor.yml`)
