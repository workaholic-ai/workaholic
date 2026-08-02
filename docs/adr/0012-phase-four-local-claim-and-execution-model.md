# ADR 0012: Phase 4 Local Claim and Execution Model

- Status: Accepted
- Decision date: 2026-08-02
- Deciders: Pavels Gurskis
- Supersedes: None
- Superseded by: None

## Context

Phase 4 must make Workaholic AI useful to autonomous local Agents while
remaining a lightweight task manager. It cannot stop or interrupt a process
that is executing work, so changing a claimed Task underneath that process
would create misleading coordination and wasted work.

Human operators also need to claim work without copying opaque execution
identifiers into normal CLI commands. At the same time, Agent execution needs a
unique Attempt identity so an expired or superseded process cannot complete
work after reclaim. Phase 5, not Phase 4, introduces additional Subjects,
Tokens, and general ProjectGrant management.

The earlier architecture also listed capability filtering for claims, but v1
has no accepted Task capability model or demonstrated need for heterogeneous
queue routing. Atomic ownership and stale-execution rejection do not depend on
that feature.

## Decision

### Claims and Attempts

A `Claim` is the current exclusive, expiring ownership record for one Task. At
most one unexpired Claim may exist for a Task. It records the Task, owning
Subject, claim and expiry timestamps, and nullable Attempt identity.

- A Human Claim has `attempt_id = null` and a longer Lease window.
- An Agent Claim has a non-null current Attempt ID and a shorter Lease window.
- No current Claim means the Task is unclaimed.
- An Attempt remains an Agent-only execution record. Human work never creates
  a synthetic Attempt, and Human Results retain null Attempt attribution.

Phase 4 local Human and Agent commands reuse the sole embedded bootstrap
Subject. The explicit Human command path and nullable Attempt attribution
distinguish Human Claims from Agent execution. Attempt identity distinguishes
concurrent local Agent processes. This provides local coordination, not
identity isolation. Phase 5 adds distinct Agent Subjects, Tokens, grants, and
authenticated Subject ownership without changing the Claim or Attempt model.

Human claiming is optional and targets one ready Task. Agent claiming pulls the
highest-ranked ready Task using the established deterministic ordering. A Task
is ready only when its stored state, availability, dependencies, and Claim
state permit work. Capability filtering is not part of v1; a claimant is
assumed capable of performing the Task it claims.

### Exclusive mutation lock

An unexpired Claim locks the Task against non-owner mutation. Reads remain
available. A rejected mutation changes no Task, Claim, Attempt, Result,
TaskEvent, version, or idempotency state.

The Human owner may use normal Human Task operations without an Attempt ID.
Definition updates, block/unblock, and dependency changes retain the Human
Claim. Human release, Lease expiry, submission, or cancellation ends it.

An Agent owner may only heartbeat, report progress, release, or submit through
the current Attempt. Agent execution cannot redefine, block, cancel, or change
dependencies on its claimed Task. Any such change requires release followed by
a normal Human mutation and a new claim.

Workaholic AI provides no force-interrupt command in v1. An operator who needs
to stop an Agent must stop or coordinate with that process outside Workaholic
AI, then wait for release or Lease expiry before mutating the Task.

### Renewal, expiry, and terminal behavior

Human `task renew` and Agent `task heartbeat` are presentation wrappers over
one `renew_claim` semantic operation. Human renewal requires no Attempt ID;
Agent renewal requires the current Attempt ID. Repeating `task claim` for an
already owned Task returns the current Claim without extending it, and normal
reads or mutations never renew a Claim implicitly.

Lease validity uses authoritative transaction time and the half-open rule
`now < lease_expires_at`. Expiry requires no background scheduler. Release,
expiry, submission, and Human cancellation remove the active lock.

Agent Attempt states are exactly:

```text
active
released
expired
submitted
```

The last three states are terminal and populate `ended_at`. Successful
submission always changes `active` to `submitted`, including when the Task
moves to review. Approval or rejection evaluates the Result and never revives
or closes the already-terminal Attempt. Rejected work requires a new Claim and
new Attempt.

### Versions and submission

Claim, renew, heartbeat, progress, release, and expiry do not change the Task
version. Human definition and lifecycle mutations retain the Phase 3 expected
version contract and increment the Task version once on success.

An Agent claim returns the current Task version. Agent submission requires
both the current Attempt ID and that expected Task version. Successful Human
or Agent submission increments the Task version exactly once, ends the Claim,
stores one Result, and moves the Task to `done` or `review` according to its
approval requirement.

### CLI boundary

The accepted Human and Agent command distinction is:

```text
workaholic task claim TASK       # Human, explicit ready Task, null Attempt
workaholic task renew TASK       # Human Claim renewal
workaholic task release TASK     # Human Claim release

workaholic task claim            # Agent pulls the next ready Task
workaholic task heartbeat TASK --attempt ATTEMPT
workaholic task progress TASK --attempt ATTEMPT
workaholic task release TASK --attempt ATTEMPT
workaholic task submit TASK --attempt ATTEMPT --expected-version VERSION
```

The Phase 4 command contract will add exact Lease bounds, structured output,
error identifiers, exit categories, and idempotency fingerprints while
preserving these semantics.

## Alternatives considered

### Pull Agent identity management into Phase 4

Additional Subjects without Phase 5 Tokens and grants would create apparent
identity separation without the accepted authentication boundary. The
bootstrap Subject plus Attempt identity is sufficient for the Phase 4 local
concurrency gate.

### Represent Human work as an Attempt

This would expose machine execution identifiers to Humans and contradict the
accepted distinction between manual submission and leased Agent execution.

### Allow operators to preempt a running Agent

Workaholic AI cannot interrupt the external process. Invalidating its claim
would only make its work uncommittable while allowing execution to continue,
which exceeds the reliable guarantees of a lightweight task manager.

### Keep capability filtering in v1

This would require a Task capability schema, matching algebra, and mutation
surface before real workflows demonstrate a need. Capability-based Task
scheduling remains a post-v1 backlog item and never becomes an authorization
mechanism.

### Use `heartbeat` for Human renewal

The underlying renewal semantics are shared, but process-oriented terminology
is poor Human-facing UX. A thin `task renew` CLI wrapper keeps one domain
operation while remaining discoverable to Human operators.

## Consequences

- Task lifecycle and Claim ownership remain orthogonal state machines.
- `running`, `stale`, and `ready` derive from any current Human or Agent Claim,
  not only from Agent Attempts.
- Human Claims require durable nullable Attempt attribution and Lease expiry.
- Phase 4 concurrency tests must cover Human/Agent claim races, lock rejection,
  renewal, expiry, release, version stability, submission, and reclaim.
- Local Phase 4 does not distinguish different Human operators; Phase 5 adds
  that identity boundary.
- Claim operations and their attributable events commit atomically without
  requiring a daemon.
- Capability-based scheduling can be designed later from observed workflows.

## References

- [Architecture](../architecture.md)
- [Delivery roadmap](../roadmap.md)
- [CLI automation contract](../cli-contract.md)
- [Persistence contract](../persistence-contract.md)
- [Threat model](../threat-model.md)
- [ADR 0005: Semantic Persistence Interface](0005-semantic-persistence-interface.md)
- [ADR 0007: Human and Agent Identity Model](0007-human-and-agent-identity-model.md)
- [ADR 0011: Phase 3 Task Mutation and Human Submission](0011-phase-three-task-mutation-and-human-submission.md)
