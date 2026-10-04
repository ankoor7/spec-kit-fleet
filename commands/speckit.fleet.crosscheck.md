---
description: "Read-only check of a fleet chain's plan or one task against its sibling chains: overlap, dependency, contradiction"
---

# Fleet cross-check

## User Input

```text
$ARGUMENTS
```

You find where this chain's work and its sibling chains' work collide. You do
**not** decide who is right and you change nothing: no edits, no commits, no
status-file writes. Your report is read by a session that holds little
context, so it must stand on its own.

`F` means `python3 .specify/extensions/fleet/scripts/python/fleet.py`.

## Inputs

- `mode`: `preflight` (whole chains, no code yet) or `unit` (one task).
- `fleet`, and `chain` (this chain's id; in `preflight` mode, `all` checks
  every pair).
- `preflight`: `draft=<dir>`, the directory `F plan --out` wrote; those chains
  are not published yet. With `live=yes`, a spec is joining a published
  fleet: also check the draft against every chain already in it.
- `unit`: `item`, `task`, `files` (the paths the task will touch) and `spec`.

If one is missing, say which and stop.

## Classes

| class | meaning | who logs it |
| --- | --- | --- |
| overlap | both chains change the same file or symbol, compatibly | the chain first in `merge_order` |
| dependency | this work needs something a sibling item introduces that is not on this branch | the chain with the need |
| contradiction | two requirements, user stories, contracts or data-model rules cannot both hold as written, built **or only planned** | either; both chains stop |

A sibling's planned task counts as a side, the same as committed code.
Shared files every chain edits by construction (lockfiles, a changelog,
`tasks.md` checkboxes) are not findings on their own.

## Method

1. **Read the fleet.** `preflight`: the manifest, queues and
   `conflicts.jsonl` under `<draft>/.fleet/<fleet>/`; with `live=yes`, also the
   published fleet as in `unit` mode. Its chains are the siblings, and work
   they have already committed counts as built. `unit`:
   `F get <fleet> manifest.json`, `F conflicts <fleet>`, and each sibling's
   queue with `F get <fleet> <chain>.queue.jsonl`. `git fetch` the trunk and
   every sibling branch; record each head SHA for the report.
2. **Do not re-report** a finding already in `conflicts.jsonl` with the same
   two sides (the latest line per `id` is its state); cite its id.
3. For each sibling chain:
   - **Specs.** Compare the user stories, functional requirements (`FR-…`),
     success criteria, `data-model.md` entities and `contracts/` each side
     builds. For a sibling from **another spec** (its manifest `feature`
     differs), compare the two specs' shared entities, endpoints, data rules,
     user-visible behaviour, and the constitution's guidance for both. These
     specs were written in separate conversations, often without knowledge of
     each other, so a contradiction is most likely here. Look for two
     statements that cannot both hold.
   - **Planned files.** The manifest's `surfaces.paths` and each item's
     `brief`. Two chains that **create** the same file conflict at every
     merge between them: report an overlap and recommend, at pre-flight,
     the owner question "one shared chain, or keep them apart and resolve at
     merge".
   - **Code** (`unit` mode, or once a sibling has commits):
     `git diff <remote>/<trunk>...<remote>/<sibling branch> --stat`, then the
     full diff only for files that meet this task's `files` or their direct
     importers. Compatible edits are an overlap; a change to what the code
     means to the other side's callers is a contradiction. A commit ending
     `(cherry picked from commit …)` is not a finding.
   - **Order.** Does this work need something a sibling item introduces? Name
     that item (`<chain>/<item id>`), and every earlier item of the same
     chain its commits build on: the cherry-pick must compile.
   - **Reservations.** A numbered file outside the chain's `reserved` block.
4. **Classify.** When you cannot tell an overlap from a contradiction, report
   a contradiction and say why. A contradiction means the task does not start;
   never recommend building first and resolving later. Recommend only: for a
   dependency, the sibling item in this item's `needs` (its commits are then
   cherry-picked) or a reorder of this chain's tasks; for an overlap at
   pre-flight, the shared-chain question. Never recommend merging a sibling's
   whole branch or an early merge into the trunk.

## Report

End with exactly this, and nothing after it:

```text
CHECKED
  trunk <trunk>: <sha>
  status branch <record>: <sha, or "draft">
  <sibling chain>: <branch> @ <sha>, <n> files changed, specs read: <paths>
  conflicts.jsonl: <n> entries read
FINDINGS (<n>)
  - class: overlap|dependency|contradiction
    sibling: <chain id>
    this: <spec section + FR/US id, or file:line @ sha, or task id>
    that: <same form>
    evidence: <both sides quoted or faithfully paraphrased>
    already logged: <conflict id, or no>
    recommendation: <what the chain or the owner should do, with options>
NOT CHECKED
  <anything you could not read or fetch, and why; or "nothing">
```

`FINDINGS (0)` is valid only with `CHECKED` filled in. Run every command in
the foreground and do not end your turn before the report is complete.
