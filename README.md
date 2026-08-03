# hermes-skills

Skills for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Each directory under `skills/` is a self-contained skill with its own `SKILL.md` and README. They're written for Hermes specifically — they lean on `hermes send`, `hermes cron`, and the `~/.hermes/scripts/` execution allowlist — so they aren't drop-in for other agents that read the same SKILL.md format.

## Skills

| Skill | What it does |
|---|---|
| [tklr-reminders](skills/tklr-reminders) | Personal schedule assistant — appointments, events, tasks, and reminders in plain language, with alerts delivered to your own chat and email channels. Backed by [tklr](https://github.com/dagraham/tklr-dgraham). |

## Installing

Register this repo as a skill source once, then install whichever skills you want:

```bash
hermes skills tap add 37Rb/hermes-skills
hermes skills install 37Rb/hermes-skills/skills/tklr-reminders --category productivity
```

A *tap* is just a GitHub repo Hermes searches for skills. `hermes skills tap add` always looks in this repo's `skills/` directory, and treats each subdirectory there as one skill.

`--category` picks the folder it lands in under `~/.hermes/skills/`. It's organisational only — a skill works the same installed flat. See each skill's own README for what it needs and how to set it up.

To browse before installing:

```bash
hermes skills inspect 37Rb/hermes-skills/skills/tklr-reminders
```

## License

MIT — see [LICENSE](LICENSE). Individual skills may drive third-party tools under their own licenses; each skill's README says which.
