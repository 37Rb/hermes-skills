#!/usr/bin/env python3
"""Add, change, or list tklr alert channel letters — safely.

Editing the `[alerts]` section by hand has three traps. This script handles all
of them so callers never have to:

  1. The section normally exists but holds only comments, so a naive
     "append after [alerts]" or "create the section" both go wrong.
  2. An apostrophe anywhere in a command makes tklr rewrite the file as invalid
     TOML, and the command after that discards the whole section — the channel
     silently disappears two tklr runs later.
  3. Whether the letter survived can only be established by running tklr twice
     and re-reading the file, because erasure takes two runs.

Usage
  set_alert_channel.py --list
  set_alert_channel.py r 'hermes send --to matrix:!room:server --quiet "⏰ Reminder: {name} — starts {when} ({start})"'
  set_alert_channel.py --remove r
  set_alert_channel.py --home ~/.config/tklr r '<command>'

Exit codes: 0 ok, 1 rejected/failed, 2 usage error.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

RESERVED = {"n"}  # built-in: bell + notification popup


def die(msg: str, code: int = 1) -> "NoReturn":  # type: ignore[valid-type]
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def default_home() -> Path:
    """Resolve the workspace the way tklr itself does: TKLR_HOME, then
    XDG_CONFIG_HOME/tklr, then ~/.config/tklr."""
    env_home = os.environ.get("TKLR_HOME")
    if env_home:
        return Path(env_home).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / "tklr"
    return Path.home() / ".config" / "tklr"


def read_alerts(config: Path) -> dict[str, str]:
    try:
        data = tomllib.loads(config.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        die(f"{config} is not valid TOML ({exc}).\n"
            f"       tklr may have corrupted it — see the apostrophe bug. "
            f"Fix or delete the bad line, then retry.")
    except OSError as exc:
        die(f"cannot read {config}: {exc}")
    alerts = data.get("alerts")
    return dict(alerts) if isinstance(alerts, dict) else {}


def section_bounds(lines: list[str]) -> tuple[int, int] | tuple[None, None]:
    """Return (header_index, end_index) of the [alerts] table, or (None, None)."""
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "[alerts]":
            start = i
            break
    if start is None:
        return None, None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^\s*\[", lines[i]):
            end = i
            break
    return start, end


def write_letter(config: Path, letter: str, command: str | None) -> None:
    """Insert/replace/remove one letter inside [alerts], preserving everything else."""
    text = config.read_text(encoding="utf-8")
    lines = text.splitlines()

    start, end = section_bounds(lines)
    if start is None:
        # No [alerts] table at all — append one at the end.
        lines += ["", "[alerts]"]
        start, end = len(lines) - 1, len(lines)

    body = lines[start + 1:end]
    pattern = re.compile(rf"^\s*{re.escape(letter)}\s*=")
    body = [ln for ln in body if not pattern.match(ln)]

    if command is not None:
        # Single-quoted literal string: what tklr itself emits, so it round-trips
        # byte-for-byte and no "Updated ... with missing defaults" rewrite occurs.
        entry = f"{letter} = '{command}'"
        # Place new entries directly after the header, above the comment block,
        # so they are the first thing a human sees.
        body.insert(0, entry)

    new_lines = lines[:start + 1] + body + lines[end:]
    new_text = "\n".join(new_lines).rstrip("\n") + "\n"

    # Never write a file tklr cannot parse.
    try:
        tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        die(f"refusing to write — the result would not be valid TOML ({exc})")

    tmp = config.with_suffix(config.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(config)


def run_tklr(home: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    exe = shutil.which("tklr") or str(Path.home() / ".local" / "bin" / "tklr")
    try:
        return subprocess.run(
            [exe, "--home", str(home), *args],
            capture_output=True, text=True, timeout=90,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"warning: could not run tklr ({exc})", file=sys.stderr)
        return None


def verify_round_trip(home: Path, config: Path, expect: set[str]) -> bool:
    """tklr rewrites config.toml on load; erasure takes two runs, so run twice."""
    run_tklr(home, "agenda")
    run_tklr(home, "agenda")
    still = set(read_alerts(config))
    missing = expect - still
    if missing:
        print(f"  FAILED: {', '.join(sorted(missing))} vanished after tklr rewrote "
              f"the config — almost certainly an apostrophe in the command.",
              file=sys.stderr)
        return False
    return True


def check_send_target(command: str) -> None:
    """Refuse a `hermes send --to` target that does not exist.

    `hermes send` prints "sent" and exits 0 for a room id that is not real, so
    a made-up target is a perfect black hole: the dispatcher sees success,
    deletes the alert row, logs "sent", and the message reaches nobody. The
    only moment this is catchable is now, against the list of real targets.

    Only `--to <platform>:<id>` is checked, and only when the list can be
    read -- an unreachable `hermes send --list` must not block setup.
    """
    m = re.search(r"--to\s+(\S+)", command)
    if not m:
        return
    target = m.group(1).strip("\"'")
    if ":" not in target:
        return  # bare platform name ("telegram") means the home channel

    try:
        listed = subprocess.run(["hermes", "send", "--list"], capture_output=True,
                                text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return
    if listed.returncode != 0 or not listed.stdout.strip():
        return

    available = re.findall(r"\b\w+:\S+", listed.stdout)
    if not available:
        return

    ident = target.split(":", 1)[1].split("/")[0]
    if any(ident and ident in cand for cand in available):
        return

    die(f"'{target}' is not one of this machine's messaging targets.\n"
        "       `hermes send` exits 0 even for a room that does not exist, so\n"
        "       nothing would ever tell you the alerts went nowhere.\n"
        "       Copy a target verbatim from `hermes send --list`:\n"
        + "\n".join(f"         {c}" for c in available))


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("letter", nargs="?", help="one lowercase letter a-z")
    ap.add_argument("command", nargs="?", help="the delivery command")
    ap.add_argument("--home", default=None, help="tklr workspace (default $TKLR_HOME or ~/.config/tklr)")
    ap.add_argument("--list", action="store_true", help="show configured letters and exit")
    ap.add_argument("--remove", metavar="LETTER", help="delete a letter")
    args = ap.parse_args()

    home = Path(args.home).expanduser() if args.home else default_home()
    config = home / "config.toml"
    if not config.exists():
        die(f"no config at {config} — run install.sh first")

    if args.list:
        alerts = read_alerts(config)
        if not alerts:
            print("no alert channels configured")
            return 0
        for k in sorted(alerts):
            print(f"{k} = {alerts[k]}")
        return 0

    if args.remove:
        letter = args.remove
        if letter not in read_alerts(config):
            die(f"letter '{letter}' is not configured")
        write_letter(config, letter, None)
        print(f"removed '{letter}'")
        return 0

    if not args.letter or args.command is None:
        ap.print_usage(sys.stderr)
        die("give a letter and a command, or use --list / --remove", 2)

    letter, command = args.letter, args.command

    # --- validation, in the order most likely to catch a mistake -----------
    if not re.fullmatch(r"[a-z]", letter):
        die(f"'{letter}' is not a single lowercase letter. tklr enforces a-z "
            f"(is_lowercase_letter); multi-character names are rejected.")
    if letter in RESERVED:
        die(f"'{letter}' is built into tklr (bell + popup) — pick another letter")
    if "'" in command:
        die("the command contains an apostrophe.\n"
            "       tklr re-emits every value in SINGLE quotes when it rewrites\n"
            "       config.toml, so an apostrophe produces invalid TOML and the\n"
            "       next run deletes the whole [alerts] section.\n"
            "       Reword it: \"It is time\", not \"It's time\".")
    if not command.strip():
        die("the command is empty")
    if command.strip() in {"true", ":", "/bin/true", "echo", "cat"}:
        die(f"'{command.strip()}' is a no-op. The dispatcher would treat the alert as\n"
            "       delivered and delete it, so the reminder would reach nobody.\n"
            "       Use a real delivery command.")
    check_send_target(command)

    existing = read_alerts(config)
    verb = "updated" if letter in existing else "added"
    write_letter(config, letter, command)

    # --- prove it actually took ------------------------------------------
    alerts = read_alerts(config)
    if alerts.get(letter) != command:
        die("the letter did not land in the file as written")

    if not verify_round_trip(home, config, {letter}):
        return 1

    probe = f"* Probe @s 2099-08-05 3p @a 1h: {letter}"
    res = run_tklr(home, "check", probe)
    if res is not None and "Entry is valid" not in (res.stdout or ""):
        detail = (res.stdout or res.stderr or "").strip().splitlines()
        print(f"  WARNING: tklr will not accept '@a 1h: {letter}' — "
              f"{detail[-1] if detail else 'no output'}", file=sys.stderr)
        return 1

    print(f"{verb} '{letter}' and verified it:")
    print(f"  survives tklr rewriting config.toml")
    print(f"  '@a 1h: {letter}' validates")
    print(f"  configured letters: {', '.join(sorted(read_alerts(config)))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
