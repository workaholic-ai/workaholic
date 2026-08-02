# Workaholic AI Glossary

This glossary defines the canonical v1 vocabulary shared by the architecture,
roadmap, contracts, CLI, and tests. The definitions describe accepted v1
semantics; they do not imply that a capability is implemented in the current
`0.3.0a1` package.

Terms use singular names and exact capitalization when they refer to domain or
interface concepts.

## Instance

One logical Workaholic AI installation and its persisted state. An Instance has
a stable identifier, contains Projects and Subjects, and serves exactly one
organization in v1. It may run as an embedded local runtime or through one
shared server process.

## Project

A task namespace within an Instance. A Project has a stable internal identifier
and an immutable, uppercase key used as the prefix for human-facing task keys.
It may be bound to multiple Workspaces. Project keys and task numbers are never
reused.

## Workspace

A local working directory bound to a Project. Multiple checkouts, worktrees, or
agent directories may be separate Workspaces for the same Project. A Workspace
supplies local path context; it is not a security principal or a second task
namespace.

## Subject

An independently authenticated Human or Agent identity. Every mutation is
attributed to a Subject. A Subject's kind describes who operates it, while
ProjectGrants and instance-administrator status determine authorization.

## Human

A Subject operated by a person, normally through interactive CLI output. Being
a Human does not automatically grant project or instance permissions. A Human
may submit completed work directly without an Attempt; that Result has null
Attempt attribution.

## Agent

A Subject operated by an autonomous or automated process. Each independently
operating Agent receives its own identity and credential so claims, Results,
and TaskEvents remain attributable. Being an Agent does not replace
authorization through ProjectGrants. Agent submission requires the current
owned Attempt.

## ProjectGrant

The assignment of one project-scoped role to a Subject for a Project. The v1
roles are Viewer, Agent, Operator, and Owner. A ProjectGrant never authorizes
access to another Project.

## Task

A desired outcome tracked inside one Project. A Task has a globally unique,
opaque UID and a stable human-facing key such as `ACME-42`. It records lifecycle
state, objective, dependencies, requirements, acceptance criteria, context,
optimistic version, and attribution. A Task cannot move between Projects.
V1 has no parent/child Task hierarchy: decomposition uses ordinary Tasks and
same-Project blocking dependencies.

## Claim

The current exclusive, expiring ownership record for one Task. A Claim records
its Task, owning Subject, Lease, and nullable Attempt identity. A Human Claim
has a null Attempt; an Agent Claim has the current non-null Attempt. No current
Claim means the Task is unclaimed. Capability filtering is not part of v1.

## Attempt

One Agent execution associated with an Agent Claim. Humans do not receive
synthetic Attempts for manual work. An Attempt has its own identifier, owner,
status, start and end timestamps, and Lease expiry. Its states are `active`,
`released`, `expired`, and `submitted`; the last three are terminal. Every
reclaim creates a new Attempt, including a reclaim by the same Agent.

## Lease

The time-bounded right attached to the current Claim. A Human renews a Claim;
an Agent heartbeats its current Attempt. Renewal succeeds only for the owner
while the Claim remains current and unexpired. Lease validity is decided
transactionally using the authoritative runtime clock, not by a background
scheduler or client clock. Human Claims use longer Lease windows than Agent
Claims.

## Result

Structured evidence submitted for a Task. A Human Result records the
authenticated Human and a null Attempt; an Agent Result requires the current
owned Attempt. A Result may record a comment, summary, acceptance-criterion
outcomes, artifact references and hashes, and proposed follow-ups. Proposed
follow-ups are inert data and do not automatically create Tasks or
relationships. Workaholic AI stores artifact references, not artifact
contents.

## TaskEvent

An immutable, typed, append-only record of a Task mutation or activity. A
TaskEvent records its event and task identities, authenticated Subject,
Subject kind, Attempt when applicable, request identity, structured payload,
timestamp, and ordered Instance cursor. One semantic mutation may append
multiple consecutive TaskEvents while incrementing the Task version once.

## Session

The presentation-independent interface through which official clients issue
application commands and receive result models. CLI behavior must not depend on
whether a Session is local or remote. The Session interface is an internal
architectural boundary, not a supported third-party Python API in v1.

## LocalSession

A Session implementation that invokes the application core in the current
process. It operates through a supported embedded JSON or SQLite persistence
adapter and still supplies authenticated Subject context for authorization and
audit behavior.

## RemoteSession

A Session implementation used by the official client to invoke the same
application commands through the private, versioned client/server protocol.
The server authenticates and authorizes the Subject and is authoritative for
state and time.

## Token

A high-entropy bearer credential belonging to one Subject. Only a Token hash is
stored by Workaholic AI. Raw Tokens must not appear in repository context,
normal command arguments, task data, events, or logs.

## Instance administrator

A trusted Instance-wide role that can create Projects, Subjects, Tokens, and
instance-wide grants. The administrator and the infrastructure controlling the
process, secrets, and persistence service are inside the trusted computing
base for v1.

## Idempotency key

A caller-supplied identifier that lets a retried mutation return its original
logical outcome without duplicating state or TaskEvents. It does not replace
authentication, authorization, optimistic versions, or Attempt validation.

## Capability

A scheduling label used to match an Agent with suitable work. A Capability does
not grant authorization and must never be interpreted as a Project role.
Capability-based Task scheduling is outside v1.

## Related documents

- [Architecture](architecture.md)
- [CLI automation contract](cli-contract.md)
- [Delivery roadmap](roadmap.md)
- [Persistence contract](persistence-contract.md)
- [Product scope](product-scope.md)
- [Compatibility policy](compatibility-policy.md)
- [Threat model](threat-model.md)
