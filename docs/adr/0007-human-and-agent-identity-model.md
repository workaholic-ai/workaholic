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
  require explicit workflows.
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

