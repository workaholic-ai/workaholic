# Golden Journey Specifications

This directory contains the six canonical user journeys that define when
Workaholic AI becomes useful and, eventually, releasable. Each file is an
executable pytest specification that uses only supported user-facing
boundaries for domain operations.

Phase 5 enables the complete Human solo, multi-project, and authenticated local
Agent journeys through a fresh-process CLI harness, real SQLite persistence,
isolated file-backed Human credentials and protected Agent Token files, durable
Workspace context discovery, and synchronized process races. The other three
journeys remain explicitly skipped until their complete real implementations
exist. A mock, in-memory stand-in, or fake product response is not sufficient.

## Journey inventory

| Journey | Canonical specification | Enabling phase | Remove the skip when |
| --- | --- | --- | --- |
| Solo | [test_solo_journey.py](test_solo_journey.py) | Phase 3 | Enabled: a Human completes dependency-bound work, submits structured evidence without an Attempt, receives review approval, and inspects complete attributable history across fresh CLI processes |
| Multi-project | [test_multi_project_journey.py](test_multi_project_journey.py) | Phase 2 | Enabled: nested and repeated Project bindings, upward context discovery, independent stable Project task keys, all-Project reads, restart persistence, and isolated profiles run across fresh CLI processes |
| Agent | [test_agent_journey.py](test_agent_journey.py) | Phase 5 | Enabled: one authenticated Human provisions two distinct Agents and protected Tokens; Viewer denial, Project filtering, exclusive pull, foreign-Attempt rejection, heartbeat, progress, alternate-Token continuity, revocation, disablement, submission, and attribution run through fresh CLI processes |
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

The shared `golden_runner` fixture pins every local CLI process to pytest-owned
configuration and data directories and forces the file credential backend. It
passes through only a small platform-runtime environment allowlist and strips
inherited application state, credentials, Tokens, and Python import paths.
Callers may repeat the documented local root/profile selectors or select an
exact mode-0600 Agent Token file below the harness-owned Token directory. Owned
directories cannot be redirected, and URL, inline Token, alternate credential
backend, Python-path, and arbitrary environment injection remain forbidden.
Remote Instance orchestration, registry package selection, and `uvx` execution
remain explicit unsupported harness operations while their journeys stay
skipped.

## Markers and selection

The permanent pytest markers are:

| Marker | Meaning |
| --- | --- |
| `contract` | Observable behavior shared across implementations or boundaries |
| `distribution` | Clean-checkout or installed-artifact acceptance behavior |
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
whole-suite coverage threshold. The CI source-test job excludes
`distribution` tests because those tests recursively create clean checkouts;
the dedicated wheel job verifies base installation and the current Phase 4
installed workflow until Task 22 upgrades it. The complete Phase 5 gate runs both the source
suite and distribution checks while enforcing at least 95 percent coverage.

Unknown marker names are errors because pytest runs with `--strict-markers`.

## Source contracts

These specifications are grounded in:

- the [delivery roadmap](../../../docs/roadmap.md);
- the [CLI automation contract](../../../docs/cli-contract.md);
- the [persistence contract](../../../docs/persistence-contract.md);
- the [architecture](../../../docs/architecture.md);
- the [threat model](../../../docs/threat-model.md).
