# Setting up tklr-reminders

Everything needed to take this skill from "just installed" to "delivering
alerts". Load this only when setup is incomplete — `SKILL.md` says how to tell.
Once alerts are being delivered you never need this file again.

**Do all of it yourself.** The user's only job is choosing which channels they
want and confirming a test alert arrived. Discovery, config editing, the cron
job, and verification are yours. Never hand them a command to run.

```bash
R=~/.hermes/skills/productivity/tklr-reminders/scripts/tklr_agent_wrapper.py
S=~/.hermes/skills/productivity/tklr-reminders/scripts/set_alert_channel.py
P=~/.hermes/scripts/tklr_alert_poller.py
H=~/.config/tklr
```

## Channel letters are the routing table

Delivery is configured entirely in the `[alerts]` section of the tklr
workspace's `config.toml`. Each key is a channel; its value is the command
that performs the delivery:

```toml
[alerts]
r = 'hermes send --to matrix:!room:matrix.org --quiet "⏰ Reminder: {name} — starts {when} ({start}). {description}"'
a = 'hermes send --to telegram:-1001234567890 --quiet "⏰ Reminder: {name} — starts {when} ({start})"'
```

tklr substitutes `{name}`, `{when}`, `{start}`, `{time}`, `{location}`, and
`{description}` before storing the command, so the message wording is config,
not code.

Three constraints that shape everything:

* **Keys must be a single lowercase letter** (`a`–`z`). Multi-character names
  are rejected. `n` is built-in (bell + popup) and useless without the UI, so
  25 letters are available.
* **A letter therefore means a (person, channel) pair**, not just "chat" — a
  fixed command cannot know *whose* chat. Name them per person, e.g. `r`/`e`
  for Alex's chat/email, `a` for Jordan's chat.
* **Commands run via `shlex.split`, not a shell.** Quote arguments containing
  spaces; wrap pipes in `sh -c "..."`. A `"` inside a subject or `@d` breaks
  parsing — the dispatcher reports and drops such an alert rather than
  looping. Prefer typographic quotes in reminder text.
* **Never put an apostrophe (`'`) in an alert command.** tklr rewrites
  `config.toml` on every run and re-emits each value inside *single* quotes,
  so an apostrophe produces invalid TOML — and the run after that discards the
  whole `[alerts]` section as unparseable and rewrites the file without it.
  Your channel silently disappears two commands later. Write `It is time`, not
  `It's time`; use `{name}` rather than possessives.

Combine freely: `@a 1d, 1h: r, e` is a day and an hour before, to Alex's chat
and email. `@a 1h: r, a` alerts Alex and Jordan. Separate `@a` tokens give
different people different lead times: `@a 1d: r @a 15m: a`.

> **Offsets are measured backwards from `@s`, not forwards from now.** This is
> the easiest thing to get wrong, and it silently produces a reminder that
> looks scheduled but stays quiet for hours:
>
> ```
> now 16:50 ·  @s 18:00  @a 5m   →  fires 17:55, i.e. in 65 minutes
> now 16:50 ·  @s 16:56  @a 5m   →  fires 16:51, i.e. in 1 minute
> ```
>
> To make an alert fire *n* minutes from now, set `@s` to
> `now + n + offset`. When you want a test that fires almost immediately,
> compute the start time — don't pick a round hour.

> **An undefined letter would make the whole entry invalid**, and tklr stores
> such entries as silent drafts. `$R` refuses an unknown `--via` letter up
> front and lists the ones that exist, so this cannot reach the database —
> check with `$R channels`.
>
> **Never define a letter as a no-op** like `'true'` to make an entry
> validate. The dispatcher would count it as delivered and delete the alert,
> so the reminder would reach nobody, silently.

**Choose letters and offsets yourself, per reminder.** This is not a question
for the user once setup is done. A flight deserves `@a 1d, 3h: r, e`; a meeting
across the hall deserves `@a 10m: r`; a shared event gets everyone's letters.
State what you chose in plain words — "I'll ping you an hour before, and email
you the day before" — so they can correct it if they want something else.

## Setting up a person's channels

**You do all of this.** The user's only job is choosing which channels they
want; discovery, config editing, and verification are yours.

**Step 1 — discover what this machine can reach. Run these yourself:**

```bash
hermes send --list                 # chat targets, grouped by platform
himalaya account list --json       # email accounts; himalaya is how Hermes does email
command -v notify-send             # desktop notification available?
```

**Chat and email are two different mechanisms.** `hermes send` covers chat;
`himalaya` covers email. Never mix them up:

* **Chat** — copy a target verbatim from `hermes send --list`. Do not invent one. An
  account or provider name is not a platform: `--to my-mail-provider` is rejected, and any
  target that merely *looks* plausible fails silently forever.
* **Email** — `himalaya account list --json` prints
  `{"accounts":[{"name":"...","default":true,...}]}`. Accounts listed means email is
  available; an empty list or a missing `himalaya` means it is not, and you should
  say so rather than improvise. Do **not** reach for `hermes send --to email:…`
  unless email actually appears in `hermes send --list` — configuring email as a
  Hermes *platform* is a separate job most people have not done, and himalaya is the
  normal route regardless.

**Two things about the email command, both verified the hard way:**

1. **`From:` is mandatory** and must be the himalaya account's own address. Omit it
   and himalaya exits 1 with ``No `From:` header found in raw message`` — nothing is
   sent, and every alert on that letter fails for the life of the reminder.
2. **`To:` is usually a different address.** The account's address is where mail is
   sent *from*; where a person *reads* mail is a separate question. Ask them.

**Getting the `From:` address.** `himalaya account list --json` returns the account
*name* only, and `himalaya account check` reports backend health — neither reveals the
address. It exists solely in himalaya's config, so ask the helper, which prints nothing
but `account<TAB>address`:

```bash
python3 $S --mail-accounts        # -> personal<TAB>you@example.com
```

**Never open `~/.config/himalaya/config.toml` yourself.** In a common setup it holds
the account password in plaintext, and anything you read from it can end up echoed
into the conversation. The helper reads it once, prunes every key that looks like a
credential, and emits only addresses. Use `--mail-accounts` and nothing else.

That gives you `From:`. `To:` is still a question for the user — the address mail is
sent *from* is rarely where they read it.

**Copy the target string from that output verbatim — never type one from
memory, and never reuse one you saw in an example or in a previous session.**
Target ids look guessable and are not; they differ between installs. `hermes
send` prints `sent` and exits 0 for a room that does not exist, so a wrong id
is a silent black hole: the dispatcher records a successful delivery, deletes
the alert, and the message reaches nobody. `set_alert_channel.py` now checks
the target against this list and refuses an unknown one, which is the only
place the mistake is catchable.

**Step 2 — report findings in plain language and ask only for a choice.** Not
a wall of output, and no shell commands. Something like:

> I can reach three places from here: a direct chat, a group chat called
> "Household", and email through your configured account. Where would you like
> your reminders — and is anyone else using this?

Name the platforms the way `hermes send --list` did — Matrix, Telegram, Signal,
Discord, Slack, SMS, whatever this machine actually has. Nothing in this skill
prefers one; the letter's command is just a shell command, so any target
`hermes send` accepts works identically.

If exactly one channel exists and only one person is involved, say what you're
going to do and proceed rather than interrogating them.

**Step 3 — add each letter with the helper script. Do not edit `config.toml`
by hand and do not write your own TOML code:**

```bash
S=~/.hermes/skills/productivity/tklr-reminders/scripts/set_alert_channel.py

python3 $S r 'hermes send --to matrix:!PASTE_FROM_SEND_LIST:matrix.org --quiet "⏰ Reminder: {name} — starts {when} ({start}). {description}"'
python3 $S e 'sh -c "printf \"From: ACCOUNT_ADDRESS\\nTo: THEIR_ADDRESS\\nSubject: Reminder: {name} - starts {when} ({start})\\n\\n{name}\\nWhen: {start} ({when})\\n{description}\\n\" | himalaya message send"'
python3 $S --list          # show what is configured
python3 $S --remove r      # delete a letter
```

The script exists because hand-editing this section goes wrong in three
specific ways, and it handles all of them: the section normally *exists but
holds only comments* (so "append after `[alerts]`" and "create the section"
both misfire); an apostrophe silently destroys the section two tklr runs later;
and confirming a letter survived requires running tklr twice. It also rejects
uppercase/multi-character letters, the reserved `n`, and no-op commands like
`true`, then verifies the letter round-trips and that `@a 1h: <letter>`
actually validates. On success it prints exactly that:

```
added 'r' and verified it:
  survives tklr rewriting config.toml
  '@a 1h: r' validates
  configured letters: r
```

Copy command shapes from `templates/alerts-config-example.toml` — chat via
`hermes send`, email via `himalaya`, desktop via `notify-send` — rather than
inventing syntax. The email one is the least forgiving: it nests a `printf` inside
`sh -c` inside a TOML single-quoted string, so copy it and change only the two
addresses.

**Step 4 —** the script has already verified the letter parses, so go straight
to *Proving it works* for the end-to-end check.

Only come back to the user to confirm it's done — or to ask whether the test
alert actually arrived, which is the one thing you cannot check.

## First-run setup

```bash
bash ~/.hermes/skills/productivity/tklr-reminders/scripts/install.sh
```

Idempotent — safe to re-run. It installs tklr with
`uv tool install --python '>=3.12' tklr-dgraham`, creates the workspace, copies
the dispatcher to `~/.hermes/scripts/`, and reports whether any `[alerts]`
letters are defined yet.

It uses **uv**, preferring Hermes' own copy at `$HERMES_HOME/bin/uv` (which is
*not* on `PATH`) over any `uv` that is. uv provisions a suitable CPython itself
when the machine has none, which is why no interpreter hunting is needed — the
`'>=3.12'` range lets it reuse an existing Python and download one only if
nothing qualifies.

**If it reports a Python problem, read carefully.** tklr requires Python
3.12+, and inside the Hermes agent `python3` is the agent's *own venv*
interpreter, which may be older (3.11 here). Passing an interpreter that old to
any installer makes pip print:

```
ERROR: Could not find a version that satisfies the requirement tklr-dgraham
       (from versions: none)
ERROR: No matching distribution found for tklr-dgraham
```

That is a **version mismatch, not a missing package** — every release requires
`>=3.12`, so a 3.11 interpreter sees zero candidates. Do not conclude tklr is
unavailable, and do not try to install it manually. `install.sh` searches for a
3.12+ interpreter itself (`python3.14/13/12`, then `/usr/bin/python3`), so just
run it; if genuinely none exists it lists every interpreter it found and tells
you to install one or pass `--python /path/to/python3.12`.

**Why the copy:** the Hermes cron scheduler will only execute scripts that
resolve *inside* `~/.hermes/scripts/`. It rejects absolute paths, `../`
traversal, and symlinks alike (`path.resolve()` then `relative_to()`, with the
comment "scripts MUST reside within HERMES_HOME/scripts/"), so pointing a job
at the skill directory is blocked and a symlink escapes the check too. The
skill directory stays the source of truth; `install.sh` copies the file in.
Re-run `install.sh` after editing the dispatcher, or the cron job keeps
running the old copy.

It deliberately does **not** invent `[alerts]` letters — a placeholder no-op
would silently swallow every reminder — and does not create the cron job.
Finish setup in this order:

1. Ask who is using this and which channels each person wants
   (`hermes send --list`).
2. Add a letter per (person, channel) with `$R channels --set <letter>
   '<command>'`, using `templates/alerts-config-example.toml` for the command
   shapes. It verifies each letter survives and validates.
3. Create the dispatcher. Run this **exactly** — the argument shapes matter:

```bash
hermes cron create '* * * * *' --script tklr_alert_poller.py \
  --no-agent --name tklr-alert-poller --deliver local
```

* **`--script` takes the bare filename**, resolved *inside*
  `~/.hermes/scripts/`. Do **not** pass a path.
  `--script ~/.hermes/skills/productivity/tklr-reminders/scripts/tklr_alert_poller.py`
  is **rejected** — the scheduler resolves the path and refuses anything
  outside `~/.hermes/scripts/` ("Blocked: script path resolves outside the
  scripts directory"), and that includes absolute paths, `../`, and symlinks.
  `install.sh` has already put the file where it needs to be.
* **The schedule is a positional argument** and comes first, before the flags.
* `--no-agent` takes no value. `--deliver local` keeps the output in the log
  instead of messaging anyone, which is what you want since the dispatcher
  sends alerts itself.

Then confirm it exists, rather than assuming:

```bash
hermes cron list | grep -A6 tklr-alert-poller
```

4. Verify end to end, following **Proving it works** below. Do not skip this
   and do not report success without it.

## Proving it works

**A silent dispatcher does not mean an alert was delivered.** It prints
nothing when it has nothing to do, so "no output" is equally consistent with
"there were no alerts at all" — which is exactly how a broken setup looks.
Never conclude "the test passed" from absence of output.

Check positive evidence at each step. Every one of these must hold:

```bash

# 0. The letters SURVIVE tklr rewriting its config. Run tklr twice — erasure
#    takes two commands — then confirm the letters are still there. If any
#    vanished, a value contains an apostrophe.
python3 $R list >/dev/null 2>&1; python3 $R list >/dev/null 2>&1
python3 -c "import tomllib;print('letters:', sorted(tomllib.load(open('$H/config.toml','rb')).get('alerts') or {}))"

# 1. The letter exists. Must print "Entry is valid" — not "Undefined alert command".
python3 $R add --type event --subject Probe --when "in 90 minutes" --alert 5m --via r --dry-run

# 2. Create a test whose ALERT fires in ~3 minutes. Note the arithmetic:
#    start = now + 8 min, offset 5m  ->  trigger = now + 3 min.
#    The trigger MUST be at least 2 minutes out. tklr schedules no alert at
#    all for a trigger in the current minute or the past — silently, with the
#    reminder still showing up in `list`. `add` now refuses that outright.
#    Do NOT pick a round time like 18:00 — with a 5m offset that fires in an
#    hour, and the test tells you nothing today.
python3 $R add --type event --subject "Alert plumbing test" \
           --when "in 8 minutes" --for alex --alert 5m --via r
#    It prints the id, when the alert fires, and a "verified:" line confirming
#    the alert row exists. No "verified:" line means it did NOT get scheduled.
#    It has already confirmed the record is not a draft and healed.

# 4. An alert row must exist. Zero here means nothing can ever fire.
python3 $P --heal                    # non-zero exit = heal was skipped, retry
python3 $P --verbose                 # must report "N still queued", N >= 1

# 5. Wait for the trigger, then confirm delivery actually happened:
python3 $P --verbose                 # should report "1 due, 1 sent"
tail -5 ~/.hermes/logs/tklr-alerts.log   # must contain a "sent" line
```

Then **ask the user whether the alert actually arrived.** Delivery leaving the
machine is not proof it was received; only the recipient can confirm that.

Finally, clean up the test reminder: `python3 $R delete <id>`.

If step 4 reports `0 due, 0 sent, 0 still queued`, the reminder has no alerts —
almost always an undefined `@a` letter or a draft, not a dispatcher fault.

## Offer the channels they are not using yet

Once delivery is proven on the first channel, **go back over what Step 1 found and
offer every route that has no letter yet.** One configured channel is a working
setup, not a finished one, and the user cannot ask for a channel they don't know
you can reach. Alerts are the whole point of the skill; a reminder that only lands
somewhere they aren't looking is the failure this exists to prevent.

Compare the discovered routes against the letters now in `[alerts]`
(`python3 $R channels`) and offer the difference by name:

> That's working now — reminders will reach you on \<configured channel>. I can
> also send them to your email, or pop a desktop notification on this machine.
> Want either of those as well? Some people like a second channel on the things
> they really can't miss.

Rules for this:

* **Offer concretely, never generically.** "Email through \<address>" or "a desktop
  notification here", not "other channels are available". Name what you actually
  found.
* **Ask once, then stop.** If they decline, don't raise it again this session —
  but do treat "not yet" as different from "no": record it as available so a later
  "actually, add email too" needs no rediscovery.
* **Don't re-ask what Step 2 already settled.** If they picked chat there and said
  nothing about email, that's an unanswered question and worth asking. If they
  explicitly said "not email for now", it is answered — respect it.
* **A second channel is per-letter, not global.** Adding email means a new letter;
  existing reminders keep their `@a` letters until changed. Say so, and offer to
  add the new letter to anything important rather than silently leaving it
  chat-only.
* Same verification bar as the first channel: a new letter is not working until an
  alert has actually been delivered through it. Don't announce a channel you have
  not tested.

## Closing out setup

When it works, tell the user **what they can now do** — an overview of the
capability, in their language, with examples of things they can actually say.
Nothing about how it was built: no command cheat sheet, no list of steps you
performed, no tool names. They asked for an assistant; a summary full of `tklr`
invocations tells them the assistant does not exist.

Cover the whole surface, because they cannot ask for what they don't know
exists. Adapt it to what you actually configured — only promise email if an
email letter exists, only mention other people if they're set up:

> You're all set — just talk to me normally about anything time-related.
>
> **Appointments and events.** "Dentist Friday at 3 for an hour." "Coffee with
> Sam tomorrow at 11:30." All-day things work too — "Jordan's birthday on
> August 15th" — as do repeating ones: "standup every weekday at 9",
> "1:1 with Dana every other Tuesday", "pay the mortgage on the 1st of each
> month". I can note a location, and hold travel time either side of a meeting.
>
> **Things to do.** "Remind me to buy milk" for something with no fixed time,
> or with a deadline and a priority: "renew my passport by September 1st, it's
> important — start warning me a month out." Bigger jobs can have steps I track
> together — "plan the Colorado trip: flights, hotel, dog sitter" — and I can
> keep habits honest too: "I want to exercise three times a week."
>
> **Asking me things.** "What's on my calendar today?" "What about tomorrow?"
> "How's my week looking?" "What do I need to get done?" "When's my next
> dentist appointment?" "Am I free Tuesday at 3 for a coffee date?" — for that
> last one I'll check what's around it, not just the slot itself.
>
> **How you get reminded.** Alerts reach you on [name the channels you actually
> configured — whatever `hermes send --list` offered, plus email or desktop if you
> set those up. Never say "Matrix" unless that is what this machine uses]. You can
> have several per event at different times — "remind me a day before and again an
> hour before" — and I'll pick sensible ones if you don't say. [If more than one
> person is configured:] Jordan gets hers on [her channel], and a shared event can
> alert you both.
>
> **Changing and finishing things.** "I've done that" marks a task complete.
> "Cancel Friday's meeting", "move the dentist to Thursday afternoon", "skip
> next week's standup but keep the rest" all work too. To change any other
> detail I'll replace the entry and tell you that's what I did.
>
> I've added a test reminder that should reach you in about a minute — tell me
> whether it arrives, since that's the one part I can't check myself.

Keep it scannable and concrete. The point is that a user who reads it knows the
range of what they can say next; a user who reads "you can use plain language
to add reminders" has learned nothing.

Bad — every line here is a mistake:

> * Installed the tklr tool via pipx (version 1.043)
> * How to use: `tklr add "* Dentist @s tomorrow 3p @a 1d, 1h: r"`

* It names the implementation, which the user should never need to know.
* It states the installer *wrongly* — this skill installs with uv, never pipx —
  and mangles the version. Don't narrate mechanics you'd have to get right;
  just leave them out.
* Worst, it teaches a command that **does not work**: `tomorrow 3p` is rejected
  by tklr. Handing over commands means handing over the traps.

Never give the user tklr syntax, even when they ask how it works — describe the
capability in plain words instead. If they explicitly want the underlying tool,
say what it is and point at `references/tklr-syntax.md`; do not improvise
examples.

## Optional: a daily briefing

A morning summary of the day is **opt-in and off by default** — never
schedule one on your own initiative. Once alerts are working, you may offer
it once:

> "Would you like a short summary of your day each morning? I can send it to
> your chat around 7am."

Only if the user agrees, and using the time and channel they choose, create a
separate job. Ask per person — one person wanting a briefing says nothing
about the others:

```bash
hermes cron create '37 6 * * *' --skill tklr-reminders \
  --name tklr-briefing-alex --deliver 'matrix:!room:server' \
  "Summarise Alex's day: run python3 ~/.hermes/skills/productivity/tklr-reminders/scripts/tklr_agent_wrapper.py list --today, keep only rows in Alex's bin, and write a short friendly summary. Mention conflicts and overdue tasks. If the day is empty and nothing is overdue, output nothing."
```

If the user later wants it to stop: `hermes cron rm <job-id>`. The
every-minute alert dispatcher is unrelated and must stay.

## How the every-minute job actually gets created

Two independent routes create it, and **neither is the blueprint block
itself**:

1. **Setup, step 3 above.** The agent runs `hermes cron create ... --script
   ... --no-agent` while setting the skill up. This is the normal path.
2. **The blueprint, as a safety net.** Its prompt tells the agent to create
   that same job if it has gone missing. So the blueprint *does* cause the
   cron job to be created — by instructing an agent run to do it, not by
   being converted into it.

The blueprint block cannot become the minute job directly, for two reasons
worth knowing before anyone tries:

* **`script` is dropped.** `blueprint_to_job_spec()` passes only prompt,
  schedule, name, deliver, skills, model, provider, toolsets, and `no_agent`.
  A `script:` key in the blueprint survives into the spec's `raw` dict and is
  then ignored, so `no_agent: true` would produce a job with no script —
  which `create_job()` rejects outright ("no_agent=True requires a script").
* **A blueprint without `no_agent` runs the LLM.** At `* * * * *` that means
  an agent invocation every minute, which is exactly what we're avoiding.

Note also that **installing a blueprint skill never schedules anything by
itself.** It registers a *suggestion* that you accept or dismiss
(`register_blueprint_suggestion`), so the daily health check only starts
running once you approve it.

**One minute is the floor.** The Hermes scheduler ticks every 60 seconds and
its interval parser accepts only minutes, hours, and days — no seconds. An
alert may therefore be delivered up to a minute after its trigger. The
poller computes "in 14 minutes" from the actual send time, so the wording
stays truthful. Use the Hermes scheduler, not the system crontab.


## Starting over

`scripts/reset.sh` returns the machine to the state of someone who
has just obtained the skill and not yet configured it: removes the cron job,
the installed dispatcher, the log, the usage registration, any pending
blueprint suggestion, the whole tklr workspace, and tklr itself (via uv, and
only if uv reports owning it). It never touches the skill's own files —
`SKILL.md`, `scripts/`, `templates/`, `references/` — since that is what a new
person starts with, and there is no flag to make it do so.

**It destroys every reminder** — `~/.config/tklr` is deleted outright. Only run
it when the user has explicitly asked to start from scratch, and mention the
data loss first. `--dry-run` shows exactly what would happen and changes
nothing; the interactive run requires typing `NUKE`.

