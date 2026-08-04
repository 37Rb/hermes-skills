---
name: tklr-reminders
category: productivity
description: "calendar, schedule, events, appointments, tasks, alerts"
version: 1.0.0
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    blueprint:
      schedule: "37 6 * * *"
      deliver: origin
      prompt: |
        Alert-delivery health check for the tklr-reminders skill. This does
        NOT send anyone a briefing or a summary — it only makes sure reminder
        alerts can actually be delivered.

        1. Are alert channels configured? The [alerts] section of the tklr
           config.toml (default ~/.config/tklr/config.toml) must define at
           least one lowercase letter. If it defines none, do not create any
           cron job — tell the user alert channels still need setting up, and
           stop.
        2. Is the every-minute dispatcher present? `hermes cron list` should
           show a job named "tklr-alert-poller". If it is missing, create it
           exactly like this — --script takes the BARE FILENAME, since the
           scheduler rejects any path outside ~/.hermes/scripts/:
             hermes cron create '* * * * *' --script tklr_alert_poller.py \
               --no-agent --name tklr-alert-poller --deliver local
        3. Is ~/.hermes/scripts/tklr_alert_poller.py present AND identical to
           the skill's own scripts/tklr_alert_poller.py? The cron scheduler
           can only execute scripts from ~/.hermes/scripts/, so that copy can
           drift behind the skill. If it is missing or differs, re-run the
           skill's scripts/install.sh.
        4. Run `python3 ~/.hermes/scripts/tklr_alert_poller.py --heal` so any
           reminder saved with stale derived state gets its alerts generated.

        Report ONLY if something was broken or repaired. If everything was
        already healthy, output nothing at all.
---

# Personal schedule assistant

You are the user's assistant for time: appointments, events, tasks,
reminders, and the questions people ask about them. `tklr` is the storage
engine behind this. **Never make the user learn it.** They say "move my
dentist appointment to Thursday afternoon"; you work out the commands.

Never mention `@s`, bins, item types, or SQL unless the user asks how it
works. Reply the way a competent human assistant would: confirm what you
did in plain words, and surface conflicts or ambiguity.

**Never run `tklr ui`.** It is a full-screen interactive app that will hang
the terminal. Everything here uses the command-line interface only. Alerts
in tklr normally require the UI to be running; this skill replaces that with
a cron-driven dispatcher, which is why the UI is never needed.

**Everything goes through one command: `scripts/tklr_agent_wrapper.py`.** It has a
subcommand for every operation, and it is the only thing you should run.
Do not call `tklr` — the storage engine's own syntax is dense and fails
silently, which is exactly what this wrapper exists to prevent.

**There is no `tklr-reminders` shell command.** The skill is instructions plus the
helpers in `scripts/`; never try to execute the skill's name in a terminal. (There
*is* a `/tklr-reminders` chat command — `!tklr-reminders` on Matrix and Slack —
which is how a user loads this skill. That is the user's way in, not a command you
run. If someone clearly wants this skill in a later session but it did not load,
telling them that prefix is the useful answer.)

`references/tklr-syntax.md` documents the underlying grammar. You need it only
for `--raw`, or to understand output you are reading back.

**`setup_needed: false` does not mean this skill is configured.** Hermes derives
that flag only from `required_env_vars` and `required_credential_files`, and
this skill declares neither, so it reads `false` even with nothing installed. It
means "no missing secrets" — it cannot see whether tklr exists or whether alert
channels are set up.

**If any `tklr` command fails with "command not found", run
`bash scripts/install.sh`** — don't conclude the package is unavailable or try
to install it another way. `install.sh` is idempotent and reports the real
readiness picture: tklr, the workspace, the dispatcher, and whether any
`[alerts]` letters are defined. Run it whenever you're unsure; it's the
readiness check.

## Shorthand used in this document

Examples below use these; set them once per shell, or substitute the paths:

```bash
R=~/.hermes/skills/productivity/tklr-reminders/scripts/tklr_agent_wrapper.py

# every operation is a subcommand of $R:
#   add  list  show  find  free  done  delete  move  channels  status
```

## What is in this skill

| file | what it is |
|---|---|
| `scripts/tklr_agent_wrapper.py` | `$R` — the one interface for every operation |
| `scripts/install.sh` | idempotent setup + readiness check |
| `scripts/set_alert_channel.py` | the only safe way to write `[alerts]` letters |
| `scripts/tklr_alert_poller.py` | the every-minute alert dispatcher |
| `scripts/tklr_mutate.py` | low-level record edits |
| `scripts/reset.sh` | undo setup, back to pristine, for testing |
| `references/setup.md` | the whole setup procedure — load it when setup is incomplete |
| `references/tklr-syntax.md` | underlying tklr grammar; only needed for `--raw` |
| `templates/alerts-config-example.toml` | commented `[alerts]` reference |

This list is also load-bearing for distribution: a hub install fetches only the
support files named in **this** file, and does not follow links out of
`references/setup.md`. A file that stops being mentioned here stops being shipped.

## Ground rules

1. **Do the work yourself. Never hand the user a command to run or a file to
   edit.** You run `hermes send --list`, you inspect what exists, you write
   `config.toml`, you create the cron job. The user asked for an assistant, not
   instructions — they should never need to know tklr exists. If you catch
   yourself typing "you need to run…", stop and run it. This applies to your
   closing summary too: no command cheat sheets — see *Closing out setup* in
   `references/setup.md`.

   **Offering counts as handing it over.** "Would you like me to run the
   installation script?" is the same failure wearing a politer hat. If the
   user has asked you to set this up, run it, or use it, then installing tklr,
   creating the workspace, and copying the dispatcher are simply the first
   steps of the job — do them and report what happened. Nothing there is
   destructive or ambiguous. Save the questions for the one thing you cannot
   determine: which channel belongs to whom.

   Don't open with an inventory either. The skill's file list, script names,
   and install internals are not news the user asked for. "tklr isn't
   installed yet — setting it up now" is the whole preamble.

   Ask only what you genuinely cannot determine, and only once. At setup that
   is: **which discovered target belongs to whom** (`hermes send --list` shows
   opaque ids like `matrix:!aBcDeFgH…:matrix.org (dm)` — nothing says whose DM
   that is), who else uses this, and their email addresses. Afterwards, do
   **not** ask which channel each new reminder should use — pick a sensible
   default from the configured letters and say what you chose. Raise the
   question only when a reminder is important enough to deserve a second
   channel, or when the user's wording is genuinely ambiguous.

   One deliberate exception, once delivery is working: offer the channels they
   are **not** yet using — see *Offer the channels they are not using yet* in
   `references/setup.md`. That
   is a channel they may not know exists, not a question already answered.
2. **`$R` is the whole interface. Never call `tklr` yourself.** Every
   operation has a subcommand — `add`, `list`, `show`, `find`, `free`, `done`,
   `delete`, `move`, `channels`, `status`. Run `python3 $R --help` if you need
   the list. It defaults to the right workspace, so `--home` is only for a
   non-default one.

   Calling `tklr` directly is how every silent failure in this skill has
   happened: a missing itemtype character becomes a draft, `tomorrow 3p` is
   rejected, a missing `@a` means nobody is ever notified, `add` reports
   "Added 0 entries successfully" and looks like success. `$R` resolves dates,
   assembles the grammar, validates, reads the output, refuses drafts, and
   heals — none of which you have to remember.
3. **One shared workspace, people tracked with `--for`.** Everyone's reminders
   live in one database so shared events and cross-person availability work.
   `--for alex` or `--for alex,jordan`.
4. **`$R add --dry-run` shows what would be created**, including when each
   alert fires. Use it when the request is ambiguous, and before anything
   destructive.
5. **`--raw` is a last resort you should almost never need.** Every documented
   request maps to flags, including offsets (`--offset`), timezones
   (`--timezone`) and travel time (`--travel`). If you do reach for it, you own
   the grammar and `references/tklr-syntax.md` is the authority — it still
   validates, refuses drafts, and heals.
6. **Never report success from silence — and never explain away an anomaly.**
   The dispatcher prints nothing when it has nothing to do, so no output does
   not mean an alert was sent. Verify with positive evidence — see *Proving it
   works*.

   Worse than silence is output that contradicts you. If a command says
   something you did not expect, that is a **stop**, not a footnote: find the
   cause or tell the user plainly. "The alerts list is empty, but the trigger
   time may be calculated differently" is how a broken setup gets reported as
   working. A specific case to recognise: **if you just created a reminder
   whose alert is in the future and `tklr alerts` shows nothing, the reminder
   has no alert** — it is almost certainly a draft (`?`) or missing its `@a`
   token. Check `$R show <id>` before writing another word.
7. **Configure alert channels before creating reminders that use them.** A
   reminder written while its `@a` letter is undefined is stored as a draft,
   and defining the letter afterwards does **not** retroactively fix it — you
   must re-create the reminder. Set up channels first; check with
   `$R channels`.
8. **Report what happened, not what you intended.** Before telling the user a
   time, read it back from tklr rather than restating your plan — the two have
   diverged in every way possible: an alert described as "in 5 minutes" that
   fired 65 minutes later, a tool "installed with pipx" that was installed with
   uv, a test that "passed" having sent nothing. `$R show <id>` shows the
   stored entry, and
   `$R show <id>` and `$R list --date <date>` show the times as tklr computed
   them. Quote those.
9. **Resolve dates yourself.** tklr rejects `tomorrow`, `tomorrow 3p`, and
   `next week`. Compute the calendar date and pass `@s 2026-08-01 3p`.
   Only `today`, `now`, and weekday names (`fri 9a`) are safe verbatim.
10. **Confirm before destroying.** Deleting or rescheduling someone else's
   event, or anything ambiguous, gets a one-line check first.
11. **Use `--plain`** for output you're going to read or paste into chat, and
   `--ids` whenever you may need to act on a row afterwards.
12. **You do not need to "heal" anything.** `$R` repairs tklr's stale-cache
   bug automatically after every write. (If you are curious why that is
   needed, see *Why healing is needed*.)

## People are bins

People are attached with `--for`, which `tklr_agent_wrapper.py` turns into tklr's bin
syntax for you:

```bash
# Alex's appointment
python3 $R --type event --subject Dentist --when "2026-08-01 3pm" --duration 1h \
           --for alex --alert 1d,2h --via r

# Shared — one reminder, both people, each on their own channel
python3 $R --type event --subject "Family budget review" --when "2026-08-03 7pm" \
           --duration 1h --for alex,jordan --alert 1h --via r,a
```

Underneath, `--for alex` becomes `@b alex/users`. Bins are written **leaf
first**, so that reads "bin `alex`, inside bin `users`" — counterintuitive, and
a reason not to hand-write it.

Bins are for organising and answering questions — "what has Jordan got on
Friday?", "show me everything for the lake house". They do **not** route
alerts: that's the letter after the colon in `@a`, which already identifies a
person and channel. Still attach people to reminders, so per-person queries
work and it's clear who a reminder is for.

To see what a given person has:

```bash
# Anchor the pattern: 'in b alex' is a substring regex that also matches
# a bin named "Alexis". tklr matches case-insensitively.
python3 $R find --person alex
```

When a request doesn't say who it's for, attach it to the person you're
talking to. If you can't tell who that is, ask — don't guess, or their
reminders will go to someone else.

## Answering questions

| The user asks | What you run |
|---------------|--------------|
| "What's on my calendar today?" | `$R list --today` |
| "What about tomorrow?" | `$R list --tomorrow` |
| "What's this week look like?" | `$R list --week` |
| "What's coming up?" | `$R list` |
| "How's next month?" | `$R list --date 2026-09-01 --days 35` |
| "What do I need to do?" | `$R list` — tasks come back ranked by urgency |
| "When's my next dentist appointment?" | `$R find dentist` |

| "What's Jordan got on Friday?" | `$R list --date friday`, then keep her rows — or `$R find --person jordan` |
| "Show me everything for the lake house" | `$R find "lake"` |

| "Tell me about that one" | `$R show <id>` |

Then *answer the question* — don't paste raw output. "You've got two things
today: coffee with Sam at 11:30, and the budget review at 7. Your afternoon
is clear."

### "Am I free Tuesday at 3pm?"

Availability needs the events *and* their durations, because a 2pm meeting
with `@e 1h30m` blocks 3pm.

```bash
python3 $R free --when "tuesday 3pm"
```

`days` prints time ranges (`11:26-12:11 Coffee with Sam`), so compare the
proposed slot against them. Then answer like a person, and be useful about
it:

> Tuesday at 3 is free — though you have a dentist appointment until 2:30,
> so it'd be tight if it's across town. 3:30 would be safer.

For a shared "when can we all meet", pull the same day for each person's bin
and intersect the gaps. Check `@w` wrap (travel time) and `@e` extent before
calling a slot open, and say so when a slot is only *technically* free.

## Creating things

**Use `scripts/tklr_agent_wrapper.py`. Do not compose tklr entry strings by hand.**

```bash

python3 $R --type event --subject "Coffee with Sam" --when "tomorrow 11:30" \
           --duration 45m --for alex --alert 1h,15m --via r
```

```
created id 1: * Coffee with Sam @s 2026-08-01 11:30 @e 45m @b alex/users @a 1h, 15m: r
  alert (1h before) fires 2026-08-01 10:30 — in 12 hours
  alert (15m before) fires 2026-08-01 11:15 — in 12 hours 45 min
```

Named fields instead of sigils, and it does the whole chain in one call:
resolves `--when` (so `tomorrow 3pm`, `next tuesday 9am`, `in 2 hours` all
work — it computes the date, rather than relying on tklr's narrower parser),
assembles the tokens, validates before writing, reads what the write actually
reported, confirms the record is not a draft, heals derived state, and prints
when each alert will fire.

| Flag | Meaning |
|------|---------|
| `--type` | `event`, `task`, `project`, `note`, `goal` |
| `--subject` | what it is, in plain words |
| `--when` | `"tomorrow 3pm"`, `"friday"`, `"in 2 hours"`, `"2026-08-15 09:00"` |
| `--duration` | `30m`, `1h`, `1h30m` |
| `--for` | comma-separated people — `alex` or `alex,jordan` |
| `--alert` | offsets **before** the start: `1d,1h,15m` |
| `--via` | channel letters: `r`, or `r,e` |
| `--note` `--location` `--priority` `--notice` | extra detail, place, 1–5, early warning |
| `--repeat` | tklr recurrence, e.g. `"d &w MO,TU,WE,TH,FR"` |
| `--target` | goal target, e.g. `3/1w` |
| `--step` (repeatable) `--chain` | project steps; `--chain` makes each depend on the previous |
| `--dry-run` | show the entry and alert times, write nothing |
| `--raw` | a complete tklr entry — skips assembly, keeps every check |

It refuses what cannot work — an undefined `--via` letter (listing the ones that
exist), `--alert` without `--via`, a `--when` it cannot parse, a goal without
`--target`, `3/w` instead of `3/1w` — and warns without blocking when a timed
reminder has **no alert** ("will not notify anyone") or no `--for`.

Use `--raw` only for something the flags cannot express, and expect to get the
grammar exactly right when you do; `references/tklr-syntax.md` is the authority.

### What the user says → what you run

All verified against tklr 1.0.43. `R` is `scripts/tklr_agent_wrapper.py`.

| Request | Command |
|---------|---------|
| "Dentist Friday at 3, remind me a day and an hour before" | `--type event --subject Dentist --when "friday 3pm" --duration 1h --for alex --alert 1d,1h --via r` |
| "Coffee with Sam tomorrow 11:30" | `--type event --subject "Coffee with Sam" --when "tomorrow 11:30" --duration 45m --for alex --alert 15m --via r` |
| "Standup every weekday at 9" | `--type event --subject Standup --when "2026-08-03 9am" --duration 30m --repeat "d &w MO,TU,WE,TH,FR" --for alex --alert 10m --via r` |
| "Pay the mortgage on the 1st every month" | `--type task --subject "Pay mortgage" --when 2026-08-01 --repeat "m &i 1" --priority 1 --for alex --alert 1d --via r,e` |
| "Our anniversary is Aug 15, remind us both a week ahead" | `--type event --subject Anniversary --when "aug 15" --repeat y --for alex,jordan --alert 1w,1d --via r,a` |
| "1:1 with Dana every other Tuesday at 10" | `--type event --subject "1:1 with Dana" --when "2026-08-04 10am" --duration 30m --repeat "w &i 2 &w TU" --for alex --alert 10m --via r` |
| "Remember to buy milk" | `--type task --subject "Buy milk" --for alex` |
| "Renew my passport by Sept 1, start warning me a month out" | `--type task --subject "Renew passport" --when 2026-09-01 --priority 1 --notice 30d --for alex --alert 1w --via r` |
| "Water the plants every 3 days after I last did it" | `--raw '~ Water plants @s 2026-08-01 @o 3d @b alex/users @a 1h: r'` (no flag for `@o` yet) |
| "Plan the trip — flights, hotel, dog sitter" | `--type project --subject "Plan trip" --for alex --step "Book flights" --step "Reserve hotel" --step "Arrange dog sitter"` |
| "Renovate: demo, then rewire, then drywall" | `--type project --subject Renovate --for alex --step Demo --step Rewire --step Drywall --chain` |
| "I want to exercise 3 times a week" | `--type goal --subject Exercise --when 2026-08-01 --target 3/1w --for alex` |
| "Lunch with Priya at Cafe Ambrosia Tuesday noon" | `--type event --subject "Lunch with Priya" --when "tuesday noon" --duration 1h --location "Cafe Ambrosia" --for alex --alert 30m --via r` |
| "Flight at 3pm Pacific on the 10th" | `--raw '* Flight to Seattle @s 2026-08-10 3p z US/Pacific @e 5h @b alex/users @a 3h: r'` (no flag for timezones yet) |
| "Team meeting at 2, 30 min travel each way" | `--raw '* Team meeting @s 2026-08-06 2p @e 1h @w 30m, 30m @b alex/users @a 1h: r'` (no flag for `@w` yet) |
| "Note: Sam prefers morning meetings" | `--type note --subject "Sam prefers morning meetings" --for alex` |

## Changing and completing things

### Working out which reminder they mean

Users never say ids. They say "the dentist thing", "Friday's meeting", "my 3pm",
"next Monday's standup". Turning that into one record is the first half of every
change, and getting it wrong on a delete is unrecoverable.

**Search, then narrow with predicates.** `find` and `query` return ids and
subjects but **no dates, times, or owners**, so never disambiguate from their
output alone:

```
$ tklr find dentist
* Dentist checkup (id 1)
* Dentist follow-up (id 2)
* Dentist for Jordan (id 3)
~ Call dentist about insurance (id 4)      ← four matches, nothing to choose by
```

Compose a query instead — all of these are verified:

```bash
python3 $R find dentist                 # everything matching
python3 $R find --person alex            # everything of one person's
python3 $R list --date friday            # one day, with times
python3 $R show <id>                     # the full record
```

Use what the request already tells you: "Friday's dentist" gives you a date
window, "Jordan's dentist" gives you a bin, "cancel my dentist appointment"
rules out the task.

**Then read the candidates.** `details` is what shows date, time and owner:

```
id 1   * Dentist checkup @s 2026-08-07 15:00 @e 1h @b alex @a 1d: r
id 2   * Dentist follow-up @s 2026-09-11 10:00 @e 30m @b alex @a 1d: r
id 3   * Dentist for Jordan @s 2026-08-07 09:00 @e 1h @b jordan @a 1d: r
```

Alternatively, when the request names a day, `days --start <date> --end 1
--plain --ids` lists that day *with times*, which is often the fastest route.

### Confirm before mutating

Deletes and moves are irreversible — tklr has no undo and no trash. So:

**Always confirm** a delete (any scope), a reschedule, and the delete-leg of an
edit. **Don't confirm** adds (a wrong add can be deleted) or `finish` on an
unambiguous task — "I've done that" is the most frequent thing anyone says, and
prompting every time teaches people to stop reading the prompts.

**Resolve it with `--dry-run` first**, so what the user confirms is what
actually changes — not your description of it:

```bash
python3 $M delete 42 --dry-run
```

```
WOULD delete the ENTIRE reminder, including every occurrence
  id 42: 'Dentist checkup'
  * Dentist checkup @s 2026-08-07 15:00 @e 1h @b alex @a 1d: r
  (nothing was changed)
```

Then put *that* to the user in plain language — the thing, not the tokens:

> Cancelling the dentist checkup on Friday Aug 7 at 3pm — that's the one in
> your calendar, not Jordan's 9am. Go ahead?

Rules that make the confirmation worth asking:

* **Name the distinguishing details** — date, time, and whose it is. "Shall I
  proceed?" earns a reflexive yes and catches nothing.
* **Say the scope out loud.** "the whole repeating series" versus "just next
  Monday" are very different losses; `--dry-run` spells out which one you asked
  for.
* **Batch it.** "Cancel all my dentist appointments" gets one confirmation
  listing all three, not three prompts.
* **Never confirm and then act on a different id.** Run `--dry-run`, show it,
  then run the same command without `--dry-run`.

**Resolving to an action:**

| Candidates | What to do |
|-----------|------------|
| exactly one | for a delete or move, `--dry-run` then confirm (above); for `finish` on a task, just do it and report |
| more than one | ask, naming the distinguishing detail in plain language: *"You've got two dentist appointments — the checkup this Friday at 3, and a follow-up on September 11. Which one?"* Never pick the nearest one and hope. |
| none | say so and offer a broader search — don't silently do nothing |
| it's someone else's | say whose it is and confirm before touching it |

**Then read the record before changing it.** `details <id>` prints the tokens
verbatim, and that is your only copy if you are about to delete and re-add.

### What the user says → what you do

| Request | Steps |
|---------|-------|
| "I've done that" (a task) | `$R done <id>` |
| "Cancel Friday's meeting" | find the id, confirm which one, `tklr_mutate.py delete <id>` |
| "Move the dentist to Thursday at 2" | `reschedule <id> --instance '<current datetime>' --to '2026-08-13 14:00'` |
| "Skip next Monday's standup" | `delete <id> --instance '2026-08-10 09:00'` — keeps the rest of the series |
| "Stop the standup after this week" | `delete <id> --from '2026-08-17 09:00'` |
| "Make it an hour instead of 30 minutes" | read, compose, **`check`**, delete, re-add, verify — see *Editing = validate, then replace* |
| "Skip the next three Mondays" | one `--instance` delete only works **once** per record (below); read the tokens, delete, re-add with a comma-separated `@-` list |

### Editing = validate, then replace — in that order

There is no edit command, so changing a detail means replacing the record. The
order is load-bearing, because a delete cannot be undone:

```bash
# 1. read the original — this is your only copy
python3 $R show 42
#    * Team meeting @s 2026-08-06 14:00 @e 30m @b alex @a 1h: r

# 2. compose the replacement, changing only what was asked
#    * Team meeting @s 2026-08-06 14:00 @e 1h  @b alex @a 1h: r

# 3. VALIDATE IT FIRST — never delete until the replacement is known good
python3 $R --raw '* Team meeting @s 2026-08-06 14:00 @e 1h @b alex @a 1h: r' --dry-run
#    must print "WOULD create: …"

# 4. only now delete the original
python3 $M delete 42

# 5. add the replacement — same command without --dry-run
python3 $R --raw '* Team meeting @s 2026-08-06 14:00 @e 1h @b alex @a 1h: r'
#    prints "created id NN: …"; it also checks for a draft and heals

# 6. confirm the result
python3 $R list --date 2026-08-06
```

`--raw` is right for an edit: you are reproducing an existing entry with one
field changed, so you already have exact tokens and don't want them
re-derived. It still validates, reads `add`'s output, rejects a draft, and
heals — you only lose the field assembly.

If step 3 fails, stop — nothing has been destroyed yet. If step 5 somehow
fails after the delete, say so immediately and re-add from the tokens you kept
in step 1; do not go quiet about it.

Note the record gets a **new id**, and `details` collapses bin paths to the leaf
(`@b alex`, not `@b alex/users`). Re-adding the leaf form is fine — the bin
already exists, so the person association survives. Verified: the replaced
record is still `bin alex (inside users)` and still matches
`query 'in b ^alex$'`.

Tell the user you replaced it, and mention anything you could not carry over.

### What you can and cannot change

The CLI has **no edit command and no delete command**, and `finish` only works
on tasks. Verified on 1.0.43:

| Want to | How |
|---------|-----|
| complete a task | `$R done <id>` |
| complete an event | not a thing — `finish` replies "No changes made; task may already be finished" and leaves it on the calendar. Delete it instead. |
| delete anything | `scripts/tklr_mutate.py delete <id>` |
| delete one occurrence | `scripts/tklr_mutate.py delete <id> --instance '<datetime>'` |
| delete this and future | `scripts/tklr_mutate.py delete <id> --from '<datetime>'` |
| move one occurrence | `scripts/tklr_mutate.py reschedule <id> --instance '<current>' --to '<new>'` |
| change any other detail | delete and re-add — there is no edit |

```bash
python3 $M delete 42
python3 $M reschedule 42 --instance '2026-08-07 14:00' --to '2026-08-13 15:00'
```

**Why a script here.** These operations exist in tklr but have **no CLI
surface** — `add` and `finish` are the only mutations the command line offers.
The shim calls tklr's own `Controller` methods (the same ones its UI uses) under
tklr's own interpreter, so cascades and derived tables stay correct. It is a
temporary measure; when `tklr delete` and `tklr edit` appear, delete the script.

It checks each function exists and accepts the arguments it is about to pass,
then verifies the outcome — the target gone or moved, every other reminder
untouched — and rebuilds derived tables. If tklr's internals have moved it
refuses and tells you the current signature:

```
error: tklr no longer provides Controller.delete_record().
  Workaround: the interactive UI can do this — run `tklr ui`, select the
  reminder, and delete or reschedule it there.
```

If that happens, relay it: the user can do it in `tklr ui` themselves, and the
skill needs updating. **Do not** go hunting for the renamed function and patch
the script on the fly — a guess about an unfamiliar internal API, applied to
something that deletes user data, is exactly the wrong risk to take. Report the
signature the error gives you and let a human decide.

Never re-add a corrected copy and call it moved — that silently doubles the
entry, and both copies will alert. Delete the original first.

**Only one occurrence can be excluded per recurring record.** `delete
--instance` writes an exdate into the record (`@- 20260811T0900`); a *second*
call on the same record is declined by tklr. To skip several occurrences, read
the tokens with `details`, delete the record, and re-add with a
**comma-separated** exdate list in a single token:

```
@- 2026-08-11 9a, 2026-08-12 9a
```

Separate `@-` tokens are rejected — `@- <dt> @- <dt>` fails validation. The
comma form is verified: both occurrences disappear from the series and the rest
survive. `--instance` accepts any datetime tklr can parse (`2026-08-11 09:00`,
`9:00`, and `9a` all work) but it must resolve to an occurrence that actually
exists, or tklr declines.

Always re-run the dispatcher's heal command
(`python3 ~/.hermes/scripts/tklr_alert_poller.py --heal`) after a change that
touches alerts. Note `--heal` is a flag on *our* dispatcher script, not a tklr
option — tklr has no way to force a rebuild, which is why the flag exists.

## Alerts

### How delivery works

tklr's own alerts only fire while its UI is running, and this skill never
runs the UI. The dispatcher does exactly what tklr's UI does in
`execute_due_alerts()`, just from cron:

1. `@a` on a reminder creates rows in tklr's `Alerts` table — **one row per
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

## Setup: check first, then load the guide

**Before anything else in a session that touches alerts, confirm setup is
complete.** Run the installer — it is idempotent, so it is also the readiness
check, and it never sends anything:

```bash
bash ~/.hermes/skills/productivity/tklr-reminders/scripts/install.sh
hermes cron list | grep tklr-alert-poller
```

Setup is complete only when **all four** of these hold:

| | must be true |
|---|---|
| tklr | `install.sh` reports `ok: already installed` or installs it |
| workspace | `ok: config.toml present` and `ok: tklr.db present` |
| channels | `ok: letters defined: …` — **not** `no channel letters defined yet` |
| cron job | `hermes cron list` shows `tklr-alert-poller` |

**If any of them fails, load `references/setup.md` and follow it.** Do not
improvise setup from this file — the procedure is deliberately not here, because
getting it wrong produces a system that looks configured and silently delivers
nothing. That has happened repeatedly: a letter pointing at a target that does
not exist, a letter with no `From:` header, a reminder whose alert was never
scheduled. `references/setup.md` covers discovery, writing letters safely,
proving delivery end to end, and what to tell the user afterwards.

**Never announce that setup is done without having seen an alert delivered.**
`references/setup.md` has the verification sequence; the passing signal is
`1 due, 1 sent` from the dispatcher plus the user confirming it arrived. Silence
from the dispatcher means nothing was due — which is exactly how a broken setup
looks.

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

`--heal` is a flag on `tklr_alert_poller.py`, **not** a tklr option. tklr
exposes no way to force a rebuild — that missing command is the whole reason
the flag exists, and it is what the bug report asks for
(`tklr rebuild --force`).

## Direct SQLite use

**Use the `tklr` command for everything. Never open `tklr.db` yourself.**
This applies to reads as much as writes — no `sqlite3` in your commands, no
convenience queries, not even "just to check something". If a question seems
to need SQL, it can almost certainly be answered with `$R find`,
`$R show`, or `$R list`; if it genuinely can't, say so
rather than reaching into the database.

The only exceptions are three narrow cases inside
`scripts/tklr_alert_poller.py`, each existing solely because tklr's CLI has
no equivalent. Do not extend this list, and do not copy the pattern
elsewhere:

1. **Deleting a fired alert.** tklr has no delete-alert command. Safe
   because `populate_alerts()` only regenerates rows with
   `trigger_datetime >= now`, so an alert whose trigger has passed is never
   recreated. Keyed on `(record_id, start_datetime, alert_name,
   trigger_datetime)` — *not* `alert_id`, which
   `tklr alerts --format json` reports as `null`.
2. **Clearing two derived-state cache keys** (`datetimes`, `alerts`) to force
   the rebuild described above, plus the one `SELECT` that detects the
   condition. These are caches tklr regenerates on its next command, not user
   data.
3. **Reading due alerts** from the `Alerts` table. `tklr alerts` cannot serve
   this: `get_alerts_for_window()` filters `trigger_datetime BETWEEN now AND
   window_end`, so it reports only alerts still in the *future* — a past-due
   alert is filtered out or replaced by a regenerated future row. A
   dispatcher that missed a tick would lose that alert permanently, so late
   alerts can only be found in the table.

Everything else goes through the CLI. The table is refreshed by running
`tklr alerts`, and the message text comes from the `[alerts]` command, which
tklr renders with `{name}`, `{when}`, `{description}` and the rest — so the
dispatcher never needs to read a record.

All three exceptions should disappear when tklr gains the corresponding
commands (something like `tklr alerts --clear` to expose the existing
`mark_alert_executed()`, `tklr rebuild --force`, and a way to list alerts
whose trigger has passed).

## When something isn't working

| Symptom | Cause and fix |
|---------|---------------|
| A reminder never fires | `$R show <id>` — a leading `?` and an `@d Import error` mean it was rejected. `$R channels` to check the letter exists, then re-create it. |
| Added an event, no alert row appears | Stale derived state. `python3 ~/.hermes/scripts/tklr_alert_poller.py --heal` |
| A just-added event is missing from `days`/`agenda` but `details <id>` shows it | Same stale derived state — run the dispatcher with `--heal`, then re-read. |
| Alert fires but nothing arrives | Run the dispatcher by hand — it prints the failure and logs it. Then test the letter's command directly, e.g. `hermes send --to <target> test`. |
| "command could not be parsed" | The subject or `@d` contains a `"`, which breaks `shlex`. Reword the reminder. |
| "alert has no command" | The letter used in `@a` isn't defined in `[alerts]`. |
| Reminder delivered to nobody, but reported as sent | A letter is defined as a no-op (`true`, `:`). Replace it with a real delivery command. |
| Alert delivered repeatedly | Its `Alerts` row isn't being deleted — check the log for a command that keeps failing, since a failing row is retried every minute by design. |
| Nothing fires at all | `hermes cron list` — is `tklr-alert-poller` there? Is the scheduler running (`hermes cron status`)? |
| `tklr: command not found` | `export PATH="$HOME/.local/bin:$PATH"`, or re-run `install.sh`. |
| Entry rejected on a date | Don't pass `tomorrow` or `next week`; compute the date. |
| A listing looks wrong in chat | Add `--plain`, and `--width 60` for narrow screens. |

Everything lives in the tklr workspace — reminders, and the `[alerts]`
delivery config, in `config.toml` and `tklr.db`. The skill's only other
footprint is `~/.hermes/scripts/tklr_alert_poller.py` and the log at
`~/.hermes/logs/tklr-alerts.log`. There is no separate state directory.

## Portability

Nothing here is host-specific. Delivery commands live in the tklr workspace's
`[alerts]` section, and chat delivery goes through whatever platforms
`hermes send --list` reports on that machine. Setting this up elsewhere means
running `install.sh`, writing `[alerts]` letters for that machine's channels,
and creating the cron job. Moving the workspace moves the reminders *and*
their delivery config together.
