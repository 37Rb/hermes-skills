# How it works, and what to do when it doesn't

Background: the delivery mechanism, the engine's stale-cache bug, the SQLite the
dispatcher uses, and the failure table. You need this to diagnose something, not
to use the skill.

## Alerts

### How delivery works

the engine's own alerts only fire while its UI is running, and this skill never
runs the UI. The dispatcher does exactly what its UI does in
`execute_due_alerts()`, just from cron:

1. An alert on a reminder creates rows in the engine's `Alerts` table — **one row per
   (offset × letter)**, each row a single delivery.
2. A cron job runs `~/.hermes/scripts/tklr_alert_poller.py` every minute with
   `--no-agent`, so no LLM is involved.
3. It asks tklr to recompute today's alerts, then reads every row whose
   trigger time has arrived **or passed within the last hour** — never a
   future one, and never one older than that.
4. For each row it runs that row's `alert_command` and, on success,
   **deletes the row**. So nothing is sent twice.
5. A row whose command fails is left in place and retried next minute. Its
   siblings are already deleted, so nobody gets a duplicate.
6. Once a row is **more than an hour past due** it is discarded and reported
   as never delivered — once, because the row is then gone. That hour is
   `MAX_LATE` in the dispatcher (`$TKLR_ALERTS_MAX_LATE`, in minutes).

Step 6 is what keeps a broken channel from becoming a permanent retry loop,
and it is why a machine that was off for a day does not deliver a day of
stale reminders the moment it wakes up. If the user asks why an alert never
arrived, `$R status` and `~/.hermes/logs/tklr-alerts.log` will both name it.

There is no routing file and no send ledger. Because each delivery is its own
row, tklr's table *is* the per-delivery state.

The dispatcher prints nothing on success — Hermes treats empty output as
silent — and appends a line per send to `~/.hermes/logs/tklr-alerts.log`. It
speaks up only about a real problem, which then reaches the user.

### Channel letters are the routing table

Delivery is configured entirely in the `[alerts]` section of the tklr workspace's
`config.toml`. Each key is ONE LOWERCASE LETTER naming a (person, channel) pair;
its value is the shell command that performs the delivery. `python3 $R channels`
lists what is configured. `--via r` on a reminder selects letter `r`.

That is all you need for day-to-day use. Creating or changing letters, and the
several ways that file will bite you, are in **`references/setup.md`**.

## Why healing is needed

tklr rebuilds its `DateTimes` and `Alerts` tables only when the day changes
or when a version string derived from `max(Records.modified)` — truncated to
**minute resolution** — changes. `Records.modified` is stored as
`YYYYMMDDTHHMMZ`, so every reminder saved within the same clock minute
produces an identical version string. A reminder saved in the same minute as
the previous rebuild therefore leaves the key unchanged, and its `DateTimes`
row is never generated. Two things then break: alert generation joins
`DateTimes`, so
**its alerts silently never fire**; and `days`, `weeks`, and `agenda` read
`DateTimes`, so **the reminder is invisible in listings** even though
`details` and `find` still show it. Both were reproduced on 1.0.43.

The poller detects the fingerprint (a scheduled reminder with alerts but no
`DateTimes` row) and repairs it automatically each run. Running the dispatcher
with `--heal` right after you add or edit something with alerts just makes the
repair immediate instead of up to a minute later.

`--heal` is a flag on `tklr_alert_poller.py`. The storage engine exposes no way
to force a rebuild of its own derived state — that missing capability is the
whole reason the flag exists, and a force-rebuild is what the upstream bug
report asks for.

## Direct SQLite use

**Use `$R` for everything. Never open `tklr.db` yourself.**
This applies to reads as much as writes — no `sqlite3` in your commands, no
convenience queries, not even "just to check something". If a question seems
to need SQL, it can almost certainly be answered with `$R find`,
`$R show`, or `$R list`; if it genuinely can't, say so
rather than reaching into the database.

The only exceptions are three narrow cases inside
`scripts/tklr_alert_poller.py`, each existing solely because the engine's CLI has
no equivalent. Do not extend this list, and do not copy the pattern
elsewhere:

1. **Deleting a fired alert.** tklr has no delete-alert command. Safe
   because `populate_alerts()` only regenerates rows with
   `trigger_datetime >= now`, so an alert whose trigger has passed is never
   recreated. Keyed on `(record_id, start_datetime, alert_name,
   trigger_datetime)` — *not* `alert_id`, which
   the engine's own alerts listing reports as `null`.
2. **Clearing two derived-state cache keys** (`datetimes`, `alerts`) to force
   the rebuild described above, plus the one `SELECT` that detects the
   condition. These are caches the engine regenerates on its next command, not user
   data.
3. **Reading due alerts** from the `Alerts` table. The engine's own alerts
   listing cannot serve this: `get_alerts_for_window()` filters `trigger_datetime BETWEEN now AND
   window_end`, so it reports only alerts still in the *future* — a past-due
   alert is filtered out or replaced by a regenerated future row. A
   dispatcher that missed a tick would lose that alert permanently, so late
   alerts can only be found in the table.

Everything else goes through the engine's own CLI, which the poller calls
internally. The table is refreshed by that alerts query, and the message text
comes from the `[alerts]` command in `config.toml`, rendered with `{name}`,
`{when}`, `{description}` and the rest — so the dispatcher never needs to read a
record.

## When something isn't working

| Symptom | Cause and fix |
|---------|---------------|
| A reminder never fires | `$R show <id>` — it says `draft` and carries an import error if it was rejected, which only happens to records written without going through `$R`. Delete it and add it again through `$R`, which refuses the input that produces a draft. |
| Added an event, no alert row appears | Stale derived state. `python3 ~/.hermes/scripts/tklr_alert_poller.py --heal` |
| A just-added event is missing from `$R list` but `$R show <id>` shows it | Same stale derived state — run the dispatcher with `--heal`, then re-read. |
| Alert fires but nothing arrives | Run the dispatcher by hand — it prints the failure and logs it. Then test the letter's command directly, e.g. `hermes send --to <target> test`. |
| "command could not be parsed" | The subject or note contains a `"`, which breaks `shlex`. Reword the reminder. |
| "alert has no command" | The channel letter on the alert isn't defined in `[alerts]`. |
| Reminder delivered to nobody, but reported as sent | A letter is defined as a no-op (`true`, `:`). Replace it with a real delivery command. |
| Alert delivered repeatedly | Its `Alerts` row isn't being deleted — check the log for a command that keeps failing, since a failing row is retried every minute by design. |
| Nothing fires at all | `hermes cron list` — is `tklr-alert-poller` there? Is the scheduler running (`hermes cron status`)? |
| `tklr: command not found` | `export PATH="$HOME/.local/bin:$PATH"`, or re-run `setup --platform <platform>`, which reinstalls it. |
| Entry rejected on a date | Don't pass `tomorrow` or `next week`; compute the date. |
| A listing looks wrong in chat | Add `--plain`, and `--width 60` for narrow screens. |

Everything lives in the tklr workspace — reminders, and the `[alerts]`
delivery config, in `config.toml` and `tklr.db`. The skill's only other
footprint is `~/.hermes/scripts/tklr_alert_poller.py` and the log at
`~/.hermes/logs/tklr-alerts.log`. There is no separate state directory.
