---
description: "Show a fleet's state: each chain's next item, running sessions, open conflicts and release blockers, and the trunk"
---

# Fleet status

## User Input

```text
$ARGUMENTS
```

Run `python3 .specify/extensions/fleet/scripts/python/fleet.py state <fleet>`
with the fleet named in the input (if none is named, list the fleets with
`git ls-tree --name-only <remote>/<record> .fleet/` after a fetch, and ask
which). Show its output unchanged. Then, in at most three lines, say what
needs the owner: open contradictions, blocked items that are not waits, and
whether the trunk is ahead of the release target with a pull request open.
Change nothing.
