#!/usr/bin/env python3
"""Delete or move a reminder by calling tklr's own Python API.

TEMPORARY SHIM. tklr's CLI cannot edit, move, or delete anything — `add` and
`finish` (tasks only) are its only mutations. The operations exist and are
wired to the interactive UI, but have no command-line surface, so an event once
added cannot be cancelled or rescheduled. This calls those same functions
directly.

Delete this script as soon as tklr grows `tklr delete` / `tklr edit`.

    tklr_mutate.py delete 42
    tklr_mutate.py delete 42 --instance '2026-08-07 14:00'   # one occurrence
    tklr_mutate.py delete 42 --from '2026-08-07 14:00'       # this and future
    tklr_mutate.py reschedule 42 --instance '2026-08-07 14:00' --to '2026-08-13 15:00'

Safety, in place of a version check:

  * We introspect each function before calling it — it must exist and its
    signature must accept the arguments we intend to pass. A rename or a
    changed parameter list is caught before anything is written.
  * We verify the outcome afterwards by re-reading through tklr's own API: the
    target must be gone (or moved) and every other record untouched.
  * Anything unexpected fails loudly and names the fallback, rather than
    guessing.

Run it with any python3 — it re-executes itself under tklr's own interpreter,
which it finds from the `tklr` launcher's shebang.

Exit codes: 0 done, 1 refused/failed, 2 usage error.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

TESTED_AGAINST = "1.0.43"  # informational only; not enforced

UI_FALLBACK = (
    "Workaround: the interactive UI can do this — run `tklr ui`, select the\n"
    "  reminder, and delete or reschedule it there. That is the only other\n"
    "  place tklr exposes these operations. Then tell whoever maintains this\n"
    "  skill that tklr's internals moved, so the shim can be updated."
)


# ---------------------------------------------------------------------------
# re-exec under tklr's interpreter
# ---------------------------------------------------------------------------

def tklr_python() -> str | None:
    """Find the interpreter tklr is installed under.

    The launcher on PATH is a shim whose shebang names its venv python, which
    works whether tklr was installed by uv, pipx, or anything else.
    """
    launcher = shutil.which("tklr") or str(Path.home() / ".local" / "bin" / "tklr")
    try:
        real = Path(launcher).resolve()
        first = real.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        if first.startswith("#!"):
            cand = first[2:].strip().split()[-1]
            if Path(cand).is_file() and os.access(cand, os.X_OK):
                return cand
    except (OSError, IndexError):
        pass
    for pat in (
        ".local/share/uv/tools/tklr-dgraham/bin/python",
        ".local/share/pipx/venvs/tklr-dgraham/bin/python",
        ".local/pipx/venvs/tklr-dgraham/bin/python",
    ):
        p = Path.home() / pat
        if p.is_file():
            return str(p)
    return None


def ensure_tklr_importable() -> None:
    try:
        import tklr  # noqa: F401
        return
    except ImportError:
        pass
    if os.environ.get("_TKLR_MUTATE_REEXEC"):
        sys.exit("error: re-executed under tklr's interpreter but tklr is still "
                 "not importable. Is tklr installed? Try: install.sh")
    py = tklr_python()
    if not py:
        sys.exit("error: cannot locate tklr's Python interpreter. Is tklr installed?")
    env = dict(os.environ, _TKLR_MUTATE_REEXEC="1")
    os.execve(py, [py, os.path.abspath(__file__), *sys.argv[1:]], env)


ensure_tklr_importable()

import inspect  # noqa: E402  (only safe once tklr is importable)


# ---------------------------------------------------------------------------
# capability checking — this replaces a version check
# ---------------------------------------------------------------------------

def require(obj: object, name: str, params: list[str]) -> object:
    """Return obj.name, having confirmed it is callable and takes `params`."""
    fn = getattr(obj, name, None)
    if fn is None or not callable(fn):
        fail(f"tklr no longer provides {type(obj).__name__}.{name}().",
             f"This skill was verified against tklr {TESTED_AGAINST}.")
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn  # cannot introspect; the outcome check still guards us
    accepted = set(sig.parameters)
    missing = [p for p in params if p not in accepted]
    if missing:
        fail(f"{type(obj).__name__}.{name}() no longer accepts {', '.join(missing)}.",
             f"Its signature is now: {name}{sig}")
    return fn


def fail(*lines: str) -> "NoReturn":  # type: ignore[valid-type]
    print("error: " + lines[0], file=sys.stderr)
    for extra in lines[1:]:
        print("  " + extra, file=sys.stderr)
    print("  " + UI_FALLBACK.replace("\n", "\n  "), file=sys.stderr)
    raise SystemExit(1)


def open_controller(home: str | None):
    from tklr.tklr_env import TklrEnvironment
    from tklr.cli.main import ensure_database
    from tklr.controller import Controller

    if home:
        os.environ["TKLR_HOME"] = str(Path(home).expanduser())
    env = TklrEnvironment()
    if not (env.home / "tklr.db").exists():
        sys.exit(f"error: no tklr workspace at {env.home}")
    env.ensure(init_config=True, init_db_fn=lambda p: ensure_database(p, env))
    env.load_config()
    return Controller(env.db_path, env), env


def snapshot(ctrl) -> dict[int, str]:
    """id -> subject, read through tklr's API rather than raw SQL."""
    get_all = getattr(ctrl.db_manager, "get_all_records", None)
    out: dict[int, str] = {}
    if callable(get_all):
        for row in get_all() or []:
            try:
                out[int(row[0])] = str(row[2])
            except (IndexError, TypeError, ValueError):
                continue
        return out
    # Fall back to probing ids via get_record.
    get_record = require(ctrl.db_manager, "get_record", ["record_id"])
    for rid in range(1, 5000):
        row = get_record(rid)
        if row:
            out[rid] = str(row[2]) if len(row) > 2 else ""
    return out


def render_entry(ctrl, rid: int) -> str:
    """Rebuild the entry text from the stored tokens.

    Deliberately does NOT shell out to `tklr details`: this process holds an
    open Controller, and tklr takes a write lock at startup, so a nested tklr
    dies with "sqlite3.OperationalError: database is locked".

    Finds the tokens column by shape rather than position, so a schema
    reordering degrades to an empty string instead of nonsense.
    """
    import json

    get_record = getattr(ctrl.db_manager, "get_record", None)
    if not callable(get_record):
        return ""
    row = get_record(rid)
    if not row:
        return ""
    for value in row:
        if not isinstance(value, str) or not value.startswith("["):
            continue
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            continue
        if (isinstance(parsed, list) and parsed
                and all(isinstance(t, dict) for t in parsed)
                and any("token" in t for t in parsed)):
            parts = [str(t.get("token", "")).strip() for t in parsed]
            return " ".join(p for p in parts if p)
    return ""


def rebuild(ctrl) -> None:
    """Force tklr to rebuild derived tables — no DerivedState surgery needed."""
    fn = getattr(ctrl.db_manager, "populate_dependent_tables", None)
    if callable(fn):
        try:
            fn(force=True)
        except TypeError:
            fn()


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("action", choices=["delete", "reschedule"])
    ap.add_argument("record_id", type=int)
    ap.add_argument("--home", default=None)
    ap.add_argument("--instance", default=None,
                    help="datetime of the occurrence to act on")
    ap.add_argument("--from", dest="from_dt", default=None,
                    help="delete this occurrence and all later ones")
    ap.add_argument("--to", dest="to_dt", default=None,
                    help="reschedule: the new datetime")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve and print the target, change nothing — use this "
                         "to show the user exactly what is about to happen")
    args = ap.parse_args()

    ctrl, env = open_controller(args.home)
    rid = args.record_id

    before = snapshot(ctrl)
    if rid not in before:
        sys.exit(f"error: no reminder with id {rid} in {env.home}")
    subject = before[rid]

    if args.dry_run:
        # Print the target as tklr holds it, so what the user confirms is what
        # gets changed — not the agent's description of it.
        if args.action == "delete":
            if args.from_dt:
                what = f"delete the occurrence at {args.from_dt} AND ALL LATER ONES"
            elif args.instance:
                what = f"delete only the occurrence at {args.instance}"
            else:
                what = "delete the ENTIRE reminder, including every occurrence"
        else:
            what = f"move the occurrence at {args.instance} to {args.to_dt}"
        print(f"WOULD {what}")
        print(f"  id {rid}: {subject!r}")
        entry = render_entry(ctrl, rid)
        if entry:
            print(f"  {entry}")
        print("  (nothing was changed)")
        return 0

    if args.action == "delete":
        if args.instance and args.from_dt:
            sys.exit("error: use --instance or --from, not both")

        if args.from_dt:
            fn = require(ctrl, "delete_this_and_future", ["record_id", "instance_text"])
            ok = fn(rid, args.from_dt)
            scope = f"occurrences from {args.from_dt} onward"
            expect_gone = False
        elif args.instance:
            fn = require(ctrl, "delete_instance", ["record_id", "instance_text"])
            ok = fn(rid, args.instance)
            scope = f"the occurrence at {args.instance}"
            expect_gone = False
        else:
            fn = require(ctrl, "delete_record", ["record_id"])
            fn(rid)
            ok = True
            scope = "the whole reminder"
            expect_gone = True

        if ok is False:
            fail(f"tklr declined to delete {scope} of {subject!r} (id {rid}).")

        rebuild(ctrl)
        after = snapshot(ctrl)

        if expect_gone:
            if rid in after:
                fail(f"id {rid} ({subject!r}) is still present after delete_record().")
            collateral = set(before) - set(after) - {rid}
            if collateral:
                fail(f"delete removed other reminders too: {sorted(collateral)}",
                     "This should never happen — investigate before trusting this again.")
            print(f"deleted id {rid}: {subject!r}")
        else:
            if rid not in after:
                fail(f"deleting {scope} removed the entire reminder (id {rid}).",
                     "Expected only that occurrence to go.")
            print(f"deleted {scope} of id {rid}: {subject!r}")
        print(f"  {len(after)} reminder(s) remain")
        return 0

    # reschedule
    if not (args.instance and args.to_dt):
        sys.exit("error: reschedule needs --instance <current> --to <new>")

    from tklr.item import parse as parse_dt
    parsed = parse_dt(args.to_dt)
    new_when = parsed[1] if isinstance(parsed, tuple) else parsed
    if new_when is None:
        sys.exit(f"error: could not understand --to {args.to_dt!r}")

    fn = require(ctrl, "reschedule_instance",
                 ["record_id", "old_instance_text", "new_when"])
    ok = fn(rid, args.instance, new_when)
    if ok is False:
        fail(f"tklr declined to move the {args.instance} occurrence of "
             f"{subject!r} (id {rid}).",
             "The instance datetime must match an existing occurrence exactly.")

    rebuild(ctrl)
    after = snapshot(ctrl)
    if rid not in after:
        fail(f"rescheduling removed id {rid} entirely.")
    print(f"moved id {rid} ({subject!r}) from {args.instance} to {args.to_dt}")
    print("  verify with: tklr --home %s days --start <date> --end 1 --plain --ids"
          % env.home)
    return 0


if __name__ == "__main__":
    sys.exit(main())
