#!/usr/bin/env python3
"""The agent-facing wrapper around tklr. Named flags in, plain English out.

This is the ONLY thing an agent should run for calendars, reminders and alerts.
Do not call `tklr` directly. The agent supplies meaning; this script produces
tklr's grammar, checks that the grammar did what was intended, and reports what
actually happened.

Why a wrapper exists at all: tklr's entry syntax is sigil-dense and nearly
every element has a SILENT failure mode. A missing itemtype character stores a
draft that never fires. `tomorrow 3p` is rejected but `tomorrow 3pm` is fine.
A missing `@a` means nobody is ever notified. `@b` is written leaf-first. An
alert whose trigger lands in the current minute is dropped with no warning.
`tklr add` prints "Added 0 entries successfully" and looks like success.
Encoding all of that once, here, turns silent wrong answers into loud ones:
a bad flag is rejected by argparse, where a bad sigil becomes a broken record.

Every operation is a subcommand. Run `--help` on any of them for its flags:

    add       create a reminder            list      what is scheduled
    show      everything about one         find      search, or one person's
    free      what is around a time        done      mark a task complete
    delete    remove one or an occurrence  move      reschedule an occurrence
    channels  list/configure alert routes  status    is it all set up
    setup     build the whole delivery     welcome   what to tell the user
              path for one platform                  (send its output as-is)

Typical use:

    tklr_agent_wrapper.py add --type event --subject "Dentist" \
        --when "tomorrow 3pm" --duration 1h --for alex --alert 1d,1h --via r
    tklr_agent_wrapper.py add --type task --subject "Buy milk" --for alex
    tklr_agent_wrapper.py list --today
    tklr_agent_wrapper.py find --person alex
    tklr_agent_wrapper.py status

What it does that raw tklr does not:
  * resolves --when itself ("tomorrow 3pm", "next tuesday 9am", "in 2 hours"),
    so callers never depend on tklr's narrower parser
  * refuses a --via letter not defined in the workspace [alerts] section
  * refuses an alert whose trigger is under 2 minutes away, because tklr would
    silently schedule nothing at all
  * warns when a timed reminder has no alert — it would notify nobody
  * validates with `tklr check` BEFORE writing anything
  * reads the output of `tklr add` instead of assuming it worked
  * confirms the stored record is not a draft, then heals derived state
  * VERIFIES afterwards that the reminder really is on the schedule and that
    its alert row exists — "saved" and "will actually fire" are not the same
    thing, and the gap between them is where every past failure has lived
  * reports the id, the entry as stored, and when each alert will fire

Workspace: --home, else $TKLR_HOME, else ~/.config/tklr. Only pass --home for
a non-default workspace.

Exit codes: 0 success, 1 refused or failed, 2 usage error.
Alerts are delivered separately, by ~/.hermes/scripts/tklr_alert_poller.py
running once a minute from Hermes cron. This script never sends anything.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import date, datetime, timedelta
from pathlib import Path

ITEMTYPE = {
    "event": "*",
    "task": "~",
    "project": "^",
    "note": "%",
    "goal": "!",
}

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

warnings: list[str] = []


def die(msg: str, *extra: str, code: int = 1) -> "NoReturn":  # type: ignore[valid-type]
    # Flush first: progress goes to stdout and errors to stderr, and a reader
    # that merges the two streams otherwise sees the error before the steps
    # that led to it.
    sys.stdout.flush()
    print(f"error: {msg}", file=sys.stderr)
    for line in extra:
        print(f"  {line}", file=sys.stderr)
    raise SystemExit(code)


# ---------------------------------------------------------------------------
# datetime resolution — the whole point of the wrapper
# ---------------------------------------------------------------------------

def parse_time(text: str) -> tuple[int, int] | None:
    """'3pm' | '3:30pm' | '15:00' | '9a' | 'noon' -> (hour, minute)."""
    t = text.strip().lower().replace(".", "")
    if t in ("noon", "midday"):
        return 12, 0
    if t == "midnight":
        return 0, 0
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)?", t)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    suffix = m.group(3)
    if suffix in ("pm", "p"):
        if hour != 12:
            hour += 12
    elif suffix in ("am", "a"):
        if hour == 12:
            hour = 0
    elif hour > 23:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def resolve_when(text: str, now: datetime) -> tuple[str, bool]:
    """Return (tklr-safe datetime string, has_time).

    Always emits 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM', which tklr accepts
    unambiguously — so the caller can write whatever a person would say.
    """
    raw = " ".join(text.strip().split())
    low = raw.lower()

    # in N units
    m = re.fullmatch(r"in\s+(\d+)\s*(minute|minutes|min|mins|hour|hours|hr|hrs|day|days|week|weeks)", low)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {
            "minute": timedelta(minutes=n), "minutes": timedelta(minutes=n),
            "min": timedelta(minutes=n), "mins": timedelta(minutes=n),
            "hour": timedelta(hours=n), "hours": timedelta(hours=n),
            "hr": timedelta(hours=n), "hrs": timedelta(hours=n),
            "day": timedelta(days=n), "days": timedelta(days=n),
            "week": timedelta(weeks=n), "weeks": timedelta(weeks=n),
        }[unit]
        target = now + delta
        return target.strftime("%Y-%m-%d %H:%M"), True

    # already absolute: YYYY-MM-DD [time]
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:[ tT]+(.+))?", raw)
    if m:
        day_part, time_part = m.group(1), m.group(2)
        if not time_part:
            return day_part, False
        hm = parse_time(time_part)
        if hm is None:
            die(f"could not understand the time in --when {text!r}",
                "Try '2026-08-01 15:00' or '2026-08-01 3pm'.")
        return f"{day_part} {hm[0]:02d}:{hm[1]:02d}", True

    # split a trailing/leading time off the rest
    tokens = low.split()
    time_hm: tuple[int, int] | None = None
    day_tokens: list[str] = []
    for tok in tokens:
        if tok in ("at", "on", "this", "next", "the"):
            continue
        hm = parse_time(tok)
        if hm is not None and time_hm is None and not re.fullmatch(r"\d{1,2}", tok):
            time_hm = hm
            continue
        day_tokens.append(tok)

    # a bare number could be a day-of-month or an hour; prefer hour if alone
    if time_hm is None and len(day_tokens) == 1 and re.fullmatch(r"\d{1,2}", day_tokens[0]):
        hm = parse_time(day_tokens[0])
        if hm:
            time_hm, day_tokens = hm, []

    target_day: date | None = None
    rest = " ".join(day_tokens).strip()

    if rest in ("", "today"):
        target_day = now.date()
    elif rest == "tomorrow":
        target_day = now.date() + timedelta(days=1)
    elif rest == "yesterday":
        target_day = now.date() - timedelta(days=1)
    elif rest in WEEKDAYS:
        ahead = (WEEKDAYS[rest] - now.weekday()) % 7
        if ahead == 0:
            ahead = 7  # "friday" on a Friday means the next one
        target_day = now.date() + timedelta(days=ahead)
    else:
        # "aug 15", "15 aug", "8/15", "8/15/2026"
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", rest)
        if m:
            mo, dy = int(m.group(1)), int(m.group(2))
            yr = int(m.group(3) or now.year)
            if yr < 100:
                yr += 2000
            target_day = safe_date(yr, mo, dy, text)
        else:
            m = re.fullmatch(r"([a-z]{3,9})\s+(\d{1,2})(?:,?\s*(\d{4}))?", rest) \
                or re.fullmatch(r"(\d{1,2})\s+([a-z]{3,9})(?:,?\s*(\d{4}))?", rest)
            if m:
                a, b = m.group(1), m.group(2)
                name, dnum = (a, b) if a[:3] in MONTHS else (b, a)
                if name[:3] not in MONTHS:
                    target_day = None
                else:
                    yr = int(m.group(3) or now.year)
                    target_day = safe_date(yr, MONTHS[name[:3]], int(dnum), text)

    if target_day is None:
        die(f"could not understand --when {text!r}",
            "Accepted: 'today', 'tomorrow', a weekday ('friday', 'next tuesday'),",
            "'in 2 hours', 'aug 15', '8/15', '2026-08-15', each optionally with a",
            "time ('3pm', '15:00', 'noon'). Or pass an absolute",
            "'YYYY-MM-DD HH:MM'.")

    if time_hm is None:
        return target_day.strftime("%Y-%m-%d"), False

    # A past time with no explicit day almost certainly means tomorrow.
    resolved = datetime.combine(target_day, datetime.min.time()).replace(
        hour=time_hm[0], minute=time_hm[1])
    if rest in ("", "today") and resolved < now:
        resolved += timedelta(days=1)
        warnings.append(
            f"{text!r} had already passed today — used {resolved:%Y-%m-%d %H:%M}")
    return resolved.strftime("%Y-%m-%d %H:%M"), True


def safe_date(y: int, m: int, d: int, text: str) -> date:
    try:
        return date(y, m, d)
    except ValueError:
        die(f"--when {text!r} is not a real date")


# ---------------------------------------------------------------------------
# workspace helpers
# ---------------------------------------------------------------------------

def tklr_home(explicit: str | None) -> Path:
    """Resolve the workspace the way tklr itself does.

    Mirrors tklr_env.TklrEnvironment._resolve_home so the agent and a human
    running `tklr` by hand always land on the same database. Missing the
    XDG_CONFIG_HOME step would silently split them into two workspaces on any
    machine that sets it.
    """
    if explicit:
        return Path(explicit).expanduser()
    env_home = os.environ.get("TKLR_HOME")
    if env_home:
        return Path(env_home).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return (Path(xdg).expanduser() / "tklr")
    return Path.home() / ".config" / "tklr"


def run_tklr(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    exe = shutil.which("tklr") or str(Path.home() / ".local" / "bin" / "tklr")
    try:
        return subprocess.run([exe, "--home", str(home), *args],
                              capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        die("tklr is not installed or not on PATH",
            "Run: tklr_agent_wrapper.py setup --platform <the platform you are on>",
            "(it installs tklr and everything else in one command)")
    except subprocess.SubprocessError as exc:
        die(f"tklr failed to run: {exc}")


def configured_letters(home: Path) -> dict[str, str]:
    cfg = home / "config.toml"
    if not cfg.exists():
        return {}
    try:
        alerts = tomllib.loads(cfg.read_text(encoding="utf-8")).get("alerts") or {}
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return {k: v for k, v in alerts.items() if isinstance(v, str)}


def clean_list(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


# ---------------------------------------------------------------------------
# entry assembly
# ---------------------------------------------------------------------------

def build_entry(args, home: Path, now: datetime) -> tuple[str, str | None, bool]:
    """Return (entry, resolved_when, has_time)."""
    if args.type not in ITEMTYPE:
        die(f"unknown --type {args.type!r}",
            "Use one of: " + ", ".join(sorted(ITEMTYPE)))
    subject = " ".join((args.subject or "").split())
    if not subject:
        die("--subject is required")
    if '"' in subject:
        warnings.append('a double quote in the subject can break alert '
                        'delivery; replaced with a typographic quote')
        subject = subject.replace('"', "”")

    parts = [ITEMTYPE[args.type], subject]

    resolved = has_time = None
    if args.when:
        resolved, has_time = resolve_when(args.when, now)
        stamp = resolved
        if args.timezone:
            tz = args.timezone.strip()
            if not re.fullmatch(r"[A-Za-z_]+(/[A-Za-z_+-]+)*|none|float", tz):
                die(f"--timezone {tz!r} does not look like a zone name",
                    "Use e.g. US/Pacific, Europe/London, or 'none' for a floating time.")
            stamp = f"{resolved} z {tz}"
        parts.append(f"@s {stamp}")
    elif args.type in ("event", "goal"):
        die(f"a {args.type} needs --when")
    elif args.timezone:
        die("--timezone only means something with --when")

    if args.duration:
        if not re.fullmatch(r"(\d+[wdhms])+", args.duration.strip()):
            die(f"--duration {args.duration!r} is not a timeperiod",
                "Use forms like 30m, 1h, 1h30m, 2d.")
        parts.append(f"@e {args.duration.strip()}")

    if args.repeat:
        parts.append(f"@r {args.repeat.strip()}")

    if args.target:
        if not re.fullmatch(r"\d+/\d+[wdhms]", args.target.strip()):
            die(f"--target {args.target!r} must look like 3/1w",
                "That is: how many completions, per how long. The period needs a "
                "number — '3/1w', not '3/w'.")
        parts.append(f"@t {args.target.strip()}")

    for person in clean_list(args.for_whom):
        # tklr writes bins leaf-first, so this is `<person>` inside `users`.
        parts.append(f"@b {person.lower()}/users")

    if args.location:
        parts.append(f"@l {args.location.strip()}")

    if args.priority:
        if args.priority not in range(1, 6):
            die("--priority must be 1 (highest) to 5 (lowest)")
        parts.append(f"@p {args.priority}")

    if args.notice:
        parts.append(f"@n {args.notice.strip()}")

    if args.offset:
        off = args.offset.strip()
        if not re.fullmatch(r"~?(\d+[wdhms])+", off):
            die(f"--offset {off!r} is not a timeperiod",
                "Use e.g. 3d — 'reschedule 3 days after I finish it'. "
                "Prefix with ~ for a learning interval: ~3d.")
        parts.append(f"@o {off}")

    if args.travel:
        legs = clean_list(args.travel)
        if len(legs) == 1:
            legs = legs * 2
        if len(legs) != 2 or not all(re.fullmatch(r"(\d+[wdhms])+", l) for l in legs):
            die(f"--travel {args.travel!r} needs one or two timeperiods",
                "e.g. --travel 30m (both sides) or --travel 30m,15m (before,after).")
        parts.append(f"@w {legs[0]}, {legs[1]}")

    # project steps -> @~ jobs, each needing an &r label (a, b, c…)
    steps = args.step or []
    if steps and args.type != "project":
        die("--step only applies to --type project")
    for index, step in enumerate(steps):
        label = chr(ord("a") + index)
        token = f"@~ {step.strip()} &r {label}"
        if args.chain and index > 0:
            token = f"@~ {step.strip()} &r {label}:{chr(ord('a') + index - 1)}"
        parts.append(token)

    # alerts
    offsets = clean_list(args.alert)
    letters = clean_list(args.via)
    if offsets and not letters:
        die("--alert needs --via to say which channel(s) to use",
            "Available letters: " + (", ".join(sorted(configured_letters(home))) or "none configured"))
    if letters and not offsets:
        offsets = ["1h"] if has_time else ["1d"]
        warnings.append(f"no --alert offset given; used {offsets[0]} before")
    if offsets:
        available = configured_letters(home)
        unknown = [l for l in letters if l not in available]
        if unknown:
            die(f"channel letter(s) not configured: {', '.join(unknown)}",
                "Available: " + (", ".join(sorted(available)) or "none"),
                "Add one with scripts/set_alert_channel.py before using it.")
        for off in offsets:
            if not re.fullmatch(r"-?(\d+[wdhms])+", off):
                die(f"--alert offset {off!r} is not a timeperiod",
                    "Offsets count BACK from the start time: 1d, 2h, 15m.")
        check_alert_margin(offsets, resolved, now, warnings)
        parts.append(f"@a {', '.join(offsets)}: {', '.join(letters)}")
    elif args.type in ("event", "task") and args.when:
        warnings.append("no alert set — this reminder will not notify anyone "
                        "(pass --alert and --via if it should)")

    if args.note:
        # @d must come last: tklr treats the rest of the entry as its value.
        parts.append(f"@d {' '.join(args.note.split())}")

    if not clean_list(args.for_whom):
        warnings.append("no --for given, so this is not attached to anyone")

    return " ".join(parts), resolved, bool(has_time)


# tklr materialises an Alerts row only for a trigger in a LATER CLOCK MINUTE
# than the moment the record is saved. A trigger in the current minute -- or
# the past -- produces no row at all, no warning, and no alert: the reminder
# looks fine in `list` and `show` and simply never fires. Two minutes is the
# smallest margin that survives a save straddling a minute boundary.
MIN_ALERT_MARGIN = timedelta(minutes=2)


def offset_seconds(off: str) -> int:
    """Seconds an @a offset counts back from the start. Negative means after."""
    secs = 0
    for num, unit in re.findall(r"(\d+)([wdhms])", off):
        secs += int(num) * {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return -secs if off.strip().startswith("-") else secs


def alert_fire_time(off: str, start: datetime) -> datetime:
    return start - timedelta(seconds=offset_seconds(off))


def parse_resolved(resolved: str | None) -> datetime | None:
    if not resolved:
        return None
    try:
        return datetime.strptime(resolved, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def stamp(when: datetime, now: datetime) -> str:
    """Clock time alone is a lie for any other day — show the date too."""
    return f"{when:%H:%M}" if when.date() == now.date() else f"{when:%Y-%m-%d %H:%M}"


def check_alert_margin(offsets, resolved, now: datetime, warnings: list) -> None:
    """Refuse an alert that tklr would silently decline to schedule.

    Refuses only when EVERY offset is too soon -- that reminder could never
    notify anyone, which is the whole reason it was asked for. If some offsets
    are fine, the good ones are kept and the doomed ones are called out, since
    '1d, 15m' on a meeting two hours away is a perfectly sensible thing to
    want.
    """
    start = parse_resolved(resolved)
    if not start:
        return

    doomed = []
    for off in offsets:
        fires = alert_fire_time(off, start)
        if fires - now < MIN_ALERT_MARGIN:
            doomed.append((off, fires))
    if not doomed:
        return

    if len(doomed) < len(offsets):
        for off, fires in doomed:
            warnings.append(
                f"alert {off} before would fire at {stamp(fires, now)}, too soon to "
                f"be scheduled — tklr will skip that one; the others still stand")
        return

    off, fires = doomed[0]
    late = now - fires
    detail = (f"{human(late)} in the past" if late.total_seconds() > 0
              else f"only {human(-late)} away")
    die(f"that alert would never fire — it lands at {fires:%Y-%m-%d %H:%M}, {detail}",
        f"start {start:%Y-%m-%d %H:%M} minus {off} = {fires:%H:%M}, and it is now "
        f"{now:%H:%M}.",
        f"tklr only schedules an alert at least {int(MIN_ALERT_MARGIN.total_seconds() // 60)} "
        "minutes out; anything sooner is dropped with no warning.",
        "Either start it later or use a smaller offset — e.g. for a test, "
        "start 8 minutes out with --alert 5m.")


def report_alert_times(entry: str, resolved: str | None, now: datetime) -> None:
    m = re.search(r"@a ([^:]+):", entry)
    start = parse_resolved(resolved)
    if not (m and start):
        return
    for off in [o.strip() for o in m.group(1).split(",")]:
        fires = alert_fire_time(off, start)
        delta = fires - now
        when = ("in " + human(delta)) if delta.total_seconds() > 0 else (human(-delta) + " ago")
        print(f"  alert ({off} before) fires {fires:%Y-%m-%d %H:%M} — {when}")


def human(delta: timedelta) -> str:
    mins = int(delta.total_seconds() // 60)
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''}"
    if mins < 60 * 48:
        h, m = divmod(mins, 60)
        return f"{h} hour{'s' if h != 1 else ''}" + (f" {m} min" if m else "")
    return f"{mins // 1440} day{'s' if mins // 1440 != 1 else ''}"


# ---------------------------------------------------------------------------
# reads — plain output, no tklr syntax exposed
# ---------------------------------------------------------------------------

SKILL_SCRIPTS = Path(__file__).resolve().parent


def show_output(proc: subprocess.CompletedProcess[str]) -> None:
    """Print tklr output minus its internal chatter."""
    for line in (proc.stdout or "").splitlines():
        if "aggregate" in line or "DateTimes entries" in line:
            continue
        print(line.rstrip())


def cmd_list(args, home: Path, now: datetime) -> int:
    if args.date:
        start, _ = resolve_when(args.date, now)
        start = start.split()[0]
        span = str(args.days or 1)
    elif args.week:
        start, span = "today", "7"
    elif args.tomorrow:
        start = (now.date() + timedelta(days=1)).strftime("%Y-%m-%d")
        span = "1"
    elif args.today:
        start, span = "today", "1"
    else:
        show_output(run_tklr(home, "agenda", "--plain", "--ids"))
        return 0
    show_output(run_tklr(home, "days", "--start", start, "--end", span, "--plain", "--ids"))
    return 0


def cmd_show(args, home: Path, now: datetime) -> int:
    show_output(run_tklr(home, "details", str(args.id)))
    return 0


def cmd_find(args, home: Path, now: datetime) -> int:
    if args.person:
        show_output(run_tklr(home, "query", f"in b ^{re.escape(args.person)}$", "--ids"))
    else:
        show_output(run_tklr(home, "find", args.text))
    return 0


def cmd_free(args, home: Path, now: datetime) -> int:
    when, has_time = resolve_when(args.when, now)
    day = when.split()[0]
    print(f"Everything on {day} — compare against it, and mind durations "
          f"and travel time:")
    show_output(run_tklr(home, "days", "--start", day, "--end", "1", "--plain", "--ids"))
    return 0


def cmd_done(args, home: Path, now: datetime) -> int:
    proc = run_tklr(home, "finish", str(args.id), "-y")
    out = (proc.stdout or "") + (proc.stderr or "")
    if "No changes made" in out:
        die(f"id {args.id} could not be completed.",
            "Only tasks can be finished. If it is an appointment, delete it "
            f"instead:  {sys.argv[0]} delete {args.id}")
    show_output(proc)
    return 0


def delegate(script: str, argv: list[str], home: Path) -> int:
    """Run a sibling helper, passing its output through minus tklr's chatter."""
    proc = subprocess.run(
        [sys.executable, str(SKILL_SCRIPTS / script), "--home", str(home), *argv],
        capture_output=True, text=True)
    for stream, sink in ((proc.stdout, sys.stdout), (proc.stderr, sys.stderr)):
        for line in (stream or "").splitlines():
            if "No data to aggregate" in line or "No event DateTimes entries" in line:
                continue
            print(line.rstrip(), file=sink)
    return proc.returncode


def cmd_delete(args, home: Path, now: datetime) -> int:
    extra = []
    if args.instance:
        extra += ["--instance", args.instance]
    if args.from_dt:
        extra += ["--from", args.from_dt]
    if args.dry_run:
        extra.append("--dry-run")
    return delegate("tklr_mutate.py", ["delete", str(args.id), *extra], home)


def cmd_move(args, home: Path, now: datetime) -> int:
    to_when, _ = resolve_when(args.to, now)
    extra = ["--instance", args.instance, "--to", to_when]
    if args.dry_run:
        extra.append("--dry-run")
    return delegate("tklr_mutate.py", ["reschedule", str(args.id), *extra], home)


def cmd_channels(args, home: Path, now: datetime) -> int:
    if args.set:
        if len(args.set) != 2:
            die("--set takes a letter and a command", code=2)
        return delegate("set_alert_channel.py", args.set, home)
    if args.remove:
        return delegate("set_alert_channel.py", ["--remove", args.remove], home)
    return delegate("set_alert_channel.py", ["--list"], home)


ALERT_TEMPLATE = ('--quiet "⏰ Reminder: {name} — starts {when} ({start}). '
                  '{description}"')


_send_list_cache: dict[str | None, str] = {}


def send_list(platform: str | None = None) -> str:
    """`hermes send --list`, optionally filtered to one platform.

    Cached per process: `setup` consults it three times and each call is a
    subprocess with a 60s timeout.
    """
    if platform in _send_list_cache:
        return _send_list_cache[platform]
    cmd = ["hermes", "send", "--list"] + ([platform] if platform else [])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        die(f"could not run `{' '.join(cmd)}`: {exc}")
    if proc.returncode != 0:
        die(f"`{' '.join(cmd)}` failed", (proc.stderr or "").strip())
    _send_list_cache[platform] = proc.stdout or ""
    return _send_list_cache[platform]


def platform_targets(platform: str) -> list[str]:
    """Every `platform:id` target `hermes send --list` reports for a platform."""
    out = send_list(platform)
    seen, targets = set(), []
    for match in re.findall(rf"\b{re.escape(platform)}:\S+", out):
        target = match.rstrip(".,")
        if target not in seen:
            seen.add(target)
            targets.append(target)
    return targets


CRON_JOB_NAME = "tklr-alert-poller"
POLLER = Path.home() / ".hermes" / "scripts" / "tklr_alert_poller.py"


def run_installer(home: Path) -> None:
    """Run install.sh and report it in one line, or die with its full output.

    Folded in so the whole setup is ONE tool call. It used to be a separate
    step, and separate steps are where this gets lost: a run on 2026-08-07
    called install.sh, received 5,356 characters back -- 28 lines of uv package
    names, then a block of instructions -- narrated "let me check a few things",
    emitted no further tool call, and the turn simply ended. Nothing was
    configured. One command cannot be abandoned halfway.
    """
    script = SKILL_SCRIPTS / "install.sh"
    if not script.is_file():
        die(f"install.sh is missing from {SKILL_SCRIPTS}")
    try:
        proc = subprocess.run(["bash", str(script), "--home", str(home)],
                              capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        die(f"could not run install.sh: {exc}")

    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        print(out, file=sys.stderr)
        die("install.sh failed — see its output above.",
            "tklr itself is not usable, so nothing further will work.")

    version = ""
    for line in out.splitlines():
        if "installed —" in line or "already installed —" in line:
            version = line.split("—", 1)[1].strip()
    print(f"tklr: ready{f' ({version})' if version else ''}")


def cron_job_present() -> bool | None:
    """True/False if `hermes cron list` could be read, None if it could not."""
    try:
        proc = subprocess.run(["hermes", "cron", "list"], capture_output=True,
                              text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return CRON_JOB_NAME in (proc.stdout or "")


def ensure_dispatcher() -> bool:
    """Copy the poller into ~/.hermes/scripts/, where cron can reach it.

    The scheduler refuses any script path outside that directory -- absolute
    paths, `../` and symlinks are all rejected -- so the skill's own copy can
    never be scheduled directly.
    """
    source = SKILL_SCRIPTS / "tklr_alert_poller.py"
    if not source.is_file():
        return False
    try:
        POLLER.parent.mkdir(parents=True, exist_ok=True)
        if not POLLER.exists() or POLLER.read_bytes() != source.read_bytes():
            shutil.copy2(source, POLLER)
            POLLER.chmod(0o755)
        return True
    except OSError:
        return False


def ensure_cron_job() -> tuple[bool, str]:
    """Create the every-minute dispatcher job if it is missing.

    Folded into `setup` rather than left as an instruction because it is the
    one step with no visible symptom when skipped: letters validate, reminders
    save, `add` reports the alert is scheduled -- and nothing is ever
    delivered, because nothing is running to deliver it. Every setup that has
    silently produced no alerts got this far and no further.
    """
    if not ensure_dispatcher():
        return False, f"could not install the dispatcher into {POLLER.parent}"

    present = cron_job_present()
    if present is None:
        return False, ("could not read `hermes cron list` -- create the job by "
                       "hand and confirm it, or nothing will ever be delivered")
    if present:
        return True, f"cron job '{CRON_JOB_NAME}' already present"

    # --script takes the BARE FILENAME: the scheduler resolves it inside
    # ~/.hermes/scripts/ and rejects anything that escapes that directory.
    try:
        proc = subprocess.run(
            ["hermes", "cron", "create", "* * * * *",
             "--script", "tklr_alert_poller.py", "--no-agent",
             "--name", CRON_JOB_NAME, "--deliver", "local"],
            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not run `hermes cron create`: {exc}"
    if proc.returncode != 0:
        return False, f"`hermes cron create` failed: {(proc.stderr or '').strip()}"

    if cron_job_present():
        return True, f"created cron job '{CRON_JOB_NAME}' — dispatching every minute"
    return False, ("`hermes cron create` reported success but the job is not in "
                   "`hermes cron list`")


def cmd_setup(args, home: Path, now: datetime) -> int:
    """Configure one platform as an alert channel, start to finish.

    The agent knows exactly one thing for certain about delivery: which
    platform the user is talking to it on. This turns that one fact into a
    working channel without asking the user anything, which is the point --
    every version of this flow that asked "where would you like reminders?"
    ended up proposing a dead platform that happened to sort first in
    `hermes send --list`.
    """
    platform = args.platform.strip().lower().rstrip(":")
    if not platform:
        die("--platform needs a name, e.g. --platform telegram", code=2)

    known = {p.lower() for p in re.findall(r"^\s*([A-Za-z][\w-]*):\s*$",
                                           send_list(), re.M)}
    known |= {t.split(":", 1)[0].lower()
              for t in re.findall(r"\b\w+:\S+", send_list())}
    if known and platform not in known:
        die(f"'{platform}' is not a messaging platform on this machine.",
            f"configured platforms: {', '.join(sorted(known))}",
            "pass the platform this conversation is on.")

    if args.target:
        target = args.target
    else:
        targets = platform_targets(platform)
        if not targets:
            die(f"'{platform}' has no targets in `hermes send --list`.",
                "it may be configured but not connected. Ask the user which",
                "channel to use instead, or pass --target explicitly.")
        if len(targets) > 1:
            die(f"'{platform}' has {len(targets)} targets — pick one and pass "
                "it as --target:",
                *(f"  {t}" for t in targets))
        target = targets[0]

    # Only now, once the destination is known to be valid: a typo in --platform
    # should not trigger a package install before it is reported.
    run_installer(home)

    command = f"hermes send --to {target} {ALERT_TEMPLATE}"
    rc = delegate("set_alert_channel.py", [args.letter, command], home)
    if rc != 0:
        return rc
    print(f"\nalert channel '{args.letter}' delivers to {target}.")

    ok, note = ensure_cron_job()
    print(f"dispatcher: {note}")
    if not ok:
        die("the channel is configured but NOTHING WILL BE DELIVERED.",
            "A reminder will save, validate, and report its alert as scheduled;",
            "no alert will ever arrive, because no job is running to send it.",
            "Fix the dispatcher before telling the user anything works.")

    if args.no_test:
        print("\n(--no-test: skipped the delivery test. Nothing has proven that "
              "an alert\ncan actually reach the user.)")
        return 0

    rc = create_test_alert(args.letter, home, now)
    if rc != 0:
        die("the channel and cron job are configured, but the delivery test "
            "could not be created.",
            "Do not tell the user setup is complete — nothing has proven an "
            "alert can reach them.")
    print("Tell the user where their reminders will arrive — do not ask them.")
    return 0


def create_test_alert(letter: str, home: Path, _now: datetime) -> int:
    """Create a reminder whose alert fires in a little over two minutes.

    Setup's own proof of delivery, created here rather than left as an
    instruction because it is the step that gets skipped -- and skipping it is
    invisible, since every other part of the chain reports healthy while
    sending nothing. It also takes the agent out of the verification loop
    entirely: the alert arrives on the user's device whether or not the agent
    remembers to mention it.

    The trigger is computed from a whole minute boundary, not from now: tklr
    stores times to the minute, so an unrounded `now + 2m` truncates to as
    little as 1m01s away and `check_alert_margin` refuses it outright.

    The boundary is taken from now + SPAWN_SLACK rather than from now. The
    margin is re-checked by the `add` SUBPROCESS, milliseconds later, against
    its own clock -- so anchoring on `now` exactly gives a worst case of
    exactly MIN_ALERT_MARGIN (when now lands on a minute boundary), which any
    spawn delay at all pushes under. The slack makes the trigger 2:05-3:05
    away, always clear of the 2:00 floor.

    The clock is re-read here rather than taken from the caller: `setup` runs
    the installer and `hermes cron` first, each with a long timeout, so the
    timestamp from program start can be minutes stale by the time we arrive.
    """
    offset_min = 2
    SPAWN_SLACK = timedelta(seconds=5)
    now = datetime.now()
    anchor = now + SPAWN_SLACK
    next_minute = anchor.replace(second=0, microsecond=0)
    if anchor != next_minute:                    # already on the boundary? keep it
        next_minute += timedelta(minutes=1)
    fires = next_minute + timedelta(minutes=offset_min)
    start = fires + timedelta(minutes=offset_min)

    print(f"\ndelivery test: alert fires {fires:%H:%M} "
          f"(in {human(fires - now)}), delivered within a minute of that.")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--home", str(home),
         "add", "--type", "event", "--subject", "tklr delivery test",
         "--when", f"{start:%Y-%m-%d %H:%M}", "--duration", "5m",
         "--alert", f"{offset_min}m", "--via", letter],
        capture_output=True, text=True)
    for line in (proc.stdout or "").splitlines():
        print(f"  {line}")
    for line in (proc.stderr or "").splitlines():
        print(f"  {line}", file=sys.stderr)
    if proc.returncode != 0:
        return proc.returncode

    print("\nNow WAIT for it to arrive, then ask the user whether it did.")
    print("That is the only proof this works, and the one thing you cannot "
          "check yourself.")
    return 0


def describe_channels(letters: dict[str, str]) -> list[str]:
    """Plain-English destinations, derived from the configured letters.

    `welcome` promises only what exists. A blurb that offers email on a
    workspace with no email letter is a promise the skill cannot keep.
    """
    out: list[str] = []
    for letter in sorted(letters):
        command = letters[letter]
        target = re.search(r"--to\s+[\"']?(\S+?)[\"']?(?:\s|$)", command)
        if "himalaya" in command:
            addr = re.search(r"To:\s*([^\s\\\"]+@[^\s\\\"]+)", command)
            out.append(f"email{f' at {addr.group(1)}' if addr else ''}")
        elif "notify-send" in command:
            out.append("a desktop notification on this machine")
        elif target:
            platform = target.group(1).split(":", 1)[0]
            out.append(f"{platform.capitalize()}")
        else:
            # A letter whose command is none of the three known shapes -- a
            # custom script, say. Still a real destination; describe it
            # vaguely rather than dropping it, because dropping every letter
            # leaves nothing to promise and used to crash on channels[0].
            out.append("the channel you set up")
    seen, unique = set(), []
    for item in out:
        if item.lower() not in seen:
            seen.add(item.lower())
            unique.append(item)
    return unique


WELCOME = """\
You're all set — just talk to me normally about anything time-related.

**Appointments and events.** "Dentist Friday at 3 for an hour." "Coffee with
Sam tomorrow at 11:30." All-day things work too — "{who}'s birthday on August
15th" — as do repeating ones: "standup every weekday at 9", "1:1 with Dana
every other Tuesday", "pay the mortgage on the 1st of each month". I can note a
location, and hold travel time either side of a meeting.

**Things to do.** "Remind me to buy milk" for something with no fixed time, or
with a deadline and a priority: "renew my passport by September 1st, it's
important — start warning me a month out." Bigger jobs can have steps I track
together — "plan the Colorado trip: flights, hotel, dog sitter" — and I can
keep habits honest too: "I want to exercise three times a week."

**Asking me things.** "What's on my calendar today?" "What about tomorrow?"
"How's my week looking?" "What do I need to get done?" "When's my next dentist
appointment?" "Am I free Tuesday at 3 for a coffee date?" — for that last one
I'll check what's around it, not just the slot itself.

**How you get reminded.** Alerts reach you on {channels}. You can have several
per event at different times — "remind me a day before and again an hour
before" — and I'll pick sensible ones if you don't say.

**Changing and finishing things.** "I've done that" marks a task complete.
"Cancel Friday's meeting", "move the dentist to Thursday afternoon", "skip next
week's standup but keep the rest" all work too. To change any other detail I'll
replace the entry and tell you that's what I did.
"""

TEST_LINE = ("\nI've added a test reminder that should reach you shortly — tell "
             "me whether it arrives, since that's the one part I can't check "
             "myself.\n")


def cmd_welcome(args, home: Path, now: datetime) -> int:
    """Print the user-facing description of this skill, ready to send as-is.

    This exists because the description is the single thing the agent gets
    wrong most reliably. Asked to explain the skill, a model reaches for the
    nearest example in its context -- which is a wrapper invocation -- and
    hands the user a command cheat sheet, teaching them the traps the wrapper
    exists to hide. Generated text cannot be trusted here, so it is not
    generated: it is printed, and the agent's only job is to relay it.
    """
    letters = configured_letters(home)
    if not letters:
        die("no alert channels are configured, so there is nothing to promise.",
            "run `setup --platform <the platform you are on>` first.")
    channels = describe_channels(letters)
    if len(channels) > 1:
        channels_text = ", ".join(channels[:-1]) + f" and {channels[-1]}"
    else:
        channels_text = channels[0]
    text = WELCOME.format(who=args.who or "Jordan", channels=channels_text)
    if not args.no_test:
        text += TEST_LINE
    # Re-wrap per paragraph: the channel list is variable-length, so the
    # template's own line breaks land wherever. Chat clients re-wrap anyway;
    # this keeps the plain-text form readable when they don't.
    import textwrap
    print("\n\n".join(
        textwrap.fill(" ".join(para.split()), width=78)
        for para in text.strip().split("\n\n")))
    return 0


def cmd_status(args, home: Path, now: datetime) -> int:
    print(f"workspace: {home}")
    letters = configured_letters(home)
    print(f"channels:  {', '.join(sorted(letters)) if letters else 'NONE — alerts cannot be created'}")
    poller = POLLER
    source = SKILL_SCRIPTS / "tklr_alert_poller.py"
    # Drift matters more than it looks. The deployed copy is what cron runs, and
    # an older one silently ignores flags it does not know -- a `--check` sent to
    # a pre-`--check` poller performs a FULL DISPATCH, so the read-only status
    # command would send and delete the very alert being inspected.
    stale = (poller.exists() and source.is_file()
             and poller.read_bytes() != source.read_bytes())
    print(f"dispatcher: {'installed' if poller.exists() else 'MISSING — run setup'}"
          + (" — OUT OF DATE vs the skill; run setup to refresh" if stale else ""))
    # The cron job is the only part of the chain with no symptom when absent:
    # everything else reports healthy and no alert is ever sent.
    cron = cron_job_present()
    print("cron job:  " + {
        True: f"'{CRON_JOB_NAME}' scheduled",
        False: f"MISSING — NOTHING WILL BE DELIVERED. Run: setup --platform <platform>",
        None: "could not read `hermes cron list` — verify by hand",
    }[cron])
    if poller.exists() and stale:
        print("  (not running it: an out-of-date poller ignores --check and would")
        print("   dispatch for real, sending and deleting any alert now due)")
    elif poller.exists():
        proc = subprocess.run([sys.executable, str(poller), "--check"],
                              capture_output=True, text=True,
                              env=dict(os.environ, TKLR_HOME=str(home)), timeout=180)
        for line in (proc.stdout or "").splitlines():
            print(f"  {line}")
    return 0


def cmd_add(args, home: Path, now: datetime) -> int:
    if args.raw:
        entry, resolved = args.raw.strip(), None
        if entry[:1] not in set(ITEMTYPE.values()) | {"-", "?"}:
            die("a raw entry must start with an itemtype character")
    else:
        if not args.type:
            die("--type is required", code=2)
        entry, resolved, _ = build_entry(args, home, now)

    for w in warnings:
        print(f"  note: {w}")

    chk = run_tklr(home, "check", entry)
    if "Entry is valid" not in (chk.stdout or ""):
        detail = [l.strip() for l in (chk.stdout or "").splitlines()
                  if l.strip() and "aggregate" not in l and "DateTimes" not in l]
        die("that reminder could not be created", f"composed: {entry}", *detail[:6])

    if args.dry_run:
        print(f"WOULD create: {entry}")
        report_alert_times(entry, resolved, now)
        print("  (nothing was written)")
        return 0

    add = run_tklr(home, "add", entry)
    out = (add.stdout or "") + (add.stderr or "")
    if "Added 1 entry" not in out:
        detail = [l.rstrip() for l in out.splitlines()
                  if l.strip() and "aggregate" not in l and "DateTimes" not in l]
        die("the reminder was not created", f"composed: {entry}", *detail[:8])

    heal = Path.home() / ".hermes" / "scripts" / "tklr_alert_poller.py"
    heal_failed = ""
    if heal.exists():
        done = subprocess.run([sys.executable, str(heal), "--heal"],
                              capture_output=True, text=True, timeout=180,
                              check=False,
                              env=dict(os.environ, TKLR_HOME=str(home)))
        if done.returncode != 0:
            # A skipped heal is the difference between a reminder that fires
            # and one that silently does not. Never swallow it.
            heal_failed = (done.stdout or done.stderr or "").strip().splitlines()
            heal_failed = heal_failed[-1] if heal_failed else "heal returned non-zero"

    import sqlite3
    try:
        conn = sqlite3.connect(home / "tklr.db", timeout=15)
        row = conn.execute(
            "SELECT id, itemtype, subject FROM Records ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
    except sqlite3.Error:
        row = None
    if row and row[1] == "?":
        die(f"it was stored as a DRAFT (id {row[0]}) and will never fire",
            f"Inspect with: {sys.argv[0]} show {row[0]}")

    print(f"created id {row[0] if row else '?'}: {entry}")
    report_alert_times(entry, resolved, now)
    if row:
        verify_scheduled(home, row[0], entry, resolved, now, heal_failed)
    return 0


def verify_scheduled(home: Path, record_id: int, entry: str, resolved: str | None,
                     now: datetime, heal_failed: str) -> None:
    """Confirm the reminder is really on the schedule, not just in the table.

    'Added 1 entry' means a Records row exists. It does NOT mean the reminder
    will ever appear on a calendar or notify anyone -- that needs a DateTimes
    row, and an alert needs an Alerts row on top of it. Both are derived, both
    have failed silently in practice (see the stale-cache bug and the
    minimum-margin rule), and both are cheap to check. Saying 'created' without
    checking is how a reminder that never fires gets reported as a success.
    """
    import sqlite3
    try:
        conn = sqlite3.connect(home / "tklr.db", timeout=15)
        occurrences = conn.execute(
            "SELECT COUNT(*) FROM DateTimes WHERE record_id = ?", (record_id,)).fetchone()[0]
        alert_rows = conn.execute(
            "SELECT COUNT(*) FROM Alerts WHERE record_id = ?", (record_id,)).fetchone()[0]
        conn.close()
    except sqlite3.Error as exc:
        print(f"  WARNING: could not verify it was scheduled: {exc}")
        return

    wanted_alert = "@a " in entry
    start = parse_resolved(resolved)

    if resolved and not occurrences:
        die(f"id {record_id} was saved but is NOT on the schedule "
            "(no occurrence was generated)",
            heal_failed or "tklr's derived tables are stale.",
            "Fix: python3 ~/.hermes/scripts/tklr_alert_poller.py --heal --verbose",
            f"Then confirm with: {sys.argv[0]} show {record_id}")

    if not wanted_alert:
        return

    # An alert further out than a day may legitimately have no row yet -- tklr
    # only materialises alerts inside its generation horizon. Only insist on a
    # row when the trigger is close enough that one must already exist.
    soonest = None
    m = re.search(r"@a ([^:]+):", entry)
    if m and start:
        fires = [alert_fire_time(o.strip(), start) for o in m.group(1).split(",")]
        soonest = min(fires) if fires else None

    if not alert_rows and (soonest is None or soonest - now < timedelta(days=1)):
        die(f"id {record_id} was saved but NO ALERT was scheduled — nobody will "
            "be notified",
            heal_failed or "The alert row tklr should have generated is missing.",
            "Fix: python3 ~/.hermes/scripts/tklr_alert_poller.py --heal --verbose",
            f"If that does not help, delete it and re-add with the alert further "
            f"out: {sys.argv[0]} delete {record_id}")

    if alert_rows:
        print(f"  verified: on the schedule, {alert_rows} alert"
              f"{'s' if alert_rows != 1 else ''} queued")
    elif wanted_alert:
        print("  verified: on the schedule; alert is beyond tklr's generation "
              "horizon and will be created closer to the time")


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="tklr_agent_wrapper.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "The single interface for calendars, reminders and alerts.\n"
            "\n"
            "Run THIS, never `tklr` itself. It takes named flags and returns plain\n"
            "English. tklr's own syntax is sigil-dense and fails silently — a wrong\n"
            "sigil becomes a record that quietly never fires, where a wrong flag is\n"
            "rejected here immediately.\n"
            "\n"
            "Pick a subcommand below, then run `%(prog)s <subcommand> --help`\n"
            "for its flags."),
        epilog=(
            "examples:\n"
            "  %(prog)s add --type event --subject \"Dentist\" \\\n"
            "      --when \"tomorrow 3pm\" --duration 1h --for alex --alert 1d,1h --via r\n"
            "  %(prog)s add --type task --subject \"Buy milk\" --for alex\n"
            "  %(prog)s add ... --dry-run     show what would happen, write nothing\n"
            "  %(prog)s list --today\n"
            "  %(prog)s find --person alex\n"
            "  %(prog)s status                is everything set up and working\n"
            "\n"
            "alerts:\n"
            "  --alert takes offsets BEFORE the start (1d,1h,15m); --via takes the\n"
            "  channel letters they are delivered on. Both are needed for anyone to\n"
            "  be notified. A trigger less than 2 minutes away is refused, because\n"
            "  tklr would schedule nothing and say nothing.\n"
            "\n"
            "  Delivery itself is not done here — ~/.hermes/scripts/tklr_alert_poller.py\n"
            "  runs every minute from Hermes cron and sends what is due.\n"
            "\n"
            "workspace:\n"
            "  --home, else $TKLR_HOME, else ~/.config/tklr. Only pass --home for a\n"
            "  non-default workspace.\n"
            "\n"
            "exit codes: 0 success, 1 refused or failed, 2 usage error.\n"))
    ap.add_argument("--home", help="tklr workspace (default $TKLR_HOME or ~/.config/tklr)")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="<subcommand>")

    a = sub.add_parser(
        "add", help="create a reminder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Create a reminder from named fields. Nothing is written until it\n"
            "validates, and afterwards it is checked to confirm it really is on\n"
            "the schedule — being saved and being able to fire are different."),
        epilog=(
            "notifying someone:\n"
            "  --alert and --via go together; neither alone notifies anyone.\n"
            "    --alert 1d,1h   fire 1 day and 1 hour BEFORE the start\n"
            "    --via r,e       deliver on channels r and e (see `channels`)\n"
            "  The trigger (start minus offset) must be at least 2 minutes out.\n"
            "  Closer than that, tklr schedules nothing and reports nothing, so\n"
            "  this refuses instead. For a quick test: --when \"in 8 minutes\"\n"
            "  --alert 5m, which fires in 3.\n"
            "\n"
            "recurring:\n"
            "  --repeat \"daily\", \"every weekday\", \"weekly on monday\"\n"
            "\n"
            "projects:\n"
            "  --step \"Book flights\" --step \"Reserve hotel\" --chain\n"
            "  --chain makes each step wait on the previous one.\n"
            "\n"
            "checking first:\n"
            "  --dry-run prints the composed entry and every alert time, and\n"
            "  writes nothing. Use it whenever the request is ambiguous.\n"
            "\n"
            "examples:\n"
            "  %(prog)s --type event --subject \"Dentist\" --when \"tomorrow 3pm\" \\\n"
            "      --duration 1h --for alex --alert 1d,1h --via r\n"
            "  %(prog)s --type task --subject \"Buy milk\" --for alex\n"
            "  %(prog)s --type event --subject \"Standup\" --when \"tomorrow 9am\" \\\n"
            "      --repeat \"every weekday\" --for alex,jordan --alert 10m --via r\n"))
    a.add_argument("--type", choices=sorted(ITEMTYPE),
                   help="event (has a time), task (to do), project, goal, note")
    a.add_argument("--subject", help="what it is, in plain words")
    a.add_argument("--when", help="'tomorrow 3pm', 'friday', 'in 2 hours', '2026-08-15 09:00'")
    a.add_argument("--duration", help="how long it lasts, e.g. 1h, 30m")
    a.add_argument("--for", dest="for_whom", help="comma-separated people, e.g. alex,jordan")
    a.add_argument("--alert", help="offsets BEFORE the start, e.g. 1d,1h,15m (needs --via)")
    a.add_argument("--via", help="channel letters to deliver on, e.g. r,e (needs --alert)")
    a.add_argument("--note", help="free-text detail")
    a.add_argument("--location", help="where")
    a.add_argument("--priority", type=int, help="1 (highest) to 5 (lowest)")
    a.add_argument("--notice", help="how long before it starts to show as pending")
    a.add_argument("--timezone", help="e.g. America/Chicago; default is local")
    a.add_argument("--offset", help="for tasks: reschedule this long after completion, e.g. 3d")
    a.add_argument("--travel", help="travel time, e.g. 30m or 30m,15m (before,after)")
    a.add_argument("--repeat", help="'daily', 'every weekday', 'weekly on monday'")
    a.add_argument("--target", help="for goals: completions per period, e.g. 3/1w")
    a.add_argument("--step", action="append",
                   help="a project step; repeat the flag for each one")
    a.add_argument("--chain", action="store_true",
                   help="each --step waits on the one before it")
    a.add_argument("--raw", help="last resort; see references/tklr-syntax.md")
    a.add_argument("--dry-run", action="store_true",
                   help="show what would be created, write nothing")
    a.set_defaults(fn=cmd_add)

    l = sub.add_parser("list", help="what is scheduled")
    g = l.add_mutually_exclusive_group()
    g.add_argument("--today", action="store_true")
    g.add_argument("--tomorrow", action="store_true")
    g.add_argument("--week", action="store_true")
    g.add_argument("--date", help="a day, e.g. 'friday' or '2026-08-07'")
    l.add_argument("--days", type=int, help="how many days from --date")
    l.set_defaults(fn=cmd_list)

    s = sub.add_parser("show", help="everything about one reminder")
    s.add_argument("id", type=int); s.set_defaults(fn=cmd_show)

    f = sub.add_parser("find", help="search by text, or list one person's items")
    f.add_argument("text", nargs="?", default="")
    f.add_argument("--person"); f.set_defaults(fn=cmd_find)

    fr = sub.add_parser("free", help="what is around a proposed time")
    fr.add_argument("--when", required=True); fr.set_defaults(fn=cmd_free)

    d = sub.add_parser("done", help="mark a task complete")
    d.add_argument("id", type=int); d.set_defaults(fn=cmd_done)

    dl = sub.add_parser("delete", help="remove a reminder or an occurrence")
    dl.add_argument("id", type=int)
    dl.add_argument("--instance"); dl.add_argument("--from", dest="from_dt")
    dl.add_argument("--dry-run", action="store_true"); dl.set_defaults(fn=cmd_delete)

    mv = sub.add_parser("move", help="reschedule one occurrence")
    mv.add_argument("id", type=int)
    mv.add_argument("--instance", required=True); mv.add_argument("--to", required=True)
    mv.add_argument("--dry-run", action="store_true"); mv.set_defaults(fn=cmd_move)

    c = sub.add_parser("channels", help="list or configure alert channels")
    c.add_argument("--set", nargs=2, metavar=("LETTER", "COMMAND"))
    c.add_argument("--remove", metavar="LETTER"); c.set_defaults(fn=cmd_channels)

    st = sub.add_parser("status", help="is everything set up and working")
    st.set_defaults(fn=cmd_status)

    su = sub.add_parser(
        "setup",
        help="configure the platform you are talking on as an alert channel")
    su.add_argument("--platform", required=True,
                    help="the platform THIS conversation is on, e.g. telegram")
    su.add_argument("--letter", default="r", help="channel letter (default r)")
    su.add_argument("--target",
                    help="only if the platform has more than one target")
    su.add_argument("--no-test", action="store_true",
                    help="skip the delivery test (leaves delivery unproven)")
    su.set_defaults(fn=cmd_setup)

    w = sub.add_parser(
        "welcome",
        help="print what to tell the user this does — send its output verbatim")
    w.add_argument("--who", help="another person's name, for the examples")
    w.add_argument("--no-test", action="store_true",
                   help="omit the closing test-reminder line")
    w.set_defaults(fn=cmd_welcome)

    args = ap.parse_args()
    home = tklr_home(args.home)
    # `setup` is the command that CREATES the workspace, so it cannot require
    # one. Everything else does — operating on a missing workspace produces
    # confusing tklr errors rather than an obvious one.
    if args.cmd != "setup" and not (home / "tklr.db").exists():
        die(f"no workspace at {home}",
            "Run: tklr_agent_wrapper.py setup --platform <the platform you are on>")
    return args.fn(args, home, datetime.now())


if __name__ == "__main__":
    sys.exit(main())
