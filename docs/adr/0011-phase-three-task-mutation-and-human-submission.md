# ADR 0011: Phase 3 Task Mutation and Human Submission

- Status: Accepted
- Decision date: 2026-08-01
- Deciders: Pavels Gurskis
- Supersedes: None
- Superseded by: None

## Context

Phase 3 introduces the first mutations of existing Tasks. Agents need explicit
optimistic concurrency because an automatic refresh-and-retry could overwrite
another actor's accepted change. Requiring Humans to copy a version before
every interactive command would preserve correctness but make the normal CLI
unnecessarily hostile.

The v1 architecture also defines Attempts as leased Agent execution records.
Treating a manual Human completion as an Attempt would mix two different
concepts: a person reporting work already performed and an Agent acquiring an
expiring right to execute. The distinction affects Result attribution, review,
future Lease validation, and CLI clarity.

Finally, Task decomposition needs one unambiguous relationship model. V1
already requires blocking dependencies; a parallel parent/child hierarchy
would add unclear completion semantics without a proven separate use case.

## Decision

Every mutation of an existing Task requires a positive `expected_version` at
the Session, application, and persistence boundaries. A successful semantic
mutation increments the Task version exactly once, including a mutation that
appends multiple TaskEvents. A stale version changes nothing and returns
`VERSION_CONFLICT`. No official client silently retries with a refreshed
version.

The Human-readable CLI may provide one interactive convenience. When a real
terminal operator omits `--expected-version`, the CLI reads the current Task,
shows its key, state, version, and intended action, asks for confirmation once,
and sends that exact version. JSON mode, `--non-interactive`, and non-terminal
input require an explicit option and never prompt. Supplying the option skips
the convenience read.

Attempts are Agent-only. Phase 3 Human operators submit directly with
`task submit`; the Result records the authenticated Human and
`attempt_id = null`. A comment and structured Result file are independently
optional. Submission from `open` with satisfied dependencies moves directly to
`done` when approval is `none`, or to `review` when approval is `human`.
Approval moves `review` to `done`; rejection records its reason, retains the
rejected Result for audit, clears it as the current review selection, and moves
the Task to `open`.

Completion is not a generic field edit. `task update` cannot accept a state,
Result, dependency, blocking reason, version, identity, actor, request,
TaskEvent, or timestamp field. State, dependency, submission, and review
changes use explicit semantic commands and repository operations.

V1 does not model parent/child Tasks. Decomposition uses ordinary Tasks and
acyclic same-Project blocking dependencies. TaskEvents and Results preserve
why follow-up work exists. Proposed follow-ups in a Result are inert data and
do not create Tasks automatically.

## Alternatives considered

### Require every Human to type an expected version

This is safe but makes the common interactive path depend on a separate lookup
and manual copy. The accepted prompt keeps the concurrency precondition
explicit at trusted boundaries without burdening terminal operators.

### Fetch and retry automatically after a conflict

This was rejected because it changes the precondition the operator or caller
actually approved and can overwrite an intervening mutation.

### Model Human work as a synthetic Attempt

This would make Attempt identity and Lease semantics misleading, create fake
execution records, and complicate future stale-Agent enforcement.

### Allow `task update --state done`

This would bypass Result validation, approval, completion events, and the
single semantic transaction required for audit consistency.

### Keep parent/child Tasks alongside dependencies

No distinct v1 behavior justifies a second graph. It can be reconsidered after
dogfooding demonstrates a relationship that blocking dependencies and
provenance cannot express.

## Consequences

- Automation always supplies an explicit concurrency precondition.
- Interactive Humans receive safe convenience without last-write-wins.
- CLI mutation helpers must detect terminal capability and centralize the
  confirmation flow.
- Human and Agent Results share a model with nullable Attempt attribution, but
  only Agent submission may populate the Attempt field.
- A semantic operation may append multiple ordered events while incrementing
  the Task version once.
- Phase 4 can add Agent submission by requiring a current owned Attempt without
  changing Phase 3 Human behavior.
- Typed hierarchy remains outside v1 until supported by observed workflows and
  a separate accepted decision.

## References

- [Architecture](../architecture.md)
- [CLI automation contract](../cli-contract.md)
- [Persistence contract](../persistence-contract.md)
- [Threat model](../threat-model.md)
- [Product scope](../product-scope.md)
