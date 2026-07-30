# Workaholic AI CLI Automation Contract

- Status: Accepted Phase 0 baseline
- Decision date: 2026-07-29
- Contract family: `workaholic.cli/v1`
- Public surface: Documented JSON output of the `workaholic` executable

## Current implementation notice

This document specifies the accepted v1 automation contract through its Phase 8
freeze. The current `0.0.0` development package implements the versioned
envelopes and exposes all six Phase 1 commands through an injected Session
boundary. Its default executable composes the embedded LocalSession and SQLite
adapter, so the durable exact-directory local journey is available. No
compatibility guarantee applies before `1.0.0`.

## Normative language

The terms **must**, **must not**, **should**, and **may** describe requirements
for documented agent-facing commands. Examples illustrate those requirements
but do not add undocumented fields to the contract.

Canonical domain terminology is defined in the [glossary](glossary.md).

## Contract boundary

The supported agent automation interface is the versioned JSON emitted by the
`workaholic` CLI. The same interface applies whether the CLI selects
LocalSession or RemoteSession.

The following are not public automation contracts:

- human-readable tables, colors, spacing, help layout, and progress displays;
- Python modules, classes, functions, or Session signatures;
- server routes and private transport request or response models;
- JSON, SQLite, or PostgreSQL physical storage layouts;
- diagnostic prose when a machine-readable field exists.

The private official-client protocol uses the separate `workaholic/v1`
identifier. It is not a supported substitute for CLI JSON.

## Compatibility timeline

The `workaholic.cli/v1` identifier names the intended v1 family during
development; it does not create an early stability promise.

- Through Phase 7, intentional reviewed breaking changes are allowed.
- At the Phase 8 exit gate, the envelope, documented command schemas, error
  codes, identifier formats, and non-interactive behavior freeze.
- The release candidate validates the frozen contract without silent changes.
- The formal backward-compatibility guarantee begins with `1.0.0`.
- Beginning with `1.0.0`, compatible releases in major version 1 must preserve
  documented v1 behavior.

Pre-release automation must pin its Workaholic AI client version and must not
assume scripts or stored data survive upgrades.

## Invocation modes

### Human-readable mode

Human-readable mode is the default for interactive operators. Its presentation
may evolve without a compatibility event when the documented meaning and JSON
contract remain unchanged.

### JSON mode

A documented agent-facing command selects JSON mode with `--json`. A
non-streaming invocation writes exactly one UTF-8 JSON object followed by one
newline to stdout.

No banner, progress indicator, warning, log, prompt, or explanatory prose may
appear on stdout in JSON mode.

### Non-interactive mode

`--non-interactive` must prevent every confirmation, selection prompt,
credential prompt, pager, and terminal-dependent interaction.

If required input is absent or ambiguous, the command returns a structured
error instead of prompting. Non-interactive behavior must not depend on whether
stdin or stdout is attached to a terminal.

Agent-facing examples should use `--json --non-interactive` together.

## JSON envelope

### Success

A successful command returns:

```json
{
  "schema": "workaholic.cli/v1",
  "ok": true,
  "data": {}
}
```

The fields have these meanings:

| Field | Type | Requirement |
| --- | --- | --- |
| `schema` | string | Required and exactly `workaholic.cli/v1` |
| `ok` | boolean | Required and `true` |
| `data` | any JSON value | Required; command-specific documented result |

A success envelope must not contain an `error` field.

### Error

A command-level failure in JSON mode returns:

```json
{
  "schema": "workaholic.cli/v1",
  "ok": false,
  "error": {
    "code": "LEASE_LOST",
    "message": "The attempt for ACME-42 is no longer active.",
    "retryable": false
  }
}
```

The fields have these meanings:

| Field | Type | Requirement |
| --- | --- | --- |
| `schema` | string | Required and exactly `workaholic.cli/v1` |
| `ok` | boolean | Required and `false` |
| `error.code` | string | Required machine-readable identifier |
| `error.message` | string | Required nonempty explanation for a Human |
| `error.retryable` | boolean | Required retry guidance for this failure |

An error envelope must not contain a `data` field. Consumers use `error.code`,
not `error.message`, for control flow. A `retryable` value of `true` does not
make an unsafe mutation safe to repeat without its required idempotency key.

Documented error codes use uppercase snake case. Their stability begins at the
Phase 8 freeze and receives the formal compatibility guarantee at `1.0.0`.

### Serialization

- Output is valid UTF-8 JSON without a byte-order mark.
- Objects do not contain duplicate keys.
- Numbers must be valid interoperable JSON numbers; `NaN` and infinities are
  forbidden.
- Identifiers are strings and must not be inferred from display names.
- Timestamps are RFC 3339 strings in UTC with an explicit `Z` offset.
- Consumers must ignore unknown object fields unless a command contract
  explicitly declares an object closed.
- Field omission and JSON `null` are distinct and must follow the
  command-specific schema.

Streaming commands require separately documented framing before implementation.
They must not concatenate partial objects or mix human prose with JSON.

## Standard output and diagnostics

In JSON mode:

- stdout contains only the contract payload;
- stderr contains diagnostics, logs, and operational warnings;
- secret values must be redacted from both streams;
- the presence of stderr does not change envelope meaning;
- a successful command exits zero;
- an error envelope is accompanied by a nonzero exit status.

Phase 1 establishes nonzero exit categories `2`, `3`, `4`, `5`, and `10` as
specified below. Additional commands may add error identifiers before the
Phase 8 freeze, but they must map failures into a documented category.

Failures before the executable can establish JSON mode, such as operating-system
startup failures, are outside the envelope guarantee and may report only on
stderr.

## Mutation idempotency

Every documented mutation command must accept:

```text
--idempotency-key KEY
```

The key is opaque to Workaholic AI. It is scoped to the authenticated Subject
and logical operation. A committed mutation and its idempotency record are one
transaction.

Repeating the same operation with the same key and semantically identical input
returns the original logical outcome without duplicating state, Results, or
TaskEvents. Reusing the key with different input returns a structured
idempotency-conflict error and performs no mutation.

Idempotency does not replace:

- authentication or authorization;
- optimistic Task versions;
- current Attempt identity and ownership;
- Lease validity;
- normal input and state-transition validation.

Durable Agents should generate unique keys, retain them across ambiguous retry
outcomes, and use bounded retries only when the failure and command semantics
permit.

## Structured input

Commands with large or nested payloads must accept a documented file input.
The common form is:

```text
--input-file PATH
```

Command-specific aliases such as `--result-file` may be used when their meaning
is clearer, but they obey the same rules.

- Input files are read as UTF-8.
- JSON payloads are validated before any mutation.
- A path of `-` explicitly selects stdin where the command documents stdin
  support.
- Commands never consume stdin implicitly in non-interactive mode.
- File input and inline fields that supply the same value are rejected as
  ambiguous unless the command documents deterministic precedence.
- Inputs are size-bounded and invalid or oversized payloads return structured
  errors without partial writes.
- Credentials are not accepted in task or Result input payloads.

## Identity, context, and security

- Authentication credentials must not appear in normal positional arguments or
  command examples.
- `.workaholic.env` supplies untrusted Project and Workspace context, never
  credentials or arbitrary endpoints.
- The trusted profile or runtime owns remote endpoint and credential
  configuration.
- Every mutation is attributed to the authenticated Subject and request.
- Agent Capabilities affect scheduling, not authorization.
- LocalSession and RemoteSession must enforce the same ProjectGrant and Attempt
  rules.
- Human-readable messages and JSON diagnostics must redact Tokens, backend
  credentials, and other secrets.

## Command-specific schemas

Each agent-facing command specification must define:

- required and optional arguments;
- whether it reads or mutates state;
- authorization requirement;
- `data` shape on success;
- documented `error.code` values;
- idempotency behavior for mutations;
- file or stdin input behavior;
- ordering and pagination for collections;
- whether unknown enum values must be tolerated;
- exit behavior and retry implications.

A command is not contract-complete until executable tests cover those points in
both supported Session modes where applicable.

## Phase 1 command contract

This section is normative for the Phase 1 LocalSession vertical slice. Every
command below accepts `--json` and `--non-interactive`; these flags have the
global behavior defined above. The Phase 1 commands do not start a daemon,
select RemoteSession, search parent directories for context, load configurable
profiles, or require a Token.

### Shared Phase 1 objects

Command data uses these closed Phase 1 shapes. Later pre-freeze phases may add
fields, and consumers must continue to ignore unknown fields as required by the
envelope contract.

| Object | Required fields |
| --- | --- |
| `instance` | `id` as a nonempty string |
| `project` | `id` and `key` as strings |
| `subject` | `id`, `kind`, `display_name`, `is_instance_admin`, and `project_role` |
| `workspace` | absolute `root` and absolute `context_file` paths |

For Phase 1, `subject.kind` is `human`, `subject.display_name` is
`Local operator`, `subject.is_instance_admin` is `true`, and
`subject.project_role` is `owner`. Project keys match
`[A-Z][A-Z0-9]{1,15}` and are immutable.

A serialized `task` contains exactly these required fields:

| Field | JSON type | Phase 1 rule |
| --- | --- | --- |
| `uid` | string | Canonical globally unique Task identity |
| `project_id` | string | Owning Project identity |
| `number` | integer | Positive, monotonic within the Project |
| `key` | string | Immutable `PROJECT-NUMBER` human identity |
| `title` | string | Trimmed length from 1 through 200 Unicode characters |
| `objective` | string | Trimmed length from 1 through 4,000 Unicode characters |
| `state` | string | `open` |
| `priority` | integer | From 0 through 100 |
| `version` | integer | `1` at creation |
| `created_by` | string | Bootstrap Human Subject identity |
| `created_at` | string | RFC 3339 UTC timestamp |
| `updated_at` | string | Same as `created_at` at creation |

### `workaholic up`

```text
workaholic up --project-key KEY
  [--idempotency-key KEY] [--json] [--non-interactive]
```

`--project-key` is required and validated before persistent state changes. The
command initializes the SQLite schema, one local Instance, one Human Subject,
Instance-administrator status, one Project, and an Owner ProjectGrant. It then
writes a strict `.workaholic.env` to the exact current directory. It creates no
Token or TaskEvent.

Success `data` is:

```json
{
  "instance": {"id": "ins_01..."},
  "project": {"id": "prj_01...", "key": "ACME"},
  "subject": {
    "id": "sub_01...",
    "kind": "human",
    "display_name": "Local operator",
    "is_instance_admin": true,
    "project_role": "owner"
  },
  "workspace": {
    "root": "/work/acme",
    "context_file": "/work/acme/.workaholic.env"
  }
}
```

### `workaholic status`

```text
workaholic status [--json] [--non-interactive]
```

The command is read-only. It reads only
`<current-working-directory>/.workaholic.env`; Phase 1 does not search a parent
directory. Success `data` is:

```json
{
  "mode": "local",
  "schema_version": 1,
  "instance": {"id": "ins_01..."},
  "project": {"id": "prj_01...", "key": "ACME"},
  "subject": {
    "id": "sub_01...",
    "kind": "human",
    "display_name": "Local operator",
    "is_instance_admin": true,
    "project_role": "owner"
  }
}
```

### `workaholic project list`

```text
workaholic project list [--json] [--non-interactive]
```

The command is read-only. Success `data` contains `projects`, an array of
`project` objects ordered by project key ascending:

```json
{"projects": [{"id": "prj_01...", "key": "ACME"}]}
```

### `workaholic task add`

```text
workaholic task add TITLE [--objective TEXT] [--priority INTEGER]
  [--idempotency-key KEY] [--json] [--non-interactive]
```

The title is required. Omitted `--objective` defaults to the normalized title;
omitted `--priority` defaults to `50`. The created Task has state `open` and
version `1`. The Task and its attributable `task_created` TaskEvent commit in
one transaction. Success `data` is:

```json
{"task": {"uid": "tsk_01...", "project_id": "prj_01...", "number": 1, "key": "ACME-1", "title": "First task", "objective": "First task", "state": "open", "priority": 50, "version": 1, "created_by": "sub_01...", "created_at": "2026-07-30T10:00:00Z", "updated_at": "2026-07-30T10:00:00Z"}}
```

### `workaholic task list`

```text
workaholic task list [--cursor CURSOR] [--limit INTEGER]
  [--json] [--non-interactive]
```

The command is read-only. `--limit` defaults to `100`, must be positive, and
must not exceed `500`. Tasks are ordered by task number ascending. The cursor
is opaque and bound to the Project and ordering; callers must not construct or
interpret it. Success `data` is:

```json
{"tasks": [], "next_cursor": null}
```

`next_cursor` is a string only when another page exists, otherwise it is JSON
`null`. Paging through unchanged records neither duplicates nor omits a Task.

### `workaholic task show`

```text
workaholic task show TASK [--json] [--non-interactive]
```

`TASK` accepts a canonical Task UID or a human key such as `ACME-1`. The
command is read-only. Success `data` is:

```json
{"task": {"uid": "tsk_01...", "project_id": "prj_01...", "number": 1, "key": "ACME-1", "title": "First task", "objective": "First task", "state": "open", "priority": 50, "version": 1, "created_by": "sub_01...", "created_at": "2026-07-30T10:00:00Z", "updated_at": "2026-07-30T10:00:00Z"}}
```

### Phase 1 idempotency

`up` and `task add` accept an optional `--idempotency-key`. The key and a
canonical fingerprint of the normalized semantic input are stored atomically
with the successful outcome. Repeating the same operation with the same key
and input returns that outcome. Reusing the key with different input returns
`IDEMPOTENCY_CONFLICT` and changes neither state nor events.

`up` is also naturally idempotent without a caller key: repeating it for the
existing Phase 1 Project returns the existing bootstrap entities, and a retry
after context-file failure safely completes the binding. A different Project
key returns `PROJECT_KEY_CONFLICT`. By contrast, omitting the key from
`task add` does not make an ambiguous failed Task creation safe to retry.

### Phase 1 errors and exits

The error message is for Humans and is not an automation discriminator. Phase
1 code and exit mappings are:

| Error code | Exit | Retryable | Meaning |
| --- | ---: | :---: | --- |
| `INVALID_INPUT` | 2 | false | Invalid argument, option, value, or cursor |
| `CONTEXT_NOT_FOUND` | 3 | false | No exact-directory context file |
| `CONTEXT_INVALID` | 3 | false | Context file is malformed or untrusted |
| `NOT_INITIALIZED` | 3 | false | Referenced local Instance is not initialized |
| `TASK_NOT_FOUND` | 3 | false | Task UID or key does not resolve |
| `PROJECT_KEY_CONFLICT` | 4 | false | Project key belongs to another Project |
| `IDEMPOTENCY_CONFLICT` | 4 | false | Key was reused with different input |
| `PERMISSION_DENIED` | 5 | false | Active Subject lacks the required grant |
| `SCHEMA_UNSUPPORTED` | 10 | false | Store schema is missing or unsupported |
| `STORAGE_BUSY` | 10 | true | Bounded SQLite lock acquisition was exhausted |
| `STORAGE_UNAVAILABLE` | 10 | false | Local storage cannot be opened or made durable |
| `INTERNAL_ERROR` | 10 | false | Redacted unexpected operational failure |

The command-to-error surface is:

| Command | Documented errors in addition to storage, schema, permission, and internal errors |
| --- | --- |
| `up` | `INVALID_INPUT`, `CONTEXT_INVALID`, `PROJECT_KEY_CONFLICT`, `IDEMPOTENCY_CONFLICT` |
| `status` | `CONTEXT_NOT_FOUND`, `CONTEXT_INVALID`, `NOT_INITIALIZED` |
| `project list` | `CONTEXT_NOT_FOUND`, `CONTEXT_INVALID`, `NOT_INITIALIZED` |
| `task add` | `INVALID_INPUT`, `CONTEXT_NOT_FOUND`, `CONTEXT_INVALID`, `NOT_INITIALIZED`, `IDEMPOTENCY_CONFLICT` |
| `task list` | `INVALID_INPUT`, `CONTEXT_NOT_FOUND`, `CONTEXT_INVALID`, `NOT_INITIALIZED` |
| `task show` | `INVALID_INPUT`, `CONTEXT_NOT_FOUND`, `CONTEXT_INVALID`, `NOT_INITIALIZED`, `TASK_NOT_FOUND` |

Command parser usage failures also use exit `2`. Once JSON mode is established,
the CLI maps them to an `INVALID_INPUT` envelope. A failure before Python can
establish JSON mode remains outside the envelope guarantee.

## Conformance requirements

Contract tests must verify at least:

- exact success and error envelope invariants;
- JSON-only stdout and diagnostics-only stderr;
- valid UTF-8 serialization and one trailing newline;
- no prompt in non-interactive mode;
- missing, invalid, and oversized structured input;
- identical logical outcomes through LocalSession and RemoteSession;
- idempotent retry and conflicting key reuse;
- optimistic-version and stale-Attempt failures;
- authentication and cross-Project authorization failures;
- Token and credential redaction;
- tolerance of documented optional unknown fields;
- pinned pre-release client behavior and frozen v1 fixtures.

## Related decisions and documents

- [ADR 0003: CLI JSON as the Public Automation Contract](adr/0003-cli-json-automation-contract.md)
- [ADR 0002: Local and Remote Sessions](adr/0002-local-and-remote-sessions.md)
- [ADR 0004: Private Versioned Client/Server Protocol](adr/0004-private-versioned-client-server-protocol.md)
- [Compatibility policy](compatibility-policy.md)
- [Threat model](threat-model.md)
- [Architecture](architecture.md)
