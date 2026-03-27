# work-time — tiny daily time tracker

A minimal CLI to log one work entry per day with a “start at HH:MM, end now” flow. Uses a local SQLite database, supports quick backfills, simple reports, CSV export, and a lightweight TUI editor.

Why: I built this as a lightweight, self‑hosted replacement for paid time‑tracking tools like Timery. It keeps your data local and makes daily logging fast without subscriptions or complexity.

Use cases:
- Keep tabs on work–life balance by viewing weekly/monthly hours at a glance.
- Maintain an accurate record you can reference to demonstrate workload or clarify expectations with a superior.

- Default timezone: `America/Chicago` (override via `TIMELOG_TZ`).
- Default start time: `07:00` (override via `TIMELOG_DEFAULT_START`).
- Default DB path: `time-log.db` next to the script (override via `--db` or `TIMELOG_DB`).

## Quick Start

- Show this week (auto-fills missing past weekdays as 8h days):
  - `python work-time.py show --range this-week`
- End today now (uses default start if not set yet):
  - `python work-time.py end`
- Start today now:
  - `python work-time.py start`
- Start today at a specific time:
  - `python work-time.py start --time 06:00`
  - `python work-time.py start --time 600`
  - `python work-time.py start --time 6a`
  - `python work-time.py start --time 6am`

Tip: Make it executable and run directly:
- `chmod +x work-time.py`
- `./work-time.py show --range this-week`

## Commands

- `end`: End a day at “now”; keeps existing start or uses default start.
  - Examples: `python work-time.py end`, `python work-time.py end --date 2025-08-13`, `python work-time.py end --start 06:30`, `python work-time.py end --time 6pm`
- `start`: Start a day at “now”, or pass `--time` with `HH:MM`, `HHMM`, `6a`, or `6am`.
  - Example: `python work-time.py start --date 2025-08-13`
- `set-start`: Set explicit start time.
  - Example: `python work-time.py set-start --date 2025-08-13 --time 06:45`
- `set-end`: Set explicit end time (or now if `--time` omitted).
  - Example: `python work-time.py set-end --date 2025-08-13 --time 15:55`
- `show`: Show entries for a range in a table (includes auto-assumed 8h for missing past weekdays).
  - Example: `python work-time.py show --range this-month`
- `totals`: Total time for a range.
  - Example: `python work-time.py totals --range last-month`
- `averages`: Averages and weekly summaries for a range; includes 40-hour target summary per week.
  - Example: `python work-time.py averages --range this-year`
- `export`: Export entries for a range to CSV.
  - Example: `python work-time.py export --range last-month --out my.csv`
- `view-db`: Show DB path and basic stats.
- `edit`: TUI to visually edit start/end times for a range.

Global option:
- `--db PATH`: SQLite DB path (default `time-log.db` beside the script).

## Ranges

Use `--range` with any of these values:
- `today`, `this-week`, `last-week`, `this-month`, `last-month`, `this-year`, `last-year`
- `YYYY-MM` (month)
- `YYYY-MM-DD:YYYY-MM-DD` (inclusive range)

You can also supply an anchor `--date` for relative ranges (defaults to today in local time).

## Data Model

SQLite table `entries` (one row per local day):
- `day`: local date `YYYY-MM-DD` (primary key)
- `start_utc`: ISO timestamp in UTC
- `end_utc`: ISO timestamp in UTC (nullable)
- `tz`: timezone name (e.g., `America/Chicago`)
- `notes`: optional text

When running `show`, `totals`, `averages`, or `export`, missing past workdays (Mon–Fri) in the selected range are auto-filled as assumed 8-hour entries starting at your default start time. Future days are never assumed.

## CSV Export

Columns: `date`, `start_local`, `end_local`, `duration_min`, `duration_hhmm`, `notes`.

Output path behavior:
- `--out` is a file: write exactly there.
- `--out` is a directory (or `.`): writes `work-time_<start>_to_<end>.csv` inside it.
- Omitted `--out`: writes the default file in the current directory.

## TUI Editor

`python work-time.py edit --range this-week`

- Arrow keys: move cursor
- `e` or Enter: edit time in the selected cell (`HH:MM`, `HHMM`, `6a`, `6am`)
- `s`: save changed rows
- `q`: quit

Only `start_local` and `end_local` are editable. Times are validated using the same formats as `start --time`.

## Configuration

Environment variables:
- `TIMELOG_DB`: override database path (same as `--db`).
- `TIMELOG_TZ`: default timezone (e.g., `America/Chicago`).
- `TIMELOG_DEFAULT_START`: default daily start time, `HH:MM` (e.g., `06:30`).

## Requirements

- Python 3.9+ (uses `zoneinfo`).
- No external dependencies for typical use.
- For Windows TUI: you may need `pip install windows-curses`.

## Examples

- Backfill yesterday quickly:
  - `python work-time.py end --date 2025-08-13`
- Override today’s default start once:
  - `python work-time.py end --start 06:30`
- See totals for last month:
  - `python work-time.py totals --range last-month`
- Export a custom range to CSV:
  - `python work-time.py export --range 2025-08-01:2025-08-31 --out ./exports/`

---

This tool optimizes for a simple “one entry per day” workflow with sane defaults and fast corrections when needed.
