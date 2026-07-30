# ADR 0007: Human and Agent Identity Model

- Status: Accepted
- Decision date: 2026-07-29
- Deciders: Pavels Gurskis
- Supersedes: None
- Superseded by: None

## Context

Workaholic AI coordinates Humans and autonomous Agents that may act
concurrently and may become compromised. Claims, Results, grants, and
TaskEvents must identify the real actor. A shared identity for a team or all
Agents would make Attempt ownership ambiguous, weaken revocation, and erase
useful attribution.

V1 serves one organization per Instance, but organization membership does not
authorize every Subject for every Project. Embedded local operation must
preserve the same authorization and audit behavior as shared remote operation.

## Decision

Represent every independently operating Human or Agent as a distinct `Subject`
with an immutable identifier, kind, display name, and enabled state.

Subject kinds are:

```text
human
agent
```

Kind describes the operator; it does not grant permission. Authorization uses
an Instance-administrator role plus ProjectGrants with Viewer, Agent, Operator,
and Owner roles. Every read and mutation is checked against the active Subject
and target Project.

Each independently operating Agent receives its own Subject and Token. Shared
"all agents" credentials are not an accepted operating model. Capabilities
affect task selection only and never authorization.

Tokens are high-entropy bearer credentials belonging to one Subject. Workaholic
AI stores only Token hashes and supports expiry, revocation, and Subject
disablement. Raw Tokens do not appear in `.workaholic.env`, normal command
arguments, task content, Results, TaskEvents, or logs.

Human credentials use the operating-system credential store where available,
with a protected user-configuration fallback. Agent credentials are supplied
through trusted environment, mounted-secret, or orchestrator-secret channels.

LocalSession supplies authenticated Subject context and applies application
authorization even though the local filesystem remains part of the operating
system trust boundary. RemoteSession authenticates every request. Every
accepted mutation records authenticated Subject and request attribution.

### Phase delivery boundary

Phase 1 bootstrap creates one real enabled Human Subject named
`Local operator`, marks that Subject as the Instance administrator, grants it
the Owner role on the bootstrapped Project, and selects it as the sole
LocalSession actor. Every accepted Phase 1 Task creation and `task_created`
event records that Subject and a generated request identity.

Because Phase 1 is an embedded single-user slice whose filesystem is inside
the local trust boundary, it creates no bearer Token, stores no credential,
and provides no identity-management commands. Phase 5 adds Tokens, secure
credential storage, additional Human and Agent Subjects, and general
ProjectGrant administration. Those additions extend the Phase 1 identity;
they do not replace anonymous or placeholder bootstrap records.

## Alternatives considered

### Use one identity for all local activity

This would simplify bootstrap but make local Agent activity indistinguishable
from the Human operator and diverge from remote authorization semantics.

### Share one credential among all Agents

This would reduce credential issuance while preventing narrow revocation,
reliable Attempt ownership, and meaningful audit history.

### Derive permissions directly from Subject kind

Hard-coding Agent and Human permissions would prevent legitimate role
variation and conflate identity classification with Project authorization.

### Trust every Subject in the organization

Single-organization scope removes cross-organization isolation, not
least-privilege requirements among Projects or Subjects.

## Consequences

- Attempts, Results, and TaskEvents remain attributable to one actor.
- Operators can revoke or disable one compromised Agent without rotating every
  Agent.
- Bootstrap, Token lifecycle, credential storage, and ProjectGrant management
  beyond the initial Owner grant require explicit workflows.
- Phase 1 preserves attribution without prematurely creating a bearer
  credential inside an embedded local process.
- Local and remote contract tests must apply the same authorization matrix.
- Compromise remains effective within a Subject's legitimate grants until its
  Token expires or is revoked, so grants and lifetimes should stay narrow.
- Future SSO, OAuth, enterprise identity, custom roles, and policy languages
  require separate scope decisions.

## References

- [Threat model](../threat-model.md)
- [Glossary](../glossary.md)
- [Architecture](../architecture.md)
- [Product scope](../product-scope.md)
- [ADR 0006: Project Context Trust Model](0006-project-context-trust-model.md)
