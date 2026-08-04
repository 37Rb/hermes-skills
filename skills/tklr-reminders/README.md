# Tklr Tasks, Scheduling, & Reminders for Hermes Agent

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) skill that turns your agent into a personal schedule assistant: appointments, events, tasks, reminders, and the questions people typically ask about them.

You talk to it in plain language. It works out the commands, and it delivers reminder alerts to whatever channels you use — Matrix, Telegram, Signal, email, SMS, desktop notifications — at the lead times you asked for.

```
You:    Dentist Friday at 3, remind me a day and an hour before
Agent:  Added — Dentist, Friday 15:00–16:00. You'll get a nudge Thursday
        at 3pm and again at 2pm Friday.

You:    What's on tomorrow?
You:    Am I free Tuesday morning?
You:    Move my 1:1 with Dana to Wednesday
You:    Standup every weekday at 9
You:    Pay the mortgage on the 1st every month, warn me a day ahead
```

Behind the scenes is [tklr](https://github.com/dagraham/tklr-dgraham). You don't need to learn its syntax. Just talk to your agent in natural language and let it use the tool.

You can learn it if you want to, though — the skill sits alongside ordinary tklr use rather than replacing it. It uses tklr's normal workspace at `~/.config/tklr`, so you and the agent share one database: anything you add with `tklr` or its UI shows up for the agent, and anything the agent adds shows up for you. [`references/tklr-syntax.md`](references/tklr-syntax.md) documents the grammar.

## Install

Register the repo as a skill source, then install from it:

```bash
hermes skills tap add 37Rb/hermes-skills
hermes skills install 37Rb/hermes-skills/skills/tklr-reminders --category productivity
```

`--category productivity` files it under `~/.hermes/skills/productivity/`. Leave the flag off and it installs flat at `~/.hermes/skills/tklr-reminders` — the skill works either way, since Hermes only uses the category for grouping.

Then invoke the skill:

> `/tklr-reminders`

No further wording needed — that loads the skill and it takes it from there: installs tklr, creates the workspace, installs the alert dispatcher, creates the cron job, and asks you the one thing it can't work out on its own, which is which of your channels should receive alerts.

**On Matrix and Slack, type `!tklr-reminders` instead.** Those clients reserve `/` for their own commands, so a typed `/` never reaches Hermes; their adapters accept `!` and rewrite it. Every other platform uses `/`.

Use the explicit invocation for the first run rather than asking in your own words. Every skill is registered as `/<skill-name>`, and invoking it loads the skill directly — no guessing about whether your phrasing matched. Something like "set up my reminders" relies on the agent picking this skill out of ~65 others from a one-line description, which it may not do, especially if you have used a different calendar tool with it before. Once setup is done, plain language works fine for everyday use.

<details>
<summary>What the installer does</summary>

```bash
bash ~/.hermes/skills/productivity/tklr-reminders/scripts/install.sh
```

Idempotent, so it doubles as a readiness check if something drifts. It:

1. installs `tklr-dgraham` via `uv` (Hermes ships its own `uv`)
2. creates the tklr workspace at `~/.config/tklr` (`config.toml` + `tklr.db`)
3. copies the alert dispatcher into `~/.hermes/scripts/`
4. reports whether any alert channels are defined yet

It deliberately does **not** invent alert channels, and does **not** create the cron job — both need to know your actual delivery targets.
</details>

## What setup involves

The agent handles all of this. It's documented here so you know what landed on your machine, and so you can fix it if something drifts.

**1. Alert channels.** The `[alerts]` section of `~/.config/tklr/config.toml` *is* the routing table. Each key is one lowercase letter naming a (person, channel) pair, and its value is the command that performs the delivery:

```toml
[alerts]
r = 'hermes send --to telegram:YOUR_CHAT_ID --quiet "⏰ Reminder: {name} — starts {when} ({start}). {description}"'
a = 'hermes send --to matrix:!YOUR_ROOM_ID:matrix.org --quiet "⏰ Reminder: {name} — starts {when} ({start})"'
```

A letter's value is a plain shell command, so anything this machine can send with works. Chat goes through `hermes send` — Matrix, Telegram, Signal, Discord, Slack, SMS, or a bare platform name for its home channel — and email goes through `himalaya`, which is how Hermes reaches email. Desktop notifications are just `notify-send`. Nothing in the skill prefers one platform over another; whatever `hermes send --list` and `himalaya account list` report is what you can use.

A reminder then picks offsets and channels: `@a 1h, 15m: r` fires an hour before and again 15 minutes before, both to `r`. See [`templates/alerts-config-example.toml`](templates/alerts-config-example.toml) for a fully commented example including email, SMS, and group chats — and for the several ways this file can bite you (an apostrophe in any value silently erases the whole section two commands later).

Get valid delivery targets with `hermes send --list`. **Use exactly what it prints.** A wrong target is a silent black hole: the send reports success, the alert is marked delivered and deleted, and the message reaches nobody.

**2. The dispatcher cron job.** Tklr normally only fires alerts while its interactive UI is running. This skill replaces that with a once-a-minute job:

```bash
hermes cron create '* * * * *' --script tklr_alert_poller.py \
  --no-agent --name tklr-alert-poller --deliver local
```

`--script` takes a **bare filename** — the scheduler rejects any path outside `~/.hermes/scripts/`, which is why `install.sh` copies the dispatcher there. The skill also ships a daily blueprint health check (06:37) that recreates the job if it goes missing and re-generates any alerts left stranded by stale derived state.

## How it works

```
you → agent → scripts/tklr_agent_wrapper.py → tklr → tklr.db
                                                       │
                          Alerts table (one row per offset × channel)
                                                       │
        hermes cron (every minute) → scripts/tklr_alert_poller.py
                                                       │
                            hermes send (chat) / himalaya (email) / notify-send
```

The dispatcher reads due alerts, runs each one's command, and deletes the row on success — so one row per (offset, channel) gives exact once-only delivery with no separate send ledger. Undelivered rows are retried until they're an hour late, then reported and dropped rather than retried forever.

## Layout

```
SKILL.md                              agent instructions (the skill itself)
README.md                             this file
references/tklr-syntax.md             underlying tklr grammar — only needed for --raw
scripts/tklr_agent_wrapper.py         the one interface: add list show find free
                                        done delete move channels status
scripts/tklr_alert_poller.py          the every-minute dispatcher
scripts/set_alert_channel.py          safely edit [alerts]; validates targets
scripts/tklr_mutate.py                low-level record edits
scripts/install.sh                    idempotent setup / readiness check
scripts/reset.sh                      undo the setup, back to a pristine state
templates/alerts-config-example.toml  commented [alerts] reference
```

## Caveats

- Tklr's own logs grow in `~/.config/tklr/logs/` and are not rotated, yet.
- If you leave `tklr ui` open, it delivers due alerts itself every 6 seconds, from the same table the dispatcher reads. Both delete each row once it's sent, so you normally still get exactly one alert — but a duplicate is possible if the two fire in the same instant.

## License

This skill is MIT licensed — see [LICENSE](../../LICENSE).

Tklr itself is a separate program, installed from PyPI as `tklr-dgraham`, and is licensed GPL-3.0-or-later. This skill invokes the `tklr` command; it does not include or link against its code.
