# ADR 0004: Private Versioned Client/Server Protocol

- Status: Accepted
- Decision date: 2026-07-29
- Deciders: Pavels Gurskis
- Supersedes: None
- Superseded by: None

## Context

RemoteSession needs a network protocol for official clients and servers.
Independent client and server upgrades require explicit compatibility
negotiation, even though third parties are not expected to call the transport
directly. Leaving the protocol unversioned would make mixed deployments fail
partially or mutate state under incompatible assumptions.

The public automation interface is already the CLI JSON contract. Promoting the
underlying transport to a public API would create duplicate public models and
route-level compatibility obligations.

## Decision

Use a private, explicitly versioned client/server protocol for official
Workaholic AI clients. The intended v1 protocol family is:

```text
workaholic/v1
```

Before normal operation, official clients and servers exchange protocol family,
product version, minimum supported client version, advertised features, and
Instance identity. The server is authoritative for its supported range,
features, state, and timestamps.

An incompatible or unexpected peer must be rejected explicitly before a
partially compatible mutation. RemoteSession verifies that the responding
Instance matches trusted profile context. Safe retries are bounded and limited
to reads or mutations protected by idempotency semantics.

Remote bearer-token traffic uses HTTPS through trusted deployment
infrastructure. Repository-controlled `.workaholic.env` files cannot select an
arbitrary endpoint; trusted user or runtime configuration owns the URL and
credential.

Routes, transport request models, response models, and wire details are private
implementation details. Direct third-party use has no compatibility guarantee.
The protocol version protects supported official client/server combinations; it
does not create a public HTTP API.

The protocol family may change incompatibly through Phase 7. It freezes at the
Phase 8 exit gate for the release candidate. Beginning with `1.0.0`, official
v1 clients and servers must negotiate and reject unsupported combinations
explicitly.

## Alternatives considered

### Use unversioned routes until release

This would postpone naming but allow silent incompatibility to spread through
official clients and tests. Early explicit negotiation makes failure behavior
testable.

### Make the HTTP protocol the public automation API

This would require public route documentation, authentication commitments, and
long-term wire compatibility while offering no embedded local path. The CLI
already provides one public automation surface.

### Reuse `workaholic.cli/v1` as the protocol version

The CLI envelope and private transport evolve for different audiences and
compatibility purposes. Separate identifiers prevent accidental coupling.

### Require exact product-version equality

Exact equality would make safe mixed-version operation impossible. Negotiated
ranges and advertised features allow explicit supported combinations.

## Consequences

- Official clients fail clearly when a server is incompatible or unexpected.
- Client/server compatibility tests must exercise negotiation before
  mutations.
- Private routes may be refactored without a public API deprecation when
  supported official combinations still work.
- Operators must configure HTTPS, trusted endpoints, and expected Instance
  identity.
- Server capability and minimum-client declarations become operational
  responsibilities.
- A future public HTTP API requires its own contract, version family, and ADR.

## References

- [Compatibility policy](../compatibility-policy.md)
- [Threat model](../threat-model.md)
- [Architecture](../architecture.md)
- [CLI contract](../cli-contract.md)
- [ADR 0002: Local and Remote Sessions](0002-local-and-remote-sessions.md)
- [ADR 0003: CLI JSON Automation Contract](0003-cli-json-automation-contract.md)

