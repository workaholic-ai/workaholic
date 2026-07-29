# Compatibility Policy

- Status: Accepted
- Decision date: 2026-07-29
- Applies to: Workaholic AI source, pre-releases, and v1 releases
- Contact: [pg@ithesion.com](mailto:pg@ithesion.com)

## Purpose

This policy identifies which Workaholic AI surfaces carry compatibility
guarantees and when those guarantees begin. It prevents experimental
pre-release identifiers from being mistaken for stable promises while ensuring
the release candidate validates the exact contracts shipped in `1.0.0`.

Normative terms such as **must**, **must not**, **should**, and **may** describe
requirements on Workaholic AI implementations and official clients.

## Versioned surfaces

Workaholic AI has several independently versioned surfaces:

| Surface | Version form | Stability at `1.0.0` |
| --- | --- | --- |
| Python distribution | Semantic release version | Public release lifecycle |
| CLI JSON | `workaholic.cli/v1` | Supported automation contract |
| Project context | `.workaholic.env` context version | Supported discovery contract |
| Persisted store | Backend-independent schema version | Supported for the documented release line |
| Private client/server protocol | `workaholic/v1` | Official client/server compatibility only |
| Task and event identifiers | Documented identifier formats | Stable identity contract |

An identifier containing `v1` before the Phase 8 freeze identifies the intended
contract family. It does not create an early compatibility guarantee.

## Compatibility timeline

### Internal foundation through Phase 7

- Versions before `1.0.0` are development releases.
- Breaking changes to commands, CLI JSON, the private protocol, context files,
  persisted schemas, error codes, and identifier representations are allowed
  when required to reach the accepted v1 design.
- Pre-release stores are disposable. An upgrade may require an explicit reset.
- Automatic schema migration, cross-backend conversion, and import/export are
  not provided.
- Unsupported schema or protocol versions must fail explicitly rather than be
  partially interpreted.
- A breaking change must be intentional, reviewed, reflected in the relevant
  contract or ADR, and described in release notes once release notes exist.

Pre-release users must pin the client version used by durable automation and
must not assume data or scripts survive an upgrade.

### Phase 8 contract freeze

At the Phase 8 exit gate, the following become release-candidate contracts:

- persisted schema version;
- `.workaholic.env` context version;
- CLI JSON schema `workaholic.cli/v1`;
- private protocol `workaholic/v1`;
- task and event identifier formats;
- documented machine-readable error codes.

After this gate, a persisted-schema change is not accepted unless the affected
feature is removed from v1 or migration support is explicitly added. Any other
incompatible contract change requires a documented release-blocking defect,
another review of all affected golden journeys, and a new release-candidate
validation cycle.

### Release candidate

The release candidate tests the frozen contracts intended for `1.0.0`. The
final release must not silently diverge from those contracts. If a critical fix
requires an incompatible change, a new release candidate must expose and
validate that change before `1.0.0`.

The project intends release-candidate data and automation to carry forward to
`1.0.0`, but the formal public backward-compatibility promise begins with the
final `1.0.0` release.

### Version 1

Beginning with `1.0.0`:

- documented public behavior follows semantic-versioning compatibility rules;
- the `workaholic.cli/v1` automation contract must remain backward compatible
  throughout major version 1;
- task UIDs and human task keys must continue to identify the same tasks and
  must never be reused;
- official v1 clients and servers must negotiate their supported private
  protocol range and reject incompatible peers explicitly;
- the `1.0.x` release line must not change the persisted schema;
- a later v1 release must not require a persisted-schema change until a
  supported upgrade policy and migration path have been defined.

## Public CLI JSON compatibility

The versioned JSON emitted by documented agent-facing CLI commands is the
supported automation interface.

Backward-compatible changes may:

- add a new command;
- add an optional command argument;
- add an optional object field;
- add a new enum or event value where the contract documents consumers must
  accept unknown values;
- add a new structured error code;
- clarify text without changing machine-readable meaning.

Breaking changes include:

- removing or renaming a documented command, flag, field, or error code;
- changing a field's type, requiredness, or meaning;
- changing success or error envelope structure incompatibly;
- writing non-envelope content to stdout in JSON mode;
- introducing an interactive prompt in non-interactive mode;
- changing task identity or idempotency semantics.

Consumers of `workaholic.cli/v1` must ignore unknown object fields unless a
contract explicitly states that an object is closed. Consumers must not infer
meaning from human-readable messages when a machine-readable field exists.

Human-readable tables, whitespace, colors, progress displays, diagnostic prose,
and help layout are not automation contracts.

## Persisted-store compatibility

Every store records a schema version. A process must validate that version
before reading or mutating application data.

V1 compatibility means a supported Workaholic AI release can continue using a
store created by an earlier supported release in the same documented compatible
line. It does not mean:

- changing an existing instance from one backend to another;
- JSON-to-SQLite or SQLite-to-PostgreSQL conversion;
- import or export between instances;
- automatic interpretation of an unknown schema;
- support for a development store after a documented pre-release reset.

Backups and backend-native recovery remain operator responsibilities. A process
must leave an unsupported store unchanged and return an actionable error.

## Private protocol compatibility

The network protocol is an implementation detail for official Workaholic AI
clients. Versioning protects mixed official-client and server deployments; it
does not make the HTTP routes a public API.

Official clients and servers must exchange protocol and product-version
information before normal operation. The server is authoritative for its
minimum supported client version and advertised features. An unsupported peer
must fail explicitly without attempting a partially compatible mutation.

Direct third-party use of server routes, internal request models, or transport
details carries no compatibility guarantee.

## Python API and internal implementation

Unless a future document explicitly designates an API as public, Python modules,
classes, functions, SQL layouts, JSON storage layout, server routes, and
application composition are internal. Refactoring them is not a compatibility
break when all supported observable behavior remains unchanged.

The session abstraction is an architectural boundary shared by official
presentation layers. It is not a supported third-party Python SDK in v1.

## Deprecation and incompatible changes

After `1.0.0`, a public contract may be deprecated only with documentation,
release-note visibility, and a supported replacement. Removal or another
incompatible change requires the next major contract or product version unless
a security issue makes continued support unsafe.

Security fixes may disable unsafe behavior immediately. When that happens, the
release must document the affected surface, safe replacement, and operational
impact without disclosing details that would put users at additional risk.
