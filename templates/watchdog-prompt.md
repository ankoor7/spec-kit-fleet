# Watchdog prompt

The prompt of the fleet's periodic tick (`fleet.py prompt watchdog <fleet>`).
Cloud: the hourly Routine bound to the originating session. Local: run it with
`/loop 15m` in the originating Claude Code session, or from cron with
`claude -p`.

```text
Fleet {fleet} watchdog tick. Read {ext}/commands/speckit.fleet.tick.md and
follow it with the argument fleet={fleet}. Say nothing to the owner unless a
rule in that file tells you to.
```
