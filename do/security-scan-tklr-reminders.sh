#!/usr/bin/env bash
# Run Hermes's own install gate against skills/tklr-reminders, locally.
#
# This is not an approximation of the check that runs on `hermes skills install`.
# It calls the same tools/skills_guard.py functions the installer calls, with the
# same source identifier the tap uses, and exits on the same
# should_allow_install() decision. Run it before publishing anything.
#
#   bash do/security-scan-tklr-reminders.sh
#
# Exit codes: 0 the gate would allow the install, 1 it would block or ask,
# 2 this script could not run the scan at all. Never treat a non-zero exit as
# "probably fine": for a community source only a SAFE verdict installs.
#
# What the gate does, so a finding is readable when you get one:
#   * any CRITICAL finding  -> verdict dangerous -> blocked, and --force cannot
#     override it for a community source
#   * any HIGH finding      -> verdict caution   -> ALSO blocked for community
#   * medium and low alone  -> verdict safe      -> installs
# So highs matter as much as criticals here, and a clean run today can break
# tomorrow on one new pattern match.
#
# Two things that surprise people:
#   * The scanner matches raw text and does not skip comments. A comment that
#     explains why you stopped using a flagged API will itself be flagged.
#   * `hermes skills publish` is NOT this check. It self-scans with
#     source="self" and refuses only on a dangerous verdict, so a skill can
#     publish cleanly and still be uninstallable for everyone.
#
# Dev artifacts that are not part of the installed skill can be excluded with a
# `.skillignore` (gitignore-style) inside the skill directory. Prefer that over
# reshaping real code to dodge a pattern.
#
# This deliberately does NOT check that the tree is committed, because you want
# to scan what you are about to publish, including work in progress. It prints
# the commit and any dirty paths so the output says what was actually scanned.
#
set -uo pipefail

SKILL_PATH="skills/tklr-reminders"
# The identifier the real tap install passes. _resolve_trust_level strips the
# "skills-sh/" prefix and then looks for the repo in its hard-coded
# TRUSTED_REPOS set; a personal repo is never in it, so this resolves to
# "community" and gets community's strict policy. There is no source string
# under which this repo scores better, which is what makes scanning locally an
# honest test rather than a friendly one.
SOURCE_ID="skills-sh/37Rb/hermes-skills/skills/tklr-reminders"

case "${1:-}" in
    -h|--help) sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    "") ;;
    *) printf 'error: unknown option %s (try --help)\n' "$1" >&2; exit 2 ;;
esac

step() { printf '\n=== %s\n' "$1"; }
die()  { printf 'error: %s\n' "$1" >&2; exit 2; }

# --- locate the skill ---------------------------------------------------------
root=$(git rev-parse --show-toplevel 2>/dev/null) \
    || die "run this from inside the hermes-skills checkout"
[[ -d "$root/$SKILL_PATH" ]] || die "$SKILL_PATH is not in the repo at $root"
cd "$root" || die "cannot enter $root"

step "scanning $SKILL_PATH at $root"
git log --oneline -1
if [[ -n "$(git status --porcelain -- "$SKILL_PATH")" ]]; then
    printf 'uncommitted changes included in this scan:\n'
    git status --short -- "$SKILL_PATH"
fi

# --- locate Hermes ------------------------------------------------------------
# skills_guard pulls in httpx transitively, so the system python will fail on
# the import rather than on anything meaningful. Use the interpreter Hermes
# itself runs on.
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
AGENT_DIR="$HERMES_HOME/hermes-agent"
PY="${HERMES_PYTHON:-$AGENT_DIR/venv/bin/python}"

[[ -d "$AGENT_DIR" ]] || die "no hermes-agent at $AGENT_DIR (set HERMES_HOME)"
[[ -x "$PY" ]] || die "no interpreter at $PY (set HERMES_PYTHON to Hermes's python)"

# --- the gate -----------------------------------------------------------------
# The policy is never reimplemented here: format_scan_report and
# should_allow_install are the same functions the installer uses, so this cannot
# drift from the real decision as the rules change upstream.
step "running skills_guard"
"$PY" - "$AGENT_DIR" "$root/$SKILL_PATH" "$SOURCE_ID" <<'PY'
import sys

agent_dir, skill_dir, source_id = sys.argv[1:4]
sys.path.insert(0, agent_dir)

try:
    from pathlib import Path
    # scan_skill, not scan_skill_cached: the cached variant writes a
    # .scan-cache directory next to the skill, which here means inside the
    # repo, and .gitignore does not cover it.
    from tools.skills_guard import scan_skill, format_scan_report, should_allow_install

    result = scan_skill(Path(skill_dir), source=source_id)
    print(format_scan_report(result))

    # format_scan_report already prints the verdict, every finding and the
    # ALLOWED/BLOCKED line; all that is left is to turn it into an exit code.
    allowed, _reason = should_allow_install(result)
    if allowed is True:
        sys.exit(0)
    sys.exit(1)
except SystemExit:
    raise
except Exception as exc:
    print(f"could not run the scan: {type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(2)
PY
status=$?

case $status in
    0) step "installable: the gate would allow this from a tap" ;;
    # Exit 1, not die's 2: a real block and a broken environment must not look
    # the same to a caller that only reads the status.
    1) printf '\nblocked: the gate would REFUSE this install. Fix the critical/high\n'
       printf 'findings above; only a SAFE verdict installs from a community source.\n'
       printf 'Do not publish until this exits 0.\n' >&2
       exit 1 ;;
    *) printf '\n'; die "the scan did not run, so nothing was verified" ;;
esac
