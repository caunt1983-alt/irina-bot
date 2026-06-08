# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
pip install -r requirements.txt
python bot.py
```

Requires a `.env` file with:
```
BOT_TOKEN=...
ANTHROPIC_API_KEY=...
```

## Deployment

The bot runs on [Bothost](https://bothost.ru) (Netherlands location — required for Telegram). Deploy by pushing to GitHub (`caunt1983-alt/irina-bot`, branch `main`) and clicking **Пересобрать** in the Bothost panel. Environment variables are set in the Bothost panel, not via `.env`.

Persistent data (`subscribers.json`) is stored in `/app/data/` on the server — use `DATA_DIR = os.getenv('DATA_DIR', '/app/data')` if the path ever needs to change.

## Architecture

Single-file bot (`bot.py`) with no database. All state lives in memory and one JSON file.

**Message generation** — `generate_message(slot)` calls `claude-haiku-4-5` via `anthropic.AsyncAnthropic`. The system prompt (`SYSTEM_PROMPT`) is cached server-side with `cache_control: {"type": "ephemeral"}`. The `slot` parameter (`morning` / `afternoon` / `evening`) injects a time-specific hint into the user message, which shapes greeting/farewell behavior and tone.

**Scheduling** — Three daily send windows: 6–8, 12–14, 20–22 MSK. On startup `_schedule_day()` picks a random time within each window and registers one-shot `DateTrigger` APScheduler jobs for today (skipping windows already past). At 00:01 MSK `_reschedule_tomorrow()` does the same for the next day. Deduplication is handled by `_sent_slots` (in-memory `set[str]` with keys like `2026-06-08_morning`).

**Subscribers** — `subscribers` is a `set[int]` loaded from `subscribers.json` at startup. `/start` adds, `/stop` removes. Broadcasts iterate the set and silently skip failed sends (blocked bots, etc.).

**Commands** — `/start` subscribes and sends an immediate first message. `/stop` unsubscribes. `/test` sends a one-off afternoon-slot message to the requester. `/next` reads the scheduler job queue to report the next fire time.

## Changing message content or tone

Edit `SYSTEM_PROMPT` for global style rules and topic list. Edit `TIME_HINTS` dict for per-slot instructions. Morning slot must open with a greeting; evening slot must close with a farewell — this is enforced via the hint text, not code.

## Changing the schedule

Edit `WINDOWS` list — each entry is `(slot_name, hour_from, hour_to)`. Restart (rebuild) applies the new windows from the next `_schedule_day()` call.
