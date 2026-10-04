# spec-kit-fleet

A [Spec Kit](https://github.com/github/spec-kit) extension that builds a
feature's user stories **in parallel**. Each story gets its own chain of agent
sessions on its own branch. The chains check each other for overlap and
contradiction, and a chain merges into one trunk only when it is complete.

`/speckit-implement` builds every task in one session, one after another.
Fleet takes the same `tasks.md` and gives each user-story phase its own chain:

| Spec Kit | Fleet |
| --- | --- |
| Setup + Foundational phases | the `<NNN>-foundation` chain; every story chain needs its work |
| Phase *n*: User Story *k* | chain `<NNN>-us<k>`, on branch `claude/fleet-<fleet>-<NNN>-us<k>` |
| Polish phase | the `<NNN>-polish` chain; it needs every story chain's work |
| Tasks `T001…` | units built one at a time inside a chain: cross-check → implement → validate → review → commit |
| `[P]` | tells you that tasks touch separate files; the parallelism comes from running chains side by side, not from `[P]` |
| `spec.md`, `plan.md`, `data-model.md`, `contracts/` | where a contradiction between chains is found and where the owner's decision is written down |

A chain that needs another chain's work does not wait for a merge. It
cherry-picks the exact commits of the item it needs, as soon as that item is
done.

## Requirements

- Spec Kit `>= 0.2.0`, with the Claude Code integration. The commands are
  Markdown, so other agents can follow them. The transports, however, start
  Claude Code sessions.
- `git` and Python `>= 3.9` (standard library only).
- A git remote that holds the trunk, the status branch and the chain branches.
  For a local fleet, a bare repository on disk is enough.
- Optional: `gh`, which local integrate items use to read the release pull
  request's checks.

## Install

```sh
specify extension add --dev /path/to/spec-kit-fleet      # from a checkout
# or, once it is in the community catalog:
specify extension add fleet
```

This installs seven commands. In Claude Code they are skills named
`/speckit-fleet-<name>`:

| Command | Runs in | Does |
| --- | --- | --- |
| `speckit.fleet.plan` | originating session | `tasks.md` → chains and queues; pre-flight cross-check; owner Q&A |
| `speckit.fleet.launch` | originating session | publish the fleet, or join a spec to a running one; start the watchdog; launch the chains |
| `speckit.fleet.chain` | each chain session | claim → merge trunk → cherry-pick needs → build tasks → close → baton |
| `speckit.fleet.crosscheck` | read-only subagent | report overlap / dependency / contradiction with sibling chains |
| `speckit.fleet.tick` | originating session | handle chain messages; watchdog rules; wrap-up |
| `speckit.fleet.resolve` | originating session | settle a contradiction with the owner, restart the chains |
| `speckit.fleet.status` | anywhere | `fleet.py state` |

It also adds an optional `after_tasks` hook, which offers to plan a fleet.

**Commit `.specify/extensions/fleet/` and the generated `.claude/skills/`.**
Every chain session starts from a fresh checkout of its branch and reads both.

## Configure

`.specify/extensions/fleet/fleet-config.yml` is created from
[`config-template.yml`](config-template.yml). The settings that matter:

```yaml
transport: cloud            # or local
gate:
  quick: ["npm run typecheck", "npm test -- --run"]   # after each task and merge
  full:  ["npm run lint", "npm test", "npm run build"] # before merging into the trunk
reserved_sequences:
  migrations: "db/migrations/*.sql"   # numbered files two chains must not both create
```

`local-config.yml` in the same directory holds overrides for one machine.
Spec Kit's `.gitignore` already excludes it.

## Use

```text
/speckit-tasks                      # as usual; the hook offers the next step
/speckit-fleet-plan albums specs/001-albums
/speckit-fleet-launch albums <draft dir>
```

Run `plan` and `launch` from the session that should hear the results: the
**originating session**. Chains report to it, it asks you the questions that
are yours to answer, and its watchdog restarts a chain whose session died.

## Several specs, one fleet

Two conversations can each write a spec and build it in the same fleet. The
fleet then checks overlap and contradiction across both specs, not only
within each one.

1. **Conversation 1** plans and launches spec `001-albums` as usual. This
   publishes the fleet, and this session becomes the **coordinator**.
2. **Conversation 2** writes spec `002-search`, then runs
   `/speckit-fleet-plan albums specs/002-search` with the same fleet name. The
   plan finds the published fleet and switches to a **join**:
   - The pre-flight check compares the new chains with every chain already in
     the fleet, including work those chains have committed.
   - `/speckit-fleet-launch` runs `fleet.py join`, which adds the new chains
     without touching the running ones. Reserved number blocks move above
     every block the fleet already holds, so the two specs never take the
     same migration number.
3. From then on the per-task checks see every chain in the fleet. A chain of
   one spec waits for, or cherry-picks, an item of the other like any other
   item it `needs`.

**Who hears what** (owner's decisions, 2026-10-04):

| Matter | Handled by |
| --- | --- |
| A chain's messages, restarts, waits and blocks | the session that launched the chain |
| A contradiction between the two specs | the session whose spec joined **later** (`fleet.py decider`); the other session is told the outcome |
| A contradiction found when a spec joins | the joining session; the chains already running keep running, and the joining spec does not launch until it is answered |
| The trunk: release pull request, failing checks, back-merges | the coordinator |

Chain ids carry the feature number (`001-us1`, `002-us1`), so two specs never
collide.

## Transports

### Cloud: Claude Code on the web

- Each chain item is a fresh cloud session. `fleet.py launch` prints the
  `create_session` arguments, and the launcher passes them unchanged.
- Chains report with `send_message` to the originating session.
- Each originating session runs its own watchdog, an hourly Routine bound to it.
- A session lineage is refused past depth 8. A chain therefore holds at most
  7 items, integrate and register included. The planner makes work items
  larger to stay under that limit.

### Local: this machine

- Each chain gets a git worktree beside your checkout (`../<repo>-fleet/`).
- `fleet.py launch` starts the configured agent command in that worktree as a
  detached process: `claude -p --model {model} …`, with the prompt appended
  and the log written under `.git/speckit-fleet/<fleet>/logs/`.
- Chains report through an inbox in the git directory. You read it with
  `fleet.py inbox <fleet> --session <id>`, one inbox per originating session, or
  `/speckit-fleet-tick` reads it for you.
- Run the watchdog with `/loop 15m /speckit-fleet-tick fleet=<fleet>`, or from
  cron.
- **Permissions.** A headless session cannot answer a permission prompt. Give
  it allow rules for `git`, `python3` and your gate in `.claude/settings.json`.
  Alternatively, run the fleet in a container and use
  `--dangerously-skip-permissions` there, never on your own machine.

## Status files

The status files live on the status branch (`claude/fleet-record`). It is an
orphan branch, so it carries no code and starts no CI.

```text
.fleet/<fleet>/manifest.json          chains, branches, merge order, reserved numbers, planned paths
.fleet/<fleet>/<chain>.queue.jsonl    one row per item: status, session, tasks, needs, commits, picked, merged
.fleet/<fleet>/conflicts.jsonl        append-only; the latest line per id is its state
.fleet/<fleet>/owner-decisions.md     Pre-flight, Stopped, Wrap-up
.fleet/<fleet>/HANDOFF-<chain>.md
.fleet/holds.jsonl                    release blockers, trunk-wide
```

Agents never edit these files by hand. Every write goes through `fleet.py`
(`claim`, `close`, `set`, `add-item`, `record-pick`, `conflict`, `hold`,
`put`). Each write validates the whole tree before it commits. If the push is
refused because another session pushed first, the write runs again on the new
head. `fleet.py check <fleet>` validates the status branch.

## The rules every chain follows

- At most one item per chain is `running`, and a chain never runs past a
  blocked item.
- A chain shares its work through its own branch, never through the trunk.
  The trunk takes a chain's work only at its integrate item.
- Every task starts with a cross-check, and every task boundary re-reads
  `conflicts.jsonl`. When a chain finds a **contradiction**, both chains stop
  and the owner decides. The decision goes into the spec, as one commit merged
  into each affected chain branch.
- The integrate item runs the full gate. It never merges into a trunk whose
  checks fail. It pushes to the trunk by fast-forward only, then waits for
  the release pull request's checks. Agents never merge that pull request:
  the owner does.

## Develop

```sh
python3 -m unittest discover -s tests -v
```

The tests run every `fleet.py` command against a throwaway bare remote, the
local launch path included, with a stub in place of the agent.

## Not yet verified

The scripts are tested; the prompts are not yet proven end to end:

- **No full fleet has run under this extension yet.** The commands are a
  port of a fleet process proven over several trial runs in one project. That
  process was rewritten here for Spec Kit's layout and for a local transport.
- **The local transport** has been exercised only with a stub agent, not with
  `claude -p` driving a real chain.
- **Cloud facts carried over from the original runs:**
  - Pushes to `claude/*` branches succeed.
  - A scheduled Routine wakes its bound session.
  - Not verified: whether `send_message` at lineage depth 8 is refused.

## License

MIT. See [LICENSE](LICENSE).
