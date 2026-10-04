---
description: "Publish a planned fleet (trunk, status branch, chain branches), start its watchdog, and launch every chain's first session"
---

# Launch a fleet

## User Input

```text
$ARGUMENTS
```

The input names the fleet and the draft directory `speckit.fleet.plan` wrote.
Run this in the originating session: the chains it launches report to it, and
its watchdog runs in it. Never run it from a chain session or an unattended
one. If the fleet already exists (another conversation's spec is in it), this
session **joins** it, and step 2 differs.

`F` means `python3 .specify/extensions/fleet/scripts/python/fleet.py`.

## Steps

1. **Identity.** Cloud transport: load
   `mcp__claude-code-remote__create_session`, `send_message`, `get_session`,
   `create_trigger` and `update_trigger` with one ToolSearch `select:` call;
   your session id is the `id` `get_session` returns. Local: no id is needed;
   `publish` makes one.
2. **Publish or join.**
   - **Join** (the fleet exists): `F join <fleet> --from <draft dir> [--originating <session id>]`.
     It adds this spec's chains to the published fleet without touching the
     chains already running, moves the reserved number blocks above every
     block the fleet holds, and creates the new chain branches from the trunk.
     The chains report to this session. The session that published the fleet
     stays its **coordinator** and keeps the trunk-level rules. Local: note
     the session id it prints; this session passes it to `F inbox` and to its
     watchdog. Then skip to step 3.
   - **New fleet:** `F publish <fleet> --from <draft dir> [--originating <session id>]`.
     Local: note the session id it prints, as for a join.
   It creates the trunk from the release target if it is missing, writes the
   status files to the status branch (an orphan branch: no code, no CI), and
   creates each chain branch from the trunk. Ask the owner, once, to protect
   the trunk and the status branch from deletion and force-push on the host
   (no pull-request rule on either: both move by direct pushes).
3. **Watchdog.**
   - Cloud: `create_trigger` with `cron_expression: "0 * * * *"`, bound to
     this session (no `persistent_session_id`, no new session on fire),
     `initiation: human_request`, and `prompt` set to the output of
     `F prompt watchdog <fleet> --session <this session's id>`. Hourly is the
     minimum interval. Every session in the fleet runs its own watchdog; each
     acts on the chains it launched.
   - Local: tell the owner to run, in this Claude Code session,
     `/loop 15m /speckit-fleet-tick fleet=<fleet> session=<id>`, or to add a
     cron entry running `claude -p "$(F prompt watchdog <fleet> --session <id>)"`
     in this checkout.
4. **Launch each chain this session added**, in `merge_order`:
   `F launch <fleet> <chain> --first`.
   - Cloud: pass the printed arguments to `create_session` **unchanged**.
   - Local: it has started the session; show the owner the log path.
   - A chain whose first item waits on another chain's work is refused at
     launch; that is expected. The sibling's `shared` message (or the
     watchdog) starts it when the work is done.
5. **Channel check.** Each launched chain sends `started` within minutes.
   Cloud: the messages arrive as turns in this session. Local: read them with
   `F inbox <fleet> --session <id>`. After 10 minutes, tell the owner in one line which
   chain has not reported.
6. Tell the owner, in one short paragraph: the chains, which ones are waiting
   on which, where to watch (`F state <fleet>`), and that every message from a
   chain is handled by the `speckit.fleet.tick` command.
