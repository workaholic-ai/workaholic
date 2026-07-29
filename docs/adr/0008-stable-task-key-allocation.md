# ADR 0008: Stable Task-Key Allocation

- Status: Accepted
- Decision date: 2026-07-29
- Deciders: Pavels Gurskis
- Supersedes: None
- Superseded by: None

## Context

Humans need concise, recognizable task identifiers while automation and
relationships need globally unambiguous identity. A mutable title or
backend-generated row number cannot satisfy both. Multiple Projects and
Instances may allocate work concurrently, and users will copy task keys into
commits, discussions, and external artifact references.

Reusing an old key or changing it when a Project is renamed would make audit
history and durable automation ambiguous.

## Decision

Every Project has:

- a globally unique opaque Project identifier;
- an immutable uppercase Project key, such as `ACME`;
- a monotonically increasing task-number sequence.

Every Task has:

- a globally unique opaque UID, which is its canonical machine identity;
- an integer number allocated atomically within its Project;
- a stable human key formed as `PROJECT-NUMBER`, such as `ACME-42`.

Project keys are unique within an Instance. Archived Project keys are never
reused. Task numbers are allocated atomically, increase monotonically, may have
gaps, and are never reused. The Task key remains stable for the lifetime of the
Task.

The same Project key may exist in separate Instances; Instance identity
disambiguates it. Relationships store Task UIDs rather than parsing human keys.

A Task cannot move between Projects. Moving would change its human key and
namespace. Users instead create a new Task in the destination Project and link
it to the original with a non-blocking relationship such as `supersedes` or
`related_to`.

## Alternatives considered

### Expose only opaque UIDs

Opaque identifiers are robust for machines but cumbersome for operators and
external collaboration.

### Use one Instance-wide integer sequence

Global numbers would not communicate Project context and would create a
cross-Project allocation bottleneck without improving identity.

### Allow Project keys or Task keys to change

Renaming would make old references ambiguous and require redirects or aliases
throughout automation and audit data.

### Reuse numbers after deletion or archival

Reuse would allow the same visible key to identify different work over time,
which violates stable audit and reference semantics.

## Consequences

- Humans receive compact Project-prefixed keys while internal relationships use
  opaque UIDs.
- Persistence adapters must allocate task numbers atomically under concurrency.
- Deletion and archival never reclaim Project keys or task numbers.
- Gaps are valid and consumers must not infer task counts from the highest
  number.
- Cross-Instance interfaces must carry Instance identity where a human key
  alone is ambiguous.
- Project moves require explicit replacement and linkage rather than identity
  mutation.
- Task UID and key formats freeze at the Phase 8 exit gate and become formal
  compatibility commitments at `1.0.0`.

## References

- [Persistence contract](../persistence-contract.md)
- [Compatibility policy](../compatibility-policy.md)
- [Architecture](../architecture.md)
- [Glossary](../glossary.md)
- [ADR 0005: Semantic Persistence Interface](0005-semantic-persistence-interface.md)

