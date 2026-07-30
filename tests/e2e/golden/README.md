# Golden Journey Specifications

This directory contains the six canonical user journeys that define when
Workaholic AI becomes useful and, eventually, releasable. Each file is an
executable pytest specification that uses only supported user-facing
boundaries for domain operations.

Phase 1 enables the solo journey through a fresh-process CLI harness and real
SQLite persistence. The other five journeys remain explicitly skipped until
their complete real implementations exist. A mock, in-memory stand-in, or fake
product response is not sufficient.

## Journey inventory

| Journey | Canonical specification | Enabling phase | Remove the skip when |
| --- | --- | --- | --- |
| Solo | [test_solo_journey.py](test_solo_journey.py) | Phase 1 | Enabled: LocalSession, SQLite, initialization, task creation, and persistence run across fresh CLI processes |
| Multi-project | [test_multi_project_journey.py](test_multi_project_journey.py) | Phase 2 | Project binding, upward context discovery, and independent stable Project task keys work |
| Agent | [test_agent_journey.py](test_agent_journey.py) | Phase 4 | Real local Agents can claim, heartbeat, submit, and receive `LEASE_LOST` after expiry through CLI JSON |
| Team | [test_team_journey.py](test_team_journey.py) | Phase 6 | Two remote Humans and an Agent can use one authenticated server through RemoteSession |
| Backend parity | [test_backend_conformance_journey.py](test_backend_conformance_journey.py) | Phase 7 | JSON, SQLite, and PostgreSQL expose equivalent behavior through the same public CLI workflow |
| Clean install | [test_clean_install_journey.py](test_clean_install_journey.py) | Phase 9 | A version-pinned release candidate can be fetched and run through `uvx` outside the source checkout |

The enabling phase is the earliest phase whose exit gate can satisfy the whole
journey. Later phases continue running every journey as a regression test.

## Execution contract

Golden tests must:

- start the real CLI in a fresh process for every invocation;
- use real persistence and a real server where the journey requires them;
- exercise domain operations through the public CLI rather than private
  protocol routes or persistence internals;
- assert the `workaholic.cli/v1` envelope for JSON-mode operations;
- use isolated temporary state and tear down processes and services;
- carry `e2e` and `golden` markers plus applicable resource markers;
- use a phase-specific `skip` while blocked and remove it only after the whole journey is real;
- never use `xfail`.

The shared `golden_runner` fixture pins every local CLI process to one
pytest-owned data directory and strips inherited `WORKAHOLIC_*` selections.
Phase 1 implements only real local CLI execution. Remote Instance
orchestration, registry package selection, and `uvx` execution remain explicit
unsupported harness operations while their journeys stay skipped.

## Markers and selection

The permanent pytest markers are:

| Marker | Meaning |
| --- | --- |
| `contract` | Observable behavior shared across implementations or boundaries |
| `integration` | Behavior spanning multiple real components |
| `e2e` | Behavior through supported user-facing interfaces |
| `golden` | One of the six canonical product journeys |
| `requires_uv` | Invokes the external `uv` or `uvx` executable |
| `requires_postgres` | Requires an isolated PostgreSQL service |
| `requires_network` | Opens network connections or accesses a registry |

Collect the specifications without running them:

```bash
uv run pytest --collect-only tests/e2e/golden
```

Run all enabled golden journeys:

```bash
uv run pytest -m golden
```

This targeted command reports journey outcomes independently of the
whole-suite coverage threshold. Unfiltered CI and pre-push runs continue to
enforce at least 95 percent coverage.

Unknown marker names are errors because pytest runs with `--strict-markers`.

## Source contracts

These specifications are grounded in:

- the [delivery roadmap](../../../docs/roadmap.md);
- the [CLI automation contract](../../../docs/cli-contract.md);
- the [persistence contract](../../../docs/persistence-contract.md);
- the [architecture](../../../docs/architecture.md);
- the [threat model](../../../docs/threat-model.md).
