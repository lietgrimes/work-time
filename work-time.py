
#!/usr/bin/env python3
"""
work-time.py — Tiny daily time tracker for "start at 7am, end now" flow.

Default behavior (no args): end today's entry (start=07:00 local, end=now).
Stores one entry per local day (America/Chicago by default).

Data store: SQLite (default path: time-log.db in the same directory as this script).
Override via --db PATH or TIMELOG_DB env var.

Subcommands:
  end                 End today's entry (or a given date) starting at 7:00.
  set-start           Set start time for a date.
  set-end             Set end time for a date.
  show                Show entries for a period.
  totals              Show totals for a period.
  export              Export entries to CSV for a period.
  view-db             Print DB path and basic stats.

Examples:
  # End today's workday now (start=07:00 unless already set)
  python work_time.py end

  # End for a specific date (e.g., backfill yesterday)
  python work_time.py end --date 2025-08-13

  # Use a custom default start (e.g., 06:30) for today only
  python work_time.py end --start 06:30

  # Adjust start/end explicitly
  python work_time.py set-start --date 2025-08-13 --time 06:45
  python work_time.py set-end   --date 2025-08-13 --time 15:55

  # Show this week / this month entries
  python work_time.py show --range this-week
  python work_time.py totals --range this-month

  # Export last month to CSV
  python work_time.py export --range last-month --out ~/Desktop/work_hours_last_month.csv

  # Totals for this year
  python work_time.py totals --range this-year
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import os
import sqlite3
from datetime import datetime, date, time, timedelta
from typing import Optional, Tuple, List

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    from backports.zoneinfo import ZoneInfo  # type: ignore

LOCAL_TZ_NAME_DEFAULT = os.environ.get("TIMELOG_TZ", "America/Chicago")
LOCAL_TZ = ZoneInfo(LOCAL_TZ_NAME_DEFAULT)

DEFAULT_START_STR = os.environ.get("TIMELOG_DEFAULT_START", "07:00")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.environ.get(
    "TIMELOG_DB",
    os.path.join(SCRIPT_DIR, "time-log.db"))

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entries (
    day TEXT PRIMARY KEY,                 -- local date YYYY-MM-DD (unique per day)
    start_utc TEXT NOT NULL,              -- ISO timestamp in UTC
    end_utc TEXT,                         -- ISO timestamp in UTC
    tz TEXT NOT NULL,                     -- e.g., America/Chicago
    notes TEXT                            -- optional
);
CREATE INDEX IF NOT EXISTS idx_entries_start ON entries(start_utc);
"""

@dataclasses.dataclass
class Entry:
    day: str        # local day YYYY-MM-DD
    start_utc: str  # ISO
    end_utc: Optional[str]
    tz: str
    notes: Optional[str]

    def local_interval(self) -> Tuple[datetime, Optional[datetime]]:
        s_utc = datetime.fromisoformat(self.start_utc.replace("Z","")).replace(tzinfo=ZoneInfo("UTC"))
        s_local = s_utc.astimezone(ZoneInfo(self.tz))
        e_local = None
        if self.end_utc:
            e_utc = datetime.fromisoformat(self.end_utc.replace("Z","")).replace(tzinfo=ZoneInfo("UTC"))
            e_local = e_utc.astimezone(ZoneInfo(self.tz))
        return s_local, e_local

    def duration_minutes(self) -> Optional[int]:
        s_local, e_local = self.local_interval()
        if e_local is None:
            return None
        return int((e_local - s_local).total_seconds() // 60)

# ---------- Utilities ----------

def parse_hhmm(s: str) -> time:
    try:
        hh, mm = s.strip().split(":")
        return time(int(hh), int(mm))
    except Exception:
        raise argparse.ArgumentTypeError(f"Time '{s}' must be HH:MM (24-hour).")

def today_local() -> date:
    return datetime.now(LOCAL_TZ).date()

def to_local_date(dstr: Optional[str]) -> date:
    if dstr:
        return date.fromisoformat(dstr)
    return today_local()

def local_dt_from_date_and_hhmm(d: date, t: time) -> datetime:
    return datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=LOCAL_TZ)

def utc_iso(dt_local: datetime) -> str:
    return dt_local.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00","Z")

def ensure_db(conn: sqlite3.Connection):
    conn.executescript(SCHEMA_SQL)
    conn.commit()

def get_conn(path: str) -> sqlite3.Connection:
    dirpath = os.path.dirname(path)
    if dirpath:
        os.makedirs(dirpath, exist_ok=True)
    conn = sqlite3.connect(path)
    ensure_db(conn)
    return conn

def upsert_entry(conn: sqlite3.Connection, day: date, start_utc: str, end_utc: Optional[str], tz: str, notes: Optional[str]=None):
    dstr = day.isoformat()
    cur = conn.cursor()
    # If exists, update; else insert
    cur.execute("SELECT day FROM entries WHERE day = ?", (dstr,))
    exists = cur.fetchone() is not None
    if exists:
        if start_utc:
            cur.execute("UPDATE entries SET start_utc = ? WHERE day = ?", (start_utc, dstr))
        if end_utc is not None:
            cur.execute("UPDATE entries SET end_utc = ? WHERE day = ?", (end_utc, dstr))
        if notes is not None:
            cur.execute("UPDATE entries SET notes = ? WHERE day = ?", (notes, dstr))
        if tz:
            cur.execute("UPDATE entries SET tz = ? WHERE day = ?", (tz, dstr))
    else:
        cur.execute(
            "INSERT INTO entries(day, start_utc, end_utc, tz, notes) VALUES(?,?,?,?,?)",
            (dstr, start_utc, end_utc, tz, notes)
        )
    conn.commit()

def fetch_entry(conn: sqlite3.Connection, day: date) -> Optional[Entry]:
    cur = conn.cursor()
    cur.execute("SELECT day, start_utc, end_utc, tz, notes FROM entries WHERE day = ?", (day.isoformat(),))
    row = cur.fetchone()
    if not row:
        return None
    return Entry(*row)

def fetch_range(conn: sqlite3.Connection, start_day: date, end_day: date) -> List[Entry]:
    cur = conn.cursor()
    cur.execute(
        "SELECT day, start_utc, end_utc, tz, notes FROM entries WHERE day BETWEEN ? AND ? ORDER BY day ASC",
        (start_day.isoformat(), end_day.isoformat())
    )
    return [Entry(*r) for r in cur.fetchall()]

def is_workday(d: date) -> bool:
    return d.weekday() < 5  # Monday=0 .. Sunday=6

def build_assumed_entry(day: date) -> Entry:
    assumed_start_time = parse_hhmm(DEFAULT_START_STR)
    start_local = local_dt_from_date_and_hhmm(day, assumed_start_time)
    end_local = start_local + timedelta(hours=8)
    return Entry(
        day=day.isoformat(),
        start_utc=utc_iso(start_local),
        end_utc=utc_iso(end_local),
        tz=LOCAL_TZ_NAME_DEFAULT,
        notes=None,
    )

def fetch_range_with_assumptions(conn: sqlite3.Connection, start_day: date, end_day: date) -> List[Entry]:
    entries = fetch_range(conn, start_day, end_day)
    existing_days = {e.day for e in entries}
    cursor_day = start_day
    while cursor_day <= end_day:
        if is_workday(cursor_day) and cursor_day.isoformat() not in existing_days:
            entries.append(build_assumed_entry(cursor_day))
        cursor_day += timedelta(days=1)
    entries.sort(key=lambda e: e.day)
    return entries

def week_bounds(d: date) -> Tuple[date,date]:
    # Monday-based ISO week; change to Sunday-based by adjusting .weekday()
    start = d - timedelta(days=d.weekday())
    end = start + timedelta(days=6)
    return start, end

def month_bounds(d: date) -> Tuple[date,date]:
    first = d.replace(day=1)
    if first.month == 12:
        next_month = first.replace(year=first.year+1, month=1, day=1)
    else:
        next_month = first.replace(month=first.month+1, day=1)
    last = next_month - timedelta(days=1)
    return first, last

def last_month_bounds(d: date) -> Tuple[date,date]:
    first_this, _ = month_bounds(d)
    last_of_prev = first_this - timedelta(days=1)
    return month_bounds(last_of_prev)[0], last_of_prev

def year_bounds(d: date) -> Tuple[date, date]:
    first = date(d.year, 1, 1)
    last = date(d.year, 12, 31)
    return first, last

def resolve_range(range_str: str|None, d: date) -> Tuple[date,date]:
    if not range_str or range_str == "today":
        return d, d
    if range_str == "this-week":
        return week_bounds(d)
    if range_str == "this-month":
        return month_bounds(d)
    if range_str == "this-year":
        return year_bounds(d)
    if range_str == "last-month":
        return last_month_bounds(d)
    if ":" in range_str:
        a, b = range_str.split(":", 1)
        return date.fromisoformat(a), date.fromisoformat(b)
    if len(range_str) == 7 and range_str.count("-")==1:
        # YYYY-MM
        year, month = map(int, range_str.split("-"))
        first = date(year, month, 1)
        return month_bounds(first)
    # fallback single date
    return date.fromisoformat(range_str), date.fromisoformat(range_str)

def minutes_to_hhmm(total_minutes: int) -> str:
    h = total_minutes // 60
    m = total_minutes % 60
    return f"{h:02d}:{m:02d}"

# ---------- Commands ----------

def cmd_end(conn: sqlite3.Connection, args: argparse.Namespace):
    d = to_local_date(args.date)
    # Determine start
    default_start = parse_hhmm(args.start) if args.start else parse_hhmm(DEFAULT_START_STR)
    # If entry exists, keep its start if already set; otherwise set to default
    existing = fetch_entry(conn, d)
    if existing and existing.start_utc:
        # keep existing start, just set end to now
        start_utc = existing.start_utc
    else:
        start_local = local_dt_from_date_and_hhmm(d, default_start)
        start_utc = utc_iso(start_local)
    end_local = datetime.now(LOCAL_TZ)
    end_utc = utc_iso(end_local)
    upsert_entry(conn, d, start_utc, end_utc, LOCAL_TZ_NAME_DEFAULT, args.notes)
    start_local_for_print = datetime.fromisoformat(start_utc.replace("Z", "")).replace(tzinfo=ZoneInfo("UTC")).astimezone(LOCAL_TZ)
    print(f"Ended {d.isoformat()} at {end_local.strftime('%H:%M')} (start {start_local_for_print.strftime('%H:%M')})")

def cmd_set_start(conn: sqlite3.Connection, args: argparse.Namespace):
    d = to_local_date(args.date)
    t = parse_hhmm(args.time)
    s_local = local_dt_from_date_and_hhmm(d, t)
    s_utc = utc_iso(s_local)
    existing = fetch_entry(conn, d)
    end_utc = existing.end_utc if existing else None
    upsert_entry(conn, d, s_utc, end_utc, LOCAL_TZ_NAME_DEFAULT, args.notes)
    print(f"Set start for {d.isoformat()} to {t.strftime('%H:%M')}")

def cmd_set_end(conn: sqlite3.Connection, args: argparse.Namespace):
    d = to_local_date(args.date)
    t = parse_hhmm(args.time) if args.time else None
    if t:
        e_local = local_dt_from_date_and_hhmm(d, t)
    else:
        e_local = datetime.now(LOCAL_TZ)
    e_utc = utc_iso(e_local)
    existing = fetch_entry(conn, d)
    if not existing:
        # Create with default start then set end
        s_local = local_dt_from_date_and_hhmm(d, parse_hhmm(DEFAULT_START_STR))
        s_utc = utc_iso(s_local)
    else:
        s_utc = existing.start_utc
    upsert_entry(conn, d, s_utc, e_utc, LOCAL_TZ_NAME_DEFAULT, args.notes)
    print(f"Set end for {d.isoformat()} to {e_local.strftime('%H:%M')}")

def make_rows(entries: List[Entry]) -> List[dict]:
    rows = []
    for e in entries:
        s_local, e_local = e.local_interval()
        dur = e.duration_minutes()
        rows.append({
            "date": e.day,
            "start_local": s_local.strftime("%Y-%m-%d %H:%M"),
            "end_local": e_local.strftime("%Y-%m-%d %H:%M") if e_local else "",
            "duration_min": dur if dur is not None else "",
            "duration_hhmm": minutes_to_hhmm(dur) if dur is not None else "",
            "notes": e.notes or ""
        })
    return rows

def cmd_show(conn: sqlite3.Connection, args: argparse.Namespace):
    d = to_local_date(None if args.date == "today" else args.date)
    start_day, end_day = resolve_range(args.range, d)
    entries = fetch_range_with_assumptions(conn, start_day, end_day)
    rows = make_rows(entries)
    if not rows:
        print("No entries.")
        return
    # simple table print
    widths = {k: max(len(k), max(len(str(r[k])) for r in rows)) for k in rows[0].keys()}
    headers = list(rows[0].keys())
    print(" | ".join(k.ljust(widths[k]) for k in headers))
    print("-+-".join("-"*widths[k] for k in headers))
    for r in rows:
        print(" | ".join(str(r[k]).ljust(widths[k]) for k in headers))

def cmd_totals(conn: sqlite3.Connection, args: argparse.Namespace):
    d = to_local_date(None if args.date == "today" else args.date)
    start_day, end_day = resolve_range(args.range, d)
    entries = fetch_range_with_assumptions(conn, start_day, end_day)
    total = 0
    for e in entries:
        dur = e.duration_minutes()
        if dur is not None:
            total += dur
    print(f"Range {start_day} to {end_day}: {minutes_to_hhmm(total)} ({total} minutes)")

def cmd_export(conn: sqlite3.Connection, args: argparse.Namespace):
    d = to_local_date(None if args.date == "today" else args.date)
    start_day, end_day = resolve_range(args.range, d)
    entries = fetch_range_with_assumptions(conn, start_day, end_day)
    rows = make_rows(entries)
    # Determine output path: allow file path, directory path, or omitted
    default_filename = f"work-time_{start_day.isoformat()}_to_{end_day.isoformat()}.csv"
    if getattr(args, "out", None):
        out_candidate = os.path.expanduser(args.out)
        if os.path.isdir(out_candidate) or out_candidate in (".", "./") or out_candidate.endswith(os.sep):
            base_dir = os.path.abspath(out_candidate if out_candidate not in (".", "./") else os.getcwd())
            out = os.path.join(base_dir, default_filename)
        else:
            out = out_candidate
    else:
        out = os.path.join(os.getcwd(), default_filename)
    parent_dir = os.path.dirname(out)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else
                                ["date","start_local","end_local","duration_min","duration_hhmm","notes"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {out}")

def cmd_view_db(conn: sqlite3.Connection, args: argparse.Namespace):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), MIN(day), MAX(day) FROM entries")
    row = cur.fetchone()
    count = row[0] if row else 0
    min_day = row[1] if row else None
    max_day = row[2] if row else None
    print(f"DB: {conn.execute('PRAGMA database_list').fetchone()[2]}")
    print(f"Entries: {count}, range: {min_day} .. {max_day}")

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Tiny daily time tracker.")
    p.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite DB path (default: ~/OneDrive/Work/time_log.db or TIMELOG_DB)")
    sub = p.add_subparsers(dest="cmd")

    # end
    sp = sub.add_parser("end", help="End today's entry (start=07:00 unless set).")
    sp.add_argument("--date", help="Local date YYYY-MM-DD (default: today)")
    sp.add_argument("--start", help="Override default start HH:MM for this run")
    sp.add_argument("--notes", help="Optional notes")
    sp.set_defaults(func=cmd_end)

    # set-start
    sp = sub.add_parser("set-start", help="Set start time for a date.")
    sp.add_argument("--date", help="Local date YYYY-MM-DD (default: today)")
    sp.add_argument("--time", required=True, help="HH:MM (24h)")
    sp.add_argument("--notes", help="Optional notes")
    sp.set_defaults(func=cmd_set_start)

    # set-end
    sp = sub.add_parser("set-end", help="Set end time for a date (or now if --time omitted).")
    sp.add_argument("--date", help="Local date YYYY-MM-DD (default: today)")
    sp.add_argument("--time", help="HH:MM (24h), omit for now")
    sp.add_argument("--notes", help="Optional notes")
    sp.set_defaults(func=cmd_set_end)

    # show
    sp = sub.add_parser("show", help="Show entries for a range.")
    sp.add_argument("--range", default="this-week", help="today|this-week|this-month|this-year|last-month|YYYY-MM|YYYY-MM-DD:YYYY-MM-DD")
    sp.add_argument("--date", help="Anchor date for relative ranges (default: today)")
    sp.set_defaults(func=cmd_show)

    # totals
    sp = sub.add_parser("totals", help="Show totals for a range.")
    sp.add_argument("--range", default="this-week", help="today|this-week|this-month|this-year|last-month|YYYY-MM|YYYY-MM-DD:YYYY-MM-DD")
    sp.add_argument("--date", help="Anchor date for relative ranges (default: today)")
    sp.set_defaults(func=cmd_totals)

    # export
    sp = sub.add_parser("export", help="Export entries to CSV for a range.")
    sp.add_argument("--range", default="this-month", help="today|this-week|this-month|this-year|last-month|YYYY-MM|YYYY-MM-DD:YYYY-MM-DD")
    sp.add_argument("--date", help="Anchor date for relative ranges (default: today)")
    sp.add_argument("--out", help="Output CSV file path or directory. If omitted or a directory, a default name is used in the current directory.")
    sp.set_defaults(func=cmd_export)

    # view-db
    sp = sub.add_parser("view-db", help="Show DB path and basic stats.")
    sp.set_defaults(func=cmd_view_db)

    return p

def main():
    parser = build_parser()
    import sys
    argv = sys.argv[1:]
    # If no subcommand (or only global flags like --db), default to 'end'.
    # Preserve top-level help (-h/--help) behavior.
    if not any(flag in argv for flag in ("-h", "--help")):
        subcommands = {"end", "set-start", "set-end", "show", "totals", "export", "view-db"}
        if not argv or argv[0] not in subcommands:
            argv = ["end"] + argv
    args = parser.parse_args(argv)
    conn = get_conn(args.db)
    try:
        # Each subparser sets func=...
        args.func(conn, args)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
