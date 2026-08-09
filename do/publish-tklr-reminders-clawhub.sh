#!/usr/bin/env bash
# Publish skills/tklr-reminders to ClawHub. Safe to run repeatedly.
#
# ClawHub never pulls from GitHub on its own: publishing is one-way, and every
# new version needs an explicit publish. So this is the thing to run after any
# change you want listed. `clawhub skill publish` skips unchanged content and
# auto-bumps the patch version, so running it when nothing changed is harmless.
#
#   bash clawhub-publish.sh            # sync, dry run, then ask before publishing
#   bash clawhub-publish.sh --dry-run  # stop after the dry run
#   bash clawhub-publish.sh --no-pull  # publish the checkout as-is
#
# Run it on a machine WITH A BROWSER, since `clawhub login` opens one. On a
# headless box use `clawhub login --device` first, then --no-pull here.
#
# Not the website: clawhub.ai's upload page fails with "Server Error Called by
# client". Its slug check calls auth.clawdhub.com (stray 'd'), which sends no
# CORS headers for the clawhub.ai origin — openclaw/clawhub #143 and #131. The
# CLI posts to /api/v1/skills and avoids that path entirely.
#
# Not the GitHub Actions workflow, yet: openclaw/clawhub #3397 is open and it
# fails with "Uploaded file does not match its skill upload ticket" precisely
# when a skill has real content changes. Dry runs pass, so it looks fine until
# it matters. Revisit when that closes; this repo's layout already suits it.
#
set -uo pipefail

REPO_URL="https://github.com/37Rb/hermes-skills.git"
SKILL_PATH="skills/tklr-reminders"
CATEGORIES="productivity"                            # max 3, from ClawHub's fixed list
TOPICS="calendar,reminders,tasks,scheduling,hermes"  # max 5, free-form, no trust words

DRY_ONLY=0
PULL=1
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_ONLY=1 ;;
        --no-pull) PULL=0 ;;
        *) printf 'error: unknown option %s\n' "$arg" >&2; exit 2 ;;
    esac
done

step() { printf '\n=== %s\n' "$1"; }
die()  { printf 'error: %s\n' "$1" >&2; exit 1; }

# --- locate the repo ----------------------------------------------------------
# Publishing uploads a directory, so this machine needs the source and nothing
# else. No tklr, no Hermes.
if root=$(git rev-parse --show-toplevel 2>/dev/null) && [[ -d "$root/$SKILL_PATH" ]]; then
    cd "$root" || die "cannot enter $root"
    step "using the checkout at $root"
else
    step "cloning $REPO_URL"
    git clone "$REPO_URL" hermes-skills || die "clone failed"
    cd hermes-skills || die "cannot enter hermes-skills"
    [[ -d "$SKILL_PATH" ]] || die "$SKILL_PATH is not in this repo"
fi

# --- make sure we publish what is on GitHub -----------------------------------
# ClawHub stores a snapshot of whatever is uploaded. Publishing a dirty or stale
# tree silently puts something on the registry that no commit corresponds to,
# which is impossible to reason about afterwards.
if [[ $PULL -eq 1 ]]; then
    step "syncing with origin"
    git pull --ff-only || die "pull failed — resolve it, or pass --no-pull to publish this checkout as-is"
fi

step "what will be published"
git log --oneline -1
if [[ -n "$(git status --porcelain -- "$SKILL_PATH")" ]]; then
    git status --short -- "$SKILL_PATH"
    die "$SKILL_PATH has uncommitted changes. Commit and push them first, or ClawHub
       will hold a version that does not exist in the repo."
fi
if [[ $PULL -eq 1 ]] && ! git diff --quiet HEAD "@{upstream}" -- "$SKILL_PATH" 2>/dev/null; then
    die "this checkout differs from its upstream branch for $SKILL_PATH. Push first."
fi

# --- the CLI ------------------------------------------------------------------
if command -v clawhub >/dev/null 2>&1; then
    step "clawhub CLI: $(clawhub --version 2>/dev/null || echo present)"
else
    step "installing the clawhub CLI"
    npm i -g clawhub \
        || npm i -g --prefix ~/.local clawhub \
        || die "could not install the CLI (is ~/.local/bin on PATH?)"
fi

if clawhub whoami >/dev/null 2>&1; then
    step "already logged in as $(clawhub whoami 2>/dev/null | head -1)"
else
    step "logging in"
    clawhub login || die "login failed (headless? use: clawhub login --device)"
    clawhub whoami || die "still not logged in"
fi

# --- dry run ------------------------------------------------------------------
step "dry run (resolves the publish, uploads nothing)"
clawhub skill publish "./$SKILL_PATH" --dry-run \
    --categories "$CATEGORIES" --topics "$TOPICS" \
    || die "dry run failed — do not publish until this passes"

if [[ $DRY_ONLY -eq 1 ]]; then
    printf '\n--dry-run given: stopping here. Nothing was published.\n'
    exit 0
fi

# --- publish ------------------------------------------------------------------
# Behind a prompt on purpose: public, under your account, and MIT-0 with no
# per-skill override.
step "ready to publish for real"
printf 'This publishes %s publicly under your ClawHub account,\n' "$SKILL_PATH"
printf 'licensed MIT-0. Unchanged content is skipped; changes bump the patch version.\n\n'
read -r -p 'Type PUBLISH to continue: ' answer
[[ "$answer" == "PUBLISH" ]] || { printf 'Aborted. Nothing was published.\n'; exit 0; }

clawhub skill publish "./$SKILL_PATH" \
    --categories "$CATEGORIES" \
    --topics "$TOPICS" \
    || die 'publish failed. If it said "Uploaded file does not match its skill upload
       ticket", that is openclaw/clawhub #3394 against 0.23.3 — reinstall the CLI
       for a newer version and retry.'

step "done"
clawhub skill info "$(basename "$SKILL_PATH")" 2>/dev/null || true
