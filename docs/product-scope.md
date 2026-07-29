# Workaholic AI Product Scope

- Status: Accepted
- Decision date: 2026-07-29
- Product owner: Pavels Gurskis
- Code ownership and security contact:
  [pg@ithesion.com](mailto:pg@ithesion.com)

## Product statement

Workaholic AI is a CLI-first task coordination system for human operators and
autonomous agents. It provides the same task semantics in an embedded local
runtime and through a shared server, allowing a workflow to begin on one
developer's machine and later operate across a single organization without
changing the supported agent interface.

The supported agent automation interface is the versioned JSON output of the
`workaholic` CLI. The client/server transport is private implementation detail,
not a public API.

## Intended users

V1 serves:

- solo developers coordinating their own work and local autonomous agents;
- development teams sharing projects through one organization-controlled
  Workaholic AI instance;
- automation authors who require deterministic, non-interactive, structured
  task operations.

Local agent execution is delivered before distributed coordination so task and
lease semantics can be validated through real use. Both local agent execution
and distributed team coordination are required before v1 is complete.

## Required v1 outcomes

### Distribution and runtime

- Publish the `workaholic-ai` Python distribution with the `workaholic` console
  command.
- Support ephemeral execution through `uvx` and persistent installation through
  `uv tool install`.
- Run embedded local commands without a persistent daemon.
- Run one authenticated server process per shared instance.
- Keep local and remote behavior behind the same session interface.

### Projects and context

- Host multiple projects in one instance.
- Bind multiple working directories to a project.
- Discover project context from a strict, nontracked `.workaholic.env` file.
- Keep credentials and arbitrary remote endpoints out of repository-controlled
  context files.
- Assign immutable project keys and stable, never-reused task keys such as
  `ACME-42`.

### Human and agent workflow

- Support task creation, editing, dependencies, blocking, review, completion,
  and cancellation.
- Coordinate agents through atomic claims, expiring attempts, heartbeats,
  releases, retries, and stale-attempt rejection.
- Accept structured results and external artifact references without storing
  artifact contents.
- Record typed, attributable, append-only task events.
- Protect updates with optimistic versions and retries with idempotency keys.

### Identity and authorization

- Give independently operating humans and agents distinct subjects and bearer
  credentials.
- Enforce project-scoped Viewer, Agent, Operator, and Owner roles plus an
  instance-administrator role.
- Apply the same application-level authorization rules in local and remote
  sessions.

### Persistence and shared operation

- Provide JSON and SQLite embedded backends.
- Provide JSON, SQLite, and PostgreSQL server backends within their documented
  single-process deployment constraints.
- Require every backend to pass the same observable-behavior contract.
- Fail explicitly on unsupported store schema versions.

### Supported interfaces

- Provide human-readable CLI output for operators.
- Provide versioned JSON CLI envelopes, stable machine-readable errors after
  the compatibility freeze, non-interactive operation, and file or standard
  input for large payloads.
- Version the private client/server protocol for official-client compatibility.
- Preserve a presentation-independent session boundary for a later TUI.

## V1 security and tenancy boundary

Each v1 instance serves one organization.

The instance administrator and the infrastructure controlling the process,
host, secrets, and persistence service are trusted. A party with administrative
host or database access is outside the application's isolation boundary.

Human and agent subjects are not implicitly trusted merely because they belong
to the organization. Authorization must constrain them to their granted
projects and operations. The design must limit the effect of a compromised
agent or stolen bearer token to the permissions associated with that subject.
All mutations must remain attributable to the authenticated subject.

Local filesystem access remains part of the embedded runtime's security
boundary. Repository-controlled context files are untrusted configuration and
must never supply credentials, execute commands, or redirect credentials to an
arbitrary endpoint.

Cross-organization tenant isolation, untrusted instance administrators, and a
public multi-tenant hosted service are not v1 requirements.

The [threat model](threat-model.md) records the trusted components, threats,
required mitigations, and phase-specific verification for this boundary.

## Explicitly outside v1

The following capabilities must not delay v1:

- storage conversion, import/export, and automated schema migrations;
- a TUI, browser UI, public API, or supported language SDK;
- GitHub synchronization, webhooks, or a plugin system;
- cross-project blocking dependencies;
- SSO, OAuth, enterprise identity providers, custom roles, or a policy
  language;
- managed blob or attachment storage;
- a workflow designer or automated acceptance checker;
- horizontal server scaling guarantees;
- an official hosted service;
- a self-contained native client or official OCI server image.

These items require separate scope decisions. Architecture-ready boundaries do
not make them implicit v1 commitments.

## Foundation metadata

| Item | Decision |
| --- | --- |
| Product | Workaholic AI |
| Repository | `workaholic-ai/workaholic` |
| Distribution | `workaholic-ai` |
| Import package | `workaholic` |
| Console command | `workaholic` |
| Initial internal version | `0.0.0` |
| License | Apache License 2.0 (`Apache-2.0`) |
| Copyright holder | Pavels Gurskis |
| Minimum development Python | Python 3.14 |
| Phase 0 CI Python | Python 3.14 |
| Code ownership and security contact | [pg@ithesion.com](mailto:pg@ithesion.com) |

Python 3.14 is the only Phase 0 development and CI line. The final public
operating-system and Python support matrix will be fixed before the release
candidate. Until then, no untested Python or operating-system combination is
advertised as supported.

## Scope and decision control

Changes to a required v1 outcome, the tenancy boundary, or a public contract
require an architecture decision record. Removing a required outcome requires
an explicit product-scope decision; silently moving it to the backlog is not
acceptable.

Pre-1.0 compatibility and the contract-freeze process are defined in
[Compatibility policy](compatibility-policy.md). Package and command naming are
defined in [ADR 0001](adr/0001-package-and-executable-naming.md). The normative
delivery boundaries are defined in the
[CLI automation contract](cli-contract.md) and
[persistence contract](persistence-contract.md).
