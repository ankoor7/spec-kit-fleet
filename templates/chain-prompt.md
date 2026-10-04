# Chain launch prompt

`fleet.py launch` sends the block below to every chain session, with the
placeholders filled in. Lines after `--- first session only ---` go only to a
chain's first session (`launch --first`). Edit the wording here; never build a
launch prompt by hand.

```text
You are running one item of chain {chain} in fleet {fleet}. Several chains are
running in parallel on separate branches, built from Spec Kit tasks, and you
must watch for overlap and contradiction with the others.

Work on branch {branch}. The fleet status files are on branch {record}
(remote {remote}); finished work is merged into the trunk, {trunk}, only when a
chain is complete. The trunk reaches {release_target} through one pull request.
Transport: {transport}. The originating session is {originating}.

Read {ext}/commands/speckit.fleet.chain.md and follow it exactly, with these
arguments: fleet={fleet} chain={chain}. It tells you how to find your session
id, claim this chain's next item, build it task by task with cross-chain
checks, close it, report upward and hand the baton to the next session.

Do not ask for permission to continue between tasks: the queue already gives
you permission. A contradiction with another chain is not yours to decide:
follow the contradiction procedure in that file.
--- first session only ---
Channel check: at boot, before the claim, report "started" upward with
`python3 {ext}/scripts/python/fleet.py message {fleet} {chain} started --text "Fleet {fleet}, chain {chain}: started <next item id>, session <your session id>."`
(cloud: pass its output to send_message).
```
