---
description: "Run one item of a fleet chain: claim it, build its tasks with cross-chain checks, close it, report, and hand the baton"
---

# Fleet chain session

## User Input

```text
$ARGUMENTS
```

The input names `fleet=<fleet> chain=<chain>`. If either is missing, stop and
say which.

You run **one item** of one chain, then start the next session for the chain
and stop. Below, `F` means `python3 .specify/extensions/fleet/scripts/python/fleet.py`.
Every status-file change goes through `F`: never edit a file on the status
branch by hand, and never write a time or a session id you did not get from a
command.

## 0. Tools and identity

- **Cloud transport** (`F config` shows `"transport": "cloud"`): load the
  deferred tools in one call, ToolSearch with
  `select:mcp__claude-code-remote__send_message,mcp__claude-code-remote__create_session,mcp__claude-code-remote__get_session,mcp__claude-code-remote__send_later`.
  Your session id is the `id` that `get_session` with no argument returns; it
  starts with `session_`. Report upward with
  `mcp__claude-code-remote__send_message`, never `SendMessage` (that reaches
  only your own subagents).
- **Local transport**: `F session` prints your id (`local_…`). Upward
  messages go through `F message`, which writes the originating session's
  inbox.

**Reporting upward**, in both transports: run
`F message <fleet> <chain> <kind> --text "<body>"`. Cloud: it prints
`{session_id, message}`; call `send_message` with exactly those. Local: it
has already delivered. Bodies follow "Upward messages" at the end.

## 1. Read

1. The project's agent instructions (`CLAUDE.md`, `AGENTS.md` or the
   equivalent), and `.specify/memory/constitution.md`.
2. `F state <fleet>`: every chain's next item, open conflicts and release
   blockers.
3. `F get <fleet> manifest.json`; this chain's entry names its feature, user
   stories, the paths its tasks name, and any reserved number blocks.
4. If this is the chain's first session (the launch prompt has a "Channel
   check" line), send the `started` message now, before the claim.

## 2. Claim

`F claim <fleet> <chain> --session <your id>`

- **Exit 0**: it prints your item (`id`, `kind`, `tasks`, `brief`, `spec`,
  `tasks_file`, `needs`). Go on.
- **Exit 4**: the item waits for a sibling item that is not done yet; the
  claim closed it `blocked` with `waits for …`. Send the `blocked` message
  and stop. Nobody asks the owner: the sibling's `shared` message ends the
  wait.
- **Exit 1**: an open conflict names this chain, another item is running,
  or nothing is left. Stop and send nothing.

## 3. Merge the trunk, take what `needs` names

Skip this step for a `register` item.

1. `git fetch <remote> <trunk>` and `git merge --no-edit <remote>/<trunk>`.
   Always a merge commit, never a rebase: other sessions hold this branch's
   history.
2. Conflicts: in a file only the trunk side changed in substance, take the
   trunk's side; where both sides changed it, keep both, then run the tests
   of that file. A conflict in `spec.md`, `plan.md`, `data-model.md` or
   `contracts/` where both sides cannot hold is a **possible contradiction**
   (section 6).
3. If `HEAD` moved: `F gate regenerate`, then `F gate quick`; commit what they
   needed.
4. `F picks <fleet> <chain> <item>` lists, per needed sibling item, its
   commits not yet on this branch. For each entry, oldest first,
   `git fetch <remote> <that chain's branch>` and
   `git cherry-pick -x <sha> …`. Resolve conflicts by the rule in 2. Then
   `F record-pick <fleet> <chain> <item> --from <chain/item> --commits <sha> …`,
   and close the matching `dependency` conflict if one is `logged`:
   `F conflict <fleet> --data '{"id":"<id>","class":"dependency","status":"closed","decision":"cherry-picked <shas>"}'`.
5. `git push <remote> HEAD:<branch>`.

## 4. Build

### Work item: one task at a time

For each task id in the item's `tasks`, in order. A `[P]` marker means the
task touches files no other open task touches; it does not mean run two tasks
at once inside a chain. The fleet's parallelism is across chains.

1. **Cross-check first**, before writing anything. Spawn a read-only subagent
   with the Agent tool (light model) whose prompt is: "Read
   `.specify/extensions/fleet/commands/speckit.fleet.crosscheck.md` and follow
   it with mode=unit fleet=<fleet> chain=<chain> item=<item> task=<task id>
   files=<the paths the task names, and their direct importers>
   spec=<spec path>." When it returns, run `F conflicts <fleet> --open` again
   before acting: a sibling may have appended a line meanwhile. Then act on
   each finding:
   - **overlap**: log it only if this chain comes first in `merge_order`
     among the two (or the other chain has no item left); then continue.
     `F conflict <fleet> --data '{"class":"overlap","status":"logged","found_by":"<chain>","chains":["<chain>","<sibling>"],"title":"…","this":"…","that":"…","evidence":"…","recommendation":"…"}'`
   - **dependency** (this task needs something a sibling item introduces):
     log it (`"status":"logged"`), then
     `F set <fleet> <chain> <item> --add-need <sibling chain>/<item id>`.
     If that item is `done`, take its commits now (step 3.4) and continue.
     If not, commit the tasks finished so far, then
     `F close <fleet> <chain> <item> --status blocked --note "waits for <chain/item>" --commits <shas>`,
     send `blocked`, and stop without handing the baton.
   - **contradiction**: section 6. Do not start the task.
2. **Implement.** Spawn an IMPLEMENTER subagent with: the task line; the
   feature's `spec.md` (the user story and its acceptance scenarios),
   `plan.md`, `data-model.md`, `contracts/` and `research.md` where present;
   the constitution; the files to touch. If the item's tasks include tests for
   this behaviour, the tests are written first and fail before the code. The
   implementer does not commit.
3. **Validate.** Spawn a VALIDATOR subagent that did not write the code: it
   runs `F gate quick` and the tests the task names, and checks the change
   against the user story's acceptance scenarios in `spec.md`. A failure goes
   back to step 2 with the validator's report; after three failed rounds,
   commit nothing, close the item `blocked` with the reason, send `blocked`
   and stop.
4. **Review.** Spawn a REVIEWER subagent with the diff. It checks the change
   against the task, the constitution and `plan.md`, and diffs the files this
   task touched against every sibling branch in the manifest
   (`git diff <remote>/<trunk>...<remote>/<sibling branch> -- <files>`). A
   commit ending `(cherry picked from commit …)` is work this chain took on
   purpose, not an overlap. A finding the reviewer classes as a contradiction
   goes to section 6 and the task is not committed to this branch.
5. **Commit and push.** Mark the task `[X]` in the feature's `tasks.md` in the
   same commit. Message: `<type>(<chain>): <task id> <summary>`. Push. Keep
   the SHA: the item's `commits` are exactly these task commits, oldest
   first, because a sibling cherry-picks them.

Without an Agent tool, play the three roles yourself in turn, re-reading the
task and the diff at the start of each role.

### Integrate item: merge the finished chain into the trunk

1. `F conflicts <fleet> --open`: an `open` conflict naming this chain →
   close `blocked` with `conflict <id>` and stop.
2. Fetch the trunk and `<release_target>`; merge the trunk as in step 3.
3. **Reserved numbers**: any file of a `reserved_sequences` pattern whose
   number is at or below the highest on the trunk or the release target, and
   was added by this chain, is renumbered into this chain's block.
4. `F gate full`. Commit what it needed. Push the chain branch.
5. **Release blocker**: if this merge would leave the trunk unreleasable
   (half of a change whose other half is a later merge), append one
   immediately before the push to the trunk:
   `F hold <fleet> --data '{"status":"open","title":"…","reason":"…","opened_by":{"fleet":"<fleet>","chain":"<chain>","item":"<item>"},"closes_with":"…"}'`.
   If this merge completes one, append its `closed` line after the push.
6. **Never merge into a trunk whose checks fail.** Read the checks of the
   release pull request (`<trunk>` → `<release_target>`) on the trunk head:
   cloud, the GitHub MCP tools (`list_pull_requests`, `pull_request_read`,
   `get_check_run`); local, `gh pr checks <number>` if `gh` is installed.
   Failing: `F close … --status ready --note "<failing run>, broken by <chain>"`,
   send `ready`, stop. Running: wait (step 8). Passing, no pull request, or no
   way to read checks (say so in the close note): go on.
7. `git push <remote> HEAD:<trunk>`. Only a fast-forward is accepted. Refused:
   a sibling merged during your gate; go back to 2. Never force, and never
   push a commit the full gate did not run on.
8. If no release pull request is open, open one. Wait for its checks on the
   commit you pushed. Cloud: `send_later` with `delay_minutes: 5`, end the
   turn, read them when it wakes you; never `sleep` in the foreground. Local:
   `gh pr checks <number> --watch`. A run on a newer trunk head that contains
   your commit counts. Checks fail: this merge broke them, so this item fixes
   them with another gated fast-forward merge (or a revert), with a release
   blocker open meanwhile.
9. `F close <fleet> <chain> <item> --status done --merged <sha> --note "merged <sha>"`.
10. For each `logged` overlap naming this chain whose other side is already in
    the trunk, append a `closed` line with a `decision` naming `<sha>`.
11. Send `merged`. Hand the baton (step 5) to the register item.

### Register item: the chain's final report

Write `HANDOFF-<chain>.md` with: every item and its outcome; every task in the
chain's `surfaces.tasks` not marked `[X]`; every conflict naming this chain,
`logged` ones included; every release blocker this chain opened that is still
open; open questions for the owner. `F put <fleet> HANDOFF-<chain>.md --file <path>`
(write the file in a scratch directory, not in the working tree).
`F close … --status done`. Send `finished`. Do not hand the baton.

## 5. Close and hand the baton

1. A work item: `F close <fleet> <chain> <item> --status done --commits <sha> …`.
   If any sibling item's `needs` names this item (`F state <fleet> --json`),
   send `shared`.
2. Update `HANDOFF-<chain>.md` (what was done, what is next, anything a cold
   reader needs) with `F put`.
3. **Baton**, only after closing `done` (never after `blocked` or `ready`):
   `F launch <fleet> <chain>`.
   - Cloud: it prints the `create_session` arguments. Pass them **unchanged**:
     a session created without `source_url`, `source_revision` or `model`
     starts with no repository and stalls.
   - Local: it has started the next session and prints its id and log.
   - It refuses when the next item is not ready to run. That is not an
     error: the originating session or the watchdog starts the chain later.
4. Stop.

## 6. Contradiction: both chains stop

A contradiction is two requirements, user stories or contracts that cannot
both be built as written, built or only planned. When unsure whether it is an
overlap or a contradiction, treat it as a contradiction.

**When you find one:**

1. Do not start the task. Unfinished work goes to a side branch,
   `<branch>-<conflict id>-wip`, pushed; never only a stash.
2. `F conflict <fleet> --data '{"class":"contradiction","status":"open","found_by":"<chain>","chains":["<chain>","<sibling>"],"items":["<chain>/<item>","<sibling>/<item>"],"title":"…","this":"<spec section + FR/US id, or file:line @ sha>","that":"…","evidence":"…","recommendation":"…"}'`
   It prints the conflict id.
3. Tell the sibling now, so it stops sooner. Cloud: `send_message` to the
   `session_id` of the sibling's running item (`F state`). Local: nothing; it
   checks at its next task boundary.
4. `F close <fleet> <chain> <item> --status blocked --note "conflict <id>" --commits <committed task shas>`.
5. Write the handoff with what actually happened in 1–4.
6. Send `conflict`. Stop. Do not hand the baton.

**At every task boundary** (before each cross-check), run
`F conflicts <fleet> --open`. An `open` conflict naming this chain: commit the
task in flight if it passed review, close `blocked` with `conflict <id>`, write
the handoff, and stop. Send nothing: the chain that found it already did.

## Upward messages

First line: `Fleet <fleet>, chain <chain>: <kind>.` Then, as they apply:

```text
Items: <id>=done <sha range>, <id>=blocked <why>, <id>=ready <failing run, chain that broke it>.
Shared: <id> commits <sha> …, needed by <chain>/<item id>.
Conflict: <id> <class> between <chains>: <one sentence>.
Merged into <trunk>: <sha>, checks <green|red|unknown>; release blockers opened/closed: <ids or none>.
Branch pushed: <branch>.
Open questions for you: <list, or none>.
```

`started` is one line: `Fleet <fleet>, chain <chain>: started <item id>, session <id>.`
