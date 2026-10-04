---
description: "Fleet watchdog and message handler: read chain messages and fleet state, restart stalled chains, end waits, and bring decisions to the owner"
---

# Fleet tick

## User Input

```text
$ARGUMENTS
```

The input names `fleet=<fleet>`, and may carry a chain message (a turn that
starts `Fleet <fleet>, chain …`). Run this in the originating session only:
on every chain message, and on every watchdog tick.

`F` means `python3 .specify/extensions/fleet/scripts/python/fleet.py`.
"Start the chain" means `F launch <fleet> <chain>`, then, on the cloud
transport, `create_session` with exactly the arguments it prints. Say nothing
to the owner unless a rule below says to.

## 1. Messages

Cloud: the message is this turn's input. Local: `F inbox <fleet>` prints
every unread message; handle each in order.

| kind | do |
| --- | --- |
| `started` | Note it for the channel check. Nothing else. |
| `shared` | For every sibling item whose `needs` names the shared item and are now all `done`: if it is a `blocked` wait, `F set <fleet> <chain> <item> --status pending --note "needs done"`; then start its chain if the chain has no running item. |
| `blocked` | A wait (`waits for …`): nothing; `shared` ends it. Anything else: put it to the owner (section 3). |
| `conflict` | Run `speckit.fleet.resolve` for the conflict id. |
| `ready` | Tell the owner in one line that the chain is ready to merge and waiting for the trunk's checks, naming the failing run and the chain that owns the fix. If that chain's items are all `done`, queue a fix item on it (`F add-item <fleet> <chain> <id> --title … --before-integrate`) and start it. |
| `merged` | Tell the owner in one line what the trunk now holds. |
| `finished` | Note it. When every chain has finished, run the wrap-up (section 4). |

## 2. State rules

Run `F state <fleet> --json`. Then:

1. **First tick after launch:** a chain with no `started` message: tell the
   owner in one line that the channel failed for that chain.
2. **Open questions:** an `open` conflict, or a `blocked` item that is not a
   wait, with no answer in `owner-decisions.md` (`F get <fleet> owner-decisions.md`):
   handle it as if its message had arrived.

For each chain, apply the **first** rule that matches. The next item is the
first item, in queue order, that is not `done`.

3. An item is `running`: is its session alive? Cloud: `get_session` on its
   `session_id`. Local: `F alive <session id>` (exit 1 means dead). Alive:
   nothing. Dead: `F set … --status pending --note "session <id> ended without closing"`,
   then start the chain.
4. An `open` conflict names the chain: nothing.
5. The next item is `pending` and every item in its `needs` is `done`: start
   the chain.
6. The next item is a `blocked` wait and its `needs` are now all `done`:
   set it `pending` with a note, and start the chain.
7. The next item is `ready` and the release pull request's checks pass on
   the trunk head: set it `pending` with a note, and start the chain.
8. Otherwise, or every item `done`: nothing.

Then the trunk:

9. The release pull request's checks fail on the trunk head, and the merge
   that broke them (the oldest failing head since the last passing one; its
   `merged <sha>` note names the item) belongs to an item already `done`,
   with no fix item pending or running: queue a fix item on that chain and
   start it.
10. The release target is ahead of the trunk (`trunk_state.behind` > 0) and
    no `trunk-sync` item is pending or running: merge the release target into
    the trunk yourself only if it is a clean merge that passes `F gate full`;
    otherwise tell the owner.
11. The trunk is ahead of the release target and no release pull request is
    open: open one (`<trunk>` → `<release_target>`). Agents never merge it;
    the owner does.

## 3. Asking the owner

One `AskUserQuestion` question per open matter, framed by purpose: what each
side's work is for, citing the user story, `FR-…` and acceptance scenario,
never conflict, item or session ids. Two to four options, the recommended one
first. Record every answer, dated, in `owner-decisions.md` (`F get`, edit a
copy in a scratch directory, `F put`), then act on it.

## 4. Wrap-up

When every chain has sent `finished` (or its items are all `done`): if the
Wrap-up section of `owner-decisions.md` still reads `_Not yet run._`, run the
owner Q&A over every chain's register (`F get <fleet> HANDOFF-<chain>.md`) and
every conflict still `logged`, and replace that line with the answers.
Checking the marker first stops two wakes from running it twice. Then tell
the owner in one line whether the trunk is releasable: its head, the release
pull request, its checks, and any open release blocker. Cloud: disable the
watchdog (`update_trigger`, `enabled: false`). Local: tell the owner to stop
the `/loop`.
