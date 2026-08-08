# Using the wrapper

Everything you run to create, read, change or complete a reminder. Load this
whenever you are about to compose a command; you do not need it to talk to the
user about what the skill does.

`$R` is `~/.hermes/skills/productivity/tklr-reminders/scripts/tklr_agent_wrapper.py`.
Every example here is something **you** run. None of it is something the user
should ever see or type — see *How to talk about this skill* in `SKILL.md`.

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
| "Where did my time go?" | `$R uses` — jot totals by category and month |

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
| `--type` | `event`, `task`, `project`, `goal`, `note`, `jot` |
| `--subject` | what it is, in plain words |
| `--when` | `"tomorrow 3pm"`, `"friday"`, `"in 2 hours"`, `"2026-08-15 09:00"` |
| `--duration` | `30m`, `1h`, `1h30m` |
| `--for` | comma-separated people — `alex` or `alex,jordan` |
| `--alert` | offsets **before** the start: `1d,1h,15m` |
| `--via` | channel letters: `r`, or `r,e` |
| `--note` `--location` `--priority` `--notice` | extra detail, place, 1–5, early warning |
| `--repeat` | tklr recurrence, e.g. `"d &w MO,TU,WE,TH,FR"` |
| `--offset` | for tasks: reschedule this long **after completion**, e.g. `3d` |
| `--travel` | travel held either side: `30m` or `30m,15m` (before,after) |
| `--timezone` | zone for `--when`, e.g. `US/Pacific`, or `none` to float |
| `--use` | jots only: time-tracking category, e.g. `exercise.walking` |
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
| "Water the plants every 3 days after I last did it" | `--type task --subject "Water plants" --when 2026-08-01 --offset 3d --for alex --alert 1h --via r` |
| "Plan the trip — flights, hotel, dog sitter" | `--type project --subject "Plan trip" --for alex --step "Book flights" --step "Reserve hotel" --step "Arrange dog sitter"` |
| "Renovate: demo, then rewire, then drywall" | `--type project --subject Renovate --for alex --step Demo --step Rewire --step Drywall --chain` |
| "I want to exercise 3 times a week" | `--type goal --subject Exercise --when 2026-08-01 --target 3/1w --for alex` |
| "Lunch with Priya at Cafe Ambrosia Tuesday noon" | `--type event --subject "Lunch with Priya" --when "tuesday noon" --duration 1h --location "Cafe Ambrosia" --for alex --alert 30m --via r` |
| "Flight at 3pm Pacific on the 10th" | `--type event --subject "Flight to Seattle" --when "2026-08-10 3pm" --timezone US/Pacific --duration 5h --for alex --alert 3h --via r` |
| "Team meeting at 2, 30 min travel each way" | `--type event --subject "Team meeting" --when "2026-08-06 2pm" --duration 1h --travel "30m,30m" --for alex --alert 1h --via r` |
| "Note: Sam prefers morning meetings" | `--type note --subject "Sam prefers morning meetings" --for alex` |
| "Jot down that I am taking a walk" | `--type jot --subject "Taking a walk" --for alex` (timestamped now; pass `--when` for a different time) |
| "That walk took an hour and a quarter, count it as exercise" | `--type jot --subject "Taking a walk" --duration 1h15m --use exercise.walking --for alex` |
| "Where did my time go this month?" | `uses` (add `--use exercise` to filter, `--months 2607-2608` for a range) |

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
