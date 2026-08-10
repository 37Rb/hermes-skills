# Portability

For developers porting this skill to another machine or another agent. The running agent never needs this file.

Two different questions hide behind "is this portable": moving it to another
machine, and moving it to another agent.

**Another machine, same agent.** Nothing is tied to a particular host. Delivery
commands live in the tklr workspace's `[alerts]` section, and chat delivery goes
through whatever platforms `hermes send --list` reports on that machine. Setting
it up elsewhere means running `install.sh`, writing `[alerts]` letters for that
machine's channels, and creating the cron job. Moving the workspace moves the
reminders *and* their delivery config together.

**Another agent.** This does need work, but it is bounded, because the agent is
a client of this skill and never a component of it. Reminders live in tklr, and
delivery is a plain shell command in `[alerts]` that the dispatcher runs on a
schedule. The dispatcher makes no agent call at all: it runs the string it is
given. So tklr, the wrapper's whole reminder surface, the healing logic and the
`MAX_LATE` reaper all carry over untouched, and the `himalaya` and `notify-send`
routes keep working as-is because those are ordinary CLIs.

Three things genuinely need an agent, and all three live in `scripts/host.py`:

| seam | functions | what a port supplies |
|------|-----------|----------------------|
| chat discovery | `chat_list`, `chat_platforms`, `chat_targets`, `chat_send_command` | some way to enumerate destinations, and the command that sends to one. Returning nothing is valid: the skill then offers email and desktop only. |
| scheduling | `cron_job_present`, `create_cron_job` | run the dispatcher once a minute: `crontab`, a systemd timer, or a refusal that names the command for the user to add by hand. |
| host paths | `dispatcher_path`, `LOG_PATH` | where a scheduled script must live. Hermes rejects any script outside `~/.hermes/scripts/`; a host without that rule returns the skill's own copy and the deploy step becomes a no-op. |

`host.py` is the only file a port edits, and its header states the contract for
each function in terms of the skill rather than of Hermes. Outside it,
`grep -c hermes` over the Python is zero; a new host call belongs in `host.py`,
not at the point of use. The two shell scripts (`install.sh`, `reset.sh`) cannot
import it and carry `HOST-SPECIFIC` headers naming the same seams instead.

What does not carry over is the part calibrated to one model: `setup --platform`
reading the platform off the system prompt, `welcome` and `print_relay`
supplying words so the model does not compose a command, and `## Do this now`
sitting near the end of SKILL.md because position beat wording. Those were
measured against a specific model and a specific way of injecting a skill into
a turn. A different agent means re-testing them live, which is the real
majority of the work, not the three seams above.
