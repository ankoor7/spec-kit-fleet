---
description: "Resolve an open fleet contradiction with the owner, write the decision into the spec, and restart the stopped chains"
---

# Resolve a fleet contradiction

## User Input

```text
$ARGUMENTS
```

The input names `fleet=<fleet>` and a conflict id (`conflict=<id>`). Without
an id, take every `open` conflict from `F conflicts <fleet> --open`.

`F` means `python3 .specify/extensions/fleet/scripts/python/fleet.py`. Run
this in the originating session, with the owner present.

## Steps

1. **Read both sides.** The conflict's `this`, `that`, `evidence` and
   `recommendation`; the cited sections of each side's `spec.md`, `plan.md`,
   `data-model.md` or `contracts/`, read from each chain's branch; and the
   `-wip` side branch if the detecting chain saved one.
2. **One question to the owner** with `AskUserQuestion`: what each side's work
   is for, citing the user story, `FR-…` and acceptance scenario; never the
   conflict or item ids. Two to four options, the recommended one first.
3. **Write the decision into the specs** it settles, as **one commit** on a
   branch cut from the trunk. Push it. Then merge that one commit into every
   affected chain branch, not into the trunk: the trunk takes it with the
   first of those chains to integrate, and the others merge it cleanly because
   it is the same commit.
4. **Record it.** Add it, dated, under "Stopped" in `owner-decisions.md`
   (`F get`, edit a copy in a scratch directory, `F put`). Append
   `F conflict <fleet> --data '{"id":"<id>","class":"contradiction","status":"resolved","decision":"<one sentence; commit <sha>>"}'`.
5. **Reset the queues.** For every item blocked with `conflict <id>`:
   `F set <fleet> <chain> <item> --status pending --note "decision <sha>"`.
   An item the decision leaves with nothing to build is closed now, so no
   session runs for it:
   `F close <fleet> <chain> <item> --status done --force --note "nothing to build: decision <sha>"`.
   Work the decision adds to a chain whose items are all
   `done`: `F add-item <fleet> <chain> <id> --title … --before-integrate`.
6. **Restart** each affected chain: `F launch <fleet> <chain>` (cloud: then
   `create_session` with its output unchanged). On the cloud transport a
   restart from here also resets the session lineage depth.
7. Tell the owner in one line what was decided and which chains restarted.
