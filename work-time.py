
#!/usr/bin/env python3
"""
work-time.py — Tiny daily time tracker for "start at 7am, end now" flow.

Default behavior (no args): end today's entry (start=07:00 local, end=now).
Stores one entry per local day (America/Chicago by default).

Data store: SQLite (default path: time-log.db in the same directory as this script).
Override via --db PATH or TIMELOG_DB env var.

Subcommands:
  end                 End today's entry (or a given date) starting at 7:00.
  start               Start today's entry (or a given date) with start=now.
  set-start           Set start time for a date.
  set-end             Set end time for a date.
  show                Show entries for a period.
  totals              Show totals for a period.
  averages            Show average work hours for a period.
  export              Export entries to CSV for a period.
  view-db             Print DB path and basic stats.
  edit                Visually edit entries for a period (TUI).

Examples:
  # End today's workday now (start=07:00 unless already set)
  python work_time.py end

  # Start today's workday now (end=None unless already set)
  python work_time.py start

  # End for a specific date (e.g., backfill yesterday)
  python work_time.py end --date 2025-08-13

  # Start for a specific date (e.g., backfill yesterday)
  python work_time.py start --date 2025-08-13

  # Use a custom default start (e.g., 06:30) for today only
  python work_time.py end --start 06:30

  # Adjust start/end explicitly
  python work_time.py set-start --date 2025-08-13 --time 06:45
  python work_time.py set-end   --date 2025-08-13 --time 15:55

  # Show this week / this month entries
  python work_time.py show --range this-week
  python work_time.py totals --range this-month

  # Export last month to CSV
  python work_time.py export --range last-month --out my.csv

  # Visually edit this week's entries in a table
  python work_time.py edit --range this-week
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import os
import re
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
    raw = s.strip().lower()

    m = re.fullmatch(r"(\d{1,2})(?::?(\d{2}))?\s*([ap])m?", raw)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or "00")
        suffix = m.group(3)
        if not (1 <= hour <= 12 and 0 <= minute <= 59):
            raise argparse.ArgumentTypeError(
                f"Time '{s}' must be like HH:MM, HHMM, 6a, or 6am."
            )
        if hour == 12:
            hour = 0
        if suffix == "p":
            hour += 12
        return time(hour, minute)

    m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        try:
            return time(hour, minute)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"Time '{s}' must be like HH:MM, HHMM, 6a, or 6am."
            ) from None

    m = re.fullmatch(r"(\d{3,4})", raw)
    if m:
        digits = m.group(1)
        hour = int(digits[:-2])
        minute = int(digits[-2:])
        try:
            return time(hour, minute)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"Time '{s}' must be like HH:MM, HHMM, 6a, or 6am."
            ) from None

    raise argparse.ArgumentTypeError(
        f"Time '{s}' must be like HH:MM, HHMM, 6a, or 6am."
    )

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
    today = today_local()
    cursor_day = start_day
    while cursor_day <= end_day:
        if is_workday(cursor_day) and cursor_day.isoformat() not in existing_days:
            # Only assume 8-hour days for past dates, not future dates
            if cursor_day < today:
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
    if range_str == "last-week":
        start, _ = week_bounds(d)
        last_week_end = start - timedelta(days=1)
        last_week_start = last_week_end - timedelta(days=6)
        return last_week_start, last_week_end
    if range_str == "this-month":
        return month_bounds(d)
    if range_str == "last-month":
        return last_month_bounds(d)
    if range_str == "this-year":
        return year_bounds(d)
    if range_str == "last-year":
        first_this, _ = year_bounds(d)
        last_of_prev = first_this - timedelta(days=1)
        return year_bounds(last_of_prev)
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

def cmd_start(conn: sqlite3.Connection, args: argparse.Namespace):
    d = to_local_date(args.date)
    if args.time:
        start_local = local_dt_from_date_and_hhmm(d, args.time)
    else:
        start_local = datetime.now(LOCAL_TZ)
    start_utc = utc_iso(start_local)
    existing = fetch_entry(conn, d)
    if existing and existing.end_utc:
        end_utc = existing.end_utc
    else:
        end_utc = None
    upsert_entry(conn, d, start_utc, end_utc, LOCAL_TZ_NAME_DEFAULT, args.notes)
    start_local_for_print = datetime.fromisoformat(start_utc.replace("Z", "")).replace(tzinfo=ZoneInfo("UTC")).astimezone(LOCAL_TZ)
    print(f"Started {d.isoformat()} at {start_local_for_print.strftime('%H:%M')}")

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

def cmd_averages(conn: sqlite3.Connection, args: argparse.Namespace):
    d = to_local_date(None if args.date == "today" else args.date)
    start_day, end_day = resolve_range(args.range, d)
    entries = fetch_range_with_assumptions(conn, start_day, end_day)
    
    # Filter entries with valid durations
    valid_entries = [e for e in entries if e.duration_minutes() is not None]
    
    if not valid_entries:
        print(f"No valid entries found in range {start_day} to {end_day}")
        return
    
    # Calculate totals
    total_minutes = sum(e.duration_minutes() for e in valid_entries)
    total_days = len(valid_entries)
    
    # Calculate averages
    avg_minutes = total_minutes / total_days
    avg_hours = avg_minutes / 60
    
    # Calculate workday averages (excluding weekends)
    workday_entries = [e for e in valid_entries if is_workday(date.fromisoformat(e.day))]
    workday_count = len(workday_entries)
    
    if workday_count > 0:
        workday_avg_minutes = sum(e.duration_minutes() for e in workday_entries) / workday_count
        workday_avg_hours = workday_avg_minutes / 60
    else:
        workday_avg_minutes = 0
        workday_avg_hours = 0
    
    # Calculate weekly totals and averages
    weeks: dict[str, List[Entry]] = {}
    week_days: dict[str, set[str]] = {}
    for e in valid_entries:
        week_start, _ = week_bounds(date.fromisoformat(e.day))
        week_key = week_start.isoformat()
        if week_key not in weeks:
            weeks[week_key] = []
            week_days[week_key] = set()
        weeks[week_key].append(e)
        week_days[week_key].add(e.day)

    if weeks:
        week_start_dates = [date.fromisoformat(k) for k in weeks.keys()]
        extended_start = min(week_start_dates)
        extended_end = max(week_start_dates) + timedelta(days=6)
        extra_entries = fetch_range(conn, extended_start, extended_end)
        for e in extra_entries:
            dur = e.duration_minutes()
            if dur is None:
                continue
            week_start, _ = week_bounds(date.fromisoformat(e.day))
            week_key = week_start.isoformat()
            if week_key not in weeks:
                continue
            if e.day in week_days[week_key]:
                continue
            weeks[week_key].append(e)
            week_days[week_key].add(e.day)

    weekly_totals = []
    weekly_averages = []
    for week_start, week_entries in weeks.items():
        durations = []
        for entry in week_entries:
            dur = entry.duration_minutes()
            if dur is not None:
                durations.append(dur)
        if not durations:
            continue
        week_total_minutes = sum(durations)
        week_total_hours = week_total_minutes / 60
        week_avg_minutes = week_total_minutes / len(durations)
        week_avg_hours = week_avg_minutes / 60
        
        weekly_totals.append((week_start, week_total_minutes, week_total_hours))
        weekly_averages.append((week_start, week_avg_minutes, week_avg_hours))
    
    weekly_totals.sort(key=lambda x: x[0])
    weekly_averages.sort(key=lambda x: x[0])
    
    # Calculate monthly averages
    months = {}
    for e in valid_entries:
        month_key = e.day[:7]  # YYYY-MM
        if month_key not in months:
            months[month_key] = []
        months[month_key].append(e)
    
    monthly_averages = []
    for month_key, month_entries in months.items():
        month_total = sum(e.duration_minutes() for e in month_entries)
        month_avg = month_total / len(month_entries)
        monthly_averages.append((month_key, month_avg))
    
    monthly_averages.sort(key=lambda x: x[0])
    
    # Display results
    print(f"=== Averages for {start_day} to {end_day} ===")
    print(f"Total entries: {total_days}")
    print(f"Total time: {minutes_to_hhmm(total_minutes)} ({total_minutes} minutes)")
    print(f"Overall average: {minutes_to_hhmm(int(avg_minutes))} ({avg_hours:.2f} hours)")
    print(f"Workday average: {minutes_to_hhmm(int(workday_avg_minutes))} ({workday_avg_hours:.2f} hours)")
    
    # Show weekly totals (this is the key for 40-hour week proof)
    if len(weekly_totals) > 0:
        print(f"\n=== Weekly Totals (40-hour target) ===")
        target_minutes = 40 * 60  # 40 hours in minutes
        target_hours = 40.0
        
        for week_start, week_total_minutes, week_total_hours in weekly_totals:
            status = "✅" if week_total_hours >= target_hours else "❌"
            shortfall = target_hours - week_total_hours if week_total_hours < target_hours else 0
            shortfall_str = f" (-{shortfall:.1f}h)" if shortfall > 0 else ""
            
            print(f"  {week_start} (week of): {minutes_to_hhmm(int(week_total_minutes))} ({week_total_hours:.1f}h) {status}{shortfall_str}")
        
        # Summary of 40-hour weeks
        weeks_meeting_target = sum(1 for _, _, hours in weekly_totals if hours >= target_hours)
        total_weeks = len(weekly_totals)
        print(f"\n  Summary: {weeks_meeting_target}/{total_weeks} weeks met 40-hour target ({weeks_meeting_target/total_weeks*100:.0f}%)")
        
        # Overall weekly average for the entire period
        if total_weeks > 0:
            total_weekly_hours = sum(hours for _, _, hours in weekly_totals)
            overall_weekly_avg = total_weekly_hours / total_weeks
            print(f"  Overall weekly average: {overall_weekly_avg:.1f} hours/week")
    
    if len(weekly_averages) > 1:
        print(f"\n=== Daily Averages by Week ===")
        for week_start, week_avg_minutes, week_avg_hours in weekly_averages:
            print(f"  {week_start} (week of): {minutes_to_hhmm(int(week_avg_minutes))} ({week_avg_hours:.2f} hours/day)")
    
    if len(monthly_averages) > 1:
        print(f"\n=== Monthly Averages ===")
        for month_key, month_avg in monthly_averages:
            print(f"  {month_key}: {minutes_to_hhmm(int(month_avg))} ({month_avg/60:.2f} hours)")
    
    # Show distribution if there are enough entries
    if total_days >= 5:
        durations = [e.duration_minutes() for e in valid_entries]
        durations.sort()
        median = durations[total_days // 2]
        min_dur = min(durations)
        max_dur = max(durations)
        print(f"\n=== Distribution ===")
        print(f"  Min: {minutes_to_hhmm(min_dur)} ({min_dur/60:.2f} hours)")
        print(f"  Median: {minutes_to_hhmm(median)} ({median/60:.2f} hours)")
        print(f"  Max: {minutes_to_hhmm(max_dur)} ({max_dur/60:.2f} hours)")

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

def cmd_edit(conn: sqlite3.Connection, args: argparse.Namespace):
    import curses
    d = to_local_date(None if args.date == "today" else args.date)
    start_day, end_day = resolve_range(args.range, d)
    entries = fetch_range_with_assumptions(conn, start_day, end_day)
    rows = make_rows(entries)
    if not rows:
        print("No entries.")
        return
    headers = ["date", "start_local", "end_local"]
    col_widths = [max(len(h), max(len(str(r[h])) for r in rows)) for h in headers]
    n_rows = len(rows)
    n_cols = len(headers)
    cursor_row, cursor_col = 0, 1  # default to first editable cell (start_local)
    message = ""
    edited = set()
    def tui(stdscr):
        nonlocal cursor_row, cursor_col, message
        curses.curs_set(0)
        while True:
            stdscr.clear()
            stdscr.addstr(0, 0, "Edit Mode: Arrow keys to move, e/Enter to edit, s to save, q to quit")
            if message:
                stdscr.addstr(1, 0, message)
            # Print headers
            x = 0
            for ci, h in enumerate(headers):
                stdscr.addstr(3, x, h.ljust(col_widths[ci]))
                x += col_widths[ci] + 2
            # Print rows
            for ri, row in enumerate(rows):
                x = 0
                for ci, h in enumerate(headers):
                    cell = str(row[h]).ljust(col_widths[ci])
                    if ri == cursor_row and ci == cursor_col:
                        stdscr.attron(curses.A_REVERSE)
                        stdscr.addstr(4+ri, x, cell)
                        stdscr.attroff(curses.A_REVERSE)
                    else:
                        stdscr.addstr(4+ri, x, cell)
                    x += col_widths[ci] + 2
            stdscr.refresh()
            key = stdscr.getch()
            message = ""
            if key == ord('q'):
                break
            elif key == ord('s'):
                # Save all edited rows
                for ri, row in enumerate(rows):
                    if ri in edited:
                        day = row['date']
                        if isinstance(day, str):
                            from datetime import date
                            day = date.fromisoformat(day)
                        try:
                            s = parse_hhmm(row['start_local'][-5:])
                            s_local = local_dt_from_date_and_hhmm(day, s)
                            s_utc = utc_iso(s_local)
                        except Exception:
                            s_utc = None
                        try:
                            e = parse_hhmm(row['end_local'][-5:]) if row['end_local'].strip() else None
                            e_local = local_dt_from_date_and_hhmm(day, e) if e else None
                            e_utc = utc_iso(e_local) if e_local else None
                        except Exception:
                            e_utc = None
                        upsert_entry(conn, day, s_utc, e_utc, LOCAL_TZ_NAME_DEFAULT, row.get('notes'))
                message = f"Saved {len(edited)} row(s)."
                break
            elif key == curses.KEY_UP:
                cursor_row = max(0, cursor_row - 1)
            elif key == curses.KEY_DOWN:
                cursor_row = min(n_rows - 1, cursor_row + 1)
            elif key == curses.KEY_LEFT:
                cursor_col = max(1, cursor_col - 1)
            elif key == curses.KEY_RIGHT:
                cursor_col = min(n_cols - 1, cursor_col + 1)
            elif key in (ord('e'), 10, 13):
                # Only allow editing start/end
                if cursor_col in (1, 2):
                    max_y, _ = stdscr.getmaxyx()
                    prompt_line = max_y - 2
                    stdscr.move(prompt_line, 0)
                    stdscr.clrtoeol()
                    stdscr.addstr(prompt_line, 0, f"Enter new time for {headers[cursor_col]} (HH:MM): ")
                    stdscr.refresh()
                    curses.echo()
                    new_val = stdscr.getstr(prompt_line, len(f"Enter new time for {headers[cursor_col]} (HH:MM): ")).decode().strip()
                    curses.noecho()
                    try:
                        if new_val:
                            parse_hhmm(new_val)
                            rows[cursor_row][headers[cursor_col]] = rows[cursor_row][headers[cursor_col]][:11] + new_val
                            edited.add(cursor_row)
                            message = f"Edited {headers[cursor_col]} for row {cursor_row+1}."
                        else:
                            message = "Input cancelled."
                    except Exception:
                        message = "Invalid time format. Use HH:MM."
    curses.wrapper(tui)

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

    # start
    sp = sub.add_parser("start", help="Start today's entry now or at a specified time.")
    sp.add_argument("--date", help="Local date YYYY-MM-DD (default: today)")
    sp.add_argument("--time", type=parse_hhmm, help="Optional start time like HH:MM, HHMM, 6a, or 6am")
    sp.add_argument("--notes", help="Optional notes")
    sp.set_defaults(func=cmd_start)

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

    # averages
    sp = sub.add_parser("averages", help="Show average work hours for a range.")
    sp.add_argument("--range", default="this-month", help="today|this-week|this-month|this-year|last-month|YYYY-MM|YYYY-MM-DD:YYYY-MM-DD")
    sp.add_argument("--date", help="Anchor date for relative ranges (default: today)")
    sp.set_defaults(func=cmd_averages)

    # export
    sp = sub.add_parser("export", help="Export entries to CSV for a range.")
    sp.add_argument("--range", default="this-month", help="today|this-week|this-month|this-year|last-month|YYYY-MM|YYYY-MM-DD:YYYY-MM-DD")
    sp.add_argument("--date", help="Anchor date for relative ranges (default: today)")
    sp.add_argument("--out", help="Output CSV file path or directory. If omitted or a directory, a default name is used in the current directory.")
    sp.set_defaults(func=cmd_export)

    # view-db
    sp = sub.add_parser("view-db", help="Show DB path and basic stats.")
    sp.set_defaults(func=cmd_view_db)

    # edit
    sp = sub.add_parser("edit", help="Visually edit entries for a range (TUI).")
    sp.add_argument("--range", default="this-week", help="today|this-week|last-week|this-month|last-month|this-year|last-year|YYYY-MM|YYYY-MM-DD:YYYY-MM-DD")
    sp.add_argument("--date", help="Anchor date for relative ranges (default: today)")
    sp.set_defaults(func=cmd_edit)

    return p

def main():
    parser = build_parser()
    import sys
    argv = sys.argv[1:]
    # If no subcommand is present, default to 'show' while preserving global flags.
    # Preserve top-level help (-h/--help) behavior.
    if not any(flag in argv for flag in ("-h", "--help")):
        subcommands = {"end", "start", "set-start", "set-end", "show", "totals", "averages", "export", "view-db", "edit"}
        if not any(arg in subcommands for arg in argv):
            insert_at = 0
            while insert_at < len(argv):
                arg = argv[insert_at]
                if arg == "--db":
                    insert_at += 2
                    continue
                if arg.startswith("--db="):
                    insert_at += 1
                    continue
                if arg.startswith("-"):
                    insert_at += 1
                    continue
                break
            argv = argv[:insert_at] + ["show"] + argv[insert_at:]
    args = parser.parse_args(argv)
    conn = get_conn(args.db)
    try:
        # Each subparser sets func=...
        args.func(conn, args)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
