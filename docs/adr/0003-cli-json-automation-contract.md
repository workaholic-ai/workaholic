# ADR 0003: CLI JSON as the Public Automation Contract

- Status: Accepted
- Decision date: 2026-07-29
- Deciders: Pavels Gurskis
- Supersedes: None
- Superseded by: None

## Context

Autonomous agents need a deterministic, non-interactive interface that works
for embedded and remote Sessions. Human-readable output is valuable for
operators but changes in tables, color, progress, and explanatory text should
not break automation.

Publishing internal Python interfaces or server routes would create additional
compatibility surfaces before the domain and deployment model are stable. A
single executable interface also supports agents regardless of implementation
language or whether a future client remains Python-based.

## Decision

The versioned JSON output of documented `workaholic` commands is the supported
v1 automation interface. Its schema identifier is:

```text
workaholic.cli/v1
```

Every non-streaming command in JSON mode returns one success or error envelope.
The required success fields are `schema`, `ok`, and `data`. The required error
fields are `schema`, `ok`, `error.code`, `error.message`, and
`error.retryable`.

JSON mode writes only the envelope to stdout. Diagnostics and logs go to
stderr. Non-interactive mode never prompts. Documented mutations accept
idempotency keys, and commands with large structured payloads accept file or
explicit standard-input sources. Credentials are never accepted through normal
visible command arguments.

Human-readable output is not an automation contract. Neither Python internals
nor the private client/server protocol become public through this decision.
Detailed field, I/O, idempotency, and compatibility requirements are normative
in the [CLI contract](../cli-contract.md).

The `workaholic.cli/v1` identifier names the intended v1 contract family during
development. Breaking changes remain allowed through Phase 7. The contract
freezes at the Phase 8 exit gate, is validated unchanged by the release
candidate, and receives its formal backward-compatibility guarantee at
`1.0.0`.

## Alternatives considered

### Treat human-readable CLI output as stable

This would make common shell inspection easy but would prevent harmless
presentation improvements and encourage fragile parsing.

### Publish the server HTTP routes as the agent API

This would exclude embedded-only use, expose transport internals, and require a
public network API lifecycle. Official agents use the CLI in both local and
remote modes.

### Publish a Python SDK first

A Python SDK would not serve non-Python agents and would make internal models a
premature compatibility commitment.

### Emit unversioned command-specific JSON

Unversioned shapes would make evolution and compatibility failures ambiguous.
A shared envelope provides explicit contract identity and common error
semantics.

## Consequences

- Agent authors can depend on one transport-independent executable contract.
- Every agent-facing feature requires JSON-mode and non-interactive tests.
- Human output may evolve independently of machine-readable meaning.
- The CLI must rigorously separate stdout from diagnostics.
- Durable automation must pin pre-release clients until the `1.0.0` guarantee.
- New optional fields can be added compatibly only where consumers are required
  to tolerate them.
- Any future public API or SDK requires a separate scope and compatibility
  decision.

## References

- [CLI contract](../cli-contract.md)
- [Compatibility policy](../compatibility-policy.md)
- [Product scope](../product-scope.md)
- [Architecture](../architecture.md)
- [ADR 0002: Local and Remote Sessions](0002-local-and-remote-sessions.md)
- [ADR 0004: Private Versioned Client/Server Protocol](0004-private-versioned-client-server-protocol.md)

