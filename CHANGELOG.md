# Changelog

## 0.2.0 (2026-10-04)

- Several specs in one fleet: `fleet.py join` adds a spec planned in another
  conversation to a running fleet. Each chain reports to the session that
  launched it. A contradiction between specs is settled by the session whose
  spec joined later (`fleet.py decider`). The first session stays the
  coordinator for the trunk.
- Chain ids always carry the feature number (`001-us1`). **Breaking** for
  fleets planned with 0.1.0.
- `message --also`, `inbox --session`, `prompt watchdog --session`,
  `state --session`; new message kind `resolved`.

## 0.1.0 (2026-10-04)

- First release: plan, launch, chain, crosscheck, tick, resolve and status
  commands; `fleet.py` helper (standard library only); cloud and local
  transports; orphan status branch; validated, retried status-file writes.
