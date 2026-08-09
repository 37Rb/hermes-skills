#!/usr/bin/env bash
# Idempotent setup for the tklr-reminders skill. Safe to re-run.
#
#   bash install.sh [--home <TKLR_HOME>]
#
# Does four things, skipping whatever is already done:
#   1. installs tklr via uv (Hermes ships its own uv)
#   2. creates the tklr workspace (config.toml + tklr.db)
#   3. installs the dispatcher into ~/.hermes/scripts/
#   4. reports whether [alerts] channel letters have been defined yet
#
# It deliberately does NOT invent [alerts] letters, and does NOT create the
# cron job.
#
# HOST-SPECIFIC. Steps 1 and 3 assume a Hermes layout: uv shipped at
# $HERMES_HOME/bin/uv and off PATH, and a scheduler that will only run scripts
# living in ~/.hermes/scripts/. The Python side of the skill keeps all of this
# in scripts/host.py (see its header for what a port has to change); a shell
# script cannot import it, so these two steps are the shell half of the same
# seam. Step 3 is the one that disappears entirely on a host whose scheduler
# will run a script where it already lives.
#
# Not inventing letters matters: a letter defined as a placeholder no-op like
# 'true' would make the dispatcher treat every alert as successfully
# delivered and delete it, so reminders would silently reach nobody. Letters
# must be real delivery commands, which needs the user's channels — see
# templates/alerts-config-example.toml.

set -uo pipefail

# Mirrors tklr's own resolution: TKLR_HOME, then XDG_CONFIG_HOME/tklr, then
# ~/.config/tklr. Skipping the XDG step would build the workspace somewhere
# tklr never looks on any machine that sets it.
TKLR_HOME="${TKLR_HOME:-${XDG_CONFIG_HOME:-$HOME/.config}/tklr}"
TKLR_PYTHON="${TKLR_PYTHON:-}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --home) TKLR_HOME="$2"; shift 2 ;;
        --python) TKLR_PYTHON="$2"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_SCRIPTS="$HOME/.hermes/scripts"
LOG_DIR="$HOME/.hermes/logs"
export PATH="$HOME/.local/bin:$PATH"

step() { printf '\n=== %s\n' "$1"; }
ok()   { printf '  ok: %s\n' "$1"; }
warn() { printf '  !! %s\n' "$1"; }

# Hermes installs itself with uv and ships its own copy, so on a Hermes machine
# uv always exists — at $HERMES_HOME/bin/uv, which is NOT on PATH. Prefer that
# one; fall back to a uv on PATH so the skill still works outside Hermes.
#
# We deliberately do NOT hunt for a system interpreter. `uv tool install
# --python '>=3.12'` reuses any suitable Python it can find and downloads one
# only if none exists, which is both simpler and more correct than guessing.
# (Never pass a bare `python3`: inside the Hermes agent that is the agent's own
# venv, which may be older than 3.12 — pip then reports "Could not find a
# version that satisfies the requirement tklr-dgraham", which reads like a
# missing package rather than a version mismatch.)
find_uv() {
    local c
    for c in "${UV:-}" "${HERMES_HOME:-$HOME/.hermes}/bin/uv" uv; do
        [[ -z "$c" ]] && continue
        c="$(command -v "$c" 2>/dev/null || { [[ -x "$c" ]] && echo "$c"; })"
        if [[ -n "$c" ]] && "$c" tool --help >/dev/null 2>&1; then echo "$c"; return 0; fi
    done
    return 1
}

# ---------------------------------------------------------------- 1. tklr
step "tklr installation"
if command -v tklr >/dev/null 2>&1; then
    ok "already installed — $(tklr --version 2>&1 | head -1)"
else
    UV="$(find_uv)" || true
    if [[ -z "$UV" ]]; then
        warn "uv not found, so tklr cannot be installed."
        warn ""
        warn "uv is normally already present on a Hermes machine at"
        warn "  ${HERMES_HOME:-$HOME/.hermes}/bin/uv"
        warn "If it is there, point this script at it:"
        warn "  UV=${HERMES_HOME:-$HOME/.hermes}/bin/uv bash install.sh"
        warn ""
        warn "Otherwise install uv by whichever method its docs recommend for"
        warn "this machine — it brings its own Python and needs none to"
        warn "bootstrap, so it works where no Python is installed at all:"
        warn "  https://docs.astral.sh/uv/getting-started/installation/"
        warn "then re-run this script."
        exit 1
    fi
    ok "using uv at $UV ($("$UV" --version 2>&1 | head -1))"

    # '>=3.12' lets uv reuse any suitable interpreter already on the machine
    # and download one only if none qualifies. --python overrides it.
    PYSPEC="${TKLR_PYTHON:->=3.12}"
    echo "  installing tklr-dgraham (python $PYSPEC) ..."
    # --quiet: uv otherwise lists all 28 resolved packages. That output is
    # noise to a human and actively harmful to an agent, which has to read it
    # before deciding what to do next -- see run_installer() in the wrapper.
    if ! "$UV" tool install --quiet --python "$PYSPEC" tklr-dgraham; then
        warn "uv could not install tklr."
        warn "If pip reported 'Could not find a version that satisfies the"
        warn "requirement tklr-dgraham', that is a Python version mismatch, NOT a"
        warn "missing package — every release requires >= 3.12."
        exit 1
    fi

    hash -r 2>/dev/null || true
    if ! command -v tklr >/dev/null 2>&1; then
        # uv puts shims in ~/.local/bin; if that is not on PATH it is invisible.
        warn "installed, but 'tklr' is not on PATH — run: $UV tool update-shell"
        exit 1
    fi
    ok "installed — $(tklr --version 2>&1 | head -1)"
fi

# ------------------------------------------------------------ 2. workspace
step "workspace at $TKLR_HOME"
mkdir -p "$TKLR_HOME"
if [[ ! -f "$TKLR_HOME/tklr.db" ]]; then
    # Any read command bootstraps config.toml and tklr.db.
    tklr --home "$TKLR_HOME" alerts >/dev/null 2>&1 || true
fi
[[ -f "$TKLR_HOME/config.toml" ]] && ok "config.toml present" || warn "config.toml missing"
[[ -f "$TKLR_HOME/tklr.db" ]]     && ok "tklr.db present"     || warn "tklr.db missing"

# ------------------------------------------------------------ 3. dispatcher
step "dispatcher in $HERMES_SCRIPTS"
mkdir -p "$HERMES_SCRIPTS" "$LOG_DIR"
if install -m 0755 "$SKILL_DIR/scripts/tklr_alert_poller.py" \
        "$HERMES_SCRIPTS/tklr_alert_poller.py"; then
    ok "installed tklr_alert_poller.py"
else
    warn "could not copy the dispatcher"
    exit 1
fi

if python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" \
        "$HERMES_SCRIPTS/tklr_alert_poller.py" 2>/dev/null; then
    ok "poller parses cleanly"
else
    warn "poller failed to parse"
fi

# --------------------------------------------- 4. are channel letters set up?
step "alert channels in $TKLR_HOME/config.toml"
LETTERS=$(python3 - "$TKLR_HOME/config.toml" <<'PY'
import sys, tomllib
from pathlib import Path
p = Path(sys.argv[1])
try:
    alerts = tomllib.loads(p.read_text(encoding="utf-8")).get("alerts") or {}
except Exception:
    alerts = {}
print(" ".join(sorted(k for k in alerts if k != "n")))
PY
)
if [[ -n "$LETTERS" ]]; then
    ok "letters defined: $LETTERS"
else
    warn "no channel letters defined yet — reminders using @a cannot be created"
fi

# ----------------------------------------------------------------- summary
step "next steps"
# Deliberately NOT a procedure. This block used to spell out the whole setup as
# numbered steps, and on 2026-08-07 an agent ran install.sh first, read them,
# and followed steps 2/3/5 verbatim -- copying the letter `a` and the sample
# message text character for character -- instead of the one command that does
# all of it. A complete ordered recipe will always beat a single instruction
# elsewhere, so the recipe is gone. `setup` runs this script itself; nothing
# below needs doing by hand.
cat <<EOF
  FOR THE AGENT — do NOT continue by hand from here. This script only installs
  tklr and the workspace; it configures no channel and schedules no delivery.

  Run this one command. It is idempotent, it calls this script for you, and it
  does everything that is left — alert channel, dispatcher, cron job, and a
  test alert that proves delivery works:

    python3 $SKILL_DIR/scripts/tklr_agent_wrapper.py --home $TKLR_HOME \\
      setup --platform <the platform this conversation is on>

  Then wait for the test alert to arrive and ask the user whether it did.
  Report what it can now do with:

    python3 $SKILL_DIR/scripts/tklr_agent_wrapper.py --home $TKLR_HOME welcome

  and send that output verbatim. Never show the user a wrapper command.
EOF
echo
echo "tklr home: $TKLR_HOME"
echo "log:       $LOG_DIR/tklr-alerts.log"
