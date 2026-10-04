---
description: "Partition one or more features' tasks.md into fleet chains (one per user story), cross-check them, and settle findings with the owner"
---

# Plan a fleet

## User Input

```text
$ARGUMENTS
```

The input may name a fleet name and feature directories. Default feature:
the current one (`.specify/feature.json`, or the newest `specs/NNN-*` with a
`tasks.md`). Default fleet name: the feature directory's name. Ask only for
what you cannot find.

`F` means `python3 .specify/extensions/fleet/scripts/python/fleet.py`. This
command runs in the session that will hear the chains' results (the
**originating session**), with the owner present.

**New fleet or join.** Run `git fetch <remote> <record>` and
`F state <fleet>`. If the fleet does not exist, this is a new fleet. If it
exists, this spec **joins** it: another conversation's spec is already being
built, and this session will own the chains this spec adds. The steps are the
same; the differences are marked **Join**. Several specs can share one fleet
this way, each planned and launched from its own conversation, so overlap and
contradiction between them are checked like those within one spec.

## Steps

1. **Configuration.** If `.specify/extensions/fleet/fleet-config.yml` does not
   exist, copy `config-template.yml` from the same directory to it. Show the
   owner `F config` and ask, with one question each, for what the template
   leaves empty and the project needs: the transport (cloud or local), the
   `gate.quick` and `gate.full` commands (read the project's `package.json`,
   `Makefile`, `pyproject.toml` or CI workflow first and propose them), and
   any `reserved_sequences` (migrations and other numbered files). Commit the
   config: chain sessions read it from their checkout.
2. **Preconditions.** Each feature has `spec.md`, `plan.md` and `tasks.md`,
   and the work tree is clean and pushed. If `__SPECKIT_COMMAND_ANALYZE__` has
   not been run since `tasks.md` changed, offer to run it first: a
   cross-artifact inconsistency is cheaper to fix now than as a fleet
   contradiction.
3. **Draft.** `F plan <fleet> <feature dir> … --out <scratch dir>`. Chain
   ids carry the feature number (`001-us1`), so two specs never collide. It
   writes the manifest, one queue per chain and an empty `conflicts.jsonl`,
   and prints the chains:
   - a `<NNN>-foundation` chain for Setup and Foundational phases, which every
     story chain `needs` (its commits are cherry-picked; nobody waits for a
     merge);
   - one chain per user-story phase (`<NNN>-us1`, `<NNN>-us2`, …);
   - a `<NNN>-polish` chain, which needs every story chain's work;
   - per chain, work items of at most `tasks_per_item` tasks, then an
     `integrate` and a `register` item. On the cloud transport a chain holds
     at most `max_items_per_chain` items; the planner widens items to fit.
   Show the owner the chain list.
4. **Pre-flight cross-check.** For each chain, spawn a read-only subagent
   with the Agent tool, all in parallel: "Read
   `.specify/extensions/fleet/commands/speckit.fleet.crosscheck.md` and follow
   it with mode=preflight fleet=<fleet> chain=<chain> draft=<scratch dir>."
   **Join:** add `live=yes` to each prompt. The check then also reads the
   published fleet's chains, their specs, queues and branches, including
   work they have already committed.
5. **Owner Q&A.** Put every finding to the owner with `AskUserQuestion`, one
   question per finding, framed by purpose: what each side is for, citing
   the user story, `FR-…` and acceptance scenario, never conflict or item
   ids. Two to four options, the recommended one first.
   - A contradiction: the answer is written into `spec.md` (and `plan.md`,
     `data-model.md` or `contracts/` as needed), committed and pushed before
     publishing. Re-run step 3 if a task list changed.
   - An overlap between two chains that create the same file: "one shared
     chain, or keep them apart and resolve at merge". Shared: edit the draft
     so the overlapping items are in one chain, and add `shared_from` with the
     replaced chain ids to its manifest entry.
   - A dependency: add the needed `<chain>/<item>` (and every earlier work
     item of that chain) to the waiting item's `needs` in the draft.
   Record each decision, dated, under "Pre-flight" in the draft's
   `owner-decisions.md`. Then `F check <fleet> <scratch dir>` until it passes.
   **Join: a finding against a chain already in the fleet.** You (the
   joining spec's owner) decide it: the spec that joins later settles
   contradictions with earlier specs (owner's decision, 2026-10-04). The
   running chains keep running while you decide; this spec does not launch
   until every such question is answered.
   - If the answer changes only this spec: edit it as above.
   - If the answer changes an earlier spec, follow the `speckit.fleet.resolve`
     command for it. That stops only the affected chains, at their next task
     boundary, writes the decision into the earlier spec as one commit merged
     into those chains' branches, and tells the earlier spec's session.
   - A dependency on a running chain's item goes into `needs` as `<chain>/<item>`
     like any other; the item waits until that work is done and cherry-picks it.
6. Offer the `speckit.fleet.launch` command (`/speckit-fleet-launch` in
   Claude Code) with the same fleet name and scratch directory.
