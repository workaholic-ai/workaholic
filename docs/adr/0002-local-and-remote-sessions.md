# ADR 0002: Local and Remote Sessions

- Status: Accepted
- Decision date: 2026-07-29
- Deciders: Pavels Gurskis
- Supersedes: None
- Superseded by: None

## Context

Workaholic AI must support embedded local operation before distributed team
coordination while preserving one CLI and one set of task semantics. A
presentation layer that branches into local storage calls and remote HTTP calls
would duplicate authorization, error mapping, and workflow behavior. It would
also make the planned TUI dependent on CLI or transport details.

Local operation must remain lightweight and must not require a daemon. Remote
operation must authenticate against a shared server without turning server
routes into the supported agent interface.

## Decision

Define a presentation-independent `Session` boundary with two official
implementations:

- `LocalSession` invokes the application core in the current process through a
  supported embedded persistence adapter.
- `RemoteSession` translates the same application commands and result models
  through the private client/server protocol.

The CLI, and a future TUI, depend on the Session interface rather than concrete
persistence or transport adapters. Both implementations carry authenticated
Subject context and apply the same application authorization, task lifecycle,
Attempt, Lease, idempotency, and error semantics.

The Session boundary owns application command invocation and result delivery.
It does not own domain rules, persistence transactions, CLI presentation, or
remote route definitions. LocalSession must not bypass application
authorization merely because it runs under the local operating-system account.

Normal local CLI startup must not start a daemon. Normal remote CLI startup must
not eagerly import server frameworks, PostgreSQL drivers, or scheduling code.

The Session interface is an internal architecture contract for official
presentation layers. It is not a supported third-party Python SDK in v1.

## Alternatives considered

### Let each CLI command choose local storage or HTTP

This would reduce the initial abstraction count, but it would duplicate
workflow behavior across commands and make local and remote conformance
difficult to prove.

### Always run a local server

A localhost server would make transport uniform, but it would add lifecycle,
port, authentication, and failure concerns to the smallest local workflow. V1
requires embedded operation without a persistent daemon.

### Implement the TUI by invoking the CLI

This would reuse the executable superficially while coupling the TUI to text or
JSON presentation and subprocess behavior. The TUI should share application
models and Session semantics directly.

### Publish Session as the public automation API

A Python API would add a compatibility surface and language-specific SDK
commitment. The supported v1 automation interface is versioned CLI JSON.

## Consequences

- Local and remote behavior can share application and contract tests.
- Presentation layers stay independent of storage and private route details.
- Adapters must translate failures into common application outcomes.
- Composition roots must select a Session explicitly and keep optional server
  dependencies out of ordinary client startup.
- Refactoring the Python Session interface is allowed when supported CLI
  behavior remains unchanged.
- A later native client can preserve the Session boundary without preserving
  Python packaging internals.

## References

- [Architecture](../architecture.md)
- [Glossary](../glossary.md)
- [Threat model](../threat-model.md)
- [CLI contract](../cli-contract.md)
- [ADR 0003: CLI JSON Automation Contract](0003-cli-json-automation-contract.md)
- [ADR 0004: Private Versioned Client/Server Protocol](0004-private-versioned-client-server-protocol.md)
