# Watchdog prompt

The prompt of one session's periodic tick
(`fleet.py prompt watchdog <fleet> --session <id>`). Every session that
brought a spec into the fleet runs its own: it acts on the chains that session
originated, and the coordinator's also keeps the trunk. Cloud: an hourly
Routine bound to that session. Local: `/loop 15m` in that Claude Code session,
or cron with `claude -p`.

```text
Fleet {fleet} watchdog tick for session {originating} (the fleet's coordinator
is {coordinator}). Read {ext}/commands/speckit.fleet.tick.md and follow it with
the arguments fleet={fleet} session={originating}. Say nothing to the owner
unless a rule in that file tells you to.
```
